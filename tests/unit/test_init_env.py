from __future__ import annotations

import base64
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from cce.shared.config import load_admin_settings, load_migration_settings, load_settings

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "init_env.py"
TEMPLATE = ROOT / ".env.example"

MARKERS = (
    "__POSTGRES_PASSWORD__",
    "__APP_PASSWORD__",
    "__MIGRATOR_PASSWORD__",
    "__PEPPER_BASE64__",
)


def run_init_env(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        check=False,
    )


def assert_refused_by_script(result: subprocess.CompletedProcess[str]) -> None:
    """Recusa feita pelo próprio script (código 1 e mensagem `init_env: ...`)."""
    assert result.returncode == 1
    assert result.stderr.startswith("init_env: ")


def assert_rejected_arguments(result: subprocess.CompletedProcess[str]) -> None:
    """Argumento inválido ou ausente, recusado pelo parser (código 2 e `usage:`)."""
    assert result.returncode == 2
    assert result.stderr.startswith("usage: init_env.py")


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        name, _, value = line.partition("=")
        values[name] = value.strip("'")
    return values


def generated_secrets(path: Path) -> list[str]:
    values = read_env(path)
    peppers = values["CCE_API_KEY_PEPPERS"]
    pepper = re.search(r'"1":"([^"]+)"', peppers)
    assert pepper is not None
    return [
        values["CCE_POSTGRES_ADMIN_PASSWORD"],
        values["CCE_APP_DB_PASSWORD"],
        values["CCE_MIGRATOR_DB_PASSWORD"],
        pepper.group(1),
    ]


def test_template_holds_only_markers_never_secrets() -> None:
    text = TEMPLATE.read_text()

    for marker in MARKERS:
        assert marker in text
    assert "CCE_ENV=local" in text
    assert "CCE_POSTGRES_DB=cce" in text


def test_generates_a_local_dotenv(tmp_path: Path) -> None:
    target = tmp_path / ".env"

    result = run_init_env("--target", str(target), "--env", "local", "--database-name", "cce")

    assert result.returncode == 0, result.stderr
    text = target.read_text()
    assert not re.search(r"__[A-Z0-9_]+__", text)
    values = read_env(target)
    assert values["CCE_ENV"] == "local"
    assert values["CCE_POSTGRES_DB"] == "cce"
    assert values["CCE_DATABASE_URL"].endswith("@127.0.0.1:5432/cce")
    assert values["CCE_MIGRATOR_DATABASE_URL"].endswith("@127.0.0.1:5432/cce")


def test_generated_secrets_are_random_and_consistent(tmp_path: Path) -> None:
    first, second = tmp_path / "first.env", tmp_path / "second.env"
    for target in (first, second):
        result = run_init_env("--target", str(target), "--env", "local", "--database-name", "cce")
        assert result.returncode == 0, result.stderr

    postgres, app, migrator, pepper = generated_secrets(first)
    for password in (postgres, app, migrator):
        assert re.fullmatch(r"[0-9a-f]{64}", password)  # secrets.token_hex(32)
    assert len({postgres, app, migrator}) == 3
    assert len(base64.b64decode(pepper, validate=True)) == 32
    values = read_env(first)
    assert f":{app}@" in values["CCE_DATABASE_URL"]
    assert f":{migrator}@" in values["CCE_MIGRATOR_DATABASE_URL"]
    assert set(generated_secrets(first)).isdisjoint(generated_secrets(second))


def test_replaces_only_the_explicit_markers(tmp_path: Path) -> None:
    target = tmp_path / ".env"
    run_init_env("--target", str(target), "--env", "local", "--database-name", "cce")

    template_lines = TEMPLATE.read_text().splitlines()
    generated_lines = target.read_text().splitlines()

    assert len(generated_lines) == len(template_lines)
    for before, after in zip(template_lines, generated_lines, strict=True):
        if any(marker in before for marker in MARKERS):
            assert before != after
            assert before.split("=")[0] == after.split("=")[0]
        else:
            assert before == after


def test_does_not_print_secrets(tmp_path: Path) -> None:
    target = tmp_path / ".env"

    result = run_init_env("--target", str(target), "--env", "local", "--database-name", "cce")

    assert result.returncode == 0
    for secret in generated_secrets(target):
        assert secret not in result.stdout
        assert secret not in result.stderr
    assert str(target) in result.stdout + result.stderr


