"""O serviço `migrate` do Compose aplica a migration e termina (execução única)."""

from __future__ import annotations

import pytest
from db_support import run_alembic
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from cce.shared.config import MigrationSettings

from .support import assert_no_leaks, require, run_compose

pytestmark = pytest.mark.integration


async def revisions(connection: AsyncConnection) -> list[str]:
    exists = (await connection.execute(text("select to_regclass('cce.alembic_version')"))).scalar()
    found = (
        list(
            (
                await connection.execute(text("select version_num from cce.alembic_version"))
            ).scalars()
        )
        if exists
        else []
    )
    await connection.rollback()
    return found


@pytest.mark.usefixtures("compose_targets_validated_database")
async def test_migrate_service_applies_0001_from_scratch_and_can_run_again(
    migrator_settings: MigrationSettings, migrator_connection: AsyncConnection
) -> None:
    build = run_compose("build", "migrate")
    require(build.returncode == 0, "docker compose build migrate failed")
    run_alembic(migrator_settings, "downgrade", "base")
    assert await revisions(migrator_connection) == []
    forbidden = {"migrator DSN": migrator_settings.migrator_database_url.get_secret_value()}

    for _ in range(2):
        result = run_compose("run", "--rm", "--no-deps", "migrate")
        require(result.returncode == 0, "the migrate service did not complete successfully")
        assert_no_leaks(result.stdout + result.stderr, forbidden, "the migrate service output")

    # O serviço aplica a cadeia inteira (o head é 0002); outros testes citam este nome.
    assert await revisions(migrator_connection) == ["0002"]
