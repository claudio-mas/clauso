"""O marcador `sensitive` esconde a chave em qualquer tipo de falha, e só nos testes marcados.

Roda pytest de verdade (`-vv --tb=long --showlocals`) sobre um módulo sintético, com o plugin
`sensitive_plugin` carregado, e procura a sentinela na saída.
"""

from __future__ import annotations

import os
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
    import os
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


def test_marked_tests_never_show_the_key_in_any_kind_of_failure(tmp_path: Path) -> None:
    key = "cce_tst_" + secrets.token_hex(16) + "_" + secrets.token_urlsafe(32)
    other = "unmarked-" + secrets.token_hex(8)

    output = run_synthetic(tmp_path, key, other)

    if key in output or key.split("_", 3)[3] in output:
        pytest.fail("a failure of a sensitive test printed the key", pytrace=False)
    # 5 testes sensíveis falham, 1 dá erro na fixture, e o teste sem marcador também falha
    if "6 failed, 1 error" not in output:
        pytest.fail("the synthetic tests did not all fail as expected", pytrace=False)
    if "the fixed message of require" not in output:
        pytest.fail("the fixed message of a require failure was lost", pytrace=False)
    if other not in output:
        pytest.fail("the hook also hid the traceback of an unmarked test", pytrace=False)
