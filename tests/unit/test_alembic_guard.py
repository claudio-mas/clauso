"""O próprio processo do Alembic recusa `downgrade` fora do banco descartável.

Chama `python -m alembic` direto (sem passar por `run_alembic`), com ambiente montado à mão e um
host que não resolve: se a recusa não vier antes de conectar, o erro seria de conexão.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from db_support import ROOT

PASSWORD = "alembic-guard-password-SENTINEL"
HOST = "db.guard.invalid"


def alembic(
    environment: str, database: str, *arguments: str, query: str = "", ca_file: Path | None = None
) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "CCE_ENV": environment,
        "CCE_MIGRATOR_DATABASE_URL": (
            f"postgresql+asyncpg://cce_migrator:{PASSWORD}@{HOST}:5432/{database}{query}"
        ),
    }
    if environment not in {"staging", "production"}:
        env["CCE_ENV_FILE"] = os.devnull  # nesses ambientes o dotenv é proibido
    if ca_file is not None:
        env["CCE_DATABASE_CA_FILE"] = str(ca_file)  # staging e production exigem a CA para carregar
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ROOT / "alembic.ini"), *arguments],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
        env=env,
    )


def combined(result: subprocess.CompletedProcess[str]) -> str:
    output = result.stdout + result.stderr
    if PASSWORD in output:
        pytest.fail("the alembic output exposes the password")
    return output


DOWNGRADES = [("downgrade", "base"), ("downgrade", "-1"), ("downgrade", "0001:base", "--sql")]


@pytest.mark.parametrize("arguments", DOWNGRADES)
@pytest.mark.parametrize("environment", ["local", "staging", "production"])
def test_downgrade_is_refused_outside_the_test_environment(
    tmp_path: Path, environment: str, arguments: tuple[str, ...]
) -> None:
    ca_file = tmp_path / "ca.pem"
    ca_file.write_text("not a real certificate")
    result = alembic(environment, "cce", *arguments, ca_file=ca_file)

    output = combined(result)
    assert result.returncode != 0
    assert "refusing to run" in output
    assert "CCE_ENV=test" in output


@pytest.mark.parametrize(
    "query", ["?database=cce", "?host=other.invalid", "?user=postgres", "?dsn=x"]
)
def test_downgrade_is_refused_when_the_query_could_redirect_the_connection(query: str) -> None:
    result = alembic("test", "cce_test", "downgrade", "base", query=query)

    output = combined(result)
    assert result.returncode != 0
    assert "refusing to run" in output
    assert "query" in output


@pytest.mark.parametrize("database", ["cce", "postgres", "test_cce", "ccetest", "cce-test"])
def test_downgrade_is_refused_in_the_test_environment_outside_a_cce_test_database(
    database: str,
) -> None:
    """Recusado ainda antes da guarda do `env.py`: a configuração de teste nem carrega."""
    result = alembic("test", database, "downgrade", "base")

    output = combined(result)
    assert result.returncode != 0
    assert "must name a database starting with cce_test" in output
    assert "Running downgrade" not in output


def test_downgrade_is_not_refused_in_the_disposable_environment() -> None:
    """A recusa não pode atrapalhar o uso legítimo: aqui o erro é só de conexão (host inválido)."""
    result = alembic("test", "cce_test", "downgrade", "base")

    output = combined(result)
    assert result.returncode != 0
    assert "refusing to run" not in output


@pytest.mark.parametrize("arguments", [("upgrade", "head"), ("current",), ("check",)])
def test_other_commands_still_run_outside_the_test_environment(arguments: tuple[str, ...]) -> None:
    result = alembic("local", "cce", *arguments)

    output = combined(result)
    assert result.returncode != 0  # sem banco: erro de conexão, não recusa
    assert "refusing to run" not in output


@pytest.mark.parametrize("function", ["downgrade", "upgrade"])
def test_programmatic_calls_fail_closed_because_the_direction_is_unknown(function: str) -> None:
    """`command.*` chamado por código não traz a direção do CLI: sem banco descartável, recusa."""
    target = "base" if function == "downgrade" else "head"
    script = (
        "from alembic import command\n"
        "from alembic.config import Config\n"
        f"command.{function}(Config('alembic.ini'), '{target}')\n"
    )
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "CCE_ENV": "local",
        "CCE_ENV_FILE": os.devnull,
        "CCE_MIGRATOR_DATABASE_URL": (
            f"postgresql+asyncpg://cce_migrator:{PASSWORD}@{HOST}:5432/cce"
        ),
    }
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
        env=env,
    )

    output = combined(result)
    assert result.returncode != 0
    assert "refusing to run" in output
