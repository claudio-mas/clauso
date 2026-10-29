"""Cadeia de migrations em banco descartável (`cce_test`), sempre pelo CLI do Alembic.

Cada comando roda em um subprocesso: é o mesmo caminho do serviço `migrate` e evita misturar o
`asyncio.run` do env.py com o laço de eventos do pytest. O DSN nunca vai no argv.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from db_support import ROOT, run_alembic
from integration.support import assert_no_leaks, require
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection

from cce.shared.config import MigrationSettings
from cce.shared.db.models import Base

pytestmark = pytest.mark.migration

TABLES = {"tenants", "api_keys"}


def forbidden_in_output(settings: MigrationSettings) -> dict[str, str]:
    url = make_url(settings.migrator_database_url.get_secret_value())
    return {
        "migrator DSN": settings.migrator_database_url.get_secret_value(),
        "migrator password": url.password or "",
    }


async def user_tables(connection: AsyncConnection) -> set[str]:
    rows = (
        await connection.execute(
            text("""
                select tablename from pg_tables
                where schemaname = 'cce' and tablename <> 'alembic_version'
            """)
        )
    ).scalars()
    found = set(rows)
    await connection.rollback()
    return found


async def applied_revisions(connection: AsyncConnection) -> list[str]:
    exists = (await connection.execute(text("select to_regclass('cce.alembic_version')"))).scalar()
    revisions = (
        list(
            (
                await connection.execute(text("select version_num from cce.alembic_version"))
            ).scalars()
        )
        if exists
        else []
    )
    await connection.rollback()
    return revisions


async def database_constraint_names(connection: AsyncConnection) -> dict[str, set[str]]:
    rows = (
        await connection.execute(
            text("""
                select c.relname, k.conname
                from pg_constraint k
                join pg_class c on c.oid = k.conrelid
                join pg_namespace n on n.oid = c.relnamespace
                where n.nspname = 'cce' and c.relname in ('tenants', 'api_keys')
                  and k.contype in ('p', 'u', 'f', 'c')
            """)
        )
    ).all()
    await connection.rollback()
    names: dict[str, set[str]] = {"tenants": set(), "api_keys": set()}
    for table, constraint in rows:
        names[table].add(constraint)
    return names


async def rls_state(connection: AsyncConnection) -> dict[str, list[tuple[object, ...]]]:
    """Flags de RLS, policies e ACLs das duas tabelas (o que o metadata não representa)."""
    queries = {
        "flags": """
            select relname, relrowsecurity, relforcerowsecurity
            from pg_class
            where oid in ('cce.tenants'::regclass, 'cce.api_keys'::regclass)
            order by relname
        """,
        "policies": """
            select tablename, policyname, permissive, roles::text, cmd, qual, with_check
            from pg_policies
            where schemaname = 'cce'
            order by tablename, policyname
        """,
        "grants": """
            select table_name, column_name, grantee, privilege, grantable
            from (
                select c.relname as table_name, null::text as column_name,
                       pg_get_userbyid(a.grantee) as grantee, a.privilege_type as privilege,
                       a.is_grantable as grantable
                from pg_class c
                cross join lateral aclexplode(c.relacl) a
                where c.oid in ('cce.tenants'::regclass, 'cce.api_keys'::regclass)
                  and a.grantee <> c.relowner
                union all
                select c.relname, t.attname, pg_get_userbyid(a.grantee), a.privilege_type,
                       a.is_grantable
                from pg_attribute t
                join pg_class c on c.oid = t.attrelid
                cross join lateral aclexplode(t.attacl) a
                where c.oid in ('cce.tenants'::regclass, 'cce.api_keys'::regclass)
            ) entries
            order by table_name, column_name nulls first, grantee, privilege
        """,
    }
    state = {}
    for name, query in queries.items():
        state[name] = [tuple(row) for row in (await connection.execute(text(query))).all()]
    await connection.rollback()
    return state


async def test_0002_round_trip_removes_and_restores_rls_policies_and_grants(
    migrator_settings: MigrationSettings, migrator_connection: AsyncConnection
) -> None:
    run_alembic(migrator_settings, "upgrade", "head")
    protected = await rls_state(migrator_connection)

    run_alembic(migrator_settings, "downgrade", "0001")
    bare = await rls_state(migrator_connection)
    assert await applied_revisions(migrator_connection) == ["0001"]

    run_alembic(migrator_settings, "upgrade", "head")
    restored = await rls_state(migrator_connection)

    assert protected["flags"] == [("api_keys", True, True), ("tenants", True, True)]
    assert [row[:2] for row in protected["policies"]] == [
        ("api_keys", "tenant_isolation"),
        ("tenants", "tenant_isolation"),
    ]
    assert protected["grants"] != []
    assert bare == {
        "flags": [("api_keys", False, False), ("tenants", False, False)],
        "policies": [],
        "grants": [],
    }
    assert restored == protected
    assert await applied_revisions(migrator_connection) == ["0002"]


async def test_empty_to_0001_to_base_to_0001(
    migrator_settings: MigrationSettings, migrator_connection: AsyncConnection
) -> None:
    run_alembic(migrator_settings, "downgrade", "base")
    assert await user_tables(migrator_connection) == set()
    assert await applied_revisions(migrator_connection) == []

    run_alembic(migrator_settings, "upgrade", "0001")
    assert await user_tables(migrator_connection) == TABLES
    assert await applied_revisions(migrator_connection) == ["0001"]

    run_alembic(migrator_settings, "downgrade", "base")
    assert await user_tables(migrator_connection) == set()
    assert await applied_revisions(migrator_connection) == []

    run_alembic(migrator_settings, "upgrade", "0001")
    assert await user_tables(migrator_connection) == TABLES
    assert await applied_revisions(migrator_connection) == ["0001"]


async def test_upgrade_head_is_idempotent_and_there_is_a_single_head(
    migrator_settings: MigrationSettings, migrator_connection: AsyncConnection
) -> None:
    run_alembic(migrator_settings, "upgrade", "head")
    again = run_alembic(migrator_settings, "upgrade", "head")
    heads = run_alembic(migrator_settings, "heads")

    assert again.returncode == 0
    assert heads.stdout.split() == ["0002", "(head)"]
    assert await applied_revisions(migrator_connection) == ["0002"]


async def test_the_migration_matches_the_metadata(
    migrator_settings: MigrationSettings, migrator_connection: AsyncConnection
) -> None:
    run_alembic(migrator_settings, "upgrade", "head")

    check = run_alembic(migrator_settings, "check")

    assert check.returncode == 0
    expected = {
        name: {c.name for c in Base.metadata.tables[f"cce.{name}"].constraints if c.name}
        for name in ("tenants", "api_keys")
    }
    assert await database_constraint_names(migrator_connection) == expected


def test_offline_mode_emits_sql_without_touching_the_database(
    migrator_settings: MigrationSettings,
) -> None:
    result = run_alembic(migrator_settings, "upgrade", "0001", "--sql")

    # Vazamento primeiro: só depois disso é seguro inspecionar a saída com `require`.
    assert_no_leaks(result.stdout + result.stderr, forbidden_in_output(migrator_settings), "SQL")
    require("CREATE TABLE cce.tenants" in result.stdout, "the offline SQL has no tenants table")
    require("CREATE TABLE cce.api_keys" in result.stdout, "the offline SQL has no api_keys table")


async def test_the_subprocess_runs_the_validated_configuration_even_if_the_ambient_diverges(
    monkeypatch: pytest.MonkeyPatch,
    migrator_settings: MigrationSettings,
    migrator_connection: AsyncConnection,
) -> None:
    run_alembic(migrator_settings, "upgrade", "head")
    # O ambiente do processo de teste aponta para outro ambiente e outro destino. Se o
    # subprocesso o herdasse, `current` tentaria conectar em poison.invalid e falharia.
    poison = {
        "CCE_ENV": "local",
        "CCE_ENV_FILE": "/poison/.env",
        "CCE_MIGRATOR_DATABASE_URL": "postgresql+asyncpg://cce_migrator:x@poison.invalid:5432/cce",
    }
    for name, value in poison.items():
        monkeypatch.setenv(name, value)

    result = run_alembic(migrator_settings, "current")

    require(result.returncode == 0, "alembic did not use the validated configuration")
    require(result.stdout.split() == ["0002", "(head)"], "alembic is not at 0002 (head)")
    assert await applied_revisions(migrator_connection) == ["0002"]


async def test_migration_output_never_contains_the_dsn_or_the_password(
    migrator_settings: MigrationSettings, migrator_connection: AsyncConnection
) -> None:
    forbidden = forbidden_in_output(migrator_settings)
    for command in (("downgrade", "base"), ("upgrade", "head"), ("check",), ("current",)):
        result = run_alembic(migrator_settings, *command)
        assert_no_leaks(result.stdout + result.stderr, forbidden, f"alembic {command[0]} output")


def test_offline_0002_emits_rls_policies_and_grants_without_the_probe(
    migrator_settings: MigrationSettings,
) -> None:
    result = run_alembic(migrator_settings, "upgrade", "0001:0002", "--sql")

    assert_no_leaks(result.stdout + result.stderr, forbidden_in_output(migrator_settings), "SQL")
    for expected in (
        "FORCE ROW LEVEL SECURITY",
        "CREATE POLICY tenant_isolation ON cce.tenants",
        "CREATE POLICY tenant_isolation ON cce.api_keys",
    ):
        require(expected in result.stdout, f"the offline SQL of 0002 has no {expected}")
    require("rls_probe" not in result.stdout, "the offline SQL of 0002 mentions the test probe")
    require("secret_digest" not in result.stdout, "0002 must not grant the digest column")


def test_the_probe_never_reaches_the_metadata_or_the_migrations() -> None:
    migrations = sorted((ROOT / "infrastructure" / "migrations" / "versions").glob("*.py"))

    assert "rls_probe" not in {table.name for table in Base.metadata.tables.values()}
    assert [path.name for path in migrations if "rls_probe" in path.read_text()] == []


def test_env_py_builds_its_engine_through_the_null_pool_factory_and_never_creates_tables() -> None:
    source = Path(ROOT / "infrastructure" / "migrations" / "env.py").read_text()

    assert "build_migration_engine" in source
    assert "fileConfig" not in source  # o alembic.ini não tem seções de logging
    assert "version_table_schema" in source
    assert "include_schemas" in source


def test_alembic_ini_has_no_url_and_points_to_the_migrations_folder() -> None:
    config = (ROOT / "alembic.ini").read_text()

    assert "script_location = %(here)s/infrastructure/migrations" in config
    assert "sqlalchemy.url" not in config
    assert "postgresql" not in config
