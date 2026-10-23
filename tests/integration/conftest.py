from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
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
def db_settings() -> Settings:
    """Settings de test. Recusa rodar contra qualquer outro ambiente (e nunca mostra o DSN)."""
    try:
        settings = load_settings()
    except ConfigurationError as error:
        pytest.fail(f"cannot load settings ({ENV_FILE_HINT}): {error}")
    if settings.env != "test":
        pytest.fail(f"integration tests need CCE_ENV=test ({ENV_FILE_HINT})")
    return settings


@pytest.fixture
def migrator_settings() -> MigrationSettings:
    try:
        settings = load_migration_settings()
    except ConfigurationError as error:
        pytest.fail(f"cannot load migration settings ({ENV_FILE_HINT}): {error}")
    if settings.env != "test":
        pytest.fail(f"integration tests need CCE_ENV=test ({ENV_FILE_HINT})")
    return settings


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
