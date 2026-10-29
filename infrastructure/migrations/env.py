"""Ambiente do Alembic.

Lê `MigrationSettings` (role `cce_migrator`), compara com `Base.metadata` e roda cada migration em
sua própria transação, com `NullPool`. O DSN só passa por `SecretStr` e pelo engine: não vai para
arquivo de configuração, log nem argumento. O schema nunca é criado por `Base.metadata`.
"""

import asyncio
import logging
from typing import Any

from alembic import context
from sqlalchemy.engine import Connection

from cce.shared.config import load_migration_settings
from cce.shared.db.base import SCHEMA
from cce.shared.db.engine import build_migration_engine
from cce.shared.db.models import Base

target_metadata = Base.metadata


def _configure_logging() -> None:
    # O alembic.ini não tem seções de logging, então não há configuração por arquivo. Saem só as
    # mensagens do próprio Alembic (revisões), nunca as do SQLAlchemy, que poderiam trazer SQL.
    logger = logging.getLogger("alembic")
    logger.setLevel(logging.INFO)
    if all(isinstance(handler, logging.NullHandler) for handler in logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s [alembic] %(message)s"))
        logger.addHandler(handler)


def include_name(name: str | None, type_: str, parent_names: Any) -> bool:
    """Só o schema técnico `cce` entra na comparação; objetos de outros schemas são ignorados."""
    if type_ == "schema":
        return name == SCHEMA
    return True


def _options() -> dict[str, Any]:
    return {
        "target_metadata": target_metadata,
        "include_schemas": True,
        "include_name": include_name,
        "version_table_schema": SCHEMA,
        "compare_type": True,
        "transaction_per_migration": True,
    }


def run_migrations_offline() -> None:
    """Só gera SQL; não prova que a migration executa."""
    settings = load_migration_settings()
    context.configure(
        url=settings.migrator_database_url.get_secret_value(),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **_options(),
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, **_options())
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = build_migration_engine(load_migration_settings())
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run_migrations)
    finally:
        await engine.dispose()


_configure_logging()
if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
