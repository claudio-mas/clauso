"""Logs estruturados e seguros (plano do M0, seção 13.1; ADR-0016).

`configure_logging(settings)` liga um único handler no logger raiz: toda linha, da aplicação ou de
biblioteca, passa pelo mesmo pipeline e sai como um objeto JSON por linha (ou legível para pessoas,
só em `local`). O pipeline, em ordem:

1. `event` é o TEMPLATE da mensagem (`record.msg`), nunca a mensagem formatada com os argumentos;
   uma mensagem que não é texto vira `<NomeDoTipo>`. Uma exceção vira só `error_type`, sem mensagem
   nem traceback.
2. Os campos padrão: `timestamp` (UTC), `level`, `logger`, `service`, `version`, `environment` e,
   durante uma requisição, `request_id`, `trace_id` e, depois da autenticação, `tenant_id` e
   `key_id` públicos.
3. Allowlist: só os campos de `ALLOWED_FIELDS` ficam; qualquer outro `extra` é descartado.
4. Redação recursiva (`redact`) dos campos que ficaram: valor sob nome sensível (`authorization`,
   `cookie`, `password`, `pepper`, `digest`, `dsn`, ...), `SecretStr` e bytes (digests, peppers)
   pelo tipo, e chaves de API, `Bearer`, senhas de URL e `senha=` no texto. Objetos que não são
   JSON nativo nunca passam por `str`/`repr`: viram `<NomeDoTipo>`, exceto mapeamentos (como os
   cabeçalhos do Starlette), sequências, named tuples, modelos Pydantic e dataclasses, que são
   percorridos campo a campo.

O access log do Uvicorn é desligado aqui, além do `--no-access-log` da imagem: ele registraria o
path e a query string crus. Quem registra cada requisição é o `RequestContextMiddleware`, com o
template da rota.
"""

from __future__ import annotations

import dataclasses
import logging
import re
import sys
from collections.abc import Iterable, Mapping, MutableMapping
from contextvars import ContextVar
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from pathlib import PurePath
from typing import Any, Final, TextIO
from uuid import UUID

import structlog
from pydantic import BaseModel, SecretBytes, SecretStr

from cce.shared.config import Settings

REDACTED: Final = "[REDACTED]"
UNMATCHED_ROUTE: Final = "unmatched"

ALLOWED_FIELDS: Final = frozenset(
    {
        # padrão
        "timestamp",
        "level",
        "logger",
        "event",
        "service",
        "version",
        "environment",
        # correlação e identidade (IDs públicos)
        "request_id",
        "trace_id",
        "tenant_id",
        "key_id",
        # requisição
        "method",
        "route",
        "status_code",
        "duration_ms",
        # diagnóstico
        "error_type",
        "frames",
        "pepper_version",
    }
)

# Nomes normalizados (minúsculos, sem `-` e `_`) cujo valor é redigido inteiro, em qualquer nível.
_SENSITIVE_NAME_PARTS: Final = (
    "authorization",
    "cookie",
    "password",
    "passwd",
    "secret",
    "token",
    "apikey",
    "rawkey",
    "pepper",
    "digest",
    "credential",
    "dsn",
    "url",
    "email",
    "document",
    "cpf",
    "cnpj",
    "phone",
    "body",
    "query",
)
_SENSITIVE_NAMES: Final = frozenset({"key", "name", "fullname", "customername"})

_TEXT_PATTERNS: Final = (
    # Chave de API do CCE (o segredo é base64url e pode conter "_").
    (re.compile(r"cce_[a-z]{3}_[0-9a-f]{32}_[A-Za-z0-9_-]+", re.ASCII), REDACTED),
    # Esquema de Authorization seguido da credencial.
    (re.compile(r"(?i)\b(bearer|basic)(\s+)\S+"), rf"\1\2{REDACTED}"),
    # Usuário e senha de uma URL (DSN do banco, Redis).
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^\s/@]*@"), rf"\1{REDACTED}@"),
    # `senha=valor`, `token: valor` e semelhantes.
    (
        re.compile(
            r"(?i)\b(password|passwd|pwd|secret|token|access_token|api_?key|pepper|session)"
            r"(\s*[=:]\s*)[^\s&,;]+"
        ),
        rf"\1\2{REDACTED}",
    ),
)

