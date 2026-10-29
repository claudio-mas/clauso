"""Guard de banco descartável e execução do Alembic para os testes.

Nada aqui mostra o DSN: as mensagens de recusa citam só o papel (`runtime`, `migrator`), e a saída
bruta do Alembic nunca é repassada ao pytest.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from integration.support import assert_no_leaks, require, run_compose
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


_LOOPBACK = ("127.0.0.1", "localhost", "::1")


def compose_target_problems(config: Mapping[str, Any], validated: URL) -> list[str]:
    """Diferenças entre o Compose renderizado e o banco descartável já validado.

    Devolve só frases fixas: nenhum valor da configuração (DSN, senha) entra nelas.
    """
    services = config.get("services") or {}
    migrate = (services.get("migrate") or {}).get("environment") or {}
    postgres = services.get("postgres") or {}
    postgres_database = str((postgres.get("environment") or {}).get("POSTGRES_DB") or "")
    validated_database = validated.database or ""
    problems: list[str] = []

    migrate_url = str(migrate.get("CCE_MIGRATOR_DATABASE_URL") or "")
    try:
        _assert_disposable_database(
            str(migrate.get("CCE_ENV") or ""), migrate_url, "Compose migrate service"
        )
        migrate_database = make_url(migrate_url).database or ""
    except UnsafeDatabaseError as error:
        problems.append(str(error))
        migrate_database = ""
    if migrate_database and migrate_database != validated_database:
        problems.append("the Compose migrate service database differs from the validated one")

    # O banco validado já passou pela guarda (prefixo cce_test); igualdade basta para o postgres.
    if postgres_database != validated_database:
        problems.append("the Compose postgres service database differs from the validated one")

    published = {
        str(port.get("published"))
        for port in postgres.get("ports") or []
        if port.get("target") == 5432 and port.get("host_ip") in _LOOPBACK
    }
    if validated.host not in _LOOPBACK or str(validated.port or 5432) not in published:
        problems.append("the validated connection does not reach the Compose postgres service")
    return problems


def assert_compose_targets_validated_database(settings: MigrationSettings) -> None:
    """Aborta, antes de qualquer etapa mutante, se o Compose mira outro banco que o validado.

    `run_compose` descarta os `CCE_*` do processo e volta ao dotenv do Compose, enquanto as
    fixtures validam dotenv mais overrides. Só `docker compose config` roda aqui, e sua saída é
    lida em memória, nunca impressa.
    """
    dsn = settings.migrator_database_url.get_secret_value()
    assert_disposable_database(settings.env, dsn, "migrator")
    result = run_compose("config", "--format", "json")
    require(result.returncode == 0, "docker compose config failed; refusing to touch the database")
    try:
        config = json.loads(result.stdout)
    except ValueError:
        config = None
    require(isinstance(config, dict), "docker compose config is unreadable; refusing to continue")
    problems = compose_target_problems(config, make_url(dsn))
    require(
        not problems,
        "refusing to run: the Compose configuration does not target the validated disposable "
        f"database ({'; '.join(problems)})",
    )
