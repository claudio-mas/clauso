"""Cadeia de migrations em banco descartável (`cce_test`), sempre pelo CLI do Alembic.

Cada comando roda em um subprocesso: é o mesmo caminho do serviço `migrate` e evita misturar o
`asyncio.run` do env.py com o laço de eventos do pytest. O DSN nunca vai no argv.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from db_support import ROOT, run_alembic, run_check_schema, sensitive_components
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
    """Flags de RLS, policies, ACLs e funções (o que o metadata não representa)."""
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
                where c.oid in ('cce.tenants'::regclass, 'cce.api_keys'::regclass,
                                'cce.alembic_version'::regclass)
                  and a.grantee <> c.relowner
                union all
                select c.relname, t.attname, pg_get_userbyid(a.grantee), a.privilege_type,
                       a.is_grantable
                from pg_attribute t
                join pg_class c on c.oid = t.attrelid
                cross join lateral aclexplode(t.attacl) a
                where c.oid in ('cce.tenants'::regclass, 'cce.api_keys'::regclass,
                                'cce.alembic_version'::regclass)
            ) entries
            order by table_name, column_name nulls first, grantee, privilege
        """,
        "functions": """
            select n.nspname || '.' || p.proname || '(' || pg_get_function_identity_arguments(p.oid)
                   || ')', pg_get_userbyid(p.proowner), p.prosecdef, p.proconfig::text,
                   p.proacl::text
            from pg_proc p
            join pg_namespace n on n.oid = p.pronamespace
            where n.nspname in ('cce', 'cce_private')
            order by 1
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
    run_alembic(migrator_settings, "downgrade", "0002")
    protected = await rls_state(migrator_connection)

    run_alembic(migrator_settings, "downgrade", "0001")
    bare = await rls_state(migrator_connection)
    assert await applied_revisions(migrator_connection) == ["0001"]

    run_alembic(migrator_settings, "upgrade", "0002")
    restored = await rls_state(migrator_connection)
    run_alembic(migrator_settings, "upgrade", "head")

    assert protected["flags"] == [("api_keys", True, True), ("tenants", True, True)]
    assert [row[:2] for row in protected["policies"]] == [
        ("api_keys", "tenant_isolation"),
        ("tenants", "tenant_isolation"),
    ]
    assert protected["grants"] != []
    assert protected["functions"] == []
    assert bare == {
        "flags": [("api_keys", False, False), ("tenants", False, False)],
        "policies": [],
        "grants": [],
        "functions": [],
    }
    assert restored == protected
    assert await applied_revisions(migrator_connection) == ["0003"]


LOOKUP_IDENTITY = "cce_private.lookup_api_key(p_key_id uuid)"
AUTH_COLUMNS = {
    "tenants": {"id", "status"},
    "api_keys": {
        "id",
        "tenant_id",
        "secret_digest",
        "pepper_version",
        "scopes",
        "expires_at",
        "revoked_at",
    },
}


async def test_0003_round_trip_adds_and_removes_the_lookup_function_policies_and_grants(
    migrator_settings: MigrationSettings, migrator_connection: AsyncConnection
) -> None:
    run_alembic(migrator_settings, "upgrade", "head")
    complete = await rls_state(migrator_connection)
    run_alembic(migrator_settings, "downgrade", "0002")
    before = await rls_state(migrator_connection)
    assert await applied_revisions(migrator_connection) == ["0002"]
    run_alembic(migrator_settings, "upgrade", "head")
    restored = await rls_state(migrator_connection)

    function = [row for row in complete["functions"] if row[0] == LOOKUP_IDENTITY]
    assert len(function) == 1 and len(complete["functions"]) == 1
    _, owner, definer, config, acl = function[0]
    assert (owner, definer) == ("cce_auth", True)
    assert "search_path=pg_catalog, pg_temp" in str(config) and "row_security=on" in str(config)
    assert {item.split("=")[0] for item in str(acl).strip("{}").split(",")} == {
        "cce_auth",
        "cce_app",
    }  # a string vazia à esquerda do "=" seria PUBLIC
    assert {row[:2] for row in complete["policies"]} == {
        ("api_keys", "auth_lookup"),
        ("api_keys", "tenant_isolation"),
        ("tenants", "auth_lookup"),
        ("tenants", "tenant_isolation"),
    }
    auth_grants = {(row[0], row[1]) for row in complete["grants"] if row[2] == "cce_auth"}
    assert auth_grants == {
        (table, column) for table, columns in AUTH_COLUMNS.items() for column in columns
    }
    assert ("alembic_version", None, "cce_app", "SELECT", False) in complete["grants"]

    assert before["functions"] == []
    assert not [row for row in before["policies"] if row[1] == "auth_lookup"]
    assert not [row for row in before["grants"] if row[2] == "cce_auth"]
    assert not [row for row in before["grants"] if row[0] == "alembic_version"]
    assert restored == complete
    assert await applied_revisions(migrator_connection) == ["0003"]


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


async def catalog_state(connection: AsyncConnection) -> dict[str, list[tuple[object, ...]]]:
    """`rls_state` mais constraints (com a definição e a validação) e índices de `cce`."""
    state = await rls_state(connection)
    queries = {
        "constraints": """
            select c.relname, k.conname, pg_get_constraintdef(k.oid), k.convalidated
            from pg_constraint k
            join pg_class c on c.oid = k.conrelid
            join pg_namespace n on n.oid = c.relnamespace
            where n.nspname = 'cce'
            order by 1, 2
        """,
        "indexes": """
            select tablename, indexname, indexdef from pg_indexes
            where schemaname = 'cce'
            order by 1, 2
        """,
    }
    for name, query in queries.items():
        state[name] = [tuple(row) for row in (await connection.execute(text(query))).all()]
    await connection.rollback()
    return state


def require_clean_schema(settings: MigrationSettings) -> None:
    result = run_check_schema(settings)
    dsn = settings.migrator_database_url.get_secret_value()
    assert_no_leaks(result.stdout + result.stderr, sensitive_components(dsn), "check_schema output")
    require(result.returncode == 0, f"check_schema exited {result.returncode} after the chain")


async def test_0001_to_head_to_base_to_head_restores_constraints_policies_and_grants(
    migrator_settings: MigrationSettings, migrator_connection: AsyncConnection
) -> None:
    run_alembic(migrator_settings, "downgrade", "base")
    require(await user_tables(migrator_connection) == set(), "base still has tables")

    run_alembic(migrator_settings, "upgrade", "0001")
    assert await applied_revisions(migrator_connection) == ["0001"]
    run_alembic(migrator_settings, "upgrade", "head")
    first = await catalog_state(migrator_connection)
    require_clean_schema(migrator_settings)

    run_alembic(migrator_settings, "downgrade", "base")
    assert await user_tables(migrator_connection) == set()
    assert await applied_revisions(migrator_connection) == []
    assert await base_functions(migrator_connection) == []

    run_alembic(migrator_settings, "upgrade", "head")
    rebuilt = await catalog_state(migrator_connection)

    assert await applied_revisions(migrator_connection) == ["0003"]
    assert rebuilt == first
    # O estado não é vazio: as categorias que o metadata não representa estão todas lá.
    assert {name for name, rows in first.items() if not rows} == set()
    require_clean_schema(migrator_settings)
    check = run_alembic(migrator_settings, "check")
    assert check.returncode == 0


async def base_functions(connection: AsyncConnection) -> list[str]:
    """Funções que sobraram nos schemas do produto (em base, nenhuma)."""
    rows = (
        await connection.execute(
            text("""
                select p.proname from pg_proc p
                join pg_namespace n on n.oid = p.pronamespace
                where n.nspname in ('cce', 'cce_private')
            """)
        )
    ).scalars()
    found = list(rows)
    await connection.rollback()
    return found


async def test_upgrade_head_is_idempotent_and_there_is_a_single_head(
    migrator_settings: MigrationSettings, migrator_connection: AsyncConnection
) -> None:
    run_alembic(migrator_settings, "upgrade", "head")
    again = run_alembic(migrator_settings, "upgrade", "head")
    heads = run_alembic(migrator_settings, "heads")

    assert again.returncode == 0
    assert heads.stdout.split() == ["0003", "(head)"]
    assert await applied_revisions(migrator_connection) == ["0003"]


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
    require(result.stdout.split() == ["0003", "(head)"], "alembic is not at 0003 (head)")
    assert await applied_revisions(migrator_connection) == ["0003"]


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


def test_offline_0003_emits_a_pinned_definer_with_explicit_grants(
    migrator_settings: MigrationSettings,
) -> None:
    result = run_alembic(migrator_settings, "upgrade", "0002:0003", "--sql")

    assert_no_leaks(result.stdout + result.stderr, forbidden_in_output(migrator_settings), "SQL")
    for expected in (
        "SECURITY DEFINER",
        "SET search_path = pg_catalog, pg_temp",
        "SET row_security = on",
        "CREATE POLICY auth_lookup ON cce.tenants",
        "CREATE POLICY auth_lookup ON cce.api_keys",
        "REVOKE ALL ON FUNCTION cce_private.lookup_api_key(uuid) FROM PUBLIC",
        "GRANT EXECUTE ON FUNCTION cce_private.lookup_api_key(uuid) TO cce_app",
        "GRANT SELECT ON cce.alembic_version TO cce_app",
    ):
        require(expected in result.stdout, f"the offline SQL of 0003 has no {expected}")
    require("rls_probe" not in result.stdout, "the offline SQL of 0003 mentions the test probe")
    require("TO PUBLIC" not in result.stdout, "0003 grants something to PUBLIC")


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
