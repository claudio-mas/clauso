"""Casos de uso do Tenant autenticado: ler e alterar a própria empresa.

Recebem a sessão já sob o contexto do Tenant e o `Principal`; nunca um Tenant vindo do cliente.
Devolvem `TenantRead` já materializado, para que nada consulte o banco depois do fim da transação.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from cce.identity.tenants import repository
from cce.identity.tenants.schemas import TenantPatch, TenantRead
from cce.integrations.api_keys.authentication import Principal


class TenantNotFound(LookupError):
    """O Tenant do `Principal` não está visível na transação (a API responde 404)."""

    def __init__(self) -> None:
        super().__init__("tenant not found")


class TenantService:
    @staticmethod
    async def get_current(session: AsyncSession, principal: Principal) -> TenantRead:
        tenant = await repository.get_current(session, principal.tenant_id)
        if tenant is None:
            raise TenantNotFound
        return TenantRead.model_validate(tenant)

    @staticmethod
    async def patch_current(
        session: AsyncSession, principal: Principal, changes: TenantPatch
    ) -> TenantRead:
        tenant = await repository.update_current(session, principal.tenant_id, changes)
        if tenant is None:
            raise TenantNotFound
        return TenantRead.model_validate(tenant)
