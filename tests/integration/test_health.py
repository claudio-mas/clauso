"""Liveness não checa dependências; readiness checa PostgreSQL, Redis e schema, sem expor detalhes.

As falhas são reais: porta fechada para o PostgreSQL ou o Redis, e um conjunto de heads esperado
diferente do gravado em `cce.alembic_version`. Nenhuma resposta contém DSN, host, porta ou senha.
"""

from __future__ import annotations

from collections.abc import Mapping
from uuid import uuid4

import httpx
import pytest
from api_support import body_of, require_status, require_trace_id, running_app, send
from db_support import sensitive_components
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy.engine import make_url

from cce.shared.config import Settings

from .support import assert_no_leaks, require

pytestmark = pytest.mark.integration

Api = tuple[FastAPI, httpx.AsyncClient]
READY = {"status": "ready", "checks": {"postgres": "ok", "redis": "ok", "schema": "ok"}}


def broken_database(settings: Settings) -> tuple[Settings, Mapping[str, str]]:
    url = make_url(settings.database_url.get_secret_value()).set(
        host="127.0.0.1", port=1, password="senha-sentinela-" + uuid4().hex
    )
    dsn = url.render_as_string(hide_password=False)
    return settings.model_copy(update={"database_url": SecretStr(dsn)}), sensitive_components(dsn)


def broken_redis(settings: Settings) -> tuple[Settings, Mapping[str, str]]:
    password = "redis-sentinela-" + uuid4().hex
    url = f"redis://:{password}@127.0.0.1:1/0"
    forbidden = {"redis URL": url, "redis password": password, "redis port": "127.0.0.1:1"}
    return settings.model_copy(update={"redis_url": SecretStr(url)}), forbidden


async def test_liveness_answers_ok(api: Api) -> None:
    _, client = api

    response = await send(client, "GET", "/health/live")

    require(require_status(response, 200, "live") == {"status": "ok"}, "unexpected live body")
    require_trace_id(response, "live")


async def test_readiness_is_ready_with_postgres_redis_and_the_schema_at_head(api: Api) -> None:
    _, client = api

    response = await send(client, "GET", "/health/ready")

    require(require_status(response, 200, "ready") == READY, "unexpected ready body")


@pytest.mark.parametrize("dependency", ["postgres", "redis"])
async def test_a_dependency_down_makes_readiness_503_while_liveness_stays_200(
    api_settings: Settings, dependency: str
) -> None:
    settings, forbidden = (
        broken_database(api_settings) if dependency == "postgres" else broken_redis(api_settings)
    )

    async with running_app(settings) as (_, client):
        ready = await send(client, "GET", "/health/ready")
        live = await send(client, "GET", "/health/live")

    body = require_status(ready, 503, f"ready with {dependency} down")
    expected = (
        {"postgres": "unavailable", "redis": "ok", "schema": "unavailable"}
        if dependency == "postgres"
        else {"postgres": "ok", "redis": "unavailable", "schema": "ok"}
    )
    require(body == {"status": "not_ready", "checks": expected}, f"{dependency}: wrong body")
    require(require_status(live, 200, "live") == {"status": "ok"}, "liveness checked a dependency")
    assert_no_leaks(ready.text, forbidden, f"ready with {dependency} down")
    for word in ("127.0.0.1", "localhost", "postgres://", "redis://", "refused", "Error"):
        require(word not in ready.text, f"{dependency}: the response exposes a raw detail")


async def test_a_schema_behind_the_expected_heads_makes_readiness_503(api: Api) -> None:
    app, client = api
    app.state.expected_heads = frozenset({"9999_not_applied"})

    response = await send(client, "GET", "/health/ready")

    body = require_status(response, 503, "ready with a missing head")
    require(
        body
        == {
            "status": "not_ready",
            "checks": {"postgres": "ok", "redis": "ok", "schema": "unavailable"},
        },
        "wrong body for a schema behind head",
    )


async def test_readiness_without_known_heads_is_not_ready(api: Api) -> None:
    app, client = api
    app.state.expected_heads = frozenset()

    response = await send(client, "GET", "/health/ready")

    require(body_of(response)["checks"]["schema"] == "unavailable", "empty heads passed")
    require(response.status_code == 503, "empty heads are ready")


async def test_the_expected_heads_come_from_the_migration_scripts(api: Api) -> None:
    app, _ = api

    require(app.state.expected_heads == frozenset({"0003"}), "the expected heads are not 0003")
