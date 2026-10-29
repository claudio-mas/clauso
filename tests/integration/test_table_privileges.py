"""Dono das tabelas e privilégios dos roles do produto depois das migrations 0002 e 0003."""

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


ACL_ENTRIES = """
    select table_name, column_name, grantee, privilege, grantable
    from (
        select c.relname as table_name, null::text as column_name,
               case when a.grantee = 0 then 'PUBLIC' else pg_get_userbyid(a.grantee) end as grantee,
               a.privilege_type as privilege, a.is_grantable as grantable
        from pg_class c
        join pg_namespace n on n.oid = c.relnamespace
        cross join lateral aclexplode(c.relacl) a
        where n.nspname = 'cce' and c.relkind = 'r' and a.grantee <> c.relowner
        union all
        select c.relname, t.attname,
               case when a.grantee = 0 then 'PUBLIC' else pg_get_userbyid(a.grantee) end,
               a.privilege_type, a.is_grantable
        from pg_attribute t
        join pg_class c on c.oid = t.attrelid
        join pg_namespace n on n.oid = c.relnamespace
        cross join lateral aclexplode(t.attacl) a
        where n.nspname = 'cce' and c.relkind = 'r' and not t.attisdropped
    ) entries
    order by table_name, column_name nulls first, grantee, privilege
"""

# Exatamente o que o runtime recebe (plano do M0, seções 9.2 e 10.1) e o que `cce_auth` recebe na
# 0003 (seção 11.2): só SELECT em colunas, as mesmas do lookup. Nada para PUBLIC; `secret_digest` e
# `pepper_version` só aparecem na linha de `cce_auth`; `alembic_version` só dá SELECT ao runtime.
EXPECTED_ACL = [
    ("alembic_version", None, "cce_app", "SELECT", False),
    ("api_keys", "created_at", "cce_app", "SELECT", False),
    ("api_keys", "expires_at", "cce_app", "SELECT", False),
    ("api_keys", "expires_at", "cce_auth", "SELECT", False),
    ("api_keys", "id", "cce_app", "SELECT", False),
    ("api_keys", "id", "cce_auth", "SELECT", False),
    ("api_keys", "label", "cce_app", "SELECT", False),
    ("api_keys", "pepper_version", "cce_auth", "SELECT", False),
    ("api_keys", "revoked_at", "cce_app", "SELECT", False),
    ("api_keys", "revoked_at", "cce_auth", "SELECT", False),
    ("api_keys", "scopes", "cce_app", "SELECT", False),
    ("api_keys", "scopes", "cce_auth", "SELECT", False),
    ("api_keys", "secret_digest", "cce_auth", "SELECT", False),
    ("api_keys", "tenant_id", "cce_app", "SELECT", False),
    ("api_keys", "tenant_id", "cce_auth", "SELECT", False),
    ("tenants", None, "cce_app", "SELECT", False),
    ("tenants", "id", "cce_auth", "SELECT", False),
    ("tenants", "status", "cce_auth", "SELECT", False),
    ("tenants", "default_currency", "cce_app", "UPDATE", False),
    ("tenants", "locale", "cce_app", "UPDATE", False),
    ("tenants", "name", "cce_app", "UPDATE", False),
    ("tenants", "timezone", "cce_app", "UPDATE", False),
    ("tenants", "updated_at", "cce_app", "UPDATE", False),
]


async def test_0002_and_0003_grant_exactly_the_minimum_and_nothing_to_anyone_else(
    app_connection: AsyncConnection,
) -> None:
    rows = (await app_connection.execute(text(ACL_ENTRIES))).all()
    await app_connection.rollback()

    assert [tuple(row) for row in rows] == EXPECTED_ACL


@pytest.mark.parametrize("column", ["secret_digest", "pepper_version"])
async def test_the_runtime_has_no_privilege_of_any_kind_on_the_secret_columns(
    app_connection: AsyncConnection, column: str
) -> None:
    row = (
        await app_connection.execute(
            text("""
                select has_column_privilege('cce_app', 'cce.api_keys', :column,
                                            'SELECT, INSERT, UPDATE, REFERENCES'),
                       has_column_privilege('cce_auth', 'cce.api_keys', :column,
                                            'INSERT, UPDATE, REFERENCES'),
                       has_column_privilege('cce_auth', 'cce.api_keys', :column, 'SELECT')
            """),
            {"column": column},
        )
    ).one()
    await app_connection.rollback()

    # O runtime não tem nada; cce_auth só lê (o definer precisa do digest e da versão do pepper).
    assert tuple(row) == (False, False, True)


async def test_cce_auth_has_no_table_level_privilege_and_no_write_of_any_kind(
    app_connection: AsyncConnection,
) -> None:
    granted = []
    for table in TABLES:
        for privilege in PRIVILEGES:
            allowed = (
                await app_connection.execute(
                    text("select has_table_privilege('cce_auth', :table, :privilege)"),
                    {"table": table, "privilege": privilege},
                )
            ).scalar_one()
            if allowed:
                granted.append(f"{table}:{privilege}")
    await app_connection.rollback()

    assert granted == []  # só colunas (has_table_privilege exige a tabela inteira)


async def test_the_runtime_reads_the_migration_version_and_cce_auth_does_not(
    app_connection: AsyncConnection,
) -> None:
    read = await sql_outcome(app_connection, "SELECT version_num FROM cce.alembic_version")
    auth = (
        await app_connection.execute(
            text("select has_any_column_privilege('cce_auth', 'cce.alembic_version', 'SELECT')")
        )
    ).scalar_one()
    await app_connection.rollback()

    assert read == "ok"  # readiness (ticket 07): é o único acesso do runtime a essa tabela
    assert auth is False


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
        "INSERT INTO cce.alembic_version (version_num) VALUES ('x')",
        "UPDATE cce.alembic_version SET version_num = 'x'",
        "DELETE FROM cce.alembic_version",
        "SELECT secret_digest FROM cce.api_keys",
        "SELECT pepper_version FROM cce.api_keys",
        "INSERT INTO cce.tenants (id, name, slug) VALUES (gen_random_uuid(), 'x', 'x')",
        "UPDATE cce.tenants SET slug = 'x'",
        "UPDATE cce.api_keys SET label = 'x'",
        "DELETE FROM cce.tenants",
        "DELETE FROM cce.api_keys",
        "TRUNCATE cce.tenants CASCADE",
    ],
)
async def test_runtime_role_cannot_read_or_write_what_it_was_not_granted(
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
