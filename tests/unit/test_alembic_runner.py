"""`run_alembic` executa exatamente a configuração validada, em ambiente controlado."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from db_support import alembic_environment, run_alembic

from cce.shared.config import MigrationSettings, load_migration_settings

PASSWORD = "runner-password-SENTINEL"
PEPPER = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch, clean_cce_env: Path) -> MigrationSettings:
    monkeypatch.setenv("CCE_ENV", "test")
    monkeypatch.setenv(
        "CCE_MIGRATOR_DATABASE_URL",
        f"postgresql+asyncpg://cce_migrator:{PASSWORD}@db.runner.invalid:5432/cce_test",
    )
    return load_migration_settings()


AMBIENT = {
    "PATH": "/usr/bin",
    "HOME": "/home/someone",
    "LANG": "C.UTF-8",
    "PYTHONPATH": "/poison",
    "CCE_ENV": "local",
    "CCE_ENV_FILE": "/poison/.env",
    "CCE_MIGRATOR_DATABASE_URL": "postgresql+asyncpg://cce_migrator:poison@poison.invalid/cce",
    "CCE_DATABASE_URL": "postgresql+asyncpg://cce_app:poison@poison.invalid/cce",
    "CCE_API_KEY_PEPPERS": json.dumps({"1": PEPPER}),
    "CCE_DATABASE_CA_FILE": "/poison/ca.pem",
}


def test_environment_comes_from_the_validated_settings_not_from_the_ambient(
    settings: MigrationSettings,
) -> None:
    environment = alembic_environment(settings, AMBIENT)

    assert environment["CCE_ENV"] == "test"
    assert environment["CCE_MIGRATOR_DATABASE_URL"] == (
        settings.migrator_database_url.get_secret_value()
    )
    assert environment["CCE_ENV_FILE"] == "/dev/null"
    assert sorted(k for k in environment if k.upper().startswith("CCE_")) == [
        "CCE_ENV",
        "CCE_ENV_FILE",
        "CCE_MIGRATOR_DATABASE_URL",
    ]
    assert environment["PATH"] == "/usr/bin" and environment["HOME"] == "/home/someone"
    assert "PYTHONPATH" not in environment
    assert "poison" not in json.dumps(environment)


def test_run_alembic_uses_that_environment_and_keeps_the_secret_out_of_argv(
    monkeypatch: pytest.MonkeyPatch, settings: MigrationSettings
) -> None:
    recorded: dict[str, Any] = {}

    def fake_run(argv: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
        recorded.update(argv=argv, env=options["env"])
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    for name, value in AMBIENT.items():
        monkeypatch.setenv(name, value)

    run_alembic(settings, "current")

    if any(PASSWORD in part for part in recorded["argv"]):
        pytest.fail("the password is in the alembic argv")
    assert recorded["env"] == alembic_environment(settings, dict(os.environ))
    assert recorded["env"]["CCE_ENV"] == "test"
