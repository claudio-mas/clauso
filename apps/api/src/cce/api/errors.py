"""Envelope de erro da API e tradução de exceções para ele (plano do M0, seção 12.5; ADR-0014).

Toda resposta de erro tem a mesma forma, com código estável e o `request_id` da requisição:

    {"error": {"code": "...", "message": "...", "details": {...}, "request_id": "req_..."}}

Nada do que o cliente enviou volta na resposta: a validação informa só campo e tipo da violação, e
um erro do banco nunca mostra SQL, parâmetros, nomes de constraint nem a mensagem do driver. Um
erro inesperado vira `INTERNAL_ERROR`; o log registra o tipo e os frames, sem mensagem nem locais.
"""

from __future__ import annotations

import logging
import traceback
from collections.abc import Mapping, Sequence
from typing import Any, Final, Literal

import asyncpg  # type: ignore[import-untyped]
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.exc import (
    DBAPIError,
    IntegrityError,
    InterfaceError,
    OperationalError,
    SQLAlchemyError,
)
from sqlalchemy.exc import TimeoutError as PoolTimeout
from starlette.exceptions import HTTPException as StarletteHTTPException

from cce.identity.tenants.model import CONFLICT_FIELDS
from cce.identity.tenants.service import TenantNotFound
from cce.integrations.api_keys.authentication import AuthenticationFailed, DependencyUnavailable
from cce.shared.logging import current_request_id

LOGGER_NAME = "cce.api.errors"
_logger = logging.getLogger(LOGGER_NAME)

# Constraint conhecida -> campo público. Só estas viram 409; qualquer outra é 500.
_CONFLICT_FIELDS: Final[Mapping[str, str]] = CONFLICT_FIELDS

_MESSAGES: Final[Mapping[str, str]] = {
    "AUTHENTICATION_FAILED": "Authentication failed.",
    "FORBIDDEN": "The API key does not have the required scope.",
    "NOT_FOUND": "Resource not found.",
    "METHOD_NOT_ALLOWED": "Method not allowed.",
    "CONFLICT": "The request conflicts with the current state of the resource.",
    "VALIDATION_ERROR": "The request is invalid.",
    "INTERNAL_ERROR": "Internal error.",
    "DEPENDENCY_UNAVAILABLE": "A required dependency is unavailable.",
}
_STATUS_CODES: Final[Mapping[int, str]] = {
    401: "AUTHENTICATION_FAILED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
    500: "INTERNAL_ERROR",
    503: "DEPENDENCY_UNAVAILABLE",
}
_CHALLENGE: Final = {"WWW-Authenticate": "Bearer"}

ErrorCode = Literal[
    "AUTHENTICATION_FAILED",
    "FORBIDDEN",
    "NOT_FOUND",
    "METHOD_NOT_ALLOWED",
    "CONFLICT",
    "VALIDATION_ERROR",
    "INTERNAL_ERROR",
    "DEPENDENCY_UNAVAILABLE",
]
EXAMPLE_REQUEST_ID: Final = "req_0123456789abcdef0123456789abcdef"
_EXAMPLE_DETAILS: Final[Mapping[int, Mapping[str, Any]]] = {
    409: {"field": "name"},
    422: {"errors": [{"field": "name", "type": "string_too_short"}]},
}

# Erros de conexão: o banco não respondeu. Chegam crus do asyncpg ou embrulhados pelo SQLAlchemy.
_UNREACHABLE = (
    OperationalError,
    InterfaceError,
    PoolTimeout,
    OSError,
    TimeoutError,
    asyncpg.InterfaceError,
    asyncpg.PostgresConnectionError,
    asyncpg.CannotConnectNowError,
    asyncpg.TooManyConnectionsError,
)
DATABASE_ERRORS: Final = (
    SQLAlchemyError,
    OSError,
    TimeoutError,
    asyncpg.PostgresError,
    asyncpg.InterfaceError,
)


