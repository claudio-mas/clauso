from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from db_support import (
    assert_compose_targets_validated_database,
    assert_disposable_database,
    run_alembic,
)
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from cce.shared.config import (
    ConfigurationError,
    MigrationSettings,
    Settings,
    load_migration_settings,
    load_settings,
)

ENV_FILE_HINT = "run from the repository root with CCE_ENV_FILE=.env.test"


@pytest.fixture
def clean_cce_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Isola o teste do ambiente real: sem variáveis CCE_* e com um diretório de trabalho vazio.

    Devolve o diretório de trabalho, onde um `.env` de teste pode ser criado.
    """
    for name in list(os.environ):
        if name.upper().startswith("CCE_"):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def db_settings() -> Settings:
    """Settings de runtime. Só roda com CCE_ENV=test e banco `cce_test*`; nunca mostra o DSN."""
    try:
        settings = load_settings()
    except ConfigurationError as error:
        pytest.fail(f"cannot load settings ({ENV_FILE_HINT}): {error}", pytrace=False)
    assert_disposable_database(settings.env, settings.database_url.get_secret_value(), "runtime")
    return settings


@pytest.fixture
def migrator_settings() -> MigrationSettings:
    """Settings do migrator, com o mesmo guard do banco descartável."""
    return _guarded_migration_settings()


def _guarded_migration_settings() -> MigrationSettings:
    try:
        settings = load_migration_settings()
    except ConfigurationError as error:
        pytest.fail(f"cannot load migration settings ({ENV_FILE_HINT}): {error}", pytrace=False)
    assert_disposable_database(
        settings.env, settings.migrator_database_url.get_secret_value(), "migrator"
    )
    return settings


@pytest.fixture(scope="module")
def migrated_database() -> None:
    """Leva o banco descartável ao head das migrations (idempotente)."""
    run_alembic(_guarded_migration_settings(), "upgrade", "head")


@pytest.fixture
def compose_targets_validated_database(migrator_settings: MigrationSettings) -> None:
    """Exige que o Compose mire o banco já validado. Peça esta fixture ANTES de qualquer conexão
    ou etapa mutante via Compose (build, run, exec) e antes de `downgrade`.
    """
    assert_compose_targets_validated_database(migrator_settings)


def _engine(url: str | URL) -> AsyncEngine:
    return create_async_engine(url, poolclass=NullPool, connect_args={"timeout": 10})


@pytest.fixture
async def app_connection(db_settings: Settings) -> AsyncIterator[AsyncConnection]:
    """Conexão real como `cce_app`, o role de runtime."""
    engine = _engine(db_settings.database_url.get_secret_value())
    try:
        async with engine.connect() as connection:
            yield connection
    finally:
        await engine.dispose()


@pytest.fixture
async def migrator_connection(
    migrator_settings: MigrationSettings,
) -> AsyncIterator[AsyncConnection]:
    """Conexão real como `cce_migrator`, o dono do schema."""
    engine = _engine(migrator_settings.migrator_database_url.get_secret_value())
    try:
        async with engine.connect() as connection:
            yield connection
    finally:
        await engine.dispose()
