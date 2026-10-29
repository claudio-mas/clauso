"""`tenant_session` e o pool de uma conexão: o contexto de um Tenant nunca sobra para o seguinte.

O engine vem da fábrica de produção com `pool_size=1` e `max_overflow=0`, então todas as sessões
dividem a MESMA conexão física (o teste confere pelo `pg_backend_pid`). Se algum caminho vazasse o
contexto, o Tenant seguinte (ou a sessão sem contexto) enxergaria as linhas do anterior.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

import pytest
from db_support import OpenSession, OpenTenantSession
from seeding import SeededTenant, apply_context
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from cce.identity.tenants.model import Tenant
from cce.shared.db.session import tenant_session

pytestmark = pytest.mark.integration


class Snapshot:
    """O que uma sessão enxerga: os Tenants visíveis, o contexto residual e a conexão física."""

    def __init__(self, tenants: list[UUID], context: str | None, backend: int) -> None:
        self.tenants = tenants
        self.context = context
        self.backend = backend


async def snapshot(session: AsyncSession) -> Snapshot:
    tenants = list((await session.execute(text("SELECT id FROM cce.tenants"))).scalars())
    context = (
        await session.execute(text("SELECT current_setting('app.tenant_id', true)"))
    ).scalar()
    backend = (await session.execute(text("SELECT pg_backend_pid()"))).scalar_one()
    return Snapshot(tenants, context, backend)


async def owner_name(migrator_engine: AsyncEngine, tenant: SeededTenant) -> str:
    async with migrator_engine.connect() as connection:
        await apply_context(connection, tenant.id)
        name = (await connection.execute(text("SELECT name FROM cce.tenants"))).scalar_one()
        await connection.rollback()
    return str(name)


# --- pool de uma conexão: A, depois nada, depois B -------------------------------------------


async def test_a_then_no_context_then_b_on_the_same_connection(
    tenant_a: SeededTenant,
    tenant_b: SeededTenant,
    runtime_engine: AsyncEngine,
    runtime_session: OpenTenantSession,
    runtime_no_context: OpenSession,
) -> None:
    async with runtime_session(tenant_a.id) as session:
        first = await snapshot(session)
    async with runtime_no_context() as session:
        second = await snapshot(session)
    async with runtime_session(tenant_b.id) as session:
        third = await snapshot(session)

    assert first.tenants == [tenant_a.id]
    assert second.tenants == []
    assert third.tenants == [tenant_b.id]
    assert second.context in (None, "")  # o contexto local acabou com a transação de A
    assert {first.backend, second.backend, third.backend} == {first.backend}
    assert runtime_engine.pool.checkedout() == 0  # type: ignore[attr-defined]


async def test_the_next_session_is_clean_after_an_error(
    tenant_a: SeededTenant,
    tenant_b: SeededTenant,
    runtime_session: OpenTenantSession,
    runtime_no_context: OpenSession,
) -> None:
    with pytest.raises(DBAPIError):
        async with runtime_session(tenant_a.id) as session:
            await session.execute(text("SELECT 1 / 0"))
    async with runtime_no_context() as session:
        after_error = await snapshot(session)
    async with runtime_session(tenant_b.id) as session:
        for_b = await snapshot(session)

    assert after_error.tenants == []
    assert after_error.context in (None, "")
    assert for_b.tenants == [tenant_b.id]
    assert for_b.backend == after_error.backend


async def test_the_next_session_is_clean_after_an_exception_in_the_caller(
    tenant_a: SeededTenant,
    tenant_b: SeededTenant,
    runtime_session: OpenTenantSession,
    runtime_no_context: OpenSession,
) -> None:
    with pytest.raises(RuntimeError, match="falha do chamador"):
        async with runtime_session(tenant_a.id) as session:
            await session.execute(text("SELECT id FROM cce.tenants"))
            raise RuntimeError("falha do chamador")
    async with runtime_no_context() as session:
        after_error = await snapshot(session)
    async with runtime_session(tenant_b.id) as session:
        for_b = await snapshot(session)

    assert after_error.tenants == []
    assert for_b.tenants == [tenant_b.id]


async def test_the_next_session_is_clean_after_a_cancellation(
    tenant_a: SeededTenant,
    tenant_b: SeededTenant,
    runtime_engine: AsyncEngine,
    runtime_session: OpenTenantSession,
    runtime_no_context: OpenSession,
) -> None:
    started = asyncio.Event()

    async def hold_a_transaction_open() -> None:
        async with runtime_session(tenant_a.id) as session:
            await session.execute(text("SELECT id FROM cce.tenants"))
            started.set()
            await session.execute(text("SELECT pg_sleep(4)"))

    task = asyncio.create_task(hold_a_transaction_open())
    await asyncio.wait_for(started.wait(), timeout=5)
    await asyncio.sleep(0.2)  # a consulta lenta já está no servidor
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    async with runtime_no_context() as session:
        after_cancel = await snapshot(session)
    async with runtime_session(tenant_b.id) as session:
        for_b = await snapshot(session)

    assert after_cancel.tenants == []
    assert after_cancel.context in (None, "")
    assert for_b.tenants == [tenant_b.id]
    assert runtime_engine.pool.checkedout() == 0  # type: ignore[attr-defined]


async def test_concurrent_sessions_of_a_and_b_share_the_single_connection_without_mixing(
    tenant_a: SeededTenant, tenant_b: SeededTenant, runtime_session: OpenTenantSession
) -> None:
    async def read_as(tenant: SeededTenant) -> list[UUID]:
        async with runtime_session(tenant.id) as session:
            await session.execute(text("SELECT pg_sleep(0.05)"))
            return list((await session.execute(text("SELECT id FROM cce.tenants"))).scalars())

    results = await asyncio.gather(
        *(read_as(tenant_a if index % 2 == 0 else tenant_b) for index in range(8))
    )

    assert results == [[tenant_a.id] if index % 2 == 0 else [tenant_b.id] for index in range(8)]


# --- ordem das instruções: o contexto vem antes de qualquer acesso ---------------------------


@contextmanager
def recorded_statements(engine: AsyncEngine) -> Iterator[list[str]]:
    statements: list[str] = []

    def record(connection: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)


async def test_the_context_is_applied_before_any_access_and_the_transaction_is_local(
    tenant_a: SeededTenant,
    runtime_engine: AsyncEngine,
    runtime_session: OpenTenantSession,
) -> None:
    async with runtime_engine.connect() as connection:  # primeira conexão: inicialização do driver
        await connection.execute(text("SELECT 1"))
        await connection.rollback()

    with recorded_statements(runtime_engine) as statements:
        async with runtime_session(tenant_a.id) as session:
            await session.execute(select(Tenant.id))

    assert "set_config('app.tenant_id'" in statements[0]
    assert statements[0].rstrip().endswith("true)")  # terceiro argumento: vale só na transação
    assert "cce.tenants" in statements[1]


# --- begin, flush, commit e rollback -----------------------------------------------------------


async def test_changes_are_committed_before_the_context_manager_returns(
    tenant_a: SeededTenant, migrator_engine: AsyncEngine, runtime_session: OpenTenantSession
) -> None:
    async with runtime_session(tenant_a.id) as session:
        tenant = await session.get(Tenant, tenant_a.id)
        assert tenant is not None
        tenant.name = "Renomeada"  # autoflush desligado: quem faz o flush é a tenant_session

    assert await owner_name(migrator_engine, tenant_a) == "Renomeada"


async def test_a_failing_flush_surfaces_and_nothing_is_committed(
    tenant_a: SeededTenant, migrator_engine: AsyncEngine, runtime_session: OpenTenantSession
) -> None:
    with pytest.raises(IntegrityError):
        async with runtime_session(tenant_a.id) as session:
            tenant = await session.get(Tenant, tenant_a.id)
            assert tenant is not None
            tenant.name = "   "  # viola ck_tenants_name_not_blank no flush final

    assert await owner_name(migrator_engine, tenant_a) == tenant_a.name


async def test_an_exception_rolls_back_the_changes(
    tenant_a: SeededTenant, migrator_engine: AsyncEngine, runtime_session: OpenTenantSession
) -> None:
    with pytest.raises(RuntimeError, match="abortar"):
        async with runtime_session(tenant_a.id) as session:
            await session.execute(text("UPDATE cce.tenants SET name = 'meio'"))
            await session.flush()
            raise RuntimeError("abortar")

    assert await owner_name(migrator_engine, tenant_a) == tenant_a.name


async def test_a_cancellation_rolls_back_the_changes(
    tenant_a: SeededTenant, migrator_engine: AsyncEngine, runtime_session: OpenTenantSession
) -> None:
    updated = asyncio.Event()

    async def update_then_wait() -> None:
        async with runtime_session(tenant_a.id) as session:
            await session.execute(text("UPDATE cce.tenants SET name = 'meio'"))
            updated.set()
            await session.execute(text("SELECT pg_sleep(4)"))

    task = asyncio.create_task(update_then_wait())
    await asyncio.wait_for(updated.wait(), timeout=5)
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert await owner_name(migrator_engine, tenant_a) == tenant_a.name


async def test_the_session_is_closed_and_the_connection_returned_on_exit(
    tenant_a: SeededTenant,
    runtime_engine: AsyncEngine,
    runtime_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with tenant_session(runtime_factory, tenant_a.id) as session:
        assert session.in_transaction()
        assert runtime_engine.pool.checkedout() == 1  # type: ignore[attr-defined]

    assert not session.in_transaction()
    assert runtime_engine.pool.checkedout() == 0  # type: ignore[attr-defined]
