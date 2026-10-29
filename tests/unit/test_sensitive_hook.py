"""O marcador `sensitive` esconde a chave em qualquer tipo de falha, e só nos testes marcados.

Roda pytest de verdade (`-vv --tb=long --showlocals`) sobre um módulo sintético, com o plugin
`sensitive_plugin` carregado, e procura a sentinela na saída.
"""

from __future__ import annotations

import os
import re
import secrets
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from db_support import ROOT

pytestmark = pytest.mark.sensitive

SYNTHETIC = textwrap.dedent(
    """
    import logging
    import os
    import sys

    import pytest
    from integration.support import require

    KEY = os.environ["SENTINEL_KEY"]
    OTHER = os.environ["SENTINEL_OTHER"]

    sensitive = pytest.mark.sensitive


    @pytest.fixture
    def broken_fixture():
        raise RuntimeError(f"fixture failed for {KEY}")


    @sensitive
    def test_unexpected_exception_with_the_key_in_its_message():
        raw = KEY
        raise ValueError(f"unexpected failure for {raw}")


    @sensitive
    def test_did_not_raise_with_the_key_in_a_local():
        raw = KEY
        with pytest.raises(ValueError):
            len(raw)


    @sensitive
    def test_prints_the_key_then_fails(capsys):
        print(f"debug: {KEY}")
        raise RuntimeError("failed after printing")


    @sensitive
    def test_introspective_assert_on_the_key():
        raw = KEY
        assert raw.startswith("not-the-prefix")


    @sensitive
    def test_error_in_a_fixture(broken_fixture):
        pass


    @sensitive
    def test_fixed_message_is_kept():
        raw = KEY
        require(raw == "never", "the fixed message of require")


    @pytest.fixture
    def teardown_prints_then_fails():
        yield
        print(KEY)
        require(False, "fixed teardown message")


    @pytest.fixture
    def setup_chains_then_fails():
        try:
            raise RuntimeError(f"setup failed for {KEY}")
        except RuntimeError:
            require(False, "fixed setup message")


    @sensitive
    def test_prints_the_key_then_fails_with_a_fixed_message():
        print(f"debug: {KEY}")
        require(False, "fixed message after print")


    @sensitive
    def test_stderr_and_log_then_fails_with_a_fixed_message():
        sys.stderr.write(f"stderr: {KEY}\\n")
        logging.getLogger("synthetic").warning("log: %s", KEY)
        require(False, "fixed message after stderr and log")


    @sensitive
    def test_chained_exception_then_fails_with_a_fixed_message():
        try:
            raise RuntimeError(f"original failure for {KEY}")
        except RuntimeError:
            require(False, "fixed message after a chain")


    @sensitive
    def test_teardown_prints_the_key_then_fails(teardown_prints_then_fails):
        pass


    @sensitive
    def test_setup_chains_the_key_then_fails(setup_chains_then_fails):
        pass


    @sensitive
    def test_passing_test_prints_the_key():
        print(f"debug: {KEY}")
        sys.stderr.write(f"stderr: {KEY}\\n")


    def test_unmarked_tests_still_show_their_traceback():
        other = OTHER
        assert other == "never"
    """
)


def run_synthetic(tmp_path: Path, key: str, other: str) -> str:
    module = tmp_path / "test_synthetic.py"
    module.write_text(SYNTHETIC)
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "PYTHONPATH": str(ROOT / "tests"),
        "SENTINEL_KEY": key,
        "SENTINEL_OTHER": other,
    }
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(module),
            "-p",
            "sensitive_plugin",
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


def count(output: str, word: str) -> int:
    found = re.search(rf"(\d+) {word}", output.splitlines()[-1] if output else "")
    return int(found.group(1)) if found else 0


def test_marked_tests_never_show_the_key_in_any_kind_of_failure(tmp_path: Path) -> None:
    key = "cce_tst_" + secrets.token_hex(16) + "_" + secrets.token_urlsafe(32)
    other = "unmarked-" + secrets.token_hex(8)

    output = run_synthetic(tmp_path, key, other)

    if key in output or key.split("_", 3)[3] in output:
        pytest.fail("a failure of a sensitive test printed the key", pytrace=False)
    for fixed in (
        "the fixed message of require",
        "fixed message after print",
        "fixed message after stderr and log",
        "fixed message after a chain",
        "fixed teardown message",
        "fixed setup message",
    ):
        if fixed not in output:
            pytest.fail(f"the fixed message {fixed!r} was lost", pytrace=False)
    if other not in output:
        pytest.fail("the hook also hid the traceback of an unmarked test", pytrace=False)


def test_the_hook_keeps_outcomes_and_phases(tmp_path: Path) -> None:
    key = "cce_tst_" + secrets.token_hex(16) + "_" + secrets.token_urlsafe(32)

    output = run_synthetic(tmp_path, key, "unmarked-" + secrets.token_hex(8))

    # 5 + 3 falhas de teste (call), 1 do teste sem marcador; 2 testes passam (um deles passa na
    # fase call e só falha no teardown); 3 erros de fixture (setup, setup e teardown)
    if (count(output, "failed"), count(output, "passed"), count(output, "error")) != (9, 2, 3):
        pytest.fail("the hook changed the outcomes of the synthetic tests", pytrace=False)
    if "ERROR at teardown of test_teardown_prints_the_key_then_fails" not in output:
        pytest.fail("the teardown phase was not preserved", pytrace=False)
    if "ERROR at setup of test_setup_chains_the_key_then_fails" not in output:
        pytest.fail("the setup phase was not preserved", pytrace=False)
    if "PASSED" not in output:
        pytest.fail("a passing test is no longer reported as passed", pytrace=False)
