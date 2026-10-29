"""Isolamento por RLS, provado no banco como `cce_app`, sem HTTP e sem bypass (ADR-003).

Como ler: `tenant_a` e `tenant_b` são gravados pelo dono do schema sob o contexto de cada um; o
runtime lê e escreve pela sessão transacional de produção (`runtime_session`) ou por uma transação
sem contexto (`runtime_no_context`). A prova de DML usa `cce.rls_probe`, criada só no banco de
teste, porque o runtime não tem INSERT/DELETE nas tabelas reais; uma escrita recusada por falta de
GRANT não prova a policy. Os grants das tabelas reais são provados à parte.

Higiene de falhas: os testes que gravam chaves (`sensitive`) usam `require` com texto fixo e nunca
leem nem comparam `secret_digest`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast
from uuid import UUID

import pytest
from db_support import OpenSession, OpenTenantSession
from seeding import SeededTenant, apply_context, seed_probe_row
from sqlalchemy import CursorResult, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from integration.support import require, sqlstate

pytestmark = pytest.mark.integration

INSUFFICIENT_PRIVILEGE = "42501"
INVALID_TEXT_REPRESENTATION = "22P02"
KEY_COLUMNS = "id, tenant_id, label, scopes, created_at, expires_at, revoked_at"


async def values_of(session: AsyncSession, statement: str, **parameters: Any) -> list[Any]:
    return list((await session.execute(text(statement), parameters)).scalars())


async def outcome(open_session: OpenSession, statement: str, **parameters: Any) -> str:
    """SQLSTATE do erro, ou 'ok' (e a transação é confirmada)."""
    try:
        async with open_session() as session:
            await session.execute(text(statement), parameters)
    except DBAPIError as error:
        return sqlstate(error) or "unknown"
    return "ok"


async def affected(open_session: OpenSession, statement: str, **parameters: Any) -> int:
    async with open_session() as session:
        result = cast(CursorResult[Any], await session.execute(text(statement), parameters))
        return int(result.rowcount)


def as_tenant(open_session: OpenTenantSession, tenant: SeededTenant) -> OpenSession:
    return lambda: open_session(tenant.id)


async def owner_rows(
    migrator_engine: AsyncEngine, tenant_id: UUID, statement: str, **parameters: Any
) -> list[Any]:
    """Lê como dono do schema, sob o contexto do Tenant (a RLS vale também para o dono)."""
    async with migrator_engine.connect() as connection:
        await apply_context(connection, tenant_id)
        rows = list((await connection.execute(text(statement), parameters)).scalars())
        await connection.rollback()
    return rows


# --- sem contexto: falha fechado ------------------------------------------------------------


async def test_runtime_without_context_sees_no_tenants(
    tenant_a: SeededTenant, tenant_b: SeededTenant, runtime_no_context: OpenSession
) -> None:
    async with runtime_no_context() as session:
        assert await values_of(session, "SELECT id FROM cce.tenants") == []


@pytest.mark.sensitive
async def test_runtime_without_context_sees_no_api_keys(
    keyed_a: SeededTenant, keyed_b: SeededTenant, runtime_no_context: OpenSession
) -> None:
    async with runtime_no_context() as session:
        rows = await values_of(session, "SELECT id FROM cce.api_keys")
    require(rows == [], "the runtime without context saw api_keys rows")


async def test_runtime_with_an_empty_context_sees_nothing(
    tenant_a: SeededTenant, runtime_no_context: OpenSession
) -> None:
    async with runtime_no_context() as session:
        await session.execute(text("SELECT set_config('app.tenant_id', '', true)"))
        assert await values_of(session, "SELECT id FROM cce.tenants") == []


async def test_a_malformed_context_fails_instead_of_falling_back_to_every_row(
    tenant_a: SeededTenant, runtime_no_context: OpenSession
) -> None:
    async def read_with(context: str) -> str:
        try:
            async with runtime_no_context() as session:
                await session.execute(
                    text("SELECT set_config('app.tenant_id', :context, true)"),
                    {"context": context},
                )
                await session.execute(text("SELECT id FROM cce.tenants"))
        except DBAPIError as error:
            return sqlstate(error) or "unknown"
        return "ok"

    assert await read_with("not-a-uuid") == INVALID_TEXT_REPRESENTATION
    assert await read_with("*") == INVALID_TEXT_REPRESENTATION


async def test_runtime_without_context_cannot_write_the_probe(
    rls_probe: None,
    migrator_engine: AsyncEngine,
    tenant_a: SeededTenant,
    runtime_no_context: OpenSession,
) -> None:
    await seed_probe_row(migrator_engine, tenant_a.id, "a")

    insert = await outcome(
        runtime_no_context,
        "INSERT INTO cce.rls_probe (tenant_id, value) VALUES (:tenant_id, 'x')",
        tenant_id=tenant_a.id,
    )
    updated = await affected(runtime_no_context, "UPDATE cce.rls_probe SET value = 'x'")
    deleted = await affected(runtime_no_context, "DELETE FROM cce.rls_probe")

    assert (insert, updated, deleted) == (INSUFFICIENT_PRIVILEGE, 0, 0)
    assert await owner_rows(migrator_engine, tenant_a.id, "SELECT value FROM cce.rls_probe") == [
        "a"
    ]


# --- A lê só A ------------------------------------------------------------------------------


async def test_tenant_a_reads_only_tenant_a(
    tenant_a: SeededTenant, tenant_b: SeededTenant, runtime_session: OpenTenantSession
) -> None:
    async with runtime_session(tenant_a.id) as session:
        assert await values_of(session, "SELECT id FROM cce.tenants") == [tenant_a.id]
        assert (
            await values_of(session, "SELECT id FROM cce.tenants WHERE id = :id", id=tenant_b.id)
            == []
        )
    async with runtime_session(tenant_b.id) as session:
        assert await values_of(session, "SELECT id FROM cce.tenants") == [tenant_b.id]


@pytest.mark.sensitive
async def test_tenant_a_reads_only_its_own_api_keys(
    keyed_a: SeededTenant, keyed_b: SeededTenant, runtime_session: OpenTenantSession
) -> None:
    async with runtime_session(keyed_a.id) as session:
        own = await values_of(session, "SELECT id FROM cce.api_keys")
        other = await values_of(
            session, "SELECT id FROM cce.api_keys WHERE tenant_id = :id", id=keyed_b.id
        )
    require(own == [keyed_a.key_id], "tenant A did not see exactly its own API key")
    require(other == [], "tenant A saw an API key of tenant B")


async def test_tenant_a_cannot_read_a_probe_row_of_b(
    rls_probe: None,
    migrator_engine: AsyncEngine,
    tenant_a: SeededTenant,
    tenant_b: SeededTenant,
    runtime_session: OpenTenantSession,
) -> None:
    await seed_probe_row(migrator_engine, tenant_a.id, "a")
    await seed_probe_row(migrator_engine, tenant_b.id, "b")

    async with runtime_session(tenant_a.id) as session:
        assert await values_of(session, "SELECT value FROM cce.rls_probe") == ["a"]
    async with runtime_session(tenant_b.id) as session:
        assert await values_of(session, "SELECT value FROM cce.rls_probe") == ["b"]


# --- UPDATE e DELETE de B afetam zero linhas ------------------------------------------------


async def test_updating_tenant_b_as_a_affects_zero_rows_and_leaves_b_intact(
    tenant_a: SeededTenant,
    tenant_b: SeededTenant,
    migrator_engine: AsyncEngine,
    runtime_session: OpenTenantSession,
) -> None:
    count = await affected(
        as_tenant(runtime_session, tenant_a),
        "UPDATE cce.tenants SET name = 'invadido' WHERE id = :id",
        id=tenant_b.id,
    )

    assert count == 0
    assert await owner_rows(migrator_engine, tenant_b.id, "SELECT name FROM cce.tenants") == [
        tenant_b.name
    ]


async def test_tenant_a_can_update_its_own_name(
    tenant_a: SeededTenant,
    migrator_engine: AsyncEngine,
    runtime_session: OpenTenantSession,
) -> None:
    count = await affected(
        as_tenant(runtime_session, tenant_a), "UPDATE cce.tenants SET name = 'Renomeada'"
    )

    assert count == 1
    assert await owner_rows(migrator_engine, tenant_a.id, "SELECT name FROM cce.tenants") == [
        "Renomeada"
    ]


async def test_updating_or_deleting_a_probe_row_of_b_as_a_affects_zero_rows(
    rls_probe: None,
    migrator_engine: AsyncEngine,
    tenant_a: SeededTenant,
    tenant_b: SeededTenant,
    runtime_session: OpenTenantSession,
) -> None:
    await seed_probe_row(migrator_engine, tenant_a.id, "a")
    await seed_probe_row(migrator_engine, tenant_b.id, "b")
    as_a = as_tenant(runtime_session, tenant_a)

    updated = await affected(as_a, "UPDATE cce.rls_probe SET value = 'x' WHERE value = 'b'")
    deleted = await affected(as_a, "DELETE FROM cce.rls_probe WHERE value = 'b'")

    assert (updated, deleted) == (0, 0)
    assert await owner_rows(migrator_engine, tenant_b.id, "SELECT value FROM cce.rls_probe") == [
        "b"
    ]


# --- WITH CHECK: INSERT ou UPDATE para B é recusado -----------------------------------------


async def test_insert_for_b_under_a_is_rejected_by_with_check_and_the_next_transaction_works(
    rls_probe: None,
    migrator_engine: AsyncEngine,
    tenant_a: SeededTenant,
    tenant_b: SeededTenant,
    runtime_session: OpenTenantSession,
) -> None:
    as_a = as_tenant(runtime_session, tenant_a)

    rejected = await outcome(
        as_a,
        "INSERT INTO cce.rls_probe (tenant_id, value) VALUES (:tenant_id, 'x')",
        tenant_id=tenant_b.id,
    )
    accepted = await outcome(
        as_a,
        "INSERT INTO cce.rls_probe (tenant_id, value) VALUES (:tenant_id, 'ok')",
        tenant_id=tenant_a.id,
    )

    assert (rejected, accepted) == (INSUFFICIENT_PRIVILEGE, "ok")
    assert await owner_rows(migrator_engine, tenant_a.id, "SELECT value FROM cce.rls_probe") == [
        "ok"
    ]
    assert await owner_rows(migrator_engine, tenant_b.id, "SELECT value FROM cce.rls_probe") == []


async def test_the_with_check_rejection_comes_from_the_policy_not_from_a_missing_grant(
    rls_probe: None,
    tenant_a: SeededTenant,
    tenant_b: SeededTenant,
    runtime_session: OpenTenantSession,
) -> None:
    with pytest.raises(DBAPIError) as caught:
        async with runtime_session(tenant_a.id) as session:
            await session.execute(
                text("INSERT INTO cce.rls_probe (tenant_id, value) VALUES (:tenant_id, 'x')"),
                {"tenant_id": tenant_b.id},
            )

    require(
        "row-level security" in str(caught.value.orig),
        "the 42501 did not come from the row-level security policy",
    )


async def test_updating_a_row_of_a_to_tenant_b_is_rejected_by_with_check(
    rls_probe: None,
    migrator_engine: AsyncEngine,
    tenant_a: SeededTenant,
    tenant_b: SeededTenant,
    runtime_session: OpenTenantSession,
) -> None:
    await seed_probe_row(migrator_engine, tenant_a.id, "a")
    as_a = as_tenant(runtime_session, tenant_a)

    moved = await outcome(
        as_a, "UPDATE cce.rls_probe SET tenant_id = :tenant_id", tenant_id=tenant_b.id
    )
    renamed = await outcome(as_a, "UPDATE cce.rls_probe SET value = 'depois'")

    assert (moved, renamed) == (INSUFFICIENT_PRIVILEGE, "ok")
    assert await owner_rows(migrator_engine, tenant_a.id, "SELECT value FROM cce.rls_probe") == [
        "depois"
    ]
    assert await owner_rows(migrator_engine, tenant_b.id, "SELECT value FROM cce.rls_probe") == []


# --- o dono também está sujeito à RLS (FORCE) -----------------------------------------------


async def test_the_owner_without_context_also_sees_zero_rows(
    tenant_a: SeededTenant, tenant_b: SeededTenant, migrator_engine: AsyncEngine
) -> None:
    async with migrator_engine.connect() as connection:
        rows = list((await connection.execute(text("SELECT id FROM cce.tenants"))).scalars())
        await connection.rollback()

    assert rows == []


async def test_the_owner_with_a_context_sees_only_that_tenant(
    tenant_a: SeededTenant, tenant_b: SeededTenant, migrator_engine: AsyncEngine
) -> None:
    assert await owner_rows(migrator_engine, tenant_a.id, "SELECT id FROM cce.tenants") == [
        tenant_a.id
    ]


async def test_the_owner_cannot_insert_a_tenant_for_another_context(
    tenant_a: SeededTenant, migrator_engine: AsyncEngine
) -> None:
    async with migrator_engine.connect() as connection:
        await apply_context(connection, tenant_a.id)
        with pytest.raises(DBAPIError) as caught:
            await connection.execute(
                text(
                    "INSERT INTO cce.tenants (id, name, slug) VALUES (gen_random_uuid(), 'x', 'x')"
                )
            )
        await connection.rollback()

    assert sqlstate(caught.value) == INSUFFICIENT_PRIVILEGE


@pytest.mark.sensitive
async def test_the_owner_without_context_sees_no_api_keys(
    keyed_a: SeededTenant, migrator_engine: AsyncEngine
) -> None:
    async with migrator_engine.connect() as connection:
        rows = list((await connection.execute(text("SELECT id FROM cce.api_keys"))).scalars())
        await connection.rollback()

    require(rows == [], "the owner without context saw api_keys rows")


# --- o runtime não alcança o segredo --------------------------------------------------------

SECRET_ACCESS = {
    "select-digest": "SELECT secret_digest FROM cce.api_keys",
    "select-pepper-version": "SELECT pepper_version FROM cce.api_keys",
    "select-star": "SELECT * FROM cce.api_keys",
    "select-whole-row": "SELECT k FROM cce.api_keys k",
    "select-row-to-json": "SELECT row_to_json(k) FROM cce.api_keys k",
    "filter-by-digest": "SELECT id FROM cce.api_keys WHERE secret_digest IS NOT NULL",
    "filter-by-digest-length": "SELECT id FROM cce.api_keys WHERE octet_length(secret_digest) = 32",
    "order-by-digest": "SELECT id FROM cce.api_keys ORDER BY secret_digest",
    "group-by-digest": "SELECT count(*) FROM cce.api_keys GROUP BY secret_digest",
    "lock-rows": "SELECT id FROM cce.api_keys FOR UPDATE",
    "update-digest": "UPDATE cce.api_keys SET secret_digest = decode(repeat('00', 32), 'hex')",
    "update-revoked-at": "UPDATE cce.api_keys SET revoked_at = now()",
    "update-label": "UPDATE cce.api_keys SET label = 'x'",
    "delete": "DELETE FROM cce.api_keys",
}


@pytest.mark.sensitive
@pytest.mark.parametrize("statement", list(SECRET_ACCESS.values()), ids=list(SECRET_ACCESS))
async def test_runtime_cannot_reach_the_secret_columns_or_change_keys(
    keyed_a: SeededTenant, runtime_session: OpenTenantSession, statement: str
) -> None:
    result = await outcome(as_tenant(runtime_session, keyed_a), statement)

    require(
        result == INSUFFICIENT_PRIVILEGE,
        f"the runtime was not refused with {INSUFFICIENT_PRIVILEGE} (got {result})",
    )


@pytest.mark.sensitive
async def test_runtime_reads_the_non_secret_key_columns(
    keyed_a: SeededTenant, runtime_session: OpenTenantSession
) -> None:
    async with runtime_session(keyed_a.id) as session:
        rows = (await session.execute(text(f"SELECT {KEY_COLUMNS} FROM cce.api_keys"))).all()

    require(len(rows) == 1, "the runtime could not read its own API key metadata")


TENANT_WRITES: Mapping[str, str] = {
    "insert": "INSERT INTO cce.tenants (id, name, slug) VALUES (gen_random_uuid(), 'x', 'x')",
    "delete": "DELETE FROM cce.tenants",
    "truncate": "TRUNCATE cce.tenants CASCADE",
    "update-id": "UPDATE cce.tenants SET id = gen_random_uuid()",
    "update-slug": "UPDATE cce.tenants SET slug = 'novo'",
    "update-status": "UPDATE cce.tenants SET status = 'suspended'",
    "update-plan": "UPDATE cce.tenants SET plan = 'enterprise'",
    "update-created-at": "UPDATE cce.tenants SET created_at = now()",
}


@pytest.mark.parametrize("statement", list(TENANT_WRITES.values()), ids=list(TENANT_WRITES))
async def test_runtime_has_no_write_on_the_protected_tenant_columns(
    tenant_a: SeededTenant, runtime_session: OpenTenantSession, statement: str
) -> None:
    assert await outcome(as_tenant(runtime_session, tenant_a), statement) == INSUFFICIENT_PRIVILEGE


# --- policies no catálogo -------------------------------------------------------------------

POLICIES = """
    select tablename, policyname, permissive, cmd, roles::text[], qual, with_check
    from pg_policies
    where schemaname = 'cce'
    order by tablename, policyname
