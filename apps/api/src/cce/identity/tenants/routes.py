"""Rotas do Tenant autenticado. Não há listagem de Tenants nem rota por ID: só a própria empresa."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from cce.api.dependencies import get_tenant_session, require_scope
from cce.identity.tenants.schemas import TenantPatch, TenantRead
from cce.identity.tenants.service import TenantService
from cce.integrations.api_keys.authentication import Principal
from cce.integrations.api_keys.scopes import TENANT_READ, TENANT_WRITE

router = APIRouter(prefix="/tenant", tags=["tenant"])

# Parâmetros nesta ordem: o scope é verificado antes de a transação abrir.
Session = Annotated[AsyncSession, Depends(get_tenant_session, scope="function")]


@router.get(
    "",
    response_model=TenantRead,
    description=f"Dados da empresa autenticada. Scope exigido: `{TENANT_READ}`.",
    openapi_extra={"x-cce-required-scopes": [TENANT_READ]},
)
async def read_tenant(
    principal: Annotated[Principal, Depends(require_scope(TENANT_READ))], session: Session
) -> TenantRead:
    return await TenantService.get_current(session, principal)


@router.patch(
    "",
    response_model=TenantRead,
    description=(
        "Altera nome, moeda, fuso e locale da empresa autenticada. "
        f"Scope exigido: `{TENANT_WRITE}`."
    ),
    openapi_extra={"x-cce-required-scopes": [TENANT_WRITE]},
)
async def patch_tenant(
    principal: Annotated[Principal, Depends(require_scope(TENANT_WRITE))],
    session: Session,
    changes: TenantPatch,
) -> TenantRead:
    return await TenantService.patch_current(session, principal, changes)
