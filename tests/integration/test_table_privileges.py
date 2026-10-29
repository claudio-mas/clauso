"""Dono das tabelas e privilégios dos roles do produto na migration 0001."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from .support import sql_outcome

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("migrated_database")]

INSUFFICIENT_PRIVILEGE = "42501"
TABLES = ("cce.tenants", "cce.api_keys", "cce.alembic_version")
PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")


async def test_tables_belong_to_the_migrator(app_connection: AsyncConnection) -> None:
    rows = (
        await app_connection.execute(
            text("""
                select tablename, tableowner
                from pg_tables
                where schemaname = 'cce'
                order by tablename
            """)
        )
    ).all()
    await app_connection.rollback()

    assert [tuple(row) for row in rows] == [
        ("alembic_version", "cce_migrator"),
        ("api_keys", "cce_migrator"),
        ("tenants", "cce_migrator"),
    ]


async def test_0001_grants_nothing_to_the_runtime_roles(app_connection: AsyncConnection) -> None:
    """Sem RLS ainda (ticket 05), qualquer SELECT do runtime enxergaria todos os Tenants."""
    granted = []
    for role in ("cce_app", "cce_auth"):
        for table in TABLES:
            for privilege in PRIVILEGES:
                row = (
                    await app_connection.execute(
                        text("select has_table_privilege(:role, :table, :privilege)"),
                        {"role": role, "table": table, "privilege": privilege},
                    )
                ).scalar_one()
                if row:
                    granted.append(f"{role}:{table}:{privilege}")
    await app_connection.rollback()

    assert granted == []


async def test_public_has_no_privilege_on_the_tables(app_connection: AsyncConnection) -> None:
    row = (
        await app_connection.execute(
            text("""
                select count(distinct c.oid), count(a.grantee)
                from pg_class c
                join pg_namespace n on n.oid = c.relnamespace
                left join lateral aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) a
                       on a.grantee = 0
                where n.nspname = 'cce' and c.relkind = 'r'
            """)
        )
    ).one()
    await app_connection.rollback()

    assert tuple(row) == (3, 0)  # tenants, api_keys e alembic_version; nenhuma concessão a PUBLIC


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT count(*) FROM cce.tenants",
        "SELECT count(*) FROM cce.api_keys",
        "SELECT secret_digest FROM cce.api_keys",
        "INSERT INTO cce.tenants (id, name, slug) VALUES (gen_random_uuid(), 'x', 'x')",
        "UPDATE cce.tenants SET name = 'x'",
        "DELETE FROM cce.api_keys",
        "TRUNCATE cce.tenants CASCADE",
    ],
)
async def test_runtime_role_cannot_read_or_write_the_tables(
    app_connection: AsyncConnection, statement: str
) -> None:
    assert await sql_outcome(app_connection, statement) == INSUFFICIENT_PRIVILEGE


@pytest.mark.parametrize(
    "statement",
    [
        "DROP TABLE cce.api_keys",
        "ALTER TABLE cce.tenants ADD COLUMN extra integer",
        "ALTER TABLE cce.tenants DISABLE TRIGGER ALL",
        "CREATE INDEX ix_forbidden ON cce.tenants (name)",
        "CREATE TABLE cce.forbidden (id integer)",
        "ALTER TABLE cce.tenants OWNER TO cce_app",
        "COMMENT ON TABLE cce.tenants IS 'x'",
    ],
)
async def test_runtime_role_has_no_ddl_on_the_tables(
    app_connection: AsyncConnection, statement: str
) -> None:
    assert await sql_outcome(app_connection, statement) == INSUFFICIENT_PRIVILEGE


async def test_migrator_owns_the_tables_so_it_can_alter_them(
    migrator_connection: AsyncConnection,
) -> None:
    assert (
        await sql_outcome(migrator_connection, "ALTER TABLE cce.tenants ADD COLUMN extra integer")
        == "ok"
    )
