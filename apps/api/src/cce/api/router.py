"""Rotas de `/api/v1`. O Tenant de toda rota é o do `Principal`; nenhum vem do cliente."""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from cce.api.dependencies import get_principal
from cce.identity.tenants.routes import router as tenant_router
from cce.integrations.api_keys.authentication import Principal
from cce.shared.ids import encode_key_id, encode_tenant_id

API_PREFIX = "/api/v1"


class AuthContext(BaseModel):
    """Quem está autenticado: IDs públicos e scopes. Nunca digest, segredo ou pepper."""

    tenant_id: str
    key_id: str
    scopes: list[str]


auth_router = APIRouter(prefix="/auth", tags=["auth"])


@auth_router.get(
    "/context",
    response_model=AuthContext,
    description="O Tenant público, o ID da chave e os scopes dela. Basta uma chave válida.",
    openapi_extra={"x-cce-required-scopes": []},
)
async def auth_context(principal: Annotated[Principal, Depends(get_principal)]) -> AuthContext:
    return AuthContext(
        tenant_id=encode_tenant_id(principal.tenant_id),
        key_id=encode_key_id(principal.key_id),
        scopes=sorted(principal.scopes),
    )


api_router = APIRouter(prefix=API_PREFIX)
api_router.include_router(auth_router)
api_router.include_router(tenant_router)
