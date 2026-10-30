"""Ajustes do documento OpenAPI que valem para todas as operações.

Toda resposta da API, de sucesso ou de erro, traz `X-Request-ID` e `X-Trace-ID` (o middleware os
põe); o documento gerado pelo FastAPI não sabe disso, então eles são acrescentados aqui, uma vez,
na primeira geração.
"""

from __future__ import annotations

from typing import Any, Final

from fastapi import FastAPI

from cce.api.middleware import REQUEST_ID_HEADER, TRACE_ID_HEADER

CORRELATION_HEADERS: Final = {
    REQUEST_ID_HEADER: {
        "description": "ID da requisição (`req_` + 32 hex), o mesmo do `request_id` do erro.",
        "schema": {"type": "string", "pattern": "^req_[0-9a-f]{32}$"},
    },
    TRACE_ID_HEADER: {
        "description": "ID de rastreamento (32 hex). No M0, só correlação local.",
        "schema": {"type": "string", "pattern": "^[0-9a-f]{32}$"},
    },
}


def install_openapi(app: FastAPI) -> None:
    """Faz `app.openapi()` documentar os cabeçalhos de correlação em toda resposta."""
    generate = app.openapi

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            schema = generate()
            for item in schema.get("paths", {}).values():
                for operation in item.values():
                    for response in operation.get("responses", {}).values():
                        response.setdefault("headers", {}).update(CORRELATION_HEADERS)
            app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = openapi  # type: ignore[method-assign]
