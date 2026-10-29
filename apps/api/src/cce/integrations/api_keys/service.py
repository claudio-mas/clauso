"""Ciclo de vida de chaves de API: gerar o material, emitir e revogar com a sessão recebida.

`issue_key_material` é pura (sem banco). `issue_api_key` e `revoke_api_key` recebem uma sessão que
já está sob o contexto do Tenant (ADR-0013) e não confirmam nada: o commit é de quem abriu a
transação. A chave bruta só existe no `IssuedKey` devolvido; o banco guarda o digest e a versão do
pepper. A autenticação fica em `authentication.py`.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from cce.integrations.api_keys import repository
from cce.integrations.api_keys.scopes import ALLOWED_SCOPES
from cce.shared.config import AdminSettings, Environment
from cce.shared.security import digest_key, generate_key, pepper_bytes

MAX_LABEL_LENGTH = 100
MIN_TTL_DAYS = 1
MAX_TTL_DAYS = 365


class InvalidKeyRequest(ValueError):
    """Pedido de chave recusado. A mensagem é fixa e nunca repete o valor recebido."""


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class IssuedKey:
    """Chave recém-emitida: o token só existe aqui, para ser mostrado uma vez; o banco guarda o
    digest e a versão do pepper. Nem o token nem o digest aparecem em `repr`/`str`.
    """

    key_id: UUID
    raw_key: SecretStr
    secret_digest: bytes
    pepper_version: int

    def __repr__(self) -> str:
        return (
            f"IssuedKey(key_id={self.key_id!s}, pepper_version={self.pepper_version}, "
            "raw_key=<redacted>, secret_digest=<redacted>)"
        )

    __str__ = __repr__


def issue_key_material(
    environment: Environment,
    peppers: Mapping[int, SecretStr],
    pepper_version: int,
    key_id: UUID | None = None,
) -> IssuedKey:
    """Gera uma chave nova e o digest que seria guardado, com o pepper da versão informada."""
    pepper = pepper_bytes(peppers, pepper_version)
    identifier = key_id if key_id is not None else uuid4()
    raw_key = generate_key(identifier, environment)
    return IssuedKey(
        key_id=identifier,
        raw_key=SecretStr(raw_key),
        secret_digest=digest_key(raw_key, pepper),
        pepper_version=pepper_version,
    )


@dataclass(frozen=True, slots=True, repr=False)
class ProvisionedKey:
    """Chave emitida e gravada na transação corrente (ainda não confirmada)."""

    issued: IssuedKey
    tenant_id: UUID
    label: str
    scopes: tuple[str, ...]
    expires_at: datetime

    def __repr__(self) -> str:
        return (
            f"ProvisionedKey(tenant_id={self.tenant_id!s}, label={self.label!r}, "
            f"scopes={self.scopes!r}, issued={self.issued!r})"
        )

    __str__ = __repr__


def validate_key_request(
    label: str, scopes: Sequence[str], ttl_days: int
) -> tuple[str, tuple[str, ...]]:
    """Rótulo e scopes normalizados, ou `InvalidKeyRequest`. Sem eco do valor recusado."""
    clean_label = label.strip() if isinstance(label, str) else ""
    if not clean_label or len(clean_label) > MAX_LABEL_LENGTH:
        raise InvalidKeyRequest(f"the label must have 1 to {MAX_LABEL_LENGTH} characters")
    unique = tuple(dict.fromkeys(scopes))
    if not unique or any(scope not in ALLOWED_SCOPES for scope in unique):
        raise InvalidKeyRequest("at least one scope is required, and every scope must be known")
    if not isinstance(ttl_days, int) or not MIN_TTL_DAYS <= ttl_days <= MAX_TTL_DAYS:
        raise InvalidKeyRequest(f"ttl_days must be between {MIN_TTL_DAYS} and {MAX_TTL_DAYS}")
    return clean_label, unique


async def issue_api_key(
    session: AsyncSession,
    tenant_id: UUID,
    label: str,
    scopes: Sequence[str],
    ttl_days: int,
    settings: AdminSettings,
) -> ProvisionedKey:
    """Gera a chave com o pepper ativo e grava o digest dela para o Tenant da sessão."""
    clean_label, unique_scopes = validate_key_request(label, scopes, ttl_days)
    issued = issue_key_material(
        settings.env, settings.api_key_peppers, settings.api_key_active_pepper_version
    )
    expires_at = await repository.add_api_key(
        session,
        key_id=issued.key_id,
        tenant_id=tenant_id,
        label=clean_label,
        secret_digest=issued.secret_digest,
        pepper_version=issued.pepper_version,
        scopes=unique_scopes,
        ttl_days=ttl_days,
    )
    return ProvisionedKey(
        issued=issued,
        tenant_id=tenant_id,
        label=clean_label,
        scopes=unique_scopes,
        expires_at=expires_at,
    )


async def revoke_api_key(session: AsyncSession, tenant_id: UUID, key_id: UUID) -> bool:
    """Revoga a chave do Tenant. Repetir é um sucesso; chave de outro Tenant ou inexistente: False.

    A RLS já esconde as chaves de outros Tenants; a comparação com `tenant_id` fecha também o caso
    de uma sessão aberta para um Tenant e um pedido para outro.
    """
    metadata = await repository.get_key_metadata(session, key_id)
    if metadata is None or metadata.tenant_id != tenant_id:
        return False
    if metadata.revoked_at is None:
        await repository.mark_revoked(session, key_id)
    return True
