"""Logs de requisições reais contra PostgreSQL e Redis de teste (plano do M0, seção 13.1).

Duas provas: (1) a aplicação de produção, com chave emitida pelo código de produção, registra os IDs
públicos do Tenant e da chave e nunca a chave, o digest, o pepper, o DSN, o corpo nem a query; e
os contextos ficam limpos depois de cada requisição, inclusive de uma autenticação recusada;
(2) um Uvicorn de verdade, iniciado SEM `--no-access-log`, não escreve o access log bruto.

Higiene de falhas: marcador `sensitive`; só `require`/`assert_no_leaks`; todo pedido passa por
`send`; o subprocesso recebe a configuração pelo ambiente, nunca pelo argv.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from typing import Any

import httpx
import pytest
from api_support import bearer, require_status, send
from db_support import sensitive_components
from fastapi import FastAPI
from key_support import secret_of
from log_support import LogOutput
from pydantic import SecretStr
from seeding import SeededTenant, emit_key
from sqlalchemy.ext.asyncio import AsyncEngine

from cce.shared.config import Settings
from cce.shared.ids import encode_key_id, encode_tenant_id
from cce.shared.logging import ALLOWED_FIELDS, current_request_id, request_context

from .support import ROOT, assert_no_leaks, require

pytestmark = [pytest.mark.integration, pytest.mark.sensitive]

Api = tuple[FastAPI, httpx.AsyncClient]
CONTEXT = "/api/v1/auth/context"
TENANT = "/api/v1/tenant"
PERSON = "Fulana Sentinela de Tal"
EMAIL = "fulana.sentinela@example.com"
DOCUMENT = "987.654.321-00"


def forbidden_of(settings: Settings, raw_key: SecretStr, digest: bytes) -> dict[str, str]:
    key = raw_key.get_secret_value()
    values = {
        "key": key,
        "secret": secret_of(key),
        "digest": digest.hex(),
        "redis URL": settings.redis_url.get_secret_value(),
        "name": PERSON,
        "e-mail": EMAIL,
        "document": DOCUMENT,
    }
    for version, pepper in settings.api_key_peppers.items():
        values[f"pepper {version}"] = pepper.get_secret_value()
    values.update(sensitive_components(settings.database_url.get_secret_value()))
    return values


async def test_real_requests_log_public_ids_and_no_credentials_or_personal_data(
    api: Api,
    log_output: LogOutput,
    migrator_engine: AsyncEngine,
    api_settings: Settings,
    tenant_a: SeededTenant,
) -> None:
    _, client = api
    log_output.install(api_settings)
    keyed, issued = await emit_key(migrator_engine, api_settings, tenant_a)
    raw = issued.raw_key.get_secret_value()
    wrong = SecretStr(raw[:-1] + ("B" if raw.endswith("A") else "A"))  # sempre outro segredo
    forbidden = forbidden_of(api_settings, issued.raw_key, issued.secret_digest)
    forbidden["wrong key"] = wrong.get_secret_value()

    ok = await send(client, "GET", CONTEXT, headers=bearer(issued.raw_key))
    require(request_context.get() is None, "the context survived the successful request")
    patched = await send(
        client,
        "PATCH",
        TENANT,
        params={"email": EMAIL, "document": DOCUMENT},
        json={"name": PERSON},
        headers=bearer(issued.raw_key, Cookie=f"session={issued.raw_key.get_secret_value()}"),
    )
    refused = await send(client, "GET", CONTEXT, headers=bearer(wrong))
    require(request_context.get() is None, "the context survived the refused authentication")
    require(current_request_id() is None, "the request_id survived the refused authentication")

    require_status(ok, 200, "context")
    require_status(patched, 200, "patch")
    require_status(refused, 401, "wrong secret")
    lines = log_output.requests()
    require(len(lines) == 3, "expected one request line per request")
    first, second, third = lines
    for line, response in ((first, ok), (second, patched), (third, refused)):
        require(set(line) <= ALLOWED_FIELDS, "a request line has a field outside the allowlist")
        require(line.get("request_id") == response.headers["x-request-id"], "wrong request_id")
        require(line.get("trace_id") == response.headers["x-trace-id"], "wrong trace_id")
    for line in (first, second):
        require(line.get("tenant_id") == encode_tenant_id(keyed.id), "no public tenant_id")
        require(line.get("key_id") == encode_key_id(issued.key_id), "no public key_id")
    require(second.get("route") == TENANT and second.get("method") == "PATCH", "wrong route")
    require("tenant_id" not in third and "key_id" not in third, "the refused request inherited")
    assert_no_leaks(log_output.text, forbidden, "request logs")


# --- Uvicorn de verdade -------------------------------------------------------------------------


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


def wait_until_live(base_url: str, server: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        require(server.poll() is None, "uvicorn exited before answering")
        try:
            if httpx.get(f"{base_url}/health/live", timeout=1).status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.2)
    require(False, "uvicorn did not answer /health/live in 30 s")


def stop(server: subprocess.Popen[str]) -> str:
    server.terminate()
    try:
        output, _ = server.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        server.kill()
        output, _ = server.communicate()
    return output


def test_a_real_uvicorn_never_writes_the_raw_access_log(api_settings: Settings) -> None:
    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    token = "cce_tst_" + "0" * 32 + "_" + "S" * 43  # sintético: nunca autentica
    environment = {**os.environ, "CCE_ENV_FILE": os.environ.get("CCE_ENV_FILE", ".env.test")}
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "cce.main:create_app",
        "--factory",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]  # sem --no-access-log: a própria aplicação precisa desligá-lo
    server = subprocess.Popen(
        command,
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        wait_until_live(base_url, server)
        headers = {"Authorization": f"Bearer {token}", "Cookie": f"session={token}"}
        responses: list[Any] = [
            httpx.get(f"{base_url}/health/live", params={"access_token": token}, headers=headers),
            httpx.get(f"{base_url}/api/v1/customers/{EMAIL}", params={"cpf": DOCUMENT}),
            httpx.get(f"{base_url}{CONTEXT}", headers=headers),
        ]
        # Método HTTP livre com um documento: o servidor pode até recusar, mas o log não o guarda.
        try:
            httpx.request(DOCUMENT, f"{base_url}{TENANT}")
        except httpx.TransportError:
            pass
    finally:
        output = stop(server)

    require([r.status_code for r in responses] == [200, 404, 401], "unexpected statuses")
    forbidden = {
        "token": token,
        "token secret": "S" * 43,
        "e-mail": EMAIL,
        "document": DOCUMENT,
        "query string": "access_token=",
        "raw access line": '"GET /',
    }
    forbidden.update(sensitive_components(api_settings.database_url.get_secret_value()))
    forbidden.pop("database host")  # o Uvicorn escuta no mesmo 127.0.0.1 do banco de teste
    assert_no_leaks(output, forbidden, "uvicorn output")
    routes = [line for line in output.splitlines() if '"event": "http.request"' in line]
    require(len(routes) >= 3, "the JSON request lines are missing from uvicorn's output")
    require(any('"route": "unmatched"' in line for line in routes), "the 404 is not unmatched")
    require(any(f'"route": "{CONTEXT}"' in line for line in routes), "the route is not logged")
