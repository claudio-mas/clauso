"""Prova que os testes de integração nunca imprimem segredos, nem ao passar nem ao falhar.

Estes testes não dependem dos helpers de higiene que verificam: usam apenas `pytest.fail` e
sentinelas, para continuarem provando algo se esses helpers mudarem.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import subprocess
from pathlib import Path
from typing import Any

import pytest
from child_pytest import pytest_command, pytest_environment

from . import support

LEAK_ANCHOR = "      CCE_DOCS_ENABLED: $CCE_DOCS_ENABLED\n"
LEAKING_LINES = (
    "      CCE_MIGRATOR_DATABASE_URL: "
    "postgresql+asyncpg://cce_migrator:$CCE_MIGRATOR_DB_PASSWORD@postgres:5432/$CCE_POSTGRES_DB\n"
    "      LEAKED_ADMIN_PASSWORD: $CCE_POSTGRES_ADMIN_PASSWORD\n"
    "      LEAKED_APP_PASSWORD: $CCE_APP_DB_PASSWORD\n"
)


def sentinel() -> str:
    return "SENTINEL-" + secrets.token_hex(16)


def test_reapply_bootstrap_keeps_the_alternative_password_out_of_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    password = sentinel()
    calls: list[dict[str, Any]] = []

    def fake_run(argv: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
        calls.append({"argv": argv, "env": options["env"]})
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    support.reapply_bootstrap(password)

    argv, environment = calls[0]["argv"], calls[0]["env"]
    if any(password in part for part in argv):
        pytest.fail("the alternative password is in the docker compose argv")
    if password not in environment.values():
        pytest.fail("the alternative password was not passed through the subprocess environment")
    if not {"CCE_APP_DB_PASSWORD", "CCE_MIGRATOR_DB_PASSWORD"} <= set(argv):
        pytest.fail("docker compose exec must forward the variables by name only (-e NAME)")


def test_run_compose_drops_cce_variables_except_the_ones_it_is_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: dict[str, Any] = {}

    def fake_run(argv: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
        recorded.update(options["env"])
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("CCE_ENV", "test")
    monkeypatch.setenv("CCE_DATABASE_URL", "ignored")

    support.run_compose("config", extra_env={"CCE_APP_DB_PASSWORD": "allowed"})

    assert sorted(name for name in recorded if name.upper().startswith("CCE_")) == [
        "CCE_APP_DB_PASSWORD"
    ]


def test_leak_helper_failure_names_the_label_and_never_the_value() -> None:
    from .support import assert_no_leaks

    value = sentinel()

    with pytest.raises(pytest.fail.Exception) as excinfo:
        assert_no_leaks(f"before {value} after", {"database password": value}, "rendered api")

    message = str(excinfo.value)
    if value in message or value in repr(excinfo.value):
        pytest.fail("the leak helper echoed the forbidden value")
    assert "database password" in message
    assert "rendered api" in message


def test_leak_helper_is_silent_when_nothing_leaks() -> None:
    from .support import assert_no_leaks

    assert_no_leaks("nothing to see here", {"database password": sentinel()}, "rendered api")


def test_require_fails_with_the_fixed_message_only() -> None:
    from .support import require

    value = sentinel()
    require(True, "never shown")
    with pytest.raises(pytest.fail.Exception) as excinfo:
        require({"dsn": value} == {}, "dsn check failed")

    if value in str(excinfo.value):
        pytest.fail("require echoed a value")
    assert str(excinfo.value) == "dsn check failed"


@pytest.mark.integration
def test_compose_config_tests_do_not_print_secrets_when_they_fail(tmp_path: Path) -> None:
    """Roda test_compose_config.py contra um Compose que vaza de propósito e examina a saída."""
    original = (support.ROOT / "docker-compose.yml").read_text()
    assert original.count(LEAK_ANCHOR) == 1
    leaking = tmp_path / "docker-compose.leaking.yml"
    leaking.write_text(original.replace(LEAK_ANCHOR, LEAK_ANCHOR + LEAKING_LINES))
    dotenv = support.read_dotenv(support.compose_env_file())
    forbidden = {
        "postgres admin password": dotenv["CCE_POSTGRES_ADMIN_PASSWORD"],
        "app password": dotenv["CCE_APP_DB_PASSWORD"],
        "migrator password": dotenv["CCE_MIGRATOR_DB_PASSWORD"],
    }

    result = subprocess.run(
        pytest_command(
            "tests/integration/test_compose_config.py",
            "-vv",  # sem truncar a introspecção: é o pior caso para vazamento
        ),
        capture_output=True,
        text=True,
        check=False,
        cwd=support.ROOT,
        env=pytest_environment({**os.environ, "CCE_TEST_COMPOSE_FILE": str(leaking)}, tmp_path),
    )
    output = result.stdout + result.stderr

    # O Compose adulterado precisa mesmo reprovar os testes, senão a prova seria vazia.
    # `assert` aqui mostraria a saída inteira, justamente o que não pode vazar.
    if result.returncode == 0 or "FAILED" not in output:
        pytest.fail("the leaking Compose did not make test_compose_config.py fail")
    leaked = [label for label, value in forbidden.items() if value in output]
    if leaked:
        pytest.fail(f"pytest output printed: {', '.join(leaked)}")


@pytest.mark.integration
def test_compose_exec_delivers_the_variable_by_name_without_argv() -> None:
    """Sem isto, o teste de reaplicação passaria mesmo se a senha nunca chegasse ao container."""
    password = sentinel()
    digest = hashlib.sha256(password.encode()).hexdigest()

    result = support.run_compose(
        "exec",
        "-T",
        "-e",
        "CCE_APP_DB_PASSWORD",
        "postgres",
        "sh",
        "-c",
        'printf %s "$CCE_APP_DB_PASSWORD" | sha256sum | cut -d" " -f1',
        extra_env={"CCE_APP_DB_PASSWORD": password},
    )

    if result.returncode != 0 or result.stdout.strip() != digest:
        pytest.fail("the container did not receive the variable value")
    if password in " ".join(result.args):
        pytest.fail("the value is in the argv")
