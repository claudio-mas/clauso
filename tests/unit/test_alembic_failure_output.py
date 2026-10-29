"""Falhas do Alembic nunca imprimem componentes sensíveis do DSN, nem isolados.

Roda `pytest` de verdade sobre um teste sintético que força saídas do Alembic contendo só a senha,
a senha codificada, `usuário:senha`, a URL normalizada ou o host, em stdout e em stderr, com
código de saída 0 e 1. Depois examina tudo o que o pytest imprimiu (stdout, stderr e traceback).
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path
from urllib.parse import quote

import pytest
from db_support import ROOT

PASSWORD = "p@ss/w%rd-ISOLATED-SENTINEL"
USER = "cce_migrator"
HOST = "db.failure.invalid"
DSN = f"postgresql+asyncpg://{USER}:{quote(PASSWORD, safe='')}@{HOST}:5432/cce_test"

INNER = textwrap.dedent(
    """
    import os
    import subprocess
    import sys
    from urllib.parse import quote

    sys.path.insert(0, os.environ["TESTS_DIR"])

    import pytest
    from db_support import run_alembic
    from sqlalchemy.engine import make_url

    from cce.shared.config import load_migration_settings

    PASSWORD = os.environ["SENTINEL_PASSWORD"]
    URL = make_url(os.environ["CCE_MIGRATOR_DATABASE_URL"])
    FORMS = {
        "password": PASSWORD,
        "encoded-password": quote(PASSWORD, safe=""),
        "user-and-password": f"{URL.username}:{PASSWORD}",
        "normalized-url": URL.render_as_string(hide_password=False),
        "raw-url": os.environ["CCE_MIGRATOR_DATABASE_URL"],
        "host": URL.host,
    }


    @pytest.mark.parametrize("exit_code", [0, 1])
    @pytest.mark.parametrize("stream", ["stdout", "stderr"])
    @pytest.mark.parametrize("form", list(FORMS))
    def test_alembic_output_with_a_sensitive_component(monkeypatch, form, stream, exit_code):
        leaking = f"line before\\n{FORMS[form]}\\nline after\\n"
        output = {"stdout": "", "stderr": ""}
        output[stream] = leaking

        def fake_run(argv, **options):
            return subprocess.CompletedProcess(argv, exit_code, output["stdout"], output["stderr"])

        monkeypatch.setattr(subprocess, "run", fake_run)

        run_alembic(load_migration_settings(), "current")


    @pytest.mark.parametrize("stream", ["stdout", "stderr"])
    def test_failing_alembic_with_clean_output(monkeypatch, stream):
        output = {"stdout": "", "stderr": ""}
        output[stream] = "something went wrong\\n"

        def fake_run(argv, **options):
            return subprocess.CompletedProcess(argv, 1, output["stdout"], output["stderr"])

        monkeypatch.setattr(subprocess, "run", fake_run)

        run_alembic(load_migration_settings(), "current")
    """
)


def test_failures_print_neither_isolated_secrets_nor_the_raw_output(tmp_path: Path) -> None:
    inner = tmp_path / "test_inner.py"
    inner.write_text(INNER)
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "TESTS_DIR": str(ROOT / "tests"),
        "SENTINEL_PASSWORD": PASSWORD,
        "CCE_ENV": "test",
        "CCE_ENV_FILE": os.devnull,
        "CCE_MIGRATOR_DATABASE_URL": DSN,
    }

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(inner),
            "-vv",
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
    output = result.stdout + result.stderr

    leaked = [
        label
        for label, value in {
            "password": PASSWORD,
            "encoded password": quote(PASSWORD, safe=""),
            "user and password": f"{USER}:{PASSWORD}",
            "host": HOST,
            "line before/after of the raw output": "line before",
            "something went wrong": "something went wrong",
        }.items()
        if value in output
    ]
    if leaked:
        pytest.fail(f"pytest printed: {', '.join(leaked)}")
    # Todos os 26 casos têm de falhar: nenhuma saída com componente sensível pode passar.
    if result.returncode == 0 or " 26 failed" not in output:
        pytest.fail("the synthetic tests did not all fail as expected")
