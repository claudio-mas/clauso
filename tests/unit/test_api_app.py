"""Aplicação HTTP sem banco: factory, envelope de erro, request/trace IDs, Swagger, CORS e lifespan.

Nada aqui conecta: o lifespan só roda nos testes que trocam engine e Redis por dublês, e as rotas
autenticadas só são chamadas em casos recusados antes de qualquer acesso ao banco.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import httpx
import pytest
from api_support import BASE_URL, REQUEST_ID, require_error, require_trace_id
from asgi_lifespan import LifespanManager
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import Message

from cce import main
from cce.api.dependencies import bearer_token
from cce.api.health import check_within
from cce.identity.tenants import repository
from cce.identity.tenants.service import TenantService
from cce.integrations.api_keys.authentication import Principal
from cce.main import create_app
from cce.shared.config import Settings
from cce.shared.redis import build_redis

pytestmark = pytest.mark.usefixtures("clean_cce_env")

PASSWORD = "app-password-SENTINEL"
PEPPER = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="  # 32 bytes, só para validar o formato
SECRET_MESSAGE = "boom: password=app-password-SENTINEL"
ALLOWED_ORIGIN = "https://app.example.com"


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "env": "local",
        "database_url": SecretStr(f"postgresql+asyncpg://cce_app:{PASSWORD}@db.invalid:5432/cce"),
        "redis_url": SecretStr("redis://redis.invalid:6379/0"),
        "api_key_peppers": {1: SecretStr(PEPPER)},
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def remote_settings(environment: str, tmp_path: Path) -> Settings:
    ca_file = tmp_path / "ca.pem"
    ca_file.write_text("not used: nothing connects in this test\n")
    return make_settings(
        env=environment,
        database_ca_file=ca_file,
        redis_url=SecretStr("rediss://redis.invalid:6379/0"),
        docs_enabled=False,
    )


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(make_settings())

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError(SECRET_MESSAGE)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url=BASE_URL
    ) as test_client:
        yield test_client


# --- envelope de erro e IDs de correlação ---------------------------------------------------------


async def test_an_unknown_route_is_a_not_found_envelope(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/tenants")

    require_error(response, 404, "NOT_FOUND", "unknown route")


async def test_a_wrong_method_is_a_method_not_allowed_envelope(client: httpx.AsyncClient) -> None:
    response = await client.delete("/api/v1/tenant")

    require_error(response, 405, "METHOD_NOT_ALLOWED", "wrong method")
    assert "GET" in response.headers["allow"]  # o Starlette cita só a primeira rota do path


async def test_an_unexpected_error_is_an_internal_error_without_its_message(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/boom")

    error = require_error(response, 500, "INTERNAL_ERROR", "unexpected error")
    assert error["details"] == {}
    assert "SENTINEL" not in response.text
    assert "boom" not in response.text
    assert "RuntimeError" not in response.text


async def test_an_unexpected_error_sends_exactly_one_response() -> None:
    app = create_app(make_settings())

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError(SECRET_MESSAGE)

    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def record(message: Message) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/boom",
        "raw_path": b"/boom",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"testserver")],
        "client": ("127.0.0.1", 1),
        "server": ("testserver", 80),
    }
    await app(scope, receive, record)

    starts = [message for message in sent if message["type"] == "http.response.start"]
    assert [start["status"] for start in starts] == [500]


async def test_every_response_carries_fresh_request_and_trace_ids(
    client: httpx.AsyncClient,
) -> None:
    first = await client.get("/health/live", headers={"X-Request-ID": "req_" + "a" * 32})
    second = await client.get("/health/live")

    for response in (first, second):
        assert REQUEST_ID.fullmatch(response.headers["x-request-id"])
        require_trace_id(response, "live")
    assert first.headers["x-request-id"] != "req_" + "a" * 32  # o do cliente é ignorado
    assert first.headers["x-request-id"] != second.headers["x-request-id"]
    assert first.headers["x-trace-id"] != second.headers["x-trace-id"]


async def test_liveness_does_not_touch_any_dependency(client: httpx.AsyncClient) -> None:
    # Sem lifespan não há engine nem Redis: a liveness responde mesmo assim.
    response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# --- Authorization: Bearer ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ([], None),
        (["Bearer abc"], "abc"),
        (["bearer abc"], "abc"),
        (["BEARER abc"], "abc"),
        (["Basic abc"], None),
        (["Bearer"], None),
        (["Bearer "], None),
        (["Bearer  abc"], None),
        (["Bearer abc def"], None),
        (["Bearer\tabc"], None),
        ([" Bearer abc"], None),
        (["Bearer abc "], None),
        (["Bearer abc", "Bearer abc"], None),
        (["Bearer abc", "Bearer def"], None),
    ],
    ids=[
        "absent",
        "bearer",
        "lowercase-scheme",
        "uppercase-scheme",
        "basic",
        "scheme-only",
        "empty-token",
        "double-space",
        "two-tokens",
        "tab",
        "leading-space",
        "trailing-space",
        "repeated-header",
        "two-headers",
    ],
)
def test_bearer_token_accepts_exactly_one_well_formed_header(
    values: list[str], expected: str | None
) -> None:
    assert bearer_token(values) == expected


@pytest.mark.parametrize(
    "headers",
    [
        [],
        [("authorization", "Basic dXNlcjpwYXNz")],
        [("authorization", "Bearer")],
        [("authorization", "Bearer a"), ("authorization", "Bearer b")],
    ],
    ids=["absent", "basic", "scheme-only", "two-headers"],
)
@pytest.mark.parametrize("path", ["/api/v1/auth/context", "/api/v1/tenant"])
async def test_a_request_without_one_bearer_token_is_401_with_the_challenge(
    client: httpx.AsyncClient, headers: list[tuple[str, str]], path: str
) -> None:
    response = await client.get(path, headers=headers)

    require_error(response, 401, "AUTHENTICATION_FAILED", "no bearer token")
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["message"] == "Authentication failed."


async def test_a_token_in_the_query_string_or_cookie_does_not_authenticate(
    client: httpx.AsyncClient,
) -> None:
    token = "cce_dev_" + "0" * 32 + "_" + "A" * 43
    response = await client.get(
        "/api/v1/auth/context",
        params={"access_token": token, "api_key": token},
        headers={"Cookie": f"access_token={token}"},
    )

    require_error(response, 401, "AUTHENTICATION_FAILED", "token outside the header")


async def test_an_unauthenticated_patch_is_401_before_body_validation(
    client: httpx.AsyncClient,
) -> None:
    response = await client.patch("/api/v1/tenant", json={"tenant_id": "x", "name": ""})

    require_error(response, 401, "AUTHENTICATION_FAILED", "unauthenticated patch")


# --- Swagger e CORS -----------------------------------------------------------------------------


@pytest.mark.parametrize("environment", ["staging", "production"])
@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"])
async def test_swagger_and_openapi_are_disabled_in_staging_and_production(
    environment: str, path: str, tmp_path: Path
) -> None:
    app = create_app(remote_settings(environment, tmp_path))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as test_client:
        response = await test_client.get(path)

    require_error(response, 404, "NOT_FOUND", f"{path} in {environment}")


@pytest.mark.parametrize("environment", ["local", "test"])
async def test_swagger_and_openapi_are_served_in_local_and_test(environment: str) -> None:
    overrides: dict[str, Any] = {"env": environment}
    if environment == "test":
        overrides["database_url"] = SecretStr(
            f"postgresql+asyncpg://cce_app:{PASSWORD}@db.invalid:5432/cce_test"
        )
    app = create_app(make_settings(**overrides))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as test_client:
        docs = await test_client.get("/docs")
        schema = await test_client.get("/openapi.json")

    assert docs.status_code == 200
    assert schema.status_code == 200
    assert "/api/v1/tenant" in schema.json()["paths"]


async def test_docs_can_be_disabled_in_local() -> None:
    app = create_app(make_settings(docs_enabled=False))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as test_client:
        response = await test_client.get("/openapi.json")

    assert response.status_code == 404


async def cors_client(origins: list[str]) -> httpx.AsyncClient:
    app = create_app(make_settings(allowed_origins=origins))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL)


def preflight(origin: str) -> dict[str, str]:
    return {
        "Origin": origin,
        "Access-Control-Request-Method": "PATCH",
        "Access-Control-Request-Headers": "authorization,content-type",
    }


async def test_cors_answers_only_the_explicit_origins_and_never_allows_credentials() -> None:
    async with await cors_client([ALLOWED_ORIGIN]) as test_client:
        allowed = await test_client.options("/api/v1/tenant", headers=preflight(ALLOWED_ORIGIN))
        other = await test_client.options(
            "/api/v1/tenant", headers=preflight("https://evil.example.com")
        )
        simple = await test_client.get(
            "/health/live", headers={"Origin": "https://evil.example.com"}
        )
        simple_allowed = await test_client.get("/health/live", headers={"Origin": ALLOWED_ORIGIN})

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert "access-control-allow-credentials" not in allowed.headers
    assert "access-control-allow-origin" not in other.headers
    assert "access-control-allow-origin" not in simple.headers
    assert simple_allowed.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert "access-control-allow-credentials" not in simple_allowed.headers
    assert "x-request-id" in simple_allowed.headers.get("access-control-expose-headers", "").lower()


async def test_without_configured_origins_there_are_no_cors_headers() -> None:
    async with await cors_client([]) as test_client:
        response = await test_client.options(
            "/api/v1/tenant", headers=preflight("https://app.example.com")
        )
        simple = await test_client.get("/health/live", headers={"Origin": ALLOWED_ORIGIN})

    assert "access-control-allow-origin" not in response.headers
    assert "access-control-allow-origin" not in simple.headers


# --- lifespan -----------------------------------------------------------------------------------


class FakeEngine:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def dispose(self) -> None:
        self.events.append("engine.dispose")


class FakeRedis:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def aclose(self) -> None:
        self.events.append("redis.aclose")


def test_creating_the_app_opens_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: object, **__: object) -> None:
        raise AssertionError("create_app must not build clients")

    monkeypatch.setattr(main, "build_engine", refuse)
    monkeypatch.setattr(main, "build_redis", refuse)

    create_app(make_settings())


async def test_the_lifespan_stores_the_clients_and_closes_them_on_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(main, "build_engine", lambda _: FakeEngine(events))
    monkeypatch.setattr(main, "build_redis", lambda _: FakeRedis(events))
    app = create_app(make_settings())

    async with LifespanManager(app):
        assert isinstance(app.state.engine, FakeEngine)
        assert isinstance(app.state.redis, FakeRedis)
        assert app.state.session_factory is not None
        assert events == []

    assert sorted(events) == ["engine.dispose", "redis.aclose"]


async def test_a_partial_startup_still_disposes_the_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []

    def broken_redis(_: Settings) -> FakeRedis:
        raise RuntimeError("redis client could not be built")

    monkeypatch.setattr(main, "build_engine", lambda _: FakeEngine(events))
    monkeypatch.setattr(main, "build_redis", broken_redis)
    app = create_app(make_settings())

    with pytest.raises(RuntimeError):
        async with LifespanManager(app):
            pass

    assert events == ["engine.dispose"]


def test_create_app_loads_settings_when_none_are_given(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "load_settings", lambda: make_settings(docs_enabled=False))

    app = create_app()

    assert app.openapi_url is None


# --- readiness: limite de tempo por dependência -------------------------------------------------


async def test_a_slow_dependency_is_unavailable_within_the_time_limit() -> None:
    async def hangs() -> None:
        await asyncio.sleep(10)

    loop = asyncio.get_running_loop()
    started = loop.time()
    result = await check_within(hangs, 0.05)

    assert result is False
    assert loop.time() - started < 1


async def test_a_failing_dependency_is_unavailable_and_a_healthy_one_is_ok() -> None:
    async def fails() -> None:
        raise OSError("connection refused to db.invalid:5432")

    async def answers() -> None:
        return None

    assert await check_within(fails, 1) is False
    assert await check_within(answers, 1) is True


# --- Tenant invisível e Redis com TLS -----------------------------------------------------------


async def test_a_tenant_that_is_not_visible_is_a_not_found_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def nothing(*_: object) -> None:
        return None

    monkeypatch.setattr(repository, "get_current", nothing)
    principal = Principal(tenant_id=uuid4(), key_id=uuid4(), scopes=frozenset({"tenant:read"}))
    app = create_app(make_settings())

    @app.get("/missing")
    async def missing() -> None:
        await TenantService.get_current(cast(AsyncSession, None), principal)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as test_client:
        response = await test_client.get("/missing")

    require_error(response, 404, "NOT_FOUND", "invisible Tenant")
    assert str(principal.tenant_id) not in response.text


async def test_redis_over_tls_verifies_the_certificate_and_the_hostname(tmp_path: Path) -> None:
    settings = remote_settings("production", tmp_path)
    settings = settings.model_copy(update={"redis_ca_file": settings.database_ca_file})

    client = build_redis(settings)
    try:
        options = client.connection_pool.connection_kwargs
        assert options["ssl_cert_reqs"] == "required"
        assert options["ssl_check_hostname"] is True
        assert options["ssl_ca_certs"] == str(settings.database_ca_file)
        assert options["socket_connect_timeout"] == 1
        assert options["socket_timeout"] == 1
    finally:
        await client.aclose()


async def test_redis_without_tls_gets_no_tls_options() -> None:
    client = build_redis(make_settings())
    try:
        assert "ssl_ca_certs" not in client.connection_pool.connection_kwargs
    finally:
        await client.aclose()
