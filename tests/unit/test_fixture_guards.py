"""As fixtures de banco recusam qualquer ambiente que não seja o descartável de teste.

Roda testes reais em um subprocesso apontando `CCE_ENV_FILE` para um dotenv de outro ambiente. A
recusa tem de vir do guard (antes de qualquer conexão, reset ou downgrade) e sem mostrar o DSN.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from db_support import ROOT

PASSWORD = "fixture-guard-password-SENTINEL"
PEPPER = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="

# Cada alvo usa uma fixture diferente do banco; o último faria downgrade se o guard falhasse.
TARGETS = [
    "tests/integration/test_db_roles.py::test_product_roles_have_the_matrix_attributes",
    "tests/integration/test_schema_constraints.py::test_api_key_with_valid_values_is_accepted",
    "tests/integration/test_migrate_service.py::"
    "test_migrate_service_applies_0001_from_scratch_and_can_run_again",
    "tests/migrations/test_migration_chain.py::test_empty_to_0001_to_base_to_0001",
]


def write_dotenv(path: Path, environment: str, database: str) -> None:
    lines = [
        f"CCE_ENV={environment}",
        f"CCE_DATABASE_URL=postgresql+asyncpg://cce_app:{PASSWORD}@db.guard.invalid:5432/{database}",
        "CCE_MIGRATOR_DATABASE_URL="
        f"postgresql+asyncpg://cce_migrator:{PASSWORD}@db.guard.invalid:5432/{database}",
        "CCE_REDIS_URL=redis://redis.guard.invalid:6379/0",
        f"""CCE_API_KEY_PEPPERS='{{"1":"{PEPPER}"}}'""",
    ]
    path.write_text("\n".join(lines) + "\n")


def run_target(target: str, dotenv: Path) -> str:
    environment = {k: v for k, v in os.environ.items() if not k.upper().startswith("CCE_")}
    environment["CCE_ENV_FILE"] = str(dotenv)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", target, "-q", "-p", "no:cacheprovider", "-vv"],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
        env=environment,
    )
    output = result.stdout + result.stderr
    if result.returncode == 0:
        pytest.fail("the guarded test ran against a non-test environment")
    return output


@pytest.mark.parametrize("target", TARGETS)
def test_fixtures_abort_outside_the_test_environment_without_showing_the_dsn(
    tmp_path: Path, target: str
) -> None:
    dotenv = tmp_path / "local.env"
    write_dotenv(dotenv, "local", "cce")

    output = run_target(target, dotenv)

    for secret in (PASSWORD, "db.guard.invalid", "cce_app:", "cce_migrator:"):
        if secret in output:
            pytest.fail("the guard output exposes part of the DSN")
    assert "refusing to run" in output
    assert "CCE_ENV=test" in output


@pytest.mark.parametrize("target", TARGETS)
def test_fixtures_abort_in_the_test_environment_with_a_non_test_database(
    tmp_path: Path, target: str
) -> None:
    dotenv = tmp_path / "test.env"
    write_dotenv(dotenv, "test", "cce")

    output = run_target(target, dotenv)

    for secret in (PASSWORD, "db.guard.invalid"):
        if secret in output:
            pytest.fail("the guard output exposes part of the DSN")
    assert "cannot load" in output or "refusing to run" in output
