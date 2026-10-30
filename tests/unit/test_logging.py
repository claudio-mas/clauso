"""Logging estruturado e seguro (plano do M0, seção 13.1; ADR-0016), sem banco.

Cobre o formato JSON e seus campos padrão, a allowlist de campos, a redação recursiva com objetos
reais (cabeçalhos do Starlette, `Settings`, `SecretStr`, chave emitida pelo código de produção,
digest, pepper, URL do SQLAlchemy), o access log bruto do Uvicorn desligado, o log de cada
requisição pelo template da rota e a limpeza dos contextos depois de cada requisição, inclusive
depois de autenticação falha.

Higiene de falhas: marcador `sensitive`; verificações com valor sensível ou com texto de log usam
`require`/`assert_no_leaks`, que só imprimem texto fixo e rótulos.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
import uvicorn.config
from api_support import BASE_URL
from fastapi import Depends, FastAPI
from integration.support import assert_no_leaks, require
from key_support import secret_of
from log_support import PASSWORD, PEPPER, LogOutput, synthetic_settings
from pydantic import SecretStr
from sqlalchemy.engine import make_url
from starlette.datastructures import Headers, MutableHeaders

from cce.api import dependencies
from cce.integrations.api_keys.authentication import (
    AuthenticationFailed,
    DependencyUnavailable,
    Principal,
)
from cce.integrations.api_keys.service import issue_key_material
from cce.main import create_app
from cce.shared.config import Settings
from cce.shared.ids import encode_key_id, encode_tenant_id
from cce.shared.logging import (
    ALLOWED_FIELDS,
    OTHER_METHOD,
    REDACTED,
    UNRECOGNIZED_EVENT,
    configure_logging,
    current_request_id,
    normalize_method,
    redact,
    request_context,
)
from cce.shared.security import pepper_bytes

pytestmark = [pytest.mark.sensitive, pytest.mark.usefixtures("clean_cce_env")]

ROOT = Path(__file__).resolve().parents[2]
DSN = f"postgresql+asyncpg://cce_app:{PASSWORD}@db.invalid:5432/cce"
EMAIL = "maria.silva@example.com"
PERSON = "Maria Silva Sentinela"
DOCUMENT = "123.456.789-09"
STANDARD_FIELDS = {"timestamp", "level", "event", "logger", "service", "version", "environment"}


def issued_key(settings: Settings) -> tuple[str, bytes]:
    """Uma chave emitida pelo código de produção: o token bruto e o digest que seria guardado."""
    key = issue_key_material(settings.env, settings.api_key_peppers, 1)
    return key.raw_key.get_secret_value(), key.secret_digest


def forbidden_values(settings: Settings) -> dict[str, str]:
    raw_key, digest = issued_key(settings)
    pepper = pepper_bytes(settings.api_key_peppers, 1)
    return {
        "key": raw_key,
        "secret": secret_of(raw_key),
        "digest hex": digest.hex(),
        "digest repr": repr(digest),
        "pepper": PEPPER,
        "pepper hex": pepper.hex(),
        "pepper repr": repr(pepper),
        "DSN password": PASSWORD,
    }


def log_line(output: LogOutput, event: str) -> dict[str, Any]:
    lines = output.events(event)
    require(len(lines) == 1, f"expected exactly one {event!r} log line, got {len(lines)}")
    return lines[0]


# --- formato e campos -----------------------------------------------------------------------------


def test_a_log_line_is_one_json_object_with_the_standard_fields(log_output: LogOutput) -> None:
    log_output.install(synthetic_settings(version="1.2.3"))

    logging.getLogger("cce.test").warning("thing.happened")

    line = log_line(log_output, "thing.happened")
    require(STANDARD_FIELDS <= set(line), "a standard field is missing")
    require(line["level"] == "warning", "the level is not the method name")
    require(line["logger"] == "cce.test", "the logger name is missing")
    require(line["service"] == "cce-api", "the service is not cce-api")
    require(line["version"] == "1.2.3", "the version does not come from the settings")
    require(line["environment"] == "local", "the environment does not come from the settings")
    require(str(line["timestamp"]).endswith("Z"), "the timestamp is not UTC")
    require("request_id" not in line, "a log outside a request has a request_id")


def test_the_log_level_comes_from_the_settings(log_output: LogOutput) -> None:
    log_output.install(synthetic_settings(log_level="WARNING"))

    logging.getLogger("cce.test").info("too.quiet")
    logging.getLogger("cce.test").warning("loud.enough")

    require(log_output.events("too.quiet") == [], "an INFO line passed a WARNING level")
    require(len(log_output.events("loud.enough")) == 1, "the WARNING line is missing")


def test_the_console_format_is_also_filtered_and_redacted(log_output: LogOutput) -> None:
    settings = synthetic_settings(log_format="console")
    log_output.install(settings)

    logging.getLogger("cce.test").warning(
        "console.line", extra={"route": DSN, "customer_email": EMAIL}
    )
    logging.getLogger("cce.test").warning(f"console {DSN} {EMAIL}")

    require("console.line" in log_output.text, "the console line is missing")
    require(UNRECOGNIZED_EVENT in log_output.text, "the free-text console line was kept")
    assert_no_leaks(log_output.text, {"DSN password": PASSWORD, "e-mail": EMAIL}, "console log")


def test_only_allowlisted_fields_are_kept(log_output: LogOutput) -> None:
    log_output.install(synthetic_settings())

    logging.getLogger("cce.test").info(
        "with.extras",
        extra={
            "status_code": 200,
            "customer_name": PERSON,
            "email": EMAIL,
            "document": DOCUMENT,
            "body": {"name": PERSON},
            "query_string": f"email={EMAIL}",
            "path": f"/customers/{EMAIL}",
            "url": f"https://app.example.com/p/{DOCUMENT}",
        },
    )

    line = log_line(log_output, "with.extras")
    require(set(line) <= ALLOWED_FIELDS, "a field outside the allowlist was logged")
    require(line.get("status_code") == 200, "an allowlisted field was dropped")
    assert_no_leaks(
        log_output.text,
        {"name": PERSON, "e-mail": EMAIL, "document": DOCUMENT},
        "log with extras",
    )


def test_the_allowlist_has_no_field_for_credentials_or_personal_data() -> None:
    risky = ("authorization", "cookie", "token", "secret", "password", "pepper", "digest", "dsn")
    personal = ("name", "email", "document", "body", "query", "url", "path", "headers")
    for field in ALLOWED_FIELDS - {"pepper_version"}:  # a versão do pepper é só um número
        require(not any(word in field for word in risky), "the allowlist has a credential field")
        require(field not in personal, "the allowlist has a personal-data field")


# --- redação recursiva com objetos reais ------------------------------------------------------


def real_objects(settings: Settings) -> tuple[dict[str, Any], dict[str, str]]:
    """Objetos que o código de verdade manipula, aninhados como um log descuidado os passaria,
    e os valores que nenhum deles pode deixar escapar.
    """
    forbidden = forbidden_values(settings)
    raw_key = forbidden["key"]
    issued = issue_key_material(settings.env, settings.api_key_peppers, 1)
    issued_raw = issued.raw_key.get_secret_value()
    forbidden.update(
        {
            "issued key": issued_raw,
            "issued secret": secret_of(issued_raw),
            "issued digest": issued.secret_digest.hex(),
        }
    )
    request_headers = Headers(
        raw=[
            (b"authorization", f"Bearer {raw_key}".encode()),
            (b"cookie", f"session={raw_key}".encode()),
            (b"x-api-key", raw_key.encode()),
            (b"content-type", b"application/json"),
        ]
    )
    response_headers = MutableHeaders()
    response_headers["Set-Cookie"] = f"token={raw_key}"
    digest = bytes.fromhex(forbidden["digest hex"])
    objects = {
        "headers": request_headers,
        "response": response_headers,
        "header_list": list(request_headers.raw),
        "settings": settings,
        "peppers": settings.api_key_peppers,
        "secret": SecretStr(raw_key),
        "issued": issued,
        "digest": digest,
        "pepper": pepper_bytes(settings.api_key_peppers, 1),
        "url": make_url(DSN),
        "nested": [{"deeper": ({"dsn": DSN},)}, {f"Bearer {raw_key}"}, [[[raw_key]]]],
        "message": f"failed for {raw_key} on {DSN}",
        "error": RuntimeError(f"boom {raw_key} {DSN}"),
    }
    return objects, forbidden


def test_redaction_removes_credentials_from_real_objects_at_any_depth() -> None:
    objects, forbidden = real_objects(synthetic_settings())

    redacted = redact(objects)
    rendered = json.dumps(redacted)  # tudo o que sobra é JSON nativo

    assert_no_leaks(rendered, forbidden, "redacted objects")
    assert_no_leaks(repr(redacted), forbidden, "repr of the redacted objects")
    require(redacted["headers"]["authorization"] == REDACTED, "Authorization was not redacted")
    require(redacted["headers"]["cookie"] == REDACTED, "Cookie was not redacted")
    require(
        redacted["headers"]["content-type"] == "application/json",
        "a harmless header was redacted",
    )
    require(redacted["secret"] == REDACTED, "a SecretStr was not redacted")
    require(redacted["digest"] == REDACTED and redacted["pepper"] == REDACTED, "bytes leaked")
    require("RuntimeError" in rendered, "the exception type was lost")


def test_redaction_keeps_harmless_values_and_ids() -> None:
    tenant = uuid4()

    redacted = redact(
        {"status_code": 404, "duration_ms": 1.5, "ok": True, "none": None, "tenant": tenant}
    )

    require(
        redacted
        == {
            "status_code": 404,
            "duration_ms": 1.5,
            "ok": True,
            "none": None,
            "tenant": str(tenant),
        },
        "a harmless value was changed",
    )


def test_redaction_survives_cycles_and_very_deep_values() -> None:
    cycle: list[Any] = []
    cycle.append(cycle)
    deep: Any = "leaf"
    for _ in range(200):
        deep = [deep]

    rendered = json.dumps(redact({"cycle": cycle, "deep": deep}))

    require("leaf" not in rendered, "a value deeper than the limit was kept")


def test_an_allowlisted_field_is_redacted_recursively_through_the_pipeline(
    log_output: LogOutput,
) -> None:
    settings = synthetic_settings()
    objects, forbidden = real_objects(settings)
    log_output.install(settings)

    # `frames` está na allowlist: até um campo permitido passa pela redação, em qualquer nível.
    logging.getLogger("cce.test").error("careless.log", extra={"frames": list(objects.values())})

    log_line(log_output, "careless.log")
    assert_no_leaks(log_output.text, forbidden, "pipeline log")


def test_a_message_that_is_not_an_event_name_never_reaches_the_log(log_output: LogOutput) -> None:
    settings = synthetic_settings()
    forbidden = forbidden_values(settings)
    log_output.install(settings)
    logger = logging.getLogger("cce.test")

    logger.error("lookup failed for %s with %r", forbidden["key"], settings)
    logger.error(settings)

    # Nem o template livre nem um objeto como mensagem viram `event`: só nomes de evento.
    require(len(log_output.events(UNRECOGNIZED_EVENT)) == 2, "a free-text message was kept")
    assert_no_leaks(log_output.text, forbidden, "template log")


def test_a_message_built_with_a_credential_is_scrubbed(log_output: LogOutput) -> None:
    settings = synthetic_settings()
    forbidden = forbidden_values(settings)
    log_output.install(settings)
    key = forbidden["key"]

    logging.getLogger("cce.test").error(
        f"call with Authorization: Bearer {key} to {DSN} and redis://:{PASSWORD}@redis:6379/0"
    )

    require(len(log_output.lines()) == 1, "the scrubbed line is missing")
    assert_no_leaks(log_output.text, forbidden, "scrubbed message")


def test_an_exception_is_logged_by_type_only(log_output: LogOutput) -> None:
    settings = synthetic_settings()
    forbidden = forbidden_values(settings)
    log_output.install(settings)

    try:
        raise RuntimeError(f"boom {forbidden['key']} {DSN}")
    except RuntimeError:
        logging.getLogger("cce.test").exception("operation.failed")

    line = log_line(log_output, "operation.failed")
    require(line.get("error_type") == "RuntimeError", "the exception type is missing")
    require("Traceback" not in log_output.text, "the traceback was logged")
    assert_no_leaks(log_output.text, forbidden, "exception log")


# --- Uvicorn ------------------------------------------------------------------------------------


def test_the_raw_uvicorn_access_log_is_disabled_even_after_uvicorn_configures_it(
    log_output: LogOutput, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = synthetic_settings()
    raw_key, _ = issued_key(settings)
    # O Uvicorn configura os próprios loggers ao montar o Config, antes de chamar a factory.
    uvicorn.config.Config(app="cce.main:create_app", factory=True, access_log=True)
    log_output.install(settings)

    logging.getLogger("uvicorn.access").info(
        '%s - "%s %s HTTP/%s" %d', "127.0.0.1:1", "GET", f"/x?access_token={raw_key}", "1.1", 200
    )
    logging.getLogger("uvicorn.error").info("Uvicorn running on %s", "http://0.0.0.0:8000")
    uvicorn_lines = [line for line in log_output.lines() if line.get("logger") == "uvicorn.error"]

    captured = capsys.readouterr()
    everything = log_output.text + captured.out + captured.err
    assert_no_leaks(everything, {"key": raw_key, "access path": "/x?"}, "uvicorn output")
    require(
        [line.get("event") for line in uvicorn_lines] == [UNRECOGNIZED_EVENT],
        "uvicorn's own messages do not go through the JSON pipeline as a fixed marker",
    )


def test_create_app_installs_the_logging(log_output: LogOutput) -> None:
    create_app(synthetic_settings())

    require(logging.getLogger("uvicorn.access").disabled, "create_app left the access log on")


def test_the_image_starts_uvicorn_without_the_access_log() -> None:
    dockerfile = (ROOT / "infrastructure/docker/api.Dockerfile").read_text()
    command = next(line for line in dockerfile.splitlines() if line.startswith("CMD"))

    assert "--no-access-log" in command


# --- uma linha por requisição -------------------------------------------------------------------


Authenticate = Any


def fake_authentication(principals: dict[str, Principal]) -> Authenticate:
    """Substitui o lookup: um token conhecido vira o `Principal`; `down` simula banco fora."""

    async def authenticate(token: str, *_: object) -> Principal:
        if token == "down":
            raise DependencyUnavailable
        principal = principals.get(token)
        if principal is None:
            raise AuthenticationFailed
        return principal

    return authenticate


def make_app(settings: Settings | None = None) -> FastAPI:
    app = create_app(settings or synthetic_settings())
    app.state.session_factory = None  # sem lifespan; o lookup é substituído nos testes

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError(f"boom {DSN}")

    @app.get("/things/{thing_id}")
    async def thing(thing_id: str) -> dict[str, str]:
        return {"ok": "yes"}

    @app.get("/authenticated-boom")
    async def authenticated_boom(
        _: Principal = Depends(dependencies.get_principal),  # noqa: B008
    ) -> None:
        logging.getLogger("cce.test").warning("inside.request")
        raise RuntimeError("boom")

    return app


@pytest.fixture
async def client(log_output: LogOutput) -> AsyncIterator[httpx.AsyncClient]:
    settings = synthetic_settings()
    app = make_app(settings)
    log_output.install(settings)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url=BASE_URL
    ) as test_client:
        yield test_client


def require_ids_of(line: dict[str, Any], response: httpx.Response, what: str) -> None:
    require(line.get("request_id") == response.headers["x-request-id"], f"{what}: request_id")
    require(line.get("trace_id") == response.headers["x-trace-id"], f"{what}: trace_id")


async def test_every_request_logs_one_line_with_template_status_duration_and_ids(
    client: httpx.AsyncClient, log_output: LogOutput
) -> None:
    response = await client.get("/health/live")

    line = log_line(log_output, "http.request")
    require(line.get("method") == "GET", "the method is missing")
    require(line.get("route") == "/health/live", "the route template is missing")
    require(line.get("status_code") == 200, "the status code is missing")
    duration = line.get("duration_ms")
    require(isinstance(duration, int | float) and duration >= 0, "the duration is missing")
    require_ids_of(line, response, "request line")
    require(set(line) <= ALLOWED_FIELDS, "the request line has a field outside the allowlist")


async def test_the_route_is_the_template_never_the_path(
    client: httpx.AsyncClient, log_output: LogOutput
) -> None:
    await client.get(f"/things/{EMAIL}", params={"document": DOCUMENT})

    line = log_line(log_output, "http.request")
    require(line.get("route") == "/things/{thing_id}", "the route is not the template")
    assert_no_leaks(log_output.text, {"e-mail": EMAIL, "document": DOCUMENT}, "templated log")


async def test_an_unmatched_request_logs_the_unmatched_route(
    client: httpx.AsyncClient, log_output: LogOutput
) -> None:
    response = await client.get(f"/api/v1/customers/{EMAIL}", params={"cpf": DOCUMENT})

    line = log_line(log_output, "http.request")
    require(response.status_code == 404, "the unknown route is not 404")
    require(line.get("route") == "unmatched", "a 404 does not log the unmatched route")
    require(line.get("status_code") == 404, "the 404 status is missing")
    assert_no_leaks(log_output.text, {"e-mail": EMAIL, "document": DOCUMENT}, "404 log")


@pytest.mark.usefixtures("authenticates")
async def test_the_request_log_has_no_body_query_credentials_or_personal_data(
    client: httpx.AsyncClient, log_output: LogOutput
) -> None:
    settings = synthetic_settings()
    forbidden = forbidden_values(settings)
    forbidden.update({"name": PERSON, "e-mail": EMAIL, "document": DOCUMENT})
    key = forbidden["key"]

    response = await client.patch(
        "/api/v1/tenant",
        params={"email": EMAIL, "access_token": key},
        json={"name": PERSON, "email": EMAIL, "document": DOCUMENT},
        headers={"Authorization": f"Bearer {key}", "Cookie": f"session={key}"},
    )

    require(response.status_code == 401, "the synthetic key was not refused")
    line = log_line(log_output, "http.request")
    require(line.get("route") == "/api/v1/tenant", "the route template is missing")
    require(line.get("status_code") == 401, "the 401 status is missing")
    assert_no_leaks(log_output.text, forbidden, "request log")


async def test_an_unexpected_error_logs_its_type_and_the_500_under_the_same_ids(
    client: httpx.AsyncClient, log_output: LogOutput
) -> None:
    response = await client.get("/boom")

    error = log_line(log_output, "api.unexpected_error")
    request = log_line(log_output, "http.request")
    require(error.get("error_type") == "RuntimeError", "the error type is missing")
    require(request.get("status_code") == 500, "the request line is not 500")
    require_ids_of(error, response, "error line")
    require_ids_of(request, response, "request line")
    assert_no_leaks(log_output.text, {"DSN password": PASSWORD, "message": "boom "}, "500 log")


# --- contextos limpos depois de cada requisição ----------------------------------------------


TENANT = UUID("11111111-1111-4111-8111-111111111111")
KEY = UUID("22222222-2222-4222-8222-222222222222")


@pytest.fixture
def authenticates(monkeypatch: pytest.MonkeyPatch) -> None:
    principal = Principal(tenant_id=TENANT, key_id=KEY, scopes=frozenset({"tenant:read"}))
    monkeypatch.setattr(
        dependencies, "authenticate_api_key", fake_authentication({"good": principal})
    )


def require_clean_context(what: str) -> None:
    require(request_context.get() is None, f"{what}: the request context was not cleared")
    require(current_request_id() is None, f"{what}: the request_id was not cleared")


@pytest.mark.usefixtures("authenticates")
async def test_an_authenticated_request_logs_the_public_tenant_and_key_ids(
    client: httpx.AsyncClient, log_output: LogOutput
) -> None:
    response = await client.get("/authenticated-boom", headers={"Authorization": "Bearer good"})

    inside = log_line(log_output, "inside.request")
    request = log_line(log_output, "http.request")
    for line in (inside, request):
        require(line.get("tenant_id") == encode_tenant_id(TENANT), "no public tenant_id")
        require(line.get("key_id") == encode_key_id(KEY), "no public key_id")
        require_ids_of(line, response, "authenticated line")
    require_clean_context("after the authenticated request")


@pytest.mark.usefixtures("authenticates")
@pytest.mark.parametrize(
    ("token", "status"), [("wrong", 401), ("down", 503)], ids=["refused", "database-down"]
)
async def test_contexts_are_cleared_after_each_request_including_failed_authentication(
    client: httpx.AsyncClient, log_output: LogOutput, token: str, status: int
) -> None:
    good = await client.get("/api/v1/auth/context", headers={"Authorization": "Bearer good"})
    require_clean_context("after the successful request")
    failed = await client.get("/api/v1/auth/context", headers={"Authorization": f"Bearer {token}"})
    require_clean_context("after the failed authentication")
    logging.getLogger("cce.test").warning("after.requests")

    require(good.status_code == 200 and failed.status_code == status, "unexpected statuses")
    first, second = log_output.requests()
    require(first.get("tenant_id") == encode_tenant_id(TENANT), "the first line has no tenant")
    require("tenant_id" not in second and "key_id" not in second, "the failed request inherited")
    require_ids_of(second, failed, "failed request line")
    require(first["request_id"] != second["request_id"], "two requests share a request_id")
    after = log_line(log_output, "after.requests")
    leftover = {"request_id", "trace_id", "tenant_id", "key_id"} & set(after)
    require(not leftover, "a log after the requests carries request context")


@pytest.mark.usefixtures("authenticates")
async def test_concurrent_requests_keep_their_own_context(
    client: httpx.AsyncClient, log_output: LogOutput
) -> None:
    responses = await asyncio.gather(
        *(
            client.get("/api/v1/auth/context", headers={"Authorization": f"Bearer {token}"})
            for token in ("good", "wrong", "good", "wrong")
        )
    )

    by_request = {line["request_id"]: line for line in log_output.requests()}
    require(len(by_request) == 4, "a request line is missing or shared")
    for response in responses:
        line = by_request.get(response.headers["x-request-id"])
        require(line is not None, "a response has no request line")
        assert line is not None
        require(line.get("trace_id") == response.headers["x-trace-id"], "a trace_id was mixed")
        authenticated = response.status_code == 200
        require(("tenant_id" in line) == authenticated, "a tenant_id crossed requests")
    require_clean_context("after the concurrent requests")


def test_configure_logging_can_run_again_without_duplicating_lines(log_output: LogOutput) -> None:
    settings = synthetic_settings()
    configure_logging(settings)
    log_output.install(settings)
    log_output.install(settings)

    logging.getLogger("cce.test").warning("once")

    require(len(log_output.events("once")) == 1, "the line was written more than once")


# --- só nomes de evento e valores no formato esperado (rodada 2) -----------------------------

# Sentinelas sintéticas: nenhuma é dado real, e nenhuma aparece em ID de teste nem em mensagem de
# falha (os `ids` são fixos e `require`/`assert_no_leaks` só citam rótulos).
DIGEST_HEX = "ab" * 32
PASSWORD_TEXT = "sentinela-sintetica-de-senha"
CNPJ = "12.345.678/0001-95"
FREE_TEXT_MESSAGES = [
    f"customer {PERSON} {EMAIL} {DOCUMENT}",
    "secret_digest=" + DIGEST_HEX,
    "senha=" + PASSWORD_TEXT,
    "customer %s with %s" % (PERSON, EMAIL),  # noqa: UP031 - mensagem já interpolada
    f"digest {DIGEST_HEX}",
    f"tenant.{EMAIL}",
    "a" * 65,
]
FREE_TEXT_IDS = [
    "personal-data",
    "secret-digest",
    "senha",
    "percent-interpolated",
    "bare-digest",
    "dotted-email",
    "too-long",
]
SENTINELS = {
    "name": PERSON,
    "e-mail": EMAIL,
    "document": DOCUMENT,
    "CNPJ": CNPJ,
    "digest": DIGEST_HEX,
    "password": PASSWORD_TEXT,
}


@pytest.mark.parametrize("message", FREE_TEXT_MESSAGES, ids=FREE_TEXT_IDS)
def test_a_free_text_message_becomes_a_fixed_marker(log_output: LogOutput, message: str) -> None:
    log_output.install(synthetic_settings())

    logging.getLogger("cce.test").error(message)

    lines = log_output.lines()
    require(len(lines) == 1, "the line is missing")
    require(lines[0].get("event") == UNRECOGNIZED_EVENT, "a free-text message was kept")
    require(lines[0].get("logger") == "cce.test", "the logger was lost")
    assert_no_leaks(log_output.text, SENTINELS | {"too long": "a" * 65}, "free-text log")


@pytest.mark.parametrize(
    "event", ["tenant.created", "api.unexpected_error", "http.request", "api_key.issued"]
)
def test_event_names_are_kept(log_output: LogOutput, event: str) -> None:
    log_output.install(synthetic_settings())

    logging.getLogger("cce.test").info(event)

    require(len(log_output.events(event)) == 1, "an event name was not kept")


def test_allowlisted_fields_keep_only_values_in_their_format(log_output: LogOutput) -> None:
    log_output.install(synthetic_settings())
    free = f"customer {PERSON} {EMAIL} senha={PASSWORD_TEXT}"

    logging.getLogger("cce.test").error(
        "fields.checked",
        extra={
            "route": free,
            "method": DOCUMENT,
            "error_type": "secret_digest=" + DIGEST_HEX,
            "request_id": EMAIL,
            "trace_id": DIGEST_HEX,
            "tenant_id": f"ten_{EMAIL}",
            "key_id": CNPJ,
            "status_code": DOCUMENT,
            "duration_ms": PERSON,
            "pepper_version": PASSWORD_TEXT,
            "frames": [free, {"digest": DIGEST_HEX}, f"app.py:12:{PERSON}"],
        },
    )

    line = log_line(log_output, "fields.checked")
    require(line.get("method") == OTHER_METHOD, "an unknown method was not reduced to the marker")
    assert_no_leaks(log_output.text, SENTINELS, "allowlisted fields")


def test_well_formed_values_of_allowlisted_fields_are_kept(log_output: LogOutput) -> None:
    log_output.install(synthetic_settings())
    values = {
        "route": "/api/v1/things/{thing_id}",
        "method": "PATCH",
        "error_type": "RuntimeError",
        "request_id": "req_" + "0" * 32,
        "trace_id": "1" * 32,
        "tenant_id": "ten_" + "2" * 32,
        "key_id": "key_" + "3" * 32,
        "status_code": 409,
        "duration_ms": 1.25,
        "pepper_version": 2,
        "frames": ["/app/cce/api/errors.py:12:handler"],
    }

    logging.getLogger("cce.test").error("fields.kept", extra=values)

    line = log_line(log_output, "fields.kept")
    for name, value in values.items():
        require(line.get(name) == value, f"the well-formed {name} was changed")


def test_key_id_as_a_uuid_is_kept(log_output: LogOutput) -> None:
    log_output.install(synthetic_settings())
    key_id = str(uuid4())

    logging.getLogger("cce.test").error("fields.uuid", extra={"key_id": key_id})

    require(log_line(log_output, "fields.uuid").get("key_id") == key_id, "a UUID key_id was lost")


@pytest.mark.parametrize(
    "text",
    [
        "secret_digest=" + DIGEST_HEX,
        "digest: " + DIGEST_HEX,
        "stored " + DIGEST_HEX,
        "senha=" + PASSWORD_TEXT,
        "chave: " + PASSWORD_TEXT,
        "password = " + PASSWORD_TEXT,
        f"contact {EMAIL}",
        f"cpf {DOCUMENT}",
        f"cnpj {CNPJ}",
        "cpf 12345678909",
    ],
    ids=[
        "secret-digest",
        "digest-colon",
        "bare-hex",
        "senha",
        "chave",
        "password-spaces",
        "e-mail",
        "cpf",
        "cnpj",
        "cpf-digits",
    ],
)
def test_text_redaction_covers_digests_passwords_and_personal_data(text: str) -> None:
    rendered = json.dumps(redact({"frames": [text], "nested": {"value": text}}))

    assert_no_leaks(rendered, SENTINELS | {"CPF digits": "12345678909"}, "redacted text")


@pytest.mark.parametrize(
    "method",
    [DOCUMENT, "SENHA-" + PASSWORD_TEXT, "PROPFIND"],
    ids=["document", "secret", "webdav"],
)
async def test_an_unknown_http_method_is_logged_as_a_fixed_marker(
    client: httpx.AsyncClient, log_output: LogOutput, method: str
) -> None:
    await client.request(method, "/api/v1/tenant")

    line = log_line(log_output, "http.request")
    require(line.get("method") == OTHER_METHOD, "an unknown method was logged")
    assert_no_leaks(log_output.text, SENTINELS, "unknown method log")


@pytest.mark.parametrize("method", ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def test_a_known_http_method_is_logged_as_is(
    client: httpx.AsyncClient, log_output: LogOutput, method: str
) -> None:
    await client.request(method, "/health/live")

    require(log_line(log_output, "http.request").get("method") == method, "the method was lost")


def test_only_the_exact_known_method_names_are_kept() -> None:
    require(normalize_method("get") == OTHER_METHOD, "a lowercase method was kept")
    require(normalize_method(None) == OTHER_METHOD, "a missing method was not reduced")
    require(normalize_method(b"GET") == OTHER_METHOD, "a bytes method was kept")
    require(normalize_method("CONNECT") == "CONNECT", "a known method was lost")
