"""Engines e sessões. Criar um engine não conecta: a primeira conexão é lazy."""

import ssl
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from cce.shared.config import AdminSettings, MigrationSettings, Settings

_REMOTE_ENVIRONMENTS = ("staging", "production")


def _tls_context(environment: str, ca_file: Path | None) -> ssl.SSLContext | None:
    """Em staging e production: CA configurada, certificado e hostname verificados."""
    if environment not in _REMOTE_ENVIRONMENTS or ca_file is None:
        return None
    context = ssl.create_default_context(cafile=str(ca_file))
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return context


def runtime_connect_args(settings: Settings) -> dict[str, Any]:
    arguments: dict[str, Any] = {"command_timeout": settings.db_command_timeout}
    context = _tls_context(settings.env, settings.database_ca_file)
    if context is not None:
        arguments["ssl"] = context
    return arguments


def migration_connect_args(settings: MigrationSettings | AdminSettings) -> dict[str, Any]:
    # Sem command_timeout: migrations e a CLI podem rodar comandos longos.
    context = _tls_context(settings.env, settings.database_ca_file)
    return {"ssl": context} if context is not None else {}


def build_engine(settings: Settings) -> AsyncEngine:
    """Engine do runtime (`cce_app`), com pool."""
    return create_async_engine(
        settings.database_url.get_secret_value(),
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
        pool_pre_ping=True,
        echo=False,
        hide_parameters=True,
        connect_args=runtime_connect_args(settings),
    )


def build_migration_engine(settings: MigrationSettings | AdminSettings) -> AsyncEngine:
    """Engine de migrations e da CLI (`cce_migrator`), sem pool. Quem chama faz o `dispose`."""
    return create_async_engine(
        settings.migrator_database_url.get_secret_value(),
        poolclass=NullPool,
        echo=False,
        hide_parameters=True,
        connect_args=migration_connect_args(settings),
    )


def build_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
