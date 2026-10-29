"""Contexto de Tenant aplicado à transação (ADR-0013).

A RLS compara cada linha ao GUC `app.tenant_id`. Ele é definido com `set_config(..., true)`, que
vale só até o fim da transação corrente: depois do COMMIT ou do ROLLBACK a conexão volta ao pool
sem contexto, e uma transação sem contexto enxerga zero linhas. Nunca `SET` de sessão, nunca SQL
concatenado e nunca configuração no checkout do pool.

O GUC não é prova de identidade: quem executa SQL arbitrário como runtime pode trocá-lo (plano do
M0, seção 10.3). A fronteira de confiança continua sendo o backend.
"""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_SET_CONTEXT = text("SELECT set_config('app.tenant_id', :tenant_id, true)")


class TenantContextError(RuntimeError):
    """O contexto de Tenant foi pedido fora de uma transação."""


async def set_tenant_context(session: AsyncSession, tenant_id: UUID) -> None:
    """Aplica `tenant_id` como contexto LOCAL da transação aberta de `session`."""
    if not isinstance(tenant_id, UUID):
        raise TypeError("the tenant context must be a UUID")
    if not session.in_transaction():
        raise TenantContextError("the tenant context requires an open transaction")
    await session.execute(_SET_CONTEXT, {"tenant_id": str(tenant_id)})