"""


async def test_rls_is_enabled_and_forced_on_both_tables(runtime_no_context: OpenSession) -> None:
    async with runtime_no_context() as session:
        rows = (
            await session.execute(
                text("""
                    select relname, relrowsecurity, relforcerowsecurity
                    from pg_class
                    where oid in ('cce.tenants'::regclass, 'cce.api_keys'::regclass)
                    order by relname
                """)
            )
        ).all()

    assert [tuple(row) for row in rows] == [("api_keys", True, True), ("tenants", True, True)]


async def test_each_table_has_one_policy_with_using_and_with_check(
    runtime_no_context: OpenSession,
) -> None:
    async with runtime_no_context() as session:
        rows = (await session.execute(text(POLICIES))).all()

    assert [(r.tablename, r.policyname, r.permissive, r.cmd) for r in rows] == [
        ("api_keys", "tenant_isolation", "PERMISSIVE", "ALL"),
        ("tenants", "tenant_isolation", "PERMISSIVE", "ALL"),
    ]
    for row in rows:
        assert sorted(row.roles) == ["cce_app", "cce_migrator"]
        assert row.qual is not None
        assert row.with_check == row.qual
        assert "app.tenant_id" in row.qual
        assert "NULLIF" in row.qual


async def test_the_probe_policy_is_the_same_expression_as_the_production_policy(
    rls_probe: None, runtime_no_context: OpenSession
) -> None:
    async with runtime_no_context() as session:
        rows = (await session.execute(text(POLICIES))).all()

    by_table = {row.tablename: row for row in rows}
    assert by_table["rls_probe"].qual == by_table["api_keys"].qual
    assert by_table["rls_probe"].with_check == by_table["api_keys"].with_check
