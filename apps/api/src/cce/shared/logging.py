"""Logs estruturados e seguros (plano do M0, seção 13.1; ADR-0016).

`configure_logging(settings)` liga um único handler no logger raiz: toda linha, da aplicação ou de
biblioteca, passa pelo mesmo pipeline e sai como um objeto JSON por linha (ou legível para pessoas,
só em `local`). O pipeline, em ordem:

1. `event` é um NOME de evento (`tenant.created`, `http.request`: minúsculas, dígitos, `_` e `.`,
   até 64 caracteres), tirado de `record.msg`, nunca da mensagem formatada com os argumentos.
   Qualquer outra mensagem (texto livre, f-string ou `%` já aplicados, objeto) vira o marcador fixo
   `log.unrecognized_message`: o texto nunca é registrado. Uma exceção vira só `error_type`, sem
   mensagem nem traceback.
2. Os campos padrão: `timestamp` (UTC), `level`, `logger`, `service`, `version`, `environment` e,
   durante uma requisição, `request_id`, `trace_id` e, depois da autenticação, `tenant_id` e
   `key_id` públicos.
3. Allowlist: só os campos de `ALLOWED_FIELDS` ficam; qualquer outro `extra` é descartado.
4. Formato por campo: cada campo que ficou só é mantido no formato exato dele (IDs `req_`/`ten_`/
   `key_` + hex, `trace_id` hex, `route` como template, `error_type` como identificador, inteiros
   e números nas faixas válidas, `frames` como `arquivo:linha:função`); `method` só aceita os
   métodos HTTP conhecidos (o resto vira `OTHER`). Um valor fora do formato vira `[REDACTED]`.

`redact` (redação recursiva de objetos, usada nos `frames` e disponível para quem precisar)
redige valores sob nome sensível (`authorization`, `cookie`, `password`, `pepper`, `digest`, `dsn`,
...), `SecretStr` e bytes (digests, peppers) pelo tipo e, no texto, chaves de API, `Bearer`, senhas
de URL, `nome=valor` com nome sensível (`senha`, `secret_digest`, `token`...), hex longo, tokens
longos, e-mail, CPF e CNPJ. Objetos que não são JSON nativo nunca passam por `str`/`repr`: viram
`<NomeDoTipo>`, exceto mapeamentos (como os cabeçalhos do Starlette), sequências, named tuples,
modelos Pydantic e dataclasses, percorridos campo a campo. A redação de texto é uma segunda
barreira, não uma garantia: a garantia vem de não registrar texto livre (itens 1, 3 e 4).

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
UNRECOGNIZED_EVENT: Final = "log.unrecognized_message"
OTHER_METHOD: Final = "OTHER"

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
    # `senha=valor`, `secret_digest: valor` e semelhantes: o nome pode ter prefixo e sufixo.
    (
        re.compile(
            r"(?i)\b([\w-]*(?:password|passwd|pwd|senha|secret|token|api_?key|pepper|digest"
            r"|chave|session|credential)[\w-]*)(\s*[=:]\s*)[^\s&,;]+"
        ),
        rf"\1\2{REDACTED}",
    ),
    # Hex longo (digest, pepper ou chave em hex) e tokens longos que misturam letras e dígitos.
    (re.compile(r"\b[0-9A-Fa-f]{32,}\b"), REDACTED),
    (
        re.compile(
            r"(?<![\w/+=-])(?=[\w+/=-]*\d)(?=[\w+/=-]*[A-Z])(?=[\w+/=-]*[a-z])"
            r"[\w+/=-]{32,}(?![\w/+=-])",
            re.ASCII,
        ),
        REDACTED,
    ),
    # E-mail, CNPJ e CPF (com ou sem pontuação).
    (re.compile(r"[\w.%+-]+@[\w-]+(?:\.[\w-]+)+", re.ASCII), REDACTED),
    (re.compile(r"(?<!\d)\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}(?!\d)"), REDACTED),
    (re.compile(r"(?<!\d)\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?!\d)"), REDACTED),
)

# Nome de evento: identificador em minúsculas com pontos (`tenant.created`, `http.request`).
_EVENT_NAME: Final = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*")
_MAX_EVENT_LENGTH: Final = 64

HTTP_METHODS: Final = frozenset(
    {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT"}
)

# Formato exato de cada campo de texto da allowlist. Um valor fora do formato vira `REDACTED`.
_FIELD_FORMATS: Final = {
    "timestamp": re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z"),
    "level": re.compile(r"debug|info|warning|error|critical"),
    "logger": re.compile(r"[A-Za-z_][A-Za-z0-9_.]{0,99}"),
    "service": re.compile(r"[a-z][a-z0-9-]{0,63}"),
    "version": re.compile(r"[A-Za-z0-9][A-Za-z0-9.+_-]{0,63}"),
    "environment": re.compile(r"local|test|staging|production"),
    "request_id": re.compile(r"req_[0-9a-f]{32}"),
    "trace_id": re.compile(r"[0-9a-f]{32}"),
    "tenant_id": re.compile(r"ten_[0-9a-f]{32}"),
    "key_id": re.compile(
        r"key_[0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    ),
    "route": re.compile(r"unmatched|/[A-Za-z0-9_./{}-]{0,199}"),
    "error_type": re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,99}"),
}
# `arquivo:linha:função` de um frame de traceback.
_FRAME: Final = re.compile(r"[^\s:]{1,300}:\d{1,7}:[A-Za-z_<][A-Za-z0-9_<>.]{0,99}")

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


def event_name(message: object) -> str:
    """`message` se for um nome de evento; qualquer outro texto ou objeto vira o marcador fixo."""
    if (
        isinstance(message, str)
        and len(message) <= _MAX_EVENT_LENGTH
        and _EVENT_NAME.fullmatch(message)
    ):
        return message
    return UNRECOGNIZED_EVENT


def normalize_method(method: object) -> str:
    """O método HTTP, se for um dos conhecidos; qualquer outro valor vira `OTHER`."""
    return method if isinstance(method, str) and method in HTTP_METHODS else OTHER_METHOD


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
    """`event` é um nome de evento, nunca texto livre; os `extra` viram campos; a exceção, só o
    tipo."""
    record = event.get("_record")
    if isinstance(record, logging.LogRecord):
        event["event"] = event_name(record.msg)
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
    """Mantém cada campo da allowlist só no formato esperado; o resto vira `REDACTED`."""
    for name, value in list(event.items()):
        event[name] = _checked(name, value)
    return event


def _checked(name: str, value: Any) -> Any:
    if name == "event":
        return event_name(value)
    if name == "method":
        return normalize_method(value)
    if name in _FIELD_FORMATS:
        valid = isinstance(value, str) and _FIELD_FORMATS[name].fullmatch(value)
        return value if valid else REDACTED
    if name == "status_code":
        return value if _is_int(value) and 100 <= value <= 599 else REDACTED
    if name == "pepper_version":
        return value if _is_int(value) and value >= 1 else REDACTED
    if name == "duration_ms":
        number = isinstance(value, int | float) and not isinstance(value, bool)
        return value if number and 0 <= value < float("inf") else REDACTED
    if name == "frames" and isinstance(value, list | tuple):
        return [
            scrub_text(item) if isinstance(item, str) and _FRAME.fullmatch(item) else REDACTED
            for item in value
        ]
    return REDACTED


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


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
