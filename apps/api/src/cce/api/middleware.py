"""Middleware ASGI puro: IDs de correlação e a última barreira contra erro sem resposta.

Cada requisição HTTP recebe um `request_id` (`req_` + 32 hex) e um `trace_id` (32 hex, nunca zero)
novos; o que o cliente mandar nesses cabeçalhos é ignorado. Os dois vão nos cabeçalhos
`X-Request-ID` e `X-Trace-ID` de TODA resposta, inclusive de erro, e ficam em contextvars durante a
requisição: definidas no início, sobrescrevendo qualquer resto, e restauradas no fim. O Tenant
nunca vem de cabeçalho. No M0 o `trace_id` é só correlação local (sem exportação OpenTelemetry).

Uma exceção que nenhum handler tratou vira um 500 padronizado, se a resposta ainda não começou, e
nunca sobe ao servidor, que imprimiria a mensagem e o traceback. Fica fora do
`ServerErrorMiddleware` do Starlette justamente para que esse 500 também leve os cabeçalhos.
"""

from __future__ import annotations

import secrets
from contextvars import ContextVar
from uuid import uuid4

from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from cce.api.errors import internal_error_body, log_unexpected, request_id_var

trace_id_var: ContextVar[str | None] = ContextVar("cce_trace_id", default=None)

REQUEST_ID_HEADER = "X-Request-ID"
TRACE_ID_HEADER = "X-Trace-ID"
_ZERO_TRACE = "0" * 32


def new_request_id() -> str:
    return "req_" + uuid4().hex


def new_trace_id() -> str:
    while True:
        trace_id = secrets.token_hex(16)
        if trace_id != _ZERO_TRACE:
            return trace_id


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = new_request_id()
        trace_id = new_trace_id()
        request_token = request_id_var.set(request_id)
        trace_token = trace_id_var.set(trace_id)
        started = False

        async def send_with_ids(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                message.setdefault("headers", [])
                headers = MutableHeaders(scope=message)
                headers[REQUEST_ID_HEADER] = request_id
                headers[TRACE_ID_HEADER] = trace_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_ids)
        except Exception as error:
            log_unexpected(error)
            if not started:
                response = JSONResponse(internal_error_body(), status_code=500)
                await response(scope, receive, send_with_ids)
            # Com a resposta já começada não há o que enviar: o servidor fecha a conexão incompleta.
        finally:
            trace_id_var.reset(trace_token)
            request_id_var.reset(request_token)
