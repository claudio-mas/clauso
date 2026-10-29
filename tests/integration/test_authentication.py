"""Autenticação por API key contra PostgreSQL real: lookup restrito, comparação e revogação.

Aceite independente do ticket: duas credenciais ligadas a Tenants distintos autenticam sem que o
cliente informe o Tenant. As chaves são emitidas pelo próprio código de produção
(`issue_key_material`), com o pepper do ambiente de teste, e gravadas pelo dono do schema.

Higiene de falhas: marcador `sensitive`; nenhum `assert` com chave, segredo, digest ou DSN nos
operandos (só `require` e `assert_no_leaks`, que não imprimem valor); chamadas que podem levantar
passam por `acall_or_fail`/`aerror_of`; os `id` dos parâmetros são fixos.
"""

from __future__ import annotations

import base64
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from db_support import OpenSession, OpenTenantSession, sensitive_components
from key_support import acall_or_fail, aerror_of
from pydantic import SecretStr
from seeding import SeededTenant, apply_context, seed_key
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from cce.integrations.api_keys.authentication import (
    AuthenticationFailed,
    DependencyUnavailable,
    Principal,
    authenticate_api_key,
)
from cce.integrations.api_keys.service import IssuedKey, issue_key_material
from cce.shared.config import Settings
from cce.shared.db.engine import build_engine, build_session_factory
from cce.shared.security import generate_key

from .support import assert_no_leaks, require

pytestmark = [
    pytest.mark.integration,
    pytest.mark.sensitive,
    pytest.mark.usefixtures("migrated_database"),
]

Factory = async_sessionmaker[AsyncSession]


async def emit_key(
    engine: AsyncEngine, settings: Settings, tenant: SeededTenant, **overrides: Any
) -> tuple[SeededTenant, IssuedKey]:
    """Emite uma chave com o código de produção e grava o digest dela para o Tenant."""
    issued = issue_key_material(settings.env, settings.api_key_peppers, 1)
    keyed = await seed_key(
        engine,
        tenant,
        id=issued.key_id,
        secret_digest=issued.secret_digest,
        pepper_version=issued.pepper_version,
        **overrides,
    )
    return keyed, issued


async def authenticate(raw_key: str | None, settings: Settings, factory: Factory) -> Principal:
    return await acall_or_fail(
        lambda: authenticate_api_key(raw_key, settings, factory), "authenticate_api_key"
    )


async def change_as_tenant(
    engine: AsyncEngine, tenant: SeededTenant, statement: str, **parameters: Any
) -> None:
    async with engine.begin() as connection:
        await apply_context(connection, tenant.id)
        await connection.execute(text(statement), {"tenant": tenant.id, **parameters})


# --- aceite independente ---------------------------------------------------------------------


async def test_two_credentials_of_distinct_tenants_authenticate_without_a_tenant_from_the_client(
    migrator_engine: AsyncEngine,
    db_settings: Settings,
    runtime_factory: Factory,
    tenant_a: SeededTenant,
    tenant_b: SeededTenant,
) -> None:
    key_a, issued_a = await emit_key(migrator_engine, db_settings, tenant_a)
    key_b, issued_b = await emit_key(migrator_engine, db_settings, tenant_b)

    principal_a = await authenticate(
        issued_a.raw_key.get_secret_value(), db_settings, runtime_factory
    )
    principal_b = await authenticate(
        issued_b.raw_key.get_secret_value(), db_settings, runtime_factory
    )

    require(
        principal_a.tenant_id == tenant_a.id and principal_a.key_id == key_a.key_id, "A is wrong"
    )
    require(
        principal_b.tenant_id == tenant_b.id and principal_b.key_id == key_b.key_id, "B is wrong"
    )
    require(principal_a.scopes == frozenset({"tenant:read", "tenant:write"}), "wrong scopes for A")


async def test_the_principal_drives_a_separate_tenant_session_that_sees_only_its_tenant(
    migrator_engine: AsyncEngine,
    db_settings: Settings,
    runtime_factory: Factory,
    runtime_session: OpenTenantSession,
    tenant_a: SeededTenant,
    tenant_b: SeededTenant,
) -> None:
    _, issued = await emit_key(migrator_engine, db_settings, tenant_a)
    principal = await authenticate(issued.raw_key.get_secret_value(), db_settings, runtime_factory)

    async with runtime_session(principal.tenant_id) as session:
        visible = set((await session.execute(text("SELECT id FROM cce.tenants"))).scalars())

    require(visible == {tenant_a.id}, "the Principal's session does not see exactly its Tenant")
    require(tenant_b.id not in visible, "the Principal's session sees another Tenant")


