"""O próprio processo do Alembic recusa `downgrade` fora do banco descartável.

Chama `python -m alembic` direto (sem passar por `run_alembic`), com ambiente montado à mão e um
host que não resolve: se a recusa não vier antes de conectar, o erro seria de conexão.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest
from db_support import ROOT

PASSWORD = "alembic-guard-password-SENTINEL"
HOST = "db.guard.invalid"


def alembic(
    environment: str, database: str, *arguments: str, query: str = ""
) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "CCE_ENV": environment,
        "CCE_ENV_FILE": os.devnull,
        "CCE_MIGRATOR_DATABASE_URL": (
            f"postgresql+asyncpg://cce_migrator:{PASSWORD}@{HOST}:5432/{database}{query}"
        ),
    }
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
    environment: str, arguments: tuple[str, ...]
) -> None:
    result = alembic(environment, "cce", *arguments)

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