_MAX_DEPTH: Final = 8
_TRUNCATED: Final = "<truncated>"


# --- contexto da requisição ---------------------------------------------------------------------


@dataclasses.dataclass(slots=True)
class RequestContext:
    """Correlação da requisição em andamento. `tenant_id` e `key_id` são os IDs públicos."""

    request_id: str
    trace_id: str
    tenant_id: str | None = None
    key_id: str | None = None


# Definido pelo middleware no início de cada requisição e restaurado no fim, sempre. É um objeto
# mutável para que a autenticação, que roda mais fundo na pilha, possa completá-lo.
request_context: ContextVar[RequestContext | None] = ContextVar("cce_request_context", default=None)


def current_request_id() -> str | None:
    context = request_context.get()
    return context.request_id if context is not None else None


def bind_principal(tenant_id: str, key_id: str) -> None:
    """Associa o Tenant e a chave autenticados (IDs públicos) à requisição em andamento."""
    context = request_context.get()
    if context is not None:
        context.tenant_id = tenant_id
        context.key_id = key_id


# --- redação ------------------------------------------------------------------------------------


def is_sensitive_name(name: str) -> bool:
    normalized = name.lower().replace("-", "").replace("_", "")
    return normalized in _SENSITIVE_NAMES or any(
        part in normalized for part in _SENSITIVE_NAME_PARTS
    )


def scrub_text(text: str) -> str:
    for pattern, replacement in _TEXT_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _type_marker(value: object) -> str:
    return f"<{type(value).__name__}>"


def redact(value: Any) -> Any:
    """Cópia de `value` só com valores JSON nativos e nenhuma credencial, em qualquer nível."""
    return _redact(value, 0, set())


def _redact(value: Any, depth: int, active: set[int]) -> Any:
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, SecretStr | SecretBytes | bytes | bytearray | memoryview):
        return REDACTED
    if isinstance(value, UUID | datetime | date | time | Decimal | PurePath):
        return scrub_text(str(value))
    if isinstance(value, Enum):
        return _redact(value.value, depth, active)
    if depth >= _MAX_DEPTH or id(value) in active:
        return _TRUNCATED
    active.add(id(value))
    try:
        return _redact_container(value, depth + 1, active)
    finally:
        active.discard(id(value))


def _redact_container(value: Any, depth: int, active: set[int]) -> Any:
    if isinstance(value, BaseModel):
        names: Iterable[str] = type(value).model_fields
        return _redact_fields(((n, getattr(value, n, None)) for n in names), depth, active)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        names = (field.name for field in dataclasses.fields(value))
        return _redact_fields(((n, getattr(value, n, None)) for n in names), depth, active)
    if isinstance(value, tuple) and hasattr(value, "_fields"):
        # Named tuple, como a URL do SQLAlchemy: os nomes dos campos decidem a redação.
        return _redact_fields(zip(value._fields, value, strict=True), depth, active)
    if isinstance(value, Mapping):
        # `items()` dos cabeçalhos do Starlette traz também os valores repetidos.
        return _redact_fields(value.items(), depth, active)
    if isinstance(value, list | tuple | set | frozenset):
        return [_redact(item, depth, active) for item in value]
    return _type_marker(value)


def _redact_fields(items: Iterable[tuple[Any, Any]], depth: int, active: set[int]) -> Any:
    redacted: dict[str, Any] = {}
    for key, item in items:
        name = scrub_text(key) if isinstance(key, str) else str(_redact(key, depth, active))
        redacted[name] = REDACTED if is_sensitive_name(name) else _redact(item, depth, active)
    return redacted


