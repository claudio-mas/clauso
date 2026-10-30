"""Operações administrativas: criar Tenant e chaves, revogar chaves e mudar o status do Tenant.

Rodam como `cce_migrator`, nunca na API, e SEMPRE sob o contexto de um Tenant (a RLS é forçada
também para o dono, ADR-0013): quem chama abre a transação com `tenant_session` e passa a sessão.
Aqui nada confirma: o commit é do gerenciador de sessão, e por isso `provision_tenant` só devolve a
chave bruta depois que o Tenant e a chave foram confirmados juntos. Um commit que o servidor recusa
(falha confirmada, seguida de rollback) levanta e não deixa Tenant, chave nem material para exibir.
Uma exceção no commit nem sempre é isso: se a conexão cair depois de o servidor confirmar, o Tenant
e a chave existem sem que a chave tenha sido vista. Esse resultado é incerto; o runbook
`docs/runbooks/tenant-bootstrap.md` ("Se algo der errado") diz como conferir.

As mensagens dos erros deste módulo são fixas e nunca repetem valores recebidos.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cce.identity.tenants.model import Tenant
from cce.identity.tenants.schemas import TenantCreate
from cce.integrations.api_keys import service
from cce.integrations.api_keys.scopes import ALLOWED_SCOPES
from cce.integrations.api_keys.service import InvalidKeyRequest, ProvisionedKey
from cce.shared.config import AdminSettings
from cce.shared.db.session import tenant_session

INITIAL_KEY_LABEL: Final = "initial"
SETTABLE_STATUSES: Final = ("active", "suspended")
_UNIQUE_VIOLATION = "23505"


class BootstrapError(Exception):
    """Operação administrativa recusada. A mensagem é fixa e segura para exibir."""


class TenantNotFound(BootstrapError):
    def __init__(self) -> None:
        super().__init__("tenant not found")


class TenantSlugTaken(BootstrapError):
    def __init__(self) -> None:
        super().__init__("the slug is already in use")


class InvalidRequest(BootstrapError):
    """Pedido inválido; a mensagem diz a regra, nunca o valor."""


@dataclass(frozen=True, slots=True)
class ProvisionedTenant:
    """Tenant criado e confirmado, com a chave inicial (a única vez em que ela é mostrada)."""

    tenant_id: UUID
    name: str
    slug: str
    status: str
    plan: str
    default_currency: str
    timezone: str
    locale: str
    api_key: ProvisionedKey


def _sqlstate(error: DBAPIError) -> str | None:
    state = getattr(error.orig, "sqlstate", None) or getattr(error.orig, "pgcode", None)
    return str(state) if state else None


async def create_tenant(session: AsyncSession, tenant_id: UUID, command: TenantCreate) -> Tenant:
    """Grava o Tenant; a sessão precisa estar sob o contexto de `tenant_id` (senão a RLS recusa)."""
    tenant = Tenant(
        id=tenant_id,
        name=command.name,
        slug=command.slug,
        status="active",
        plan=command.plan,
        default_currency=command.default_currency,
        timezone=command.timezone,
        locale=command.locale,
    )
    session.add(tenant)
    try:
        await session.flush()
    except IntegrityError as error:
        if _sqlstate(error) == _UNIQUE_VIOLATION:
            raise TenantSlugTaken from None
        raise
    return tenant


async def _tenant_exists(session: AsyncSession, tenant_id: UUID) -> bool:
    row = (await session.execute(select(Tenant.id).where(Tenant.id == tenant_id))).first()
    return row is not None


async def issue_api_key(
    session: AsyncSession,
    tenant_id: UUID,
    label: str,
    scopes: Sequence[str],
    ttl_days: int,
    settings: AdminSettings,
) -> ProvisionedKey:
    """Emite uma chave para um Tenant que a sessão enxerga (alheio ou ausente: não existe)."""
    try:
        service.validate_key_request(label, scopes, ttl_days)
    except InvalidKeyRequest as error:
        raise InvalidRequest(str(error)) from None
    if not await _tenant_exists(session, tenant_id):
        raise TenantNotFound from None
    return await service.issue_api_key(session, tenant_id, label, scopes, ttl_days, settings)


async def revoke_api_key(session: AsyncSession, tenant_id: UUID, key_id: UUID) -> bool:
    """True se a chave é do Tenant e está revogada (mesmo já estando); False caso contrário."""
    return await service.revoke_api_key(session, tenant_id, key_id)


async def set_tenant_status(session: AsyncSession, tenant_id: UUID, status: str) -> None:
    """Suspende ou ativa o Tenant. Não apaga nada; vale na próxima autenticação."""
    if status not in SETTABLE_STATUSES:
        raise InvalidRequest("the status must be active or suspended")
    updated = (
        await session.execute(
            update(Tenant)
            .where(Tenant.id == tenant_id)
            .values(status=status, updated_at=func.now())
            .returning(Tenant.id)
            .execution_options(synchronize_session=False)
        )
    ).first()
    if updated is None:
        raise TenantNotFound from None
    await session.flush()


async def provision_tenant(
    factory: async_sessionmaker[AsyncSession],
    settings: AdminSettings,
    command: TenantCreate,
    *,
    label: str = INITIAL_KEY_LABEL,
) -> ProvisionedTenant:
    """Cria o Tenant e a chave inicial (todos os scopes) na MESMA transação.

    O UUID nasce aqui, antes do INSERT, e vira o contexto da transação. O resultado, que carrega a
    chave bruta, só é montado depois que `tenant_session` confirmou; se o commit levantar, a chave
    nunca é devolvida. Só uma falha confirmada pelo servidor, com rollback, garante que nada foi
    gravado; uma conexão perdida no commit deixa o resultado incerto (ver o runbook
    `docs/runbooks/tenant-bootstrap.md`).
    """
    tenant_id = uuid4()
    async with tenant_session(factory, tenant_id) as session:
        tenant = await create_tenant(session, tenant_id, command)
        key = await issue_api_key(
            session, tenant_id, label, ALLOWED_SCOPES, settings.api_key_ttl_days, settings
        )
    return ProvisionedTenant(
        tenant_id=tenant_id,
        name=tenant.name,
        slug=tenant.slug,
        status=tenant.status,
        plan=tenant.plan,
        default_currency=tenant.default_currency,
        timezone=tenant.timezone,
        locale=tenant.locale,
        api_key=key,
    )
