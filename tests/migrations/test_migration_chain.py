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
    assert heads.stdout.split() == ["0001", "(head)"]
    assert await applied_revisions(migrator_connection) == ["0001"]


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
    require(result.stdout.split() == ["0001", "(head)"], "alembic is not at 0001 (head)")
    assert await applied_revisions(migrator_connection) == ["0001"]


async def test_migration_output_never_contains_the_dsn_or_the_password(
    migrator_settings: MigrationSettings, migrator_connection: AsyncConnection
) -> None:
    forbidden = forbidden_in_output(migrator_settings)
    for command in (("downgrade", "base"), ("upgrade", "head"), ("check",), ("current",)):
        result = run_alembic(migrator_settings, *command)
        assert_no_leaks(result.stdout + result.stderr, forbidden, f"alembic {command[0]} output")


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
