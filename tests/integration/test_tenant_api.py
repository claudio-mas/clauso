"""API HTTP com dois Tenants: cada chave lê e altera só a própria empresa, com scopes e erros.

As chaves são emitidas pelo código de produção e apresentadas em `Authorization: Bearer`. Higiene
de falhas: marcador `sensitive`; nenhum `assert` com chave ou resposta nos operandos (só `require`,
com status e código de erro); todo pedido passa por `send`.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import httpx
import pytest
from api_support import bearer, body_of, require_error, require_status, running_app, send
from db_support import sensitive_components
from fastapi import FastAPI
from pydantic import SecretStr
from seeding import SeededTenant, emit_key, read_tenant
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine

from cce.integrations.api_keys.service import IssuedKey
from cce.shared.config import Settings
from cce.shared.ids import encode_key_id, encode_tenant_id

from .support import assert_no_leaks, require

pytestmark = [pytest.mark.integration, pytest.mark.sensitive]

Api = tuple[FastAPI, httpx.AsyncClient]
TENANT = "/api/v1/tenant"
CONTEXT = "/api/v1/auth/context"
FORBIDDEN_WORDS = ("digest", "secret", "pepper", "hash")


async def key_for(
    engine: AsyncEngine, settings: Settings, tenant: SeededTenant, *scopes: str
) -> IssuedKey:
    overrides: dict[str, Any] = {"scopes": list(scopes)} if scopes else {}
    _, issued = await emit_key(engine, settings, tenant, **overrides)
    return issued


# --- GET próprio e X-Tenant-ID -----------------------------------------------------------------


async def test_each_key_reads_only_its_own_tenant(
    api: Api,
    migrator_engine: AsyncEngine,
    api_settings: Settings,
    tenant_a: SeededTenant,
    tenant_b: SeededTenant,
) -> None:
    _, client = api
    key_a = await key_for(migrator_engine, api_settings, tenant_a)
    key_b = await key_for(migrator_engine, api_settings, tenant_b)

    body_a = require_status(
        await send(client, "GET", TENANT, headers=bearer(key_a.raw_key)), 200, "A"
    )
    body_b = require_status(
        await send(client, "GET", TENANT, headers=bearer(key_b.raw_key)), 200, "B"
    )

    require(body_a["id"] == encode_tenant_id(tenant_a.id), "A did not read Tenant A")
    require(body_a["name"] == tenant_a.name and body_a["slug"] == tenant_a.slug, "wrong A data")
    require(body_b["id"] == encode_tenant_id(tenant_b.id), "B did not read Tenant B")
    require(
        set(body_a)
        == {
            "id",
            "name",
            "slug",
            "status",
            "plan",
            "default_currency",
            "timezone",
            "locale",
            "created_at",
            "updated_at",
        },
        "the Tenant representation has unexpected fields",
    )


@pytest.mark.parametrize(
    "forged", ["public-id", "uuid", "garbage"], ids=["public-id", "uuid", "garbage"]
)
async def test_x_tenant_id_is_ignored_and_never_changes_the_result(
    api: Api,
    migrator_engine: AsyncEngine,
    api_settings: Settings,
    tenant_a: SeededTenant,
    tenant_b: SeededTenant,
    forged: str,
) -> None:
    _, client = api
    key_a = await key_for(migrator_engine, api_settings, tenant_a)
    header = {
        "public-id": encode_tenant_id(tenant_b.id),
        "uuid": str(tenant_b.id),
        "garbage": "not-a-tenant",
    }[forged]

    read = await send(
        client, "GET", TENANT, headers=bearer(key_a.raw_key, **{"X-Tenant-ID": header})
    )
    context = await send(
        client, "GET", CONTEXT, headers=bearer(key_a.raw_key, **{"X-Tenant-ID": header})
    )
    patched = await send(
        client,
        "PATCH",
        TENANT,
        headers=bearer(key_a.raw_key, **{"X-Tenant-ID": header}),
        json={"name": "Renomeada por A"},
    )

    require(require_status(read, 200, "GET")["id"] == encode_tenant_id(tenant_a.id), "GET moved")
    require(
        require_status(context, 200, "context")["tenant_id"] == encode_tenant_id(tenant_a.id),
        "the auth context moved",
    )
    require(require_status(patched, 200, "PATCH")["id"] == encode_tenant_id(tenant_a.id), "moved")
    require((await read_tenant(migrator_engine, tenant_b.id))["name"] == tenant_b.name, "B changed")
    require(
        (await read_tenant(migrator_engine, tenant_a.id))["name"] == "Renomeada por A",
        "A did not change",
    )


# --- contexto de autenticação ---------------------------------------------------------------


async def test_the_auth_context_shows_the_public_tenant_key_id_and_scopes_without_a_digest(
    api: Api, migrator_engine: AsyncEngine, api_settings: Settings, tenant_a: SeededTenant
) -> None:
    _, client = api
    keyed, issued = await emit_key(
        migrator_engine, api_settings, tenant_a, scopes=["tenant:write", "tenant:read"]
    )

    response = await send(client, "GET", CONTEXT, headers=bearer(issued.raw_key))

    body = require_status(response, 200, "auth context")
    require(
        body
        == {
            "tenant_id": encode_tenant_id(tenant_a.id),
            "key_id": encode_key_id(issued.key_id),
            "scopes": ["tenant:read", "tenant:write"],
        },
        "the auth context is not the public Tenant, the key id and the sorted scopes",
    )
    require(keyed.key_id == issued.key_id, "seeded another key")
    lowered = response.text.lower()
    require(not any(word in lowered for word in FORBIDDEN_WORDS), "the context names a secret")
    assert_no_leaks(response.text, {"raw key": issued.raw_key}, "the auth context")


@pytest.mark.parametrize("scope", ["tenant:read", "tenant:write"])
async def test_the_auth_context_needs_only_a_valid_key(
    api: Api,
    migrator_engine: AsyncEngine,
    api_settings: Settings,
    tenant_a: SeededTenant,
    scope: str,
) -> None:
    _, client = api
    issued = await key_for(migrator_engine, api_settings, tenant_a, scope)

    body = require_status(
        await send(client, "GET", CONTEXT, headers=bearer(issued.raw_key)), 200, scope
    )

    require(body["scopes"] == [scope], "the context does not list the key scopes")


# --- scopes ----------------------------------------------------------------------------------


async def test_reading_without_tenant_read_is_forbidden(
    api: Api, migrator_engine: AsyncEngine, api_settings: Settings, tenant_a: SeededTenant
) -> None:
    _, client = api
    issued = await key_for(migrator_engine, api_settings, tenant_a, "tenant:write")

    response = await send(client, "GET", TENANT, headers=bearer(issued.raw_key))

    error = require_error(response, 403, "FORBIDDEN", "GET without tenant:read")
    require(error["details"] == {"required_scopes": ["tenant:read"]}, "wrong required scopes")


async def test_patching_without_tenant_write_is_forbidden_and_changes_nothing(
    api: Api, migrator_engine: AsyncEngine, api_settings: Settings, tenant_a: SeededTenant
) -> None:
    _, client = api
    issued = await key_for(migrator_engine, api_settings, tenant_a, "tenant:read")

    response = await send(
        client, "PATCH", TENANT, headers=bearer(issued.raw_key), json={"name": "Nunca"}
    )

    error = require_error(response, 403, "FORBIDDEN", "PATCH without tenant:write")
    require(error["details"] == {"required_scopes": ["tenant:write"]}, "wrong required scopes")
    require((await read_tenant(migrator_engine, tenant_a.id))["name"] == tenant_a.name, "changed")


# --- PATCH ----------------------------------------------------------------------------------


async def test_patch_changes_the_four_allowed_fields_of_its_own_tenant_only(
    api: Api,
    migrator_engine: AsyncEngine,
    api_settings: Settings,
    tenant_a: SeededTenant,
    tenant_b: SeededTenant,
) -> None:
    _, client = api
    key_a = await key_for(migrator_engine, api_settings, tenant_a)
    before_b = await read_tenant(migrator_engine, tenant_b.id)
    before_a = await read_tenant(migrator_engine, tenant_a.id)
    changes = {
        "name": "  Empresa A Renomeada  ",
        "default_currency": "USD",
        "timezone": "America/New_York",
        "locale": "en-US",
    }

    response = await send(client, "PATCH", TENANT, headers=bearer(key_a.raw_key), json=changes)

    body = require_status(response, 200, "PATCH")
    stored = await read_tenant(migrator_engine, tenant_a.id)
    require(body["id"] == encode_tenant_id(tenant_a.id), "patched another Tenant")
    require(
        (body["name"], body["default_currency"], body["timezone"], body["locale"])
        == ("Empresa A Renomeada", "USD", "America/New_York", "en-US"),
        "the response is not the updated representation",
    )
    require(
        (stored["name"], stored["default_currency"], stored["timezone"], stored["locale"])
        == ("Empresa A Renomeada", "USD", "America/New_York", "en-US"),
        "the change was not committed",
    )
    require(stored["updated_at"] > before_a["updated_at"], "updated_at did not move")
    require(await read_tenant(migrator_engine, tenant_b.id) == before_b, "Tenant B changed")


async def test_patch_of_a_single_field_keeps_the_others(
    api: Api, migrator_engine: AsyncEngine, api_settings: Settings, tenant_a: SeededTenant
) -> None:
    _, client = api
    issued = await key_for(migrator_engine, api_settings, tenant_a)
    before = await read_tenant(migrator_engine, tenant_a.id)

    response = await send(
        client, "PATCH", TENANT, headers=bearer(issued.raw_key), json={"locale": "en-US"}
    )

    require_status(response, 200, "PATCH locale")
    after = await read_tenant(migrator_engine, tenant_a.id)
    require(after["locale"] == "en-US", "the locale did not change")
    require(
        (after["name"], after["default_currency"], after["timezone"])
        == (before["name"], before["default_currency"], before["timezone"]),
        "another field changed",
    )


INVALID_PATCHES: dict[str, Any] = {
    "empty-object": {},
    "tenant-id": {"tenant_id": "ten_" + "0" * 32},
    "tenant-id-with-a-valid-field": {"name": "Ok", "tenant_id": "ten_" + "0" * 32},
    "id": {"id": "ten_" + "0" * 32},
    "status": {"status": "suspended"},
    "plan": {"plan": "enterprise"},
    "slug": {"slug": "outra"},
    "empty-name": {"name": ""},
    "blank-name": {"name": "   "},
    "long-name": {"name": "x" * 201},
    "null-name": {"name": None},
    "number-name": {"name": 7},
    "unknown-timezone": {"timezone": "Mars/Olympus_Mons"},
    "path-timezone": {"timezone": "../../etc/passwd"},
    "currency-outside-the-list": {"default_currency": "JPY"},
    "lowercase-currency": {"default_currency": "brl"},
    "locale-outside-the-list": {"locale": "fr-FR"},
    "array-body": ["name"],
    "string-body": "name",
}


@pytest.mark.parametrize("case", list(INVALID_PATCHES), ids=list(INVALID_PATCHES))
async def test_an_invalid_patch_is_422_with_field_and_type_only_and_changes_nothing(
    api: Api,
    migrator_engine: AsyncEngine,
    api_settings: Settings,
    tenant_a: SeededTenant,
    case: str,
) -> None:
    _, client = api
    issued = await key_for(migrator_engine, api_settings, tenant_a)
    before = await read_tenant(migrator_engine, tenant_a.id)

    response = await send(
        client, "PATCH", TENANT, headers=bearer(issued.raw_key), json=INVALID_PATCHES[case]
    )

    error = require_error(response, 422, "VALIDATION_ERROR", case)
    errors = error["details"].get("errors")
    require(isinstance(errors, list) and len(errors) > 0, f"{case}: no validation errors listed")
    for item in errors:
        require(
            isinstance(item, dict) and set(item) == {"field", "type"},
            f"{case}: a validation error carries more than field and type",
        )
    require(await read_tenant(migrator_engine, tenant_a.id) == before, f"{case}: changed")


async def test_validation_errors_never_echo_what_was_received(
    api: Api, migrator_engine: AsyncEngine, api_settings: Settings, tenant_a: SeededTenant
) -> None:
    _, client = api
    issued = await key_for(migrator_engine, api_settings, tenant_a)
    sentinel = "eco-SENTINEL-" + uuid4().hex
    payload = {
        "timezone": sentinel,
        "default_currency": sentinel,
        sentinel: sentinel,
        "name": {"nested": sentinel},
    }

    response = await send(client, "PATCH", TENANT, headers=bearer(issued.raw_key), json=payload)

    require_error(response, 422, "VALIDATION_ERROR", "echo")
    require("SENTINEL" not in response.text, "the validation error echoes the received data")


async def test_a_malformed_json_body_is_422(
    api: Api, migrator_engine: AsyncEngine, api_settings: Settings, tenant_a: SeededTenant
) -> None:
    _, client = api
    issued = await key_for(migrator_engine, api_settings, tenant_a)

    response = await send(
        client,
        "PATCH",
        TENANT,
        headers=bearer(issued.raw_key, **{"Content-Type": "application/json"}),
        content=b'{"name": "eco-SENTINEL"',
    )

    require_error(response, 422, "VALIDATION_ERROR", "malformed JSON")
    require("SENTINEL" not in response.text, "the JSON error echoes the body")


# --- 401 e 503 ------------------------------------------------------------------------------


async def test_every_rejected_key_gets_the_same_401_with_the_bearer_challenge(
    api: Api, migrator_engine: AsyncEngine, api_settings: Settings, tenant_a: SeededTenant
) -> None:
    _, client = api
    issued = await key_for(migrator_engine, api_settings, tenant_a)
    raw = issued.raw_key.get_secret_value()
    presented = {
        "malformed": "Bearer cce_tst_nothex",
        "other-environment": "Bearer " + raw.replace("cce_tst_", "cce_prd_", 1),
        "wrong-secret": "Bearer "
        + raw[:-6]
        + ("AAAAAA" if not raw.endswith("AAAAAA") else "BBBBBB"),
        "unknown-id": "Bearer " + raw[:8] + uuid4().hex + raw[40:],
        "lowercase-key": "Bearer " + raw.lower(),
    }
    bodies = set()
    for case, header in presented.items():
        response = await send(client, "GET", TENANT, headers={"Authorization": header})
        error = require_error(response, 401, "AUTHENTICATION_FAILED", case)
        require(response.headers.get("www-authenticate") == "Bearer", f"{case}: no challenge")
        bodies.add((error["message"], str(error["details"])))
        assert_no_leaks(response.text, {"raw key": issued.raw_key}, f"{case} response")

    require(len(bodies) == 1, "two rejections can be told apart")


async def test_two_authorization_headers_are_refused_even_if_one_is_valid(
    api: Api, migrator_engine: AsyncEngine, api_settings: Settings, tenant_a: SeededTenant
) -> None:
    _, client = api
    issued = await key_for(migrator_engine, api_settings, tenant_a)
    header = bearer(issued.raw_key)["Authorization"]

    response = await send(
        client, "GET", TENANT, headers=[("Authorization", header), ("Authorization", header)]
    )

    require_error(response, 401, "AUTHENTICATION_FAILED", "two Authorization headers")


async def test_an_unknown_route_with_a_valid_key_is_a_not_found_envelope(
    api: Api, migrator_engine: AsyncEngine, api_settings: Settings, tenant_a: SeededTenant
) -> None:
    _, client = api
    issued = await key_for(migrator_engine, api_settings, tenant_a)

    response = await send(
        client,
        "GET",
        f"/api/v1/tenants/{encode_tenant_id(tenant_a.id)}",
        headers=bearer(issued.raw_key),
    )

    require_error(response, 404, "NOT_FOUND", "per-tenant route")


async def test_an_unavailable_database_during_authentication_is_503_without_details(
    api_settings: Settings,
) -> None:
    sentinel_password = "senha-sentinela-" + uuid4().hex
    url = make_url(api_settings.database_url.get_secret_value()).set(
        host="127.0.0.1", port=1, password=sentinel_password
    )
    dsn = url.render_as_string(hide_password=False)
    broken = api_settings.model_copy(update={"database_url": SecretStr(dsn)})
    raw = SecretStr("cce_tst_" + uuid4().hex + "_" + "A" * 43)

    async with running_app(broken) as (_, client):
        response = await send(client, "GET", CONTEXT, headers=bearer(raw))

    error = require_error(response, 503, "DEPENDENCY_UNAVAILABLE", "database down")
    require(error["details"] == {}, "the 503 carries details")
    require("www-authenticate" not in response.headers, "a dependency failure is not a 401")
    assert_no_leaks(response.text, sensitive_components(dsn), "the 503 response")
    body_of(response)