class ApiError(Exception):
    """Erro com status, código estável e `details` seguros. A mensagem é fixa por código."""

    def __init__(
        self,
        status_code: int,
        details: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__()
        self.status_code = status_code
        self.code = _STATUS_CODES[status_code]
        self.message = _MESSAGES[self.code]
        self.details = dict(details or {})
        self.headers = dict(headers or {})

    def __str__(self) -> str:
        return self.code


class ErrorBody(BaseModel):
    """O erro: código estável, mensagem fixa por código, `details` seguros e o `request_id`."""

    code: ErrorCode
    message: str
    details: dict[str, Any]
    request_id: str | None


class ErrorEnvelope(BaseModel):
    """Forma de toda resposta de erro da API."""

    error: ErrorBody


def error_responses(*status_codes: int, scope: str | None = None) -> dict[int | str, Any]:
    """Documentação OpenAPI dos erros de uma operação: envelope, exemplo e cabeçalhos.

    `scope` é o scope exigido pela rota, citado no exemplo do 403.
    """
    responses: dict[int | str, Any] = {}
    for status_code in status_codes:
        code = _STATUS_CODES[status_code]
        details = _EXAMPLE_DETAILS.get(status_code, {})
        if status_code == 403:
            details = {"required_scopes": [scope] if scope else []}
        example = {
            "error": {
                "code": code,
                "message": _MESSAGES[code],
                "details": details,
                "request_id": EXAMPLE_REQUEST_ID,
            }
        }
        response: dict[str, Any] = {
            "model": ErrorEnvelope,
            "description": f"`{code}`: {_MESSAGES[code]}",
            "content": {"application/json": {"example": example}},
        }
        if status_code == 401:
            response["headers"] = {
                "WWW-Authenticate": {
                    "description": "Sempre `Bearer`, para qualquer motivo de recusa.",
                    "schema": {"type": "string"},
                }
            }
        responses[status_code] = response
    return responses


def forbidden(required_scopes: Sequence[str]) -> ApiError:
    return ApiError(403, {"required_scopes": sorted(required_scopes)})


def error_body(code: str, message: str, details: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": dict(details or {}),
            "request_id": current_request_id(),
        }
    }


def render(error: ApiError) -> JSONResponse:
    return JSONResponse(
        error_body(error.code, error.message, error.details),
        status_code=error.status_code,
        headers=error.headers or None,
    )


def internal_error_body() -> dict[str, Any]:
    return error_body("INTERNAL_ERROR", _MESSAGES["INTERNAL_ERROR"])


def log_unexpected(error: BaseException) -> None:
    """Tipo e frames (arquivo, linha, função) do erro, sem mensagem nem locais."""
    frames = [
        f"{frame.filename}:{frame.lineno}:{frame.name}"
        for frame in traceback.extract_tb(error.__traceback__)
    ]
    _logger.error(
        "api.unexpected_error",
        extra={"error_type": type(error).__name__, "frames": frames},
    )


def _constraint_name(error: IntegrityError) -> str | None:
    """Nome da constraint violada, lido do erro do driver (direto ou como causa do adaptado)."""
    candidates = (error.orig, getattr(error.orig, "__cause__", None))
    for candidate in candidates:
        name = getattr(candidate, "constraint_name", None)
        if isinstance(name, str):
            return name
    return None


def from_database_error(error: BaseException) -> ApiError:
    """409 para constraint conhecida, 503 para banco inalcançável e 500 para todo o resto."""
    if isinstance(error, IntegrityError):
        field = _CONFLICT_FIELDS.get(_constraint_name(error) or "")
        return ApiError(409, {"field": field}) if field else ApiError(500)
    if isinstance(error, _UNREACHABLE) or (
        isinstance(error, DBAPIError) and error.connection_invalidated
    ):
        return ApiError(503)
    return ApiError(500)


def _validation_errors(error: RequestValidationError) -> list[dict[str, Any]]:
    """Só o campo e o tipo de cada violação.

    `loc` é do schema (`body`, nome de campo, índice), exceto em `extra_forbidden`, cujo último
    elemento é a chave que o cliente enviou: ela nunca volta. `input`, `ctx` e `msg` também não.
    """
    items = []
    for item in error.errors():
        location = [part for part in item.get("loc", ()) if part != "body"]
        if item.get("type") == "extra_forbidden" and location:
            location = location[:-1]
        named = [part for part in location if isinstance(part, str)]
        items.append({"field": ".".join(named) or None, "type": str(item.get("type", "invalid"))})
    return items


async def _api_error(_: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, ApiError)
    return render(error)


async def _validation_error(_: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, RequestValidationError)
    return render(ApiError(422, {"errors": _validation_errors(error)}))


async def _http_error(_: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, StarletteHTTPException)
    status_code = error.status_code if error.status_code in _STATUS_CODES else 500
    allow = (error.headers or {}).get("Allow") if status_code == 405 else None
    return render(ApiError(status_code, headers={"Allow": allow} if allow else None))


async def _authentication_failed(_: Request, __: Exception) -> JSONResponse:
    return render(ApiError(401, headers=_CHALLENGE))


async def _not_found(_: Request, __: Exception) -> JSONResponse:
    return render(ApiError(404))


async def _dependency_unavailable(_: Request, __: Exception) -> JSONResponse:
    return render(ApiError(503))


async def _database_error(_: Request, error: Exception) -> JSONResponse:
    translated = from_database_error(error)
    if translated.status_code == 500:
        log_unexpected(error)
    return render(translated)


def install_error_handlers(app: FastAPI) -> None:
    """Registra os tradutores. O que não é tratado aqui o middleware transforma em 500."""
    app.add_exception_handler(ApiError, _api_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(AuthenticationFailed, _authentication_failed)
    app.add_exception_handler(DependencyUnavailable, _dependency_unavailable)
    app.add_exception_handler(TenantNotFound, _not_found)
    for database_error in DATABASE_ERRORS:
        app.add_exception_handler(database_error, _database_error)
