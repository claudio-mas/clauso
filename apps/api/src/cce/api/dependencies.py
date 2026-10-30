"""Dependências HTTP: quem chama (`Principal`), o scope exigido e a sessão transacional da rota.

`Authorization: Bearer <chave>` é a única forma de autenticar: exatamente um cabeçalho, no formato
exato; query string, cookie e qualquer cabeçalho de Tenant são ignorados. O Tenant vem da chave.
`HTTPBearer(auto_error=False)` só documenta o esquema no OpenAPI: a decisão é de `bearer_token`, e
toda recusa é o mesmo `AuthenticationFailed` (401), nunca uma mensagem diferente por motivo.

As rotas pedem `Depends(get_tenant_session, scope="function")`: a transação abre depois da
autenticação e do scope, e termina (flush e commit, ou rollback) assim que a função da rota
retorna, ANTES de a resposta ser enviada. Uma falha no commit sai como erro HTTP, nunca como 200.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from typing import Annotated

from fastapi import Depends, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from cce.api.errors import forbidden
from cce.integrations.api_keys.authentication import (
    AuthenticationFailed,
    Principal,
    authenticate_api_key,
)
from cce.shared.db.session import tenant_session

_bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="ApiKey",
    description="API key do CCE em `Authorization: Bearer cce_<ambiente>_<id>_<segredo>`.",
)


def bearer_token(values: Sequence[str]) -> str | None:
    """O token de um único cabeçalho `Bearer <token>`, ou None para qualquer outra forma."""
    if len(values) != 1:
        return None
    scheme, separator, token = values[0].partition(" ")
    if not separator or scheme.lower() != "bearer" or not token:
        return None
    if any(character.isspace() for character in token):
        return None
    return token


async def get_principal(
    request: Request,
    _: Annotated[HTTPAuthorizationCredentials | None, Security(_bearer_scheme)],
) -> Principal:
    """Autentica a chave do cabeçalho. Cacheada dentro da mesma requisição (padrão do FastAPI)."""
    token = bearer_token(request.headers.getlist("authorization"))
    if token is None:
        raise AuthenticationFailed
    state = request.app.state
    return await authenticate_api_key(token, state.settings, state.session_factory)


def require_scope(scope: str) -> Callable[..., Awaitable[Principal]]:
    """Dependência que exige `scope` na chave autenticada (403 sem ele)."""

    async def principal_with_scope(
        principal: Annotated[Principal, Depends(get_principal)],
    ) -> Principal:
        if scope not in principal.scopes:
            raise forbidden([scope])
        return principal

    return principal_with_scope


async def get_tenant_session(
    request: Request, principal: Annotated[Principal, Depends(get_principal)]
) -> AsyncIterator[AsyncSession]:
    """A transação da operação, sob o Tenant do `Principal` (ADR-0013). Use com scope="function"."""
    async with tenant_session(request.app.state.session_factory, principal.tenant_id) as session:
        yield session
