"""Cliente HTTP dos testes da API: a aplicação de produção (`create_app`), com lifespan, via ASGI.

Os pedidos levam a chave bruta no cabeçalho. Por isso toda chamada passa por `send`, que nunca deixa
uma exceção do cliente subir com os cabeçalhos nos locais do traceback, e os testes que usam chave
levam o marcador `sensitive`. Mensagens de falha citam só status e código de erro, nunca o corpo.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from integration.support import require
from key_support import acall_or_fail
from pydantic import SecretStr

from cce.main import create_app
from cce.shared.config import Settings

BASE_URL = "http://testserver"
REQUEST_ID = re.compile(r"req_[0-9a-f]{32}")
TRACE_ID = re.compile(r"[0-9a-f]{32}")


@asynccontextmanager
async def running_app(settings: Settings) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient]]:
    """A aplicação com o lifespan rodando e um cliente ASGI; o shutdown roda na saída."""
    app = create_app(settings)
    async with (
        LifespanManager(app) as manager,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=manager.app), base_url=BASE_URL
        ) as client,
    ):
        yield app, client


def bearer(raw_key: SecretStr, **extra: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw_key.get_secret_value()}", **extra}


async def send(client: httpx.AsyncClient, method: str, path: str, **options: Any) -> httpx.Response:
    """Um pedido que nunca levanta com a chave nos locais: um erro vira falha de texto fixo."""
    return await acall_or_fail(lambda: client.request(method, path, **options), f"{method} {path}")


def body_of(response: httpx.Response) -> dict[str, Any]:
    """O JSON do corpo, ou uma falha de texto fixo (o corpo nunca é impresso)."""
    try:
        body = response.json()
    except ValueError:
        body = None
    require(isinstance(body, dict), f"the {response.status_code} response is not a JSON object")
    assert isinstance(body, dict)
    return body


def require_status(response: httpx.Response, expected: int, what: str) -> dict[str, Any]:
    """Confere o status; na falha cita só o status e o código de erro recebidos."""
    code = error_code(response)
    require(
        response.status_code == expected,
        f"{what}: expected {expected}, got {response.status_code} ({code or 'no error code'})",
    )
    return body_of(response)


def error_code(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    error = body.get("error") if isinstance(body, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    return code if isinstance(code, str) and re.fullmatch(r"[A-Z_]{1,40}", code) else None


def require_error(response: httpx.Response, status: int, code: str, what: str) -> dict[str, Any]:
    """Confere o envelope padronizado: status, código, mensagem, `details` e `request_id`.

    O `request_id` do corpo é o mesmo do cabeçalho `X-Request-ID`, e há um `X-Trace-ID`.
    """
    body = require_status(response, status, what)
    error = body.get("error")
    require(isinstance(error, dict) and set(body) == {"error"}, f"{what}: not an error envelope")
    assert isinstance(error, dict)
    require(
        set(error) == {"code", "message", "details", "request_id"},
        f"{what}: the error envelope has other fields",
    )
    require(error["code"] == code, f"{what}: expected {code}, got {error_code(response)}")
    require(isinstance(error["message"], str) and error["message"] != "", f"{what}: no message")
    require(isinstance(error["details"], dict), f"{what}: details is not an object")
    request_id = response.headers.get("x-request-id", "")
    require(REQUEST_ID.fullmatch(request_id) is not None, f"{what}: bad X-Request-ID header")
    require(error["request_id"] == request_id, f"{what}: request_id differs from the header")
    require_trace_id(response, what)
    return error


def require_trace_id(response: httpx.Response, what: str) -> None:
    trace_id = response.headers.get("x-trace-id", "")
    require(
        TRACE_ID.fullmatch(trace_id) is not None and trace_id != "0" * 32,
        f"{what}: bad X-Trace-ID header",
    )