async def test_the_authentication_session_leaves_no_context_and_returns_the_connection(
    migrator_engine: AsyncEngine,
    db_settings: Settings,
    runtime_engine: AsyncEngine,
    runtime_factory: Factory,
    runtime_no_context: OpenSession,
    tenant_a: SeededTenant,
) -> None:
    _, issued = await emit_key(migrator_engine, db_settings, tenant_a)

    await authenticate(issued.raw_key.get_secret_value(), db_settings, runtime_factory)

    async with runtime_no_context() as session:
        context = (
            await session.execute(
                text("SELECT coalesce(current_setting('app.tenant_id', true), '')")
            )
        ).scalar_one()
    checked_out = runtime_engine.pool.checkedout()  # type: ignore[attr-defined]
    require(context == "", "the authentication left a tenant context on the connection")
    require(checked_out == 0, "the authentication did not return its connection")


# --- uma única resposta para todo motivo de rejeição -----------------------------------------


async def build_rejection(
    name: str, migrator_engine: AsyncEngine, settings: Settings, tenant: SeededTenant
) -> str | None:
    """Prepara o banco para o caso e devolve a chave apresentada."""
    past = datetime.now(UTC) - timedelta(days=2)
    if name == "valid-key-of-an-unknown-id":
        return generate_key(uuid4(), settings.env)
    if name == "other-environment":
        return generate_key(uuid4(), "production")
    if name == "absent":
        return None
    if name == "malformed":
        return "cce_tst_nothex_short"

    overrides: dict[str, Any] = {}
    if name == "expired":
        overrides = {"created_at": past, "expires_at": past + timedelta(days=1)}
    _, issued = await emit_key(migrator_engine, settings, tenant, **overrides)
    raw = issued.raw_key.get_secret_value()
    if name == "wrong-secret":
        return generate_key(issued.key_id, settings.env)
    if name == "revoked":
        await change_as_tenant(
            migrator_engine,
            tenant,
            "UPDATE cce.api_keys SET revoked_at = now() WHERE id = :key",
            key=issued.key_id,
        )
    if name == "tenant-suspended":
        await change_as_tenant(
            migrator_engine,
            tenant,
            "UPDATE cce.tenants SET status = 'suspended' WHERE id = :tenant",
        )
    if name == "tenant-archived":
        await change_as_tenant(
            migrator_engine, tenant, "UPDATE cce.tenants SET status = 'archived' WHERE id = :tenant"
        )
    return raw


REJECTIONS = [
    "absent",
    "malformed",
    "other-environment",
    "valid-key-of-an-unknown-id",
    "wrong-secret",
    "expired",
    "revoked",
    "tenant-suspended",
    "tenant-archived",
]


async def test_every_rejection_has_the_same_type_message_and_arguments(
    migrator_engine: AsyncEngine,
    db_settings: Settings,
    runtime_factory: Factory,
    tenant_a: SeededTenant,
) -> None:
    described = {}
    for name in REJECTIONS:
        presented = await build_rejection(name, migrator_engine, db_settings, tenant_a)

        async def present(presented: str | None = presented) -> Principal:
            return await authenticate_api_key(presented, db_settings, runtime_factory)

        error = await aerror_of(present, AuthenticationFailed, f"authenticating ({name})")
        described[name] = (type(error).__name__, str(error), repr(error), error.args)
        await change_as_tenant(
            migrator_engine, tenant_a, "UPDATE cce.tenants SET status = 'active' WHERE id = :tenant"
        )

    require(len(set(described.values())) == 1, "two rejections can be told apart")
    require(set(described) == set(REJECTIONS), "a rejection case was skipped")


# --- revogação e suspensão valem na autenticação seguinte -----------------------------------


async def test_revocation_applies_to_the_very_next_authentication(
    migrator_engine: AsyncEngine,
    db_settings: Settings,
    runtime_factory: Factory,
    tenant_a: SeededTenant,
) -> None:
    keyed, issued = await emit_key(migrator_engine, db_settings, tenant_a)
    raw = issued.raw_key.get_secret_value()
    await authenticate(raw, db_settings, runtime_factory)
    await authenticate(raw, db_settings, runtime_factory)

    await change_as_tenant(
        migrator_engine,
        tenant_a,
        "UPDATE cce.api_keys SET revoked_at = now() WHERE id = :key",
        key=keyed.key_id,
    )

    await aerror_of(
        lambda: authenticate_api_key(raw, db_settings, runtime_factory),
        AuthenticationFailed,
        "authenticating a revoked key",
    )


