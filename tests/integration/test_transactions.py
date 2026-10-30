"""Commit antes da resposta, pool reutilizado e requisições concorrentes de dois Tenants.

As falhas são REAIS, no PostgreSQL: um trigger só no banco de teste faz falhar o UPDATE do Tenant
no flush ou no COMMIT. Uma conexão independente da API confirma que nada persistiu. A API roda com
pool de UMA conexão, então a requisição seguinte reutiliza a conexão que acabou de falhar.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from uuid import UUID

import httpx
import pytest
from api_support import bearer, require_error, require_status, running_app, send
from fastapi import FastAPI
from key_support import acall_or_fail
from seeding import SeededTenant, emit_key, read_tenant
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.types import Message

from cce.shared.config import Settings
from cce.shared.ids import encode_tenant_id

from .support import require

pytestmark = [pytest.mark.integration, pytest.mark.sensitive]

Api = tuple[FastAPI, httpx.AsyncClient]
Inject = Callable[[str, UUID], Awaitable[None]]
TENANT = "/api/v1/tenant"

FAILURES = {
    "flush": (500, "INTERNAL_ERROR"),
    "commit": (500, "INTERNAL_ERROR"),
    "known-conflict": (409, "CONFLICT"),
    "unknown-conflict": (500, "INTERNAL_ERROR"),
}


@pytest.mark.parametrize("kind", list(FAILURES), ids=list(FAILURES))
async def test_a_failure_in_flush_or_commit_never_answers_200_and_persists_nothing(
    api: Api,
    migrator_engine: AsyncEngine,
    api_settings: Settings,
    tenant_a: SeededTenant,
    tenant_b: SeededTenant,
    tenant_failure: Inject,
    kind: str,
) -> None:
    _, client = api
    _, key_a = await emit_key(migrator_engine, api_settings, tenant_a)
    _, key_b = await emit_key(migrator_engine, api_settings, tenant_b)
    before = await read_tenant(migrator_engine, tenant_a.id)
    await tenant_failure(kind, tenant_a.id)
    status, code = FAILURES[kind]

    response = await send(
        client, "PATCH", TENANT, headers=bearer(key_a.raw_key), json={"name": "Nunca gravado"}
    )

    error = require_error(response, status, code, f"{kind} failure")
    require("probe" not in response.text, f"{kind}: the database message leaked")
    require("uq_tenants_slug" not in response.text, f"{kind}: an internal name leaked")
    if kind == "known-conflict":
        require(error["details"] == {"field": "slug"}, "the conflict does not name the field")
    require(await read_tenant(migrator_engine, tenant_a.id) == before, f"{kind}: persisted")

    # A mesma (e única) conexão do pool, logo depois da falha, atende B com o contexto de B.
    after = await send(client, "GET", TENANT, headers=bearer(key_b.raw_key))
    require(
        require_status(after, 200, "B after the failure")["id"] == encode_tenant_id(tenant_b.id),
        "the reused connection did not serve Tenant B",
    )
    again = await send(client, "GET", TENANT, headers=bearer(key_a.raw_key))
    require(require_status(again, 200, "A after the failure")["name"] == tenant_a.name, "A moved")


@pytest.mark.parametrize("kind", ["flush", "commit"])
async def test_the_failed_commit_sends_a_single_error_response_and_no_success_before_it(
    api: Api,
    migrator_engine: AsyncEngine,
    api_settings: Settings,
    tenant_a: SeededTenant,
    tenant_failure: Inject,
    kind: str,
) -> None:
    """No nível ASGI: o único `http.response.start` enviado é o do erro."""
    app, _ = api
    _, issued = await emit_key(migrator_engine, api_settings, tenant_a)
    await tenant_failure(kind, tenant_a.id)
    body = b'{"name": "Nunca gravado"}'
    sent: list[Message] = []
    delivered = False

    async def receive() -> Message:
        nonlocal delivered
        if delivered:
            await asyncio.sleep(3600)
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def record(message: Message) -> None:
        sent.append(message)

    authorization = bearer(issued.raw_key)["Authorization"].encode()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "PATCH",
        "scheme": "http",
        "path": TENANT,
        "raw_path": TENANT.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"testserver"),
            (b"authorization", authorization),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
        "client": ("127.0.0.1", 1),
        "server": ("testserver", 80),
        "state": {},
    }

    await acall_or_fail(lambda: app(scope, receive, record), "the ASGI app")

    statuses = [message["status"] for message in sent if message["type"] == "http.response.start"]
    require(statuses == [500], f"{kind}: response starts were {statuses}, expected only [500]")


async def test_concurrent_requests_of_two_tenants_each_see_only_their_own(
    api: Api,
    migrator_engine: AsyncEngine,
    api_settings: Settings,
    tenant_a: SeededTenant,
    tenant_b: SeededTenant,
) -> None:
    _, client = api
    _, key_a = await emit_key(migrator_engine, api_settings, tenant_a)
    _, key_b = await emit_key(migrator_engine, api_settings, tenant_b)
    expected = {"A": encode_tenant_id(tenant_a.id), "B": encode_tenant_id(tenant_b.id)}
    keys = {"A": key_a.raw_key, "B": key_b.raw_key}

    async def read(who: str) -> tuple[str, int, str | None]:
        response = await send(client, "GET", TENANT, headers=bearer(keys[who]))
        tenant_id = response.json().get("id") if response.status_code == 200 else None
        return who, response.status_code, tenant_id

    async def patch(who: str, index: int) -> tuple[str, int, str | None]:
        response = await send(
            client,
            "PATCH",
            TENANT,
            headers=bearer(keys[who]),
            json={"name": f"Empresa {who} {index}"},
        )
        tenant_id = response.json().get("id") if response.status_code == 200 else None
        return who, response.status_code, tenant_id

    calls = []
    for index in range(15):
        calls += [read("A"), read("B"), patch("A", index), patch("B", index)]
    results = await asyncio.gather(*calls)

    for who, status, tenant_id in results:
        require(status == 200, f"a concurrent request of {who} returned {status}")
        require(tenant_id == expected[who], f"a concurrent request of {who} saw another Tenant")
    names = {
        who: (await read_tenant(migrator_engine, tenant.id))["name"]
        for who, tenant in (("A", tenant_a), ("B", tenant_b))
    }
    require(names["A"].startswith("Empresa A "), "A holds a name written for B")
    require(names["B"].startswith("Empresa B "), "B holds a name written for A")


async def test_every_connection_returns_to_the_pool_after_requests_and_errors(
    api: Api,
    migrator_engine: AsyncEngine,
    api_settings: Settings,
    tenant_a: SeededTenant,
    tenant_failure: Inject,
) -> None:
    app, client = api
    _, issued = await emit_key(migrator_engine, api_settings, tenant_a)
    await send(client, "GET", TENANT, headers=bearer(issued.raw_key))
    await send(client, "PATCH", TENANT, headers=bearer(issued.raw_key), json={"name": ""})
    await tenant_failure("commit", tenant_a.id)
    await send(client, "PATCH", TENANT, headers=bearer(issued.raw_key), json={"name": "Nunca"})

    require(app.state.engine.pool.checkedout() == 0, "a request kept a connection checked out")


async def test_the_shutdown_disposes_the_pool_and_closes_redis(api_settings: Settings) -> None:
    async with running_app(api_settings) as (app, client):
        response = await send(client, "GET", "/health/ready")
        require_status(response, 200, "ready before shutdown")
        engine = app.state.engine
        redis = app.state.redis
        pool = engine.pool

    require(pool.checkedout() == 0, "a connection is still checked out after shutdown")
    require(pool.checkedin() == 0, "the pool still holds connections after shutdown")
    connections = [
        *redis.connection_pool._available_connections,
        *redis.connection_pool._in_use_connections,
    ]
    require(len(connections) > 0, "the readiness check did not use redis")
    require(
        not any(connection.is_connected for connection in connections),
        "redis kept a connection open after shutdown",
    )
