"""Middleware ASGI puro: IDs de correlação, uma linha de log por requisição e a última barreira
contra erro sem resposta.

Cada requisição HTTP recebe um `request_id` (`req_` + 32 hex) e um `trace_id` (32 hex, nunca zero)
novos; o que o cliente mandar nesses cabeçalhos é ignorado. Os dois vão nos cabeçalhos
`X-Request-ID` e `X-Trace-ID` de TODA resposta, inclusive de erro, e ficam no contexto da
requisição (`cce.shared.logging.request_context`), que a autenticação completa com os IDs públicos
do Tenant e da chave. O contexto é definido no início, sobrescrevendo qualquer resto, e restaurado
no fim, inclusive depois de autenticação recusada ou de erro. O Tenant nunca vem de cabeçalho. No M0
o `trace_id` é só correlação local (sem exportação OpenTelemetry; ADR-0016).

No fim de cada requisição sai uma linha `http.request` com o método (só os métodos HTTP conhecidos;
qualquer outro, que é texto livre do cliente, vira `OTHER`), o template da rota (`unmatched`
quando nenhuma rota casou), status e duração. Nunca o path, a query string, cabeçalhos ou corpo.

Uma exceção que nenhum handler tratou vira um 500 padronizado, se a resposta ainda não começou, e
nunca sobe ao servidor, que imprimiria a mensagem e o traceback. Fica fora do
`ServerErrorMiddleware` do Starlette justamente para que esse 500 também leve os cabeçalhos.
"""

from __future__ import annotations

import logging
import secrets
import time
from re import Pattern
from typing import Any
from uuid import uuid4

from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse
from starlette.routing import compile_path
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from cce.api.errors import internal_error_body, log_unexpected
from cce.shared.logging import (
    HTTP_REQUEST_EVENT,
    UNMATCHED_ROUTE,
    RequestContext,
    declare_routes,
    normalize_method,
    request_context,
)

LOGGER_NAME = "cce.api.request"
_logger = logging.getLogger(LOGGER_NAME)

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


def route_template(scope: Scope) -> str:
    """O template completo da rota que o roteador escolheu (`/api/v1/tenant`), ou `unmatched`.

    Com roteadores incluídos, `scope["route"].path` é relativo ao roteador (`/tenant`). O template
    completo é o path do OpenAPI da aplicação que termina nele e casa com o path da requisição. O
    resultado vem sempre de um template declarado, nunca do path recebido.
    """
    relative = getattr(scope.get("route"), "path", None)
    if not isinstance(relative, str):
        return UNMATCHED_ROUTE
    path = scope.get("path", "")
    for template, pattern in _templates(scope.get("app")):
        if template.endswith(relative) and pattern.fullmatch(path):
            return template
    declare_routes([relative])  # rota declarada fora do OpenAPI (como /docs)
    return relative


def _templates(app: Any) -> tuple[tuple[str, Pattern[str]], ...]:
    """Templates do OpenAPI da aplicação, compilados uma vez e guardados em `app.state`."""
    state = getattr(app, "state", None)
    cached = getattr(state, "route_templates", None)
    if cached is not None:
        return cached  # type: ignore[no-any-return]
    openapi = getattr(app, "openapi", None)
    try:
        paths = openapi().get("paths", {}) if callable(openapi) else {}
    except Exception:
        # O log da requisição nunca derruba a resposta; sem templates, sai o path relativo da rota.
        paths = {}
    compiled = tuple((template, compile_path(template)[0]) for template in paths)
    declare_routes(template for template, _ in compiled)
    if state is not None:
        state.route_templates = compiled
    return compiled


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        context = RequestContext(request_id=new_request_id(), trace_id=new_trace_id())
        token = request_context.set(context)
        started = time.perf_counter()
        status_code: int | None = None

        async def send_with_ids(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                message.setdefault("headers", [])
                headers = MutableHeaders(scope=message)
                headers[REQUEST_ID_HEADER] = context.request_id
                headers[TRACE_ID_HEADER] = context.trace_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_ids)
        except Exception as error:
            log_unexpected(error)
            if status_code is None:
                response = JSONResponse(internal_error_body(), status_code=500)
                await response(scope, receive, send_with_ids)
            # Com a resposta já começada não há o que enviar: o servidor fecha a conexão incompleta.
        finally:
            try:
                _logger.info(
                    HTTP_REQUEST_EVENT,
                    extra={
                        "method": normalize_method(scope.get("method")),
                        "route": route_template(scope),
                        "status_code": status_code,
                        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                    },
                )
            finally:
                request_context.reset(token)
