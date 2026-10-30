"""Health: `/health/live` diz que o processo responde; `/health/ready`, que as dependências também.

A liveness não toca em nada externo. A readiness verifica, com limite de 750 ms cada, o PostgreSQL
(`SELECT 1` como runtime), o Redis (`PING`) e o schema: o conjunto de revisions em
`cce.alembic_version` precisa ser igual aos heads dos scripts de migration, lidos no startup. Sem
heads conhecidos o schema nunca está pronto. Qualquer falha dá 503 com o check `unavailable`, sem
URL, host, porta nem mensagem de exceção. As rotas não exigem autenticação: o acesso a elas é
restrito na rede de operação.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

LOGGER_NAME = "cce.api.health"
_logger = logging.getLogger(LOGGER_NAME)

CHECK_TIMEOUT_SECONDS = 0.75
DEFAULT_ALEMBIC_CONFIG = Path("alembic.ini")
_SELECT_ONE = text("SELECT 1")
_REVISIONS = text("SELECT version_num FROM cce.alembic_version")
OK = "ok"
UNAVAILABLE = "unavailable"

router = APIRouter(prefix="/health", tags=["health"])


def load_expected_heads(config_path: Path = DEFAULT_ALEMBIC_CONFIG) -> frozenset[str]:
    """Heads dos scripts de migration. Vazio (schema nunca pronto) se não puderem ser lidos."""
    try:
        config = Config(str(config_path))
        return frozenset(ScriptDirectory.from_config(config).get_heads())
    except Exception as error:
        _logger.warning(
            "migration heads could not be read", extra={"error_type": type(error).__name__}
        )
        return frozenset()


async def check_within(check: Callable[[], Awaitable[Any]], limit: float) -> bool:
    """True se `check` terminar sem erro dentro de `limit` segundos; o erro em si é descartado."""
    try:
        async with asyncio.timeout(limit):
            await check()
    except Exception:
        return False
    return True


@router.get("/live", description="200 enquanto o processo responde. Não checa dependências.")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get(
    "/ready",
    description="200 se PostgreSQL, Redis e schema estão prontos; 503 caso contrário.",
    responses={503: {"description": "Alguma dependência não está pronta."}},
)
async def ready(request: Request) -> JSONResponse:
    state = request.app.state
    revisions: set[str] = set()

    async def postgres() -> None:
        async with state.session_factory() as session, session.begin():
            await session.execute(_SELECT_ONE)

    async def schema() -> None:
        async with state.session_factory() as session, session.begin():
            revisions.update((await session.execute(_REVISIONS)).scalars())
        if not state.expected_heads or revisions != state.expected_heads:
            raise LookupError("the schema is not at the expected heads")

    async def redis() -> None:
        if await state.redis.ping() is not True:
            raise ConnectionError("redis did not answer the ping")

    postgres_ok, redis_ok = await asyncio.gather(
        check_within(postgres, CHECK_TIMEOUT_SECONDS), check_within(redis, CHECK_TIMEOUT_SECONDS)
    )
    schema_ok = postgres_ok and await check_within(schema, CHECK_TIMEOUT_SECONDS)
    checks = {
        "postgres": OK if postgres_ok else UNAVAILABLE,
        "redis": OK if redis_ok else UNAVAILABLE,
        "schema": OK if schema_ok else UNAVAILABLE,
    }
    all_ok = postgres_ok and redis_ok and schema_ok
    return JSONResponse(
        {"status": "ready" if all_ok else "not_ready", "checks": checks},
        status_code=200 if all_ok else 503,
    )
