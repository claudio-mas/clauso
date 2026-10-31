"""Deriva de schema GRAVADA no banco descartável: `check_schema` falha e só volta a passar no head.

Diferente de `tests/integration/test_check_schema.py`, que desfaz cada defeito na mesma transação,
aqui o defeito é commitado, como faria uma correção manual num ambiente, e o verificador roda pela
linha de comando, como na CI. O banco é sempre reconstruído do zero no fim, mesmo se o teste falhar.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from db_support import rebuild_schema, run_alembic, run_check_schema, sensitive_components
from integration.support import assert_no_leaks, require
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from cce.shared.config import MigrationSettings

pytestmark = pytest.mark.migration


@pytest.fixture
async def drift(
    migrator_settings: MigrationSettings, admin_engine: AsyncEngine
) -> AsyncIterator[None]:
    """O banco no head antes do teste; no fim, reconstruído do zero pelas migrations."""
    run_alembic(migrator_settings, "upgrade", "head")
    try:
        yield
    finally:
        await rebuild_schema(admin_engine, migrator_settings)


def check(settings: MigrationSettings) -> tuple[int, list[str]]:
    """Código de saída e linhas do `check_schema`, depois de conferir que nada vazou."""
    result = run_check_schema(settings)
    dsn = settings.migrator_database_url.get_secret_value()
    assert_no_leaks(result.stdout + result.stderr, sensitive_components(dsn), "check_schema output")
    return result.returncode, result.stdout.splitlines()


async def commit(engine: AsyncEngine, *statements: str) -> None:
    async with engine.begin() as connection:
        for statement in statements:
            await connection.execute(text(statement))


async def test_a_revoked_grant_fails_the_check_until_the_head_is_reapplied(
    migrator_settings: MigrationSettings, migrator_engine: AsyncEngine, drift: None
) -> None:
    require(check(migrator_settings) == (0, ["schema check passed"]), "the head is not clean")

    await commit(
        migrator_engine,
        "REVOKE SELECT (scopes) ON cce.api_keys FROM cce_app",
        "REVOKE SELECT (secret_digest) ON cce.api_keys FROM cce_auth",
    )
    code, lines = check(migrator_settings)

    require(code == 1, f"check_schema exited {code} on a revoked grant, expected 1")
    require(
        lines
        == [
            "missing grant: cce_app SELECT on cce.api_keys.scopes",
            "missing grant: cce_auth SELECT on cce.api_keys.secret_digest",
        ],
        f"check_schema reported {lines}",
    )

    # As revisões que concedem esses grants são desfeitas e reaplicadas: o head volta inteiro.
    run_alembic(migrator_settings, "downgrade", "0001")
    run_alembic(migrator_settings, "upgrade", "head")

    require(check(migrator_settings) == (0, ["schema check passed"]), "the head was not restored")


async def test_a_dropped_policy_fails_the_check_until_the_schema_is_rebuilt(
    migrator_settings: MigrationSettings,
    migrator_engine: AsyncEngine,
    admin_engine: AsyncEngine,
    drift: None,
) -> None:
    await commit(migrator_engine, "DROP POLICY tenant_isolation ON cce.api_keys")
    code, lines = check(migrator_settings)

    require(code == 1, f"check_schema exited {code} on a dropped policy, expected 1")
    require(
        lines == ["cce.api_keys: policy tenant_isolation is missing"],
        f"check_schema reported {lines}",
    )

    # O downgrade da 0002 removeria a policy que já não existe; no banco descartável o schema é
    # reconstruído do zero, só pelas migrations.
    await rebuild_schema(admin_engine, migrator_settings)

    require(check(migrator_settings) == (0, ["schema check passed"]), "the head was not restored")
    current = run_alembic(migrator_settings, "current")
    require(current.stdout.split() == ["0003", "(head)"], "the rebuilt schema is not at the head")
