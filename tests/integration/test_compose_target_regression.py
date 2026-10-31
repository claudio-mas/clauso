"""Testes mutantes via Compose recusam um Compose que não mira o banco descartável já validado.

Cenário do achado: o dotenv do Compose diz `local`/`cce`, mas o ambiente do processo (que as
fixtures validam) diz `test`/`cce_test`. `run_compose` ignora os overrides e voltaria ao dotenv, de
modo que o teste faria `downgrade` no banco validado e o serviço `migrate` agiria em outro. A
recusa tem de acontecer no *setup*, antes de build, downgrade, run ou exec.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from child_pytest import pytest_command, pytest_environment

from .support import ROOT, compose_env_file, read_dotenv

pytestmark = pytest.mark.integration

MUTATING_TARGETS = [
    "tests/integration/test_migrate_service.py::"
    "test_migrate_service_applies_0001_from_scratch_and_can_run_again",
    "tests/integration/test_db_roles.py::"
    "test_bootstrap_is_reapplicable_and_never_rotates_passwords",
]


@pytest.mark.parametrize("target", MUTATING_TARGETS)
def test_compose_mutations_are_refused_when_dotenv_and_process_environment_diverge(
    tmp_path: Path, target: str
) -> None:
    diverging = tmp_path / "local.env"
    generated = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "init_env.py"),
            "--target",
            str(diverging),
            "--env",
            "local",
            "--database-name",
            "cce",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert generated.returncode == 0

    # O banco validado é o de teste de verdade (as conexões das fixtures precisam funcionar); o
    # dotenv do Compose é o divergente. Os segredos viajam pelo ambiente do subprocesso, não pelo
    # argv.
    real = read_dotenv(compose_env_file())
    environment = {k: v for k, v in os.environ.items() if not k.upper().startswith("CCE_")}
    environment.update(
        CCE_ENV_FILE=str(diverging),
        CCE_ENV="test",
        CCE_DATABASE_URL=real["CCE_DATABASE_URL"],
        CCE_MIGRATOR_DATABASE_URL=real["CCE_MIGRATOR_DATABASE_URL"],
    )
    forbidden = {
        **{
            f"generated {name}": value
            for name, value in read_dotenv(diverging).items()
            if "PASSWORD" in name or name == "CCE_API_KEY_PEPPERS"
        },
        "validated app DSN": real["CCE_DATABASE_URL"],
        "validated migrator DSN": real["CCE_MIGRATOR_DATABASE_URL"],
        "validated admin password": real["CCE_POSTGRES_ADMIN_PASSWORD"],
        "validated app password": real["CCE_APP_DB_PASSWORD"],
        "validated migrator password": real["CCE_MIGRATOR_DB_PASSWORD"],
    }

    result = subprocess.run(
        pytest_command(target, "-q", "-rA", "-vv"),
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
        env=pytest_environment(environment, tmp_path),
    )
    output = result.stdout + result.stderr

    leaked = [label for label, value in forbidden.items() if value and value in output]
    if leaked:
        pytest.fail(f"the refusal output exposes: {', '.join(leaked)}")
    if result.returncode == 0 or "ERROR at setup" not in output:
        pytest.fail("the mutating test was not refused at setup, before build/downgrade/run/exec")
    if "does not target the validated disposable database" not in output:
        pytest.fail("the setup error is not the Compose target check")


MIGRATE_DSN_FRAGMENT = "cce_migrator:$CCE_MIGRATOR_DB_PASSWORD@postgres:5432"
ALTERED_ENDPOINTS = {
    "another host": "cce_migrator:$CCE_MIGRATOR_DB_PASSWORD@other-db.invalid:5432",
    "another internal port": "cce_migrator:$CCE_MIGRATOR_DB_PASSWORD@postgres:6543",
}


@pytest.mark.parametrize("target", MUTATING_TARGETS)
@pytest.mark.parametrize("alteration", list(ALTERED_ENDPOINTS))
def test_compose_mutations_are_refused_when_the_migrate_endpoint_is_not_the_internal_postgres(
    tmp_path: Path, target: str, alteration: str
) -> None:
    """Cópia do docker-compose.yml em que só host ou só porta do DSN do `migrate` mudam.

    O banco continua `cce_test`, mas a instância é outra; como o CLI permite `upgrade`, a guarda do
    banco por nome não bastaria. A recusa tem de vir no setup, antes de build, downgrade, run e
    exec.
    """
    original = (ROOT / "docker-compose.yml").read_text()
    assert original.count(MIGRATE_DSN_FRAGMENT) == 1
    altered = tmp_path / "docker-compose.altered.yml"
    altered.write_text(original.replace(MIGRATE_DSN_FRAGMENT, ALTERED_ENDPOINTS[alteration]))
    real = read_dotenv(compose_env_file())
    environment = {k: v for k, v in os.environ.items() if not k.upper().startswith("CCE_")}
    environment.update(
        CCE_ENV_FILE=str(compose_env_file()),
        CCE_TEST_COMPOSE_FILE=str(altered),
    )
    forbidden = {
        "admin password": real["CCE_POSTGRES_ADMIN_PASSWORD"],
        "app password": real["CCE_APP_DB_PASSWORD"],
        "migrator password": real["CCE_MIGRATOR_DB_PASSWORD"],
        "migrator DSN": real["CCE_MIGRATOR_DATABASE_URL"],
        "app DSN": real["CCE_DATABASE_URL"],
        "altered host": "other-db.invalid",
    }

    result = subprocess.run(
        pytest_command(target, "-q", "-rA", "-vv"),
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
        env=pytest_environment(environment, tmp_path),
    )
    output = result.stdout + result.stderr

    leaked = [label for label, value in forbidden.items() if value and value in output]
    if leaked:
        pytest.fail(f"the refusal output exposes: {', '.join(leaked)}")
    if result.returncode == 0 or "ERROR at setup" not in output:
        pytest.fail("the mutating test was not refused at setup, before build/downgrade/run/exec")
    if "does not target the validated disposable database" not in output:
        pytest.fail("the setup error is not the Compose target check")
