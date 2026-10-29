"""Acesso a dados de API keys, sempre por uma sessão recebida (nunca abre nem confirma transação).

O runtime não lê `secret_digest` nem `pepper_version` da tabela: não tem grant para as colunas. O
digest de um candidato só chega pela função `cce_private.lookup_api_key` (migration 0003), e todo
acesso a metadados lista as colunas não secretas explicitamente, em vez de carregar o modelo `APIKey`
inteiro, que inclui o digest. Estas funções só dão `flush`; quem termina a transação é o
gerenciador de sessão (ADR-0013).
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import bindparam, func, insert, select, text, update
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession

from cce.integrations.api_keys.model import APIKey

# O único acesso do runtime ao digest. Recebe só o id da chave; não há tenant, padrão nem página.
LOOKUP_SQL = (
    "SELECT key_id, tenant_id, secret_digest, pepper_version, scopes "
    "FROM cce_private.lookup_api_key(:key_id)"
)

# Colunas que o runtime pode ler de `api_keys` (as mesmas do GRANT da migration 0002).
METADATA_COLUMNS = (
    APIKey.id,
    APIKey.tenant_id,
    APIKey.label,
    APIKey.scopes,
    APIKey.created_at,
    APIKey.expires_at,
    APIKey.revoked_at,
)


@dataclass(frozen=True, slots=True, repr=False)
class KeyCandidate:
    """Candidato à autenticação: o que a função de lookup devolve. O digest nunca aparece em
    `repr`/`str`.
    """

    key_id: UUID
    tenant_id: UUID
    secret_digest: bytes
    pepper_version: int
    scopes: tuple[str, ...]

    def __repr__(self) -> str:
        return (
            f"KeyCandidate(key_id={self.key_id!s}, tenant_id={self.tenant_id!s}, "
            f"pepper_version={self.pepper_version}, secret_digest=<redacted>)"
        )

    __str__ = __repr__


@dataclass(frozen=True, slots=True)
class KeyMetadata:
    """Uma chave sem nada secreto."""

    id: UUID
    tenant_id: UUID
    label: str
    scopes: tuple[str, ...]
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None


async def lookup_candidate(session: AsyncSession, key_id: UUID) -> KeyCandidate | None:
    """O candidato ativo da chave, ou None. Sem contexto de Tenant: a função cuida do acesso."""
    statement = text(LOOKUP_SQL).bindparams(bindparam("key_id", type_=PG_UUID(as_uuid=True)))
    row = (await session.execute(statement, {"key_id": key_id})).one_or_none()
    if row is None:
        return None
    return KeyCandidate(
        key_id=row.key_id,
        tenant_id=row.tenant_id,
        secret_digest=bytes(row.secret_digest),
        pepper_version=int(row.pepper_version),
        scopes=tuple(row.scopes),
    )


async def get_key_metadata(session: AsyncSession, key_id: UUID) -> KeyMetadata | None:
    """Metadados de uma chave visível sob o contexto da sessão (a RLS esconde as de outros)."""
    row = (await session.execute(select(*METADATA_COLUMNS).where(APIKey.id == key_id))).one_or_none()
    if row is None:
        return None
    return KeyMetadata(
        id=row.id,
        tenant_id=row.tenant_id,
        label=row.label,
        scopes=tuple(row.scopes),
        created_at=row.created_at,
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
    )


async def add_api_key(
    session: AsyncSession,
    *,
    key_id: UUID,
    tenant_id: UUID,
    label: str,
    secret_digest: bytes,
    pepper_version: int,
    scopes: tuple[str, ...],
    ttl_days: int,
) -> datetime:
    """Grava a chave e devolve quando expira, pelo relógio do banco (o mesmo do lookup)."""
    statement = (
        insert(APIKey)
        .values(
            id=key_id,
            tenant_id=tenant_id,
            label=label,
            secret_digest=secret_digest,
            pepper_version=pepper_version,
            scopes=list(scopes),
            expires_at=func.now() + func.make_interval(0, 0, 0, ttl_days),
        )
        .returning(APIKey.expires_at)
    )
    expires_at = (await session.execute(statement)).scalar_one()
    await session.flush()
    return expires_at


async def mark_revoked(session: AsyncSession, key_id: UUID) -> None:
    """Revoga a chave visível agora; uma chave já revogada mantém o primeiro instante."""
    await session.execute(
        update(APIKey)
        .where(APIKey.id == key_id, APIKey.revoked_at.is_(None))
        .values(revoked_at=func.now())
    )
    await session.flush()
