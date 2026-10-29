"""Sessão transacional por operação, sempre sob o contexto de um Tenant (ADR-0013).

`tenant_session` é o único lugar que abre, confirma e desfaz transações de negócio. Repositories
recebem a sessão pronta e podem dar `flush`, nunca `commit` nem `rollback`; casos de uso não abrem
transações aninhadas para trocar de Tenant. Trabalho em segundo plano abre a própria sessão.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cce.shared.db.rls import set_tenant_context


@asynccontextmanager
async def tenant_session(
    factory: async_sessionmaker[AsyncSession], tenant_id: UUID
) -> AsyncIterator[AsyncSession]:
    """Abre uma transação, aplica o contexto antes de qualquer acesso e a confirma na saída.

    Na saída normal faz `flush` e `commit`, nessa ordem, antes de devolver o controle; quem
    responde ao cliente só o faz depois disso. Em exceção ou cancelamento desfaz a transação. Em
    todos os casos fecha a sessão e devolve a conexão ao pool.
    """
    async with factory() as session, session.begin():
        await set_tenant_context(session, tenant_id)
        yield session
        await session.flush()
