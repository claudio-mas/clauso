"""Guarda do banco descartável: operações destrutivas só em `CCE_ENV=test` e banco `cce_test*`.

Vale para `downgrade` (no `env.py` do Alembic) e para as fixtures de teste. A guarda olha o que o
driver realmente vai usar, não só `URL.database`: o asyncpg aplica a query da URL por cima do
restante (`...@h/cce_test?database=cce` conecta em `cce`), então qualquer query é recusada, e o
banco é lido de `dialect.create_connect_args`. As mensagens citam o papel e nunca o DSN.
"""

from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError, NoSuchModuleError

DISPOSABLE_PREFIX = "cce_test"


class UnsafeDatabaseError(Exception):
    """Recusa de uma operação destrutiva fora do banco descartável. Sem DSN na mensagem."""


def _effective_database(url: URL) -> str:
    try:
        _, parameters = url.get_dialect()().create_connect_args(url)
    except (ArgumentError, NoSuchModuleError, ImportError):
        raise UnsafeDatabaseError("the driver of the database URL cannot be inspected") from None
    return str(parameters.get("database") or "")


def assert_disposable_database(environment: str, url: str | URL, role: str) -> None:
    if environment != "test":
        raise UnsafeDatabaseError(f"refusing to run: the {role} configuration is not CCE_ENV=test")
    try:
        parsed = make_url(url) if isinstance(url, str) else url
    except ArgumentError:
        raise UnsafeDatabaseError(
            f"refusing to run: the {role} database URL cannot be parsed"
        ) from None
    if parsed.query:
        raise UnsafeDatabaseError(
            f"refusing to run: the {role} database URL must not carry query parameters, "
            "because the driver lets them override the database, role and host"
        )
    database = _effective_database(parsed)
    if database != (parsed.database or "") or not database.startswith(DISPOSABLE_PREFIX):
        raise UnsafeDatabaseError(
            f"refusing to run: the {role} database name must start with {DISPOSABLE_PREFIX}"
        )
