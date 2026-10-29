"""Guard de banco descartável e execução do Alembic para os testes.

Nada aqui mostra o DSN: as mensagens de recusa citam só o papel (`runtime`, `migrator`), e a saída
bruta do Alembic nunca é repassada ao pytest.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import quote

import pytest
from integration.support import assert_no_leaks, require
from sqlalchemy.engine import URL, make_url

from cce.shared.config import MigrationSettings
from cce.shared.db.disposable import UnsafeDatabaseError
from cce.shared.db.disposable import assert_disposable_database as _assert_disposable_database

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE_HINT = "run from the repository root with CCE_ENV_FILE=.env.test"
# O subprocesso do Alembic recebe só isto do ambiente do pytest; todo o resto é montado à mão.
PASSTHROUGH = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT")


def assert_disposable_database(environment: str, url: str | URL, role: str) -> None:
    """Aborta o teste, sem mostrar o DSN, se não for o banco descartável de teste."""
    try:
        _assert_disposable_database(environment, url, role)
    except UnsafeDatabaseError as error:
        pytest.fail(f"{error} ({ENV_FILE_HINT})", pytrace=False)


def sensitive_components(dsn: str) -> dict[str, str]:
    """Pedaços do DSN que nenhuma saída de teste pode conter (rótulo -> valor)."""
    url = make_url(dsn)
    password = url.password or ""
    return {
        "DSN": dsn,
        "normalized DSN": url.render_as_string(hide_password=False),
        "password": password,
        "URL-encoded password": quote(password, safe=""),
        "user and password": f"{url.username}:{password}" if password else "",
        "database host": url.host or "",
    }


def alembic_environment(settings: MigrationSettings, ambient: Mapping[str, str]) -> dict[str, str]:
    """Ambiente do subprocesso do Alembic: exatamente os settings já validados.

    Nada de `CCE_*` do ambiente do pytest (nem dotenv) chega ao `env.py`; o segredo vai pelo
    ambiente do processo, nunca pelo argv.
    """
    environment = {name: ambient[name] for name in PASSTHROUGH if name in ambient}
    environment["CCE_ENV"] = settings.env
    environment["CCE_ENV_FILE"] = os.devnull
    environment["CCE_MIGRATOR_DATABASE_URL"] = settings.migrator_database_url.get_secret_value()
    if settings.database_ca_file is not None:
        environment["CCE_DATABASE_CA_FILE"] = str(settings.database_ca_file)
    return environment


def run_alembic(
    settings: MigrationSettings, *arguments: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    """Roda `alembic -c alembic.ini ...` como subprocesso, só contra o banco descartável.

    O processo executa a configuração validada aqui; o `env.py` ainda repete a guarda no
    `downgrade`. A saída é examinada em busca de componentes do DSN antes de qualquer outra coisa
    e nunca é repassada ao pytest.
    """
    dsn = settings.migrator_database_url.get_secret_value()
    assert_disposable_database(settings.env, dsn, "migrator")
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ROOT / "alembic.ini"), *arguments],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
        env=alembic_environment(settings, os.environ),
    )
    assert_no_leaks(
        result.stdout + result.stderr, sensitive_components(dsn), f"alembic {arguments[0]} output"
    )
    if check:
        require(
            result.returncode == 0,
            f"alembic {arguments[0]} failed (exit code {result.returncode}); its output is "
            "withheld, so re-run the command by hand to read it",
        )
    return result