# --- processors do pipeline ---------------------------------------------------------------------

EventDict = MutableMapping[str, Any]
_RECORD_ATTRIBUTES: Final = frozenset(vars(logging.makeLogRecord({})))


def _from_record(_: Any, __: str, event: EventDict) -> EventDict:
    """`event` é o template, sem argumentos; os `extra` viram campos; a exceção, só o tipo."""
    record = event.get("_record")
    if isinstance(record, logging.LogRecord):
        event["event"] = record.msg if isinstance(record.msg, str) else _type_marker(record.msg)
        event["logger"] = record.name
        for name, value in vars(record).items():
            if name not in _RECORD_ATTRIBUTES and not name.startswith("_"):
                event.setdefault(name, value)
    exc_info = event.pop("exc_info", None)
    event.pop("stack_info", None)
    if isinstance(exc_info, tuple) and exc_info and isinstance(exc_info[0], type):
        event.setdefault("error_type", exc_info[0].__name__)
    return event


def _with_request_context(_: Any, __: str, event: EventDict) -> EventDict:
    context = request_context.get()
    if context is not None:
        event["request_id"] = context.request_id
        event["trace_id"] = context.trace_id
        if context.tenant_id is not None:
            event["tenant_id"] = context.tenant_id
        if context.key_id is not None:
            event["key_id"] = context.key_id
    return event


def _with_service(settings: Settings) -> structlog.typing.Processor:
    fields = {
        "service": settings.service_name,
        "version": settings.version,
        "environment": settings.env,
    }

    def add(_: Any, __: str, event: EventDict) -> EventDict:
        event.update(fields)
        return event

    return add


def keep_allowed_fields(_: Any, __: str, event: EventDict) -> EventDict:
    """Descarta todo campo fora de `ALLOWED_FIELDS`, inclusive os internos do structlog."""
    for name in [name for name in event if name not in ALLOWED_FIELDS]:
        del event[name]
    return event


def redact_event(_: Any, __: str, event: EventDict) -> EventDict:
    """Redige recursivamente cada campo que sobrou da allowlist."""
    for name, value in list(event.items()):
        event[name] = redact(value)
    return event


# --- instalação ---------------------------------------------------------------------------------


class _Handler(logging.StreamHandler[TextIO]):
    """O handler do CCE. Sem stream fixo, escreve no `sys.stdout` do momento da escrita."""

    def __init__(self, stream: TextIO | None) -> None:
        self._fixed = stream
        super().__init__(stream)

    @property
    def stream(self) -> TextIO:
        return self._fixed if self._fixed is not None else sys.stdout

    @stream.setter
    def stream(self, _: TextIO) -> None:
        pass


def _formatter(settings: Settings) -> logging.Formatter:
    renderer: structlog.typing.Processor = (
        structlog.dev.ConsoleRenderer(colors=False)
        if settings.log_format == "console"
        else structlog.processors.JSONRenderer()
    )
    return structlog.stdlib.ProcessorFormatter(
        processors=[
            _from_record,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True, key="timestamp"),
            _with_service(settings),
            _with_request_context,
            keep_allowed_fields,
            redact_event,
            renderer,
        ],
        use_get_message=False,
        pass_foreign_args=False,
    )


def configure_logging(settings: Settings, stream: TextIO | None = None) -> None:
    """Instala o pipeline no logger raiz, no nível de `settings`. Pode ser chamada de novo.

    Sem `stream`, escreve no stdout do processo. O access log do Uvicorn fica desligado, e os
    outros loggers dele passam a sair pelo mesmo pipeline.
    """
    handler = _Handler(stream)
    handler.setFormatter(_formatter(settings))
    root = logging.getLogger()
    for existing in [h for h in root.handlers if isinstance(h, _Handler)]:
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(settings.log_level)

    access = logging.getLogger("uvicorn.access")
    access.handlers.clear()
    access.propagate = False
    access.disabled = True
    for name in ("uvicorn", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
