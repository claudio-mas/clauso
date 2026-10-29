"""A falha de um teste que usa credenciais do banco nunca imprime a senha.

Roda pytest de verdade (`-vv -rA --tb=long --showlocals`) sobre um módulo sintético com o plugin
`redaction_plugin` carregado. As sentinelas são geradas a cada execução, e a configuração delas
vem do ambiente do subprocesso e de um dotenv temporário, como na suíte real.
"""

from __future__ import annotations

import os
import re
import secrets
import subprocess
import sys
import textwrap
from pathlib import Path
from urllib.parse import quote

import pytest
import redaction_plugin
from db_support import ROOT

pytestmark = pytest.mark.sensitive

SYNTHETIC = textwrap.dedent(
    """
    import os
    import sys

    import pytest

    DSN = os.environ["CCE_DATABASE_URL"]
    PASSWORD = os.environ["CCE_APP_DB_PASSWORD"]
    FROM_DOTENV = os.environ["SENTINEL_FROM_DOTENV"]


    def connect(parameters):
        raise RuntimeError("could not connect")


    def test_driver_argument_with_the_dsn_password():
        connect(f"user=cce_app password={PASSWORD}")


    def test_dsn_in_a_local_variable():
        url = DSN
        raise ConnectionError("password authentication failed")


    def test_exception_message_with_the_password():
        raise ValueError(f"bad credentials: {PASSWORD}")


    def test_captured_output_with_the_password():
        print(f"debug: {PASSWORD}")
        sys.stderr.write(f"stderr: {PASSWORD}\\n")
        raise RuntimeError("failed after printing")


    def test_secret_that_only_the_dotenv_defines():
        connect(FROM_DOTENV)


    def test_assertion_on_the_secret():
        assert PASSWORD == "never"


    def test_plain_failure_keeps_its_diagnosis():
        marker = "visible-diagnosis-value"
        assert marker == "other"


    def test_passing_test_is_untouched():
        print(PASSWORD)
    """
)


def run_synthetic(tmp_path: Path, secrets_by_label: dict[str, str]) -> str:
    module = tmp_path / "test_synthetic.py"
    module.write_text(SYNTHETIC)
    dotenv = tmp_path / "synthetic.env"
    dotenv.write_text(f"CCE_MIGRATOR_DB_PASSWORD='{secrets_by_label['dotenv']}'\n")
    encoded_url_password = quote(secrets_by_label["url"], safe="")
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "PYTHONPATH": str(ROOT / "tests"),
        "CCE_ENV_FILE": dotenv.name,
        "CCE_APP_DB_PASSWORD": secrets_by_label["password"],
        "CCE_DATABASE_URL": f"postgresql+asyncpg://cce_app:{encoded_url_password}@h/db",
        "SENTINEL_FROM_DOTENV": secrets_by_label["dotenv"],
    }
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(module),
            "-p",
            "redaction_plugin",
            "-vv",
            "-rA",
            "--tb=long",
            "--showlocals",
            "-p",
            "no:cacheprovider",
            "--rootdir",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
        env=environment,
    )
    return result.stdout + result.stderr


def sentinels() -> dict[str, str]:
    return {
        "password": "pw-" + secrets.token_hex(12),
        "url": "url:pw/" + secrets.token_hex(12),
        "dotenv": "env-" + secrets.token_hex(12),
    }


def test_no_report_shows_a_credential_of_the_configuration(tmp_path: Path) -> None:
    found = sentinels()

    output = run_synthetic(tmp_path, found)

    leaked = [label for label, value in found.items() if value in output]
    if leaked or quote(found["url"], safe="") in output:
        pytest.fail(
            f"a failure printed a credential: {', '.join(leaked) or 'url (encoded)'}", pytrace=False
        )


def test_the_diagnosis_survives_and_outcomes_are_unchanged(tmp_path: Path) -> None:
    output = run_synthetic(tmp_path, sentinels())

    if "visible-diagnosis-value" not in output:
        pytest.fail("the redaction hid the diagnosis of an unrelated failure", pytrace=False)
    if "***" not in output:
        pytest.fail("the credentials were not replaced by a mask", pytrace=False)
    summary = output.splitlines()[-1] if output else ""
    counts = {word: int(n) for n, word in re.findall(r"(\d+) (failed|passed)", summary)}
    if counts != {"failed": 7, "passed": 1}:
        pytest.fail("the redaction changed the outcomes of the synthetic tests", pytrace=False)


def test_secrets_come_from_passwords_peppers_and_urls_only() -> None:
    found = redaction_plugin.collect_secrets(
        {
            "CCE_APP_DB_PASSWORD": "senha-do-app",
            "CCE_API_KEY_PEPPERS": '{"1": "pepper-um-base64", "2": "pepper-dois-base64"}',
            "CCE_DATABASE_URL": "postgresql+asyncpg://cce_app:p%40ss-url-123@host:5432/cce_test",
            "CCE_REDIS_URL": "redis://redis:6379/0",
            "CCE_ENV": "testing-env-value",
            "CCE_LOG_LEVEL": "INFO-LEVEL",
            "OTHER_PASSWORD": "not-a-cce-variable",
            "CCE_SHORT_PASSWORD": "abc",
        },
        None,
    )

    assert set(found) == {
        "senha-do-app",
        '{"1": "pepper-um-base64", "2": "pepper-dois-base64"}',
        "pepper-um-base64",
        "pepper-dois-base64",
        "p%40ss-url-123",
        "p@ss-url-123",
    }
    assert found == tuple(sorted(found, key=len, reverse=True))
