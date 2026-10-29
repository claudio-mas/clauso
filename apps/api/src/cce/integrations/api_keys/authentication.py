"""Autenticação por API key: da chave apresentada a um `Principal` imutável.

O cliente nunca informa o Tenant: ele vem da chave. Fluxo (plano do M0, seção 11.3):

1. Interpretar a chave para o ambiente deste processo. Ausente, malformada e de outro ambiente
   falham aqui, sem tocar no banco.
2. Chamar a função de lookup numa sessão curta, SEM contexto de Tenant, e fechá-la antes de
   comparar qualquer coisa. Falha do banco é `DependencyUnavailable` (503), nunca credencial
   inválida (401).
3. Calcular o HMAC com o pepper da versão guardada na chave e compará-lo em tempo constante.
   Sem candidato (ausente, expirada, revogada ou Tenant suspenso, que a função já filtra), ou sem
   o pepper da versão, a comparação roda do mesmo jeito, contra um digest sentinela, e o resultado
   é descartado.
4. Só uma comparação válida devolve o `Principal`.

Todo motivo de rejeição levanta o mesmo `AuthenticationFailed`, com a mesma mensagem e sem causa
encadeada. Nenhuma chave, segredo, digest ou pepper entra em mensagem, `repr` ou log. Nada é
guardado entre autenticações: revogar ou suspender vale na seguinte. Este módulo não importa
FastAPI; o transporte (cabeçalho `Authorization`, status HTTP) é do ticket 07.
"""

import hashlib
import logging
from dataclasses import dataclass
from uuid import UUID

import asyncpg
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cce.integrations.api_keys.repository import KeyCandidate, lookup_candidate
from cce.shared.config import Settings
from cce.shared.security import (
    InvalidKeyError,
    UnknownPepperVersion,
    digest_key,
    parse_key,
    pepper_bytes,
    verify_digest,
)

LOGGER_NAME = "cce.api_keys.authentication"
_logger = logging.getLogger(LOGGER_NAME)

# Comparado no lugar do digest guardado quando não há candidato utilizável. Nenhuma chave o iguala.
SENTINEL_DIGEST = hashlib.sha256(b"cce-api-key:sentinel:v1").digest()

_FAILED = "authentication failed"
_UNAVAILABLE = "authentication dependency unavailable"
_DATABASE_ERRORS = (SQLAlchemyError, OSError, asyncpg.PostgresError, asyncpg.InterfaceError)


class AuthenticationFailed(Exception):
    """A chave não autentica. Um único tipo e uma única mensagem para qualquer motivo."""

    def __init__(self) -> None:
        super().__init__()

    def __str__(self) -> str:
        return _FAILED


class DependencyUnavailable(Exception):
    """O banco não respondeu à autenticação. Não diz nada sobre a chave (503, nunca 401)."""

    def __init__(self) -> None:
        super().__init__()

    def __str__(self) -> str:
        return _UNAVAILABLE


@dataclass(frozen=True, slots=True)
class Principal:
    """Quem fez a requisição: o Tenant e a chave verificados, com os scopes dela."""

    tenant_id: UUID
    key_id: UUID
    scopes: frozenset[str]


async def authenticate_api_key(
    raw_key: str | None,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
) -> Principal:
    """Autentica a chave apresentada ou levanta `AuthenticationFailed`/`DependencyUnavailable`."""
    key_id = _key_id(raw_key, settings)
    if key_id is None or raw_key is None:
        raise AuthenticationFailed from None

    candidate = await _lookup(key_id, session_factory)

    active = pepper_bytes(settings.api_key_peppers, settings.api_key_active_pepper_version)
    if candidate is None:
        _compare_with_sentinel(raw_key, active)
        raise AuthenticationFailed from None
    try:
        pepper = pepper_bytes(settings.api_key_peppers, candidate.pepper_version)
    except UnknownPepperVersion:
        _logger.error(
            "api key pepper version is not configured",
            extra={"key_id": str(candidate.key_id), "pepper_version": candidate.pepper_version},
        )
        _compare_with_sentinel(raw_key, active)
        raise AuthenticationFailed from None

    if not verify_digest(digest_key(raw_key, pepper), candidate.secret_digest):
        raise AuthenticationFailed from None
    return Principal(
        tenant_id=candidate.tenant_id,
        key_id=candidate.key_id,
        scopes=frozenset(candidate.scopes),
    )


def _key_id(raw_key: str | None, settings: Settings) -> UUID | None:
    if not isinstance(raw_key, str):
        return None
    try:
        return parse_key(raw_key, settings.env).key_id
    except InvalidKeyError:
        return None


async def _lookup(
    key_id: UUID, session_factory: async_sessionmaker[AsyncSession]
) -> KeyCandidate | None:
    """Sessão e transação curtas, sem contexto de Tenant, fechadas antes de qualquer comparação."""
    try:
        async with session_factory() as session, session.begin():
            return await lookup_candidate(session, key_id)
    except _DATABASE_ERRORS:
        raise DependencyUnavailable from None


def _compare_with_sentinel(raw_key: str, pepper: bytes) -> None:
    """Gasta o mesmo trabalho de uma chave real; o resultado nunca decide nada."""
    verify_digest(digest_key(raw_key, pepper), SENTINEL_DIGEST)