@pytest.mark.skipif(os.name != "posix", reason="permissões POSIX")
def test_creates_the_file_with_owner_only_permissions(tmp_path: Path) -> None:
    target = tmp_path / ".env"
    run_init_env("--target", str(target), "--env", "local", "--database-name", "cce")

    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_test_environment_uses_the_test_database(tmp_path: Path) -> None:
    target = tmp_path / ".env.test"

    result = run_init_env("--target", str(target), "--env", "test", "--database-name", "cce_test")

    assert result.returncode == 0, result.stderr
    values = read_env(target)
    assert values["CCE_ENV"] == "test"
    assert values["CCE_POSTGRES_DB"] == "cce_test"
    assert values["CCE_DATABASE_URL"].endswith("/cce_test")
    assert values["CCE_MIGRATOR_DATABASE_URL"].endswith("/cce_test")


@pytest.mark.parametrize(
    ("environment", "database"), [("local", "cce"), ("test", "cce_test"), ("test", "cce_test_ci")]
)
def test_generated_dotenv_passes_settings_validation(
    monkeypatch: pytest.MonkeyPatch,
    clean_cce_env: Path,
    environment: str,
    database: str,
) -> None:
    target = clean_cce_env / "generated.env"
    run_init_env("--target", str(target), "--env", environment, "--database-name", database)
    monkeypatch.setenv("CCE_ENV_FILE", str(target))

    assert load_settings().env == environment
    assert load_migration_settings().env == environment
    assert load_admin_settings().env == environment


def test_refuses_to_overwrite_an_existing_file(tmp_path: Path) -> None:
    target = tmp_path / ".env"
    target.write_text("CCE_ENV=local\nCUSTOM=keep-me\n")

    result = run_init_env("--target", str(target), "--env", "local", "--database-name", "cce")

    assert_refused_by_script(result)
    assert target.read_text() == "CCE_ENV=local\nCUSTOM=keep-me\n"
    assert "keep-me" not in result.stdout + result.stderr


@pytest.mark.skipif(os.name != "posix", reason="symlinks POSIX")
def test_does_not_follow_a_symlink_at_the_target(tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    target = tmp_path / ".env"
    target.symlink_to(elsewhere)

    result = run_init_env("--target", str(target), "--env", "local", "--database-name", "cce")

    assert_refused_by_script(result)
    assert not elsewhere.exists()


@pytest.mark.parametrize("environment", ["staging", "production", "dev", "LOCAL", ""])
def test_accepts_only_local_and_test_environments(tmp_path: Path, environment: str) -> None:
    target = tmp_path / ".env"

    result = run_init_env("--target", str(target), "--env", environment, "--database-name", "cce")

    assert_rejected_arguments(result)
    assert not target.exists()


@pytest.mark.parametrize(
    ("environment", "database"),
    [
        ("test", "cce"),
        ("test", "other_test"),
        ("local", ""),
        ("local", "CCE"),
        ("local", "cce-prod"),
        ("local", "cce;drop"),
        ("local", "../cce"),
        ("local", "cce db"),
        ("local", "1cce"),
        ("local", "c" * 64),
    ],
)
def test_refuses_unsafe_or_mismatched_database_names(
    tmp_path: Path, environment: str, database: str
) -> None:
    target = tmp_path / ".env"

    result = run_init_env(
        "--target", str(target), "--env", environment, "--database-name", database
    )

    assert_refused_by_script(result)
    assert not target.exists()


def test_refuses_a_target_in_a_missing_directory(tmp_path: Path) -> None:
    target = tmp_path / "missing" / ".env"

    result = run_init_env("--target", str(target), "--env", "local", "--database-name", "cce")

    assert_refused_by_script(result)
    assert not target.parent.exists()


def test_requires_every_argument(tmp_path: Path) -> None:
    target = tmp_path / ".env"

    for args in (
        ["--env", "local", "--database-name", "cce"],
        ["--target", str(target), "--database-name", "cce"],
        ["--target", str(target), "--env", "local"],
    ):
        assert_rejected_arguments(run_init_env(*args))
    assert not target.exists()
