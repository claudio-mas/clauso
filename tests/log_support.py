"""Captura dos logs JSON da aplicação nos testes.

`configure_logging` grava no stdout do processo; aqui ele recebe um `StringIO`, e cada linha é lida
como JSON. A configuração global de logging (handlers e nível do root e dos loggers do Uvicorn) é
salva antes e restaurada depois de cada teste que pede a fixture `log_output`.

Mensagens de falha citam só o que faltou, nunca a linha de log: ela pode carregar o que o teste
procura justamente por estar vazando.
"""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Iterable, Iterator
from typing import Any

import pytest
from integration.support import require
from pydantic import SecretStr

from cce.shared.config import Settings
from cce.shared.logging import configure_logging

PASSWORD = "app-password-LOGSENTINEL"
PEPPER = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="  # 32 bytes, só para validar o formato
# Nomes de evento que só os testes emitem, acrescentados ao catálogo fechado de produção.
TEST_EVENTS = frozenset(
    {
        "thing.happened",
        "too.quiet",
        "loud.enough",
        "with.extras",
        "careless.log",
        "operation.failed",
        "inside.request",
        "after.requests",
        "once",
        "console.line",
        "fields.checked",
        "fields.kept",
        "fields.uuid",
    }
)
_LOGGERS = ("", "uvicorn", "uvicorn.error", "uvicorn.access", "cce")


def synthetic_settings(**overrides: Any) -> Settings:
    """Settings de `local` com DSN e pepper sintéticos; nada conecta."""
    values: dict[str, Any] = {
        "env": "local",
        "database_url": SecretStr(f"postgresql+asyncpg://cce_app:{PASSWORD}@db.invalid:5432/cce"),
        "redis_url": SecretStr("redis://redis.invalid:6379/0"),
        "api_key_peppers": {1: SecretStr(PEPPER)},
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


class LogOutput:
    """As linhas JSON gravadas pela configuração de logging da aplicação."""

    def __init__(self) -> None:
        self.stream = io.StringIO()

    def install(self, settings: Settings, events: Iterable[str] = ()) -> None:
        """Instala o pipeline com o catálogo de produção mais os eventos dos testes."""
        configure_logging(settings, stream=self.stream, events={*TEST_EVENTS, *events})

    @property
    def text(self) -> str:
        return self.stream.getvalue()

    def lines(self) -> list[dict[str, Any]]:
        parsed: list[dict[str, Any]] = []
        for number, line in enumerate(self.text.splitlines(), start=1):
            try:
                value = json.loads(line)
            except ValueError:
                value = None
            require(isinstance(value, dict), f"log line {number} is not a JSON object")
            parsed.append(value)
        return parsed

    def events(self, event: str) -> list[dict[str, Any]]:
        return [line for line in self.lines() if line.get("event") == event]

    def requests(self) -> list[dict[str, Any]]:
        return self.events("http.request")


@pytest.fixture
def log_output() -> Iterator[LogOutput]:
    saved = []
    for name in _LOGGERS:
        logger = logging.getLogger(name)
        saved.append(
            (logger, list(logger.handlers), logger.level, logger.propagate, logger.disabled)
        )
    try:
        yield LogOutput()
    finally:
        for logger, handlers, level, propagate, disabled in saved:
            logger.handlers[:] = handlers
            logger.setLevel(level)
            logger.propagate = propagate
            logger.disabled = disabled