async def test_suspending_and_reactivating_a_tenant_applies_to_the_next_authentication(
    migrator_engine: AsyncEngine,
    db_settings: Settings,
    runtime_factory: Factory,
    tenant_a: SeededTenant,
) -> None:
    _, issued = await emit_key(migrator_engine, db_settings, tenant_a)
    raw = issued.raw_key.get_secret_value()
    await authenticate(raw, db_settings, runtime_factory)

    await change_as_tenant(
        migrator_engine, tenant_a, "UPDATE cce.tenants SET status = 'suspended' WHERE id = :tenant"
    )
    await aerror_of(
        lambda: authenticate_api_key(raw, db_settings, runtime_factory),
        AuthenticationFailed,
        "authenticating for a suspended Tenant",
    )
    await change_as_tenant(
        migrator_engine, tenant_a, "UPDATE cce.tenants SET status = 'active' WHERE id = :tenant"
    )

    principal = await authenticate(raw, db_settings, runtime_factory)
    require(principal.tenant_id == tenant_a.id, "the reactivated Tenant did not authenticate")


async def test_the_pepper_version_stored_with_the_key_selects_the_pepper(
    migrator_engine: AsyncEngine,
    db_settings: Settings,
    runtime_factory: Factory,
    tenant_a: SeededTenant,
) -> None:
    """Rotação: sob o pepper 2 a chave autentica; sem o pepper 2 no mapa, deixa de autenticar."""
    rotated = db_settings.model_copy(
        update={
            "api_key_peppers": {
                **db_settings.api_key_peppers,
                2: SecretStr(base64.b64encode(secrets.token_bytes(32)).decode()),
            }
        }
    )
    issued = issue_key_material(rotated.env, rotated.api_key_peppers, 2)
    await seed_key(
        migrator_engine,
        tenant_a,
        id=issued.key_id,
        secret_digest=issued.secret_digest,
        pepper_version=2,
    )
    raw = issued.raw_key.get_secret_value()

    principal = await authenticate(raw, rotated, runtime_factory)
    await aerror_of(
        lambda: authenticate_api_key(raw, db_settings, runtime_factory),  # sem o pepper 2
        AuthenticationFailed,
        "authenticating without the pepper of the key",
    )

    require(principal.tenant_id == tenant_a.id, "the key under pepper 2 did not authenticate")


# --- banco fora do ar ------------------------------------------------------------------------


BROKEN_CONNECTIONS = {
    "connection-refused": {"port": 1},
    "wrong-password": {},
    "nonexistent-database": {"database": "cce_test_inexistente"},
}


@pytest.mark.parametrize("change", list(BROKEN_CONNECTIONS.values()), ids=list(BROKEN_CONNECTIONS))
async def test_an_unavailable_database_is_a_dependency_error_and_leaks_no_connection_detail(
    db_settings: Settings, change: dict[str, Any]
) -> None:
    sentinel_password = "senha-sentinela-" + uuid4().hex
    url = make_url(db_settings.database_url.get_secret_value()).set(
        host="127.0.0.1", password=sentinel_password, **change
    )
    broken = db_settings.model_copy(
        update={
            "database_url": SecretStr(url.render_as_string(hide_password=False)),
            "db_pool_timeout": 2,
        }
    )
    engine = build_engine(broken)
    raw = generate_key(uuid4(), broken.env)
    try:
        error = await aerror_of(
            lambda: authenticate_api_key(raw, broken, build_session_factory(engine)),
            DependencyUnavailable,
            "authenticating with the database unavailable",
        )
    finally:
        await engine.dispose()

    text_of_error = f"{error!s}{error!r}{error.args}"
    assert_no_leaks(
        text_of_error,
        {**sensitive_components(url.render_as_string(hide_password=False)), "key": raw},
        "the dependency error",
    )
    require(not isinstance(error, AuthenticationFailed), "a dependency error must not be a 401")
    require(error.__cause__ is None and error.__suppress_context__, "the driver error is chained")


async def test_a_database_without_the_lookup_function_is_still_a_dependency_error(
    migrator_engine: AsyncEngine,
    db_settings: Settings,
    runtime_factory: Factory,
    tenant_a: SeededTenant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Qualquer erro do banco durante o lookup é 503, nunca um 401 que esconda a falha."""
    import cce.integrations.api_keys.repository as repository

    monkeypatch.setattr(
        repository, "LOOKUP_SQL", "SELECT * FROM cce_private.missing_function(:key_id)"
    )
    _, issued = await emit_key(migrator_engine, db_settings, tenant_a)

    await aerror_of(
        lambda: authenticate_api_key(
            issued.raw_key.get_secret_value(), db_settings, runtime_factory
        ),
        DependencyUnavailable,
        "authenticating with a broken lookup",
    )
