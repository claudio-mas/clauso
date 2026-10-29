"""Guard de banco descartável e execução do Alembic para os testes.

Nada aqui mostra o DSN: as mensagens de recusa citam só o papel (`runtime`, `migrator`).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

from cce.shared.config import MigrationSettings

ROOT = Path(__file__).resolve().parents[1]
DISPOSABLE_PREFIX = "cce_test"


def assert_disposable_database(environment: str, url: str | URL, role: str) -> None:
    """Aborta o teste, sem mostrar o DSN, se não for o banco descartável de teste.

    Exige `CCE_ENV=test` e, no nome do banco obtido ao parsear a URL (não por substring), o
    prefixo `cce_test`. Vale para qualquer reset, downgrade ou escrita feita pelos testes.
    """
    if environment != "test":
        pytest.fail(
            f"refusing to run: the {role} configuration is not CCE_ENV=test "
            "(run from the repository root with CCE_ENV_FILE=.env.test)",
            pytrace=False,
        )
    try:
        database = (make_url(url) if isinstance(url, str) else url).database or ""
    except ArgumentError:
        pytest.fail(f"refusing to run: the {role} database URL cannot be parsed", pytrace=False)
    if not database.startswith(DISPOSABLE_PREFIX):
        pytest.fail(
            f"refusing to run: the {role} database name must start with {DISPOSABLE_PREFIX}",
            pytrace=False,
        )


def run_alembic(
    settings: MigrationSettings, *arguments: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    """Roda `alembic -c alembic.ini ...` como subprocesso, só contra o banco descartável.

    O DSN chega ao env.py pelo ambiente (`CCE_ENV_FILE`), nunca pelo argv.
    """
    assert_disposable_database(
        settings.env, settings.migrator_database_url.get_secret_value(), "migrator"
    )
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ROOT / "alembic.ini"), *arguments],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
        env=dict(os.environ),
    )
    if check and result.returncode != 0:
        dsn = settings.migrator_database_url.get_secret_value()
        output = result.stdout + result.stderr
        detail = "output withheld: it contains the DSN" if dsn in output else output[-1500:]
        pytest.fail(
            f"alembic {arguments[0]} failed (exit {result.returncode}):\n{detail}", pytrace=False
        )
    return result
