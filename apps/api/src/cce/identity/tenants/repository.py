"""Acesso ao Tenant corrente, sempre por uma sessão recebida (nunca abre nem confirma transação).

Filtra pelo ID do Tenant E depende da RLS: defesa em profundidade. Sem o contexto do mesmo Tenant
a linha não aparece, e com outro ID o filtro não encontra nada. Só `flush`; o commit é do
gerenciador de sessão (ADR-0013).
"""

from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from cce.identity.tenants.model import Tenant
from cce.identity.tenants.schemas import TenantPatch


async def get_current(session: AsyncSession, tenant_id: UUID) -> Tenant | None:
    result = await session.execute(select(Tenant).where(Tenant.id == tenant_id))
    return result.scalar_one_or_none()


async def update_current(
    session: AsyncSession, tenant_id: UUID, changes: TenantPatch
) -> Tenant | None:
    """Aplica só os campos enviados, mais `updated_at`, e devolve a linha já atualizada."""
    values = changes.model_dump(exclude_unset=True)
    statement = (
        update(Tenant)
        .where(Tenant.id == tenant_id)
        .values(**values, updated_at=func.now())
        .returning(Tenant)
        .execution_options(synchronize_session=False, populate_existing=True)
    )
    tenant = (await session.execute(statement)).scalar_one_or_none()
    await session.flush()
    return tenant
