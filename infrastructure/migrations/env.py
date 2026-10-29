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

from cce.shared.config import MigrationSettings, load_migration_settings
from cce.shared.db.base import SCHEMA
from cce.shared.db.disposable import assert_disposable_database
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


def _guard_destructive_commands(settings: MigrationSettings) -> None:
    """`downgrade` só roda em CCE_ENV=test e banco descartável `cce_test*` (plano, seção 9.3).

    Calculado sobre a configuração efetiva do engine, antes de qualquer conexão. Sem CLI
    (`command.downgrade(...)` chamado por código) a direção é desconhecida: falha fechado.
    """
    options = context.config.cmd_opts
    command = getattr(options, "cmd", None)
    name = command[0].__name__ if command else None
    if name is None or name == "downgrade":
        assert_disposable_database(
            settings.env, settings.migrator_database_url.get_secret_value(), "migrator"
        )


def run_migrations_offline() -> None:
    """Só gera SQL; não prova que a migration executa."""
    settings = load_migration_settings()
    _guard_destructive_commands(settings)
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
    settings = load_migration_settings()
    _guard_destructive_commands(settings)
    engine = build_migration_engine(settings)
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
