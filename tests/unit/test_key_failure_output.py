"""Quando a primitiva de chave tem um defeito, os testes o acusam sem imprimir segredo nenhum.

Roda `pytest` de verdade (`-vv --tb=long --showlocals`, o pior caso de saída) sobre
`tests/unit/test_key_security.py` com defeitos injetados por um plugin, e sobre testes sintéticos.
O plugin grava, num arquivo temporário, toda chave, segredo, pepper e digest que o processo interno
gera; depois procuramos cada um deles em stdout, stderr e traceback. Tudo é sintético.
"""

from __future__ import annotations

import os
import secrets
import subprocess
import textwrap
from pathlib import Path

import pytest
from child_pytest import pytest_command, pytest_environment
from db_support import ROOT
from integration.support import require

pytestmark = pytest.mark.sensitive

PLUGIN = textwrap.dedent(
    """
    import base64
    import os
    import secrets as std_secrets

    import cce.integrations.api_keys.service as service
    import cce.shared.security as security

    RECORD = os.environ["KEY_RECORD_FILE"]
    DEFECT = os.environ.get("KEY_DEFECT", "")


    def record(*values):
        with open(RECORD, "a", encoding="utf-8") as handle:
            for value in values:
                if value:
                    handle.write(str(value) + "\\n")


    original_token_bytes = std_secrets.token_bytes


    def token_bytes(nbytes=None):
        value = original_token_bytes(nbytes)
        if len(value) >= 16:
            record(value.hex(), base64.b64encode(value).decode(), repr(value))
        return value


    std_secrets.token_bytes = token_bytes

    original_generate = security.generate_key
    original_digest = security.digest_key
    original_parse = security.parse_key
    original_pepper = security.pepper_bytes
    original_issue = service.issue_key_material


    def generate_key(key_id, environment):
        raw = original_generate(key_id, environment)
        record(raw, raw.split("_", 3)[3])
        if DEFECT == "wrong-format":
            raw = raw + "x"
            record(raw, raw.split("_", 3)[3])
        return raw


    def digest_key(raw_key, pepper):
        digest = original_digest(raw_key, pepper)
        record(digest.hex(), repr(digest), pepper.hex())
        return digest[:-1] if DEFECT == "wrong-digest" else digest


    def parse_key(raw_key, environment):
        if DEFECT == "unexpected-exception-type":
            raise ValueError("a fixed message that has nothing to do with the key")
        if DEFECT == "unexpected-rejection":
            raise security.InvalidKeyError("invalid API key")
        try:
            return original_parse(raw_key, environment)
        except security.InvalidKeyError:
            if DEFECT == "error-echoes-key":
                raise security.InvalidKeyError(f"invalid API key: {raw_key}") from None
            raise


    def pepper_bytes(peppers, version):
        try:
            value = original_pepper(peppers, version)
        except security.UnknownPepperVersion:
            if DEFECT == "pepper-error-leaks":
                known = [p.get_secret_value() for p in peppers.values()]
                record(*known)
                raise security.UnknownPepperVersion(f"no pepper, known: {known}") from None
            raise
        record(value.hex(), repr(value))
        return value


    security.generate_key = generate_key
    security.digest_key = digest_key
    security.parse_key = parse_key
    security.pepper_bytes = pepper_bytes
    service.generate_key = generate_key
    service.digest_key = digest_key
    service.pepper_bytes = pepper_bytes

    if DEFECT == "parsed-repr-leaks":
        def leaky(self):
            return "ParsedKey(" + self.raw_key.get_secret_value() + ")"

        security.ParsedKey.__repr__ = leaky
        security.ParsedKey.__str__ = leaky

    if DEFECT == "issued-repr-leaks":
        def leaky_issued(self):
            return f"IssuedKey({self.raw_key.get_secret_value()} {self.secret_digest.hex()})"

        service.IssuedKey.__repr__ = leaky_issued
        service.IssuedKey.__str__ = leaky_issued

    GENERIC = os.environ.get("KEY_GENERIC_DEFECT", "")
    if GENERIC:
        target_name, mode = GENERIC.split(":")

        def record_arguments(args, kwargs):
            for value in [*args, *kwargs.values()]:
                if isinstance(value, str):
                    record(value, value.split("_", 3)[-1])
                elif isinstance(value, bytes):
                    record(value.hex(), base64.b64encode(value).decode(), repr(value))
                elif isinstance(value, dict):
                    for item in value.values():
                        if hasattr(item, "get_secret_value"):
                            record(item.get_secret_value())

        def wrap(original):
            def defective(*args, **kwargs):
                record_arguments(args, kwargs)
                if mode == "raises-valueerror":
                    raise ValueError(f"unexpected failure for {args!r} {kwargs!r}")
                if mode == "raises-keyerror":
                    raise KeyError(f"unexpected failure for {args!r} {kwargs!r}")
                result = original(*args, **kwargs)
                if isinstance(result, str):
                    record(result)
                elif isinstance(result, bytes):
                    record(result.hex(), repr(result))
                elif hasattr(result, "raw_key"):
                    record(result.raw_key.get_secret_value())
                    record(getattr(result, "secret_digest", b"").hex())
                replacements = {"returns-none": None, "returns-empty": "", "returns-wrong-type": 1}
                return replacements[mode]

            return defective

        for name in (target_name,):
            for module in (security, service):
                if hasattr(module, name):
                    setattr(module, name, wrap(getattr(module, name)))
    """
)

DEFECTS = [
    "wrong-format",
    "parsed-repr-leaks",
    "issued-repr-leaks",
    "wrong-digest",
    "error-echoes-key",
    "pepper-error-leaks",
    "unexpected-exception-type",
    "unexpected-rejection",
]


def run_key_tests(
    tmp_path: Path, defect: str, generic: str = ""
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Roda o arquivo de testes de chave com defeitos injetados e devolve os valores gravados.

    `KEY_TEST_TARGET` e `KEY_RECORD_DIR` (ambiente do processo externo) só existem para os
    meta-testes, que apontam um alvo vazador de propósito e precisam ler os valores gravados.
    """
    target = os.environ.get("KEY_TEST_TARGET", "tests/unit/test_key_security.py")
    record_dir = Path(os.environ.get("KEY_RECORD_DIR", str(tmp_path)))
    plugin = tmp_path / "keydefects.py"
    plugin.write_text(PLUGIN)
    record = record_dir / "recorded.txt"
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "PYTHONPATH": str(tmp_path),
        "KEY_RECORD_FILE": str(record),
        "KEY_DEFECT": defect,
        "KEY_GENERIC_DEFECT": generic,
    }
    result = subprocess.run(
        pytest_command(
            target,
            "-p",
            "keydefects",
            "-vv",
            "--tb=long",
            "--showlocals",
        ),
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
        env=pytest_environment(environment, tmp_path),
    )
    values = record.read_text().splitlines() if record.exists() else []
    # Valores forçados pelos testes (como "_" * 43) são constantes públicas, não segredos gerados.
    return result, [v for v in values if len(v) >= 8 and len(set(v)) >= 12]


def leaked_labels(output: str, recorded: list[str]) -> int:
    """Quantos valores sensíveis gerados pelo processo interno aparecem na saída."""
    return sum(1 for value in set(recorded) if value in output)


def test_the_control_run_passes_and_records_the_sensitive_values(tmp_path: Path) -> None:
    result, recorded = run_key_tests(tmp_path, "")

    if result.returncode != 0:
        pytest.fail("the key tests fail without any injected defect", pytrace=False)
    # chaves, segredos, digests e peppers de verdade
    require(len(recorded) > 500, "the control run recorded too few sensitive values")
    if leaked_labels(result.stdout + result.stderr, recorded):
        pytest.fail("a passing run printed a sensitive value", pytrace=False)


@pytest.mark.parametrize("defect", DEFECTS)
def test_a_defect_is_reported_without_printing_any_key_secret_digest_or_pepper(
    tmp_path: Path, defect: str
) -> None:
    result, recorded = run_key_tests(tmp_path, defect)
    output = result.stdout + result.stderr

    leaks = leaked_labels(output, recorded)
    if leaks:
        pytest.fail(
            f"pytest printed {leaks} sensitive value(s) while reporting the defect", pytrace=False
        )
    if result.returncode == 0 or " failed" not in output:
        pytest.fail("the injected defect was not detected by the key tests", pytrace=False)
    if len(recorded) < 100:
        pytest.fail(
            "the plugin recorded too few values for the scan to mean anything", pytrace=False
        )


# --- testes sintéticos dos padrões de falha -------------------------------------------------

SYNTHETIC = textwrap.dedent(
    """
    import os
    import sys

    sys.path.insert(0, os.environ["TESTS_DIR"])

    from integration.support import assert_no_leaks, require

    TOKEN = os.environ["SENTINEL_TOKEN"]
    DIGEST = os.environ["SENTINEL_DIGEST"]
    MODE = os.environ["SYNTHETIC_MODE"]


    class LeakyKey:
        def __repr__(self):
            return TOKEN

        __str__ = __repr__


    class LeakyIssued:
        def __repr__(self):
            return f"IssuedKey({TOKEN} {DIGEST})"

        __str__ = __repr__


    def test_key_with_the_wrong_format():
        raw = TOKEN + "x"
        require(len(raw) == 84, "the key has the wrong length")
        require(raw.startswith("cce_") and raw == TOKEN, "the key does not match")


    def test_repr_returns_the_token():
        assert_no_leaks(repr(LeakyKey()) + str(LeakyKey()), {"token": TOKEN}, "the key repr")


    def test_exception_message_contains_the_key():
        message = ""
        try:
            raise RuntimeError(f"failed for {TOKEN}")
        except RuntimeError as error:
            message = str(error)
        assert_no_leaks(message, {"token": TOKEN}, "an exception message")


    def test_issued_key_in_an_exception_message():
        message = ""
        try:
            raise RuntimeError(f"failed for {LeakyIssued()!r}")
        except RuntimeError as error:
            message = str(error)
        assert_no_leaks(message, {"token": TOKEN, "digest": DIGEST}, "an exception message")


    def test_the_old_unsafe_pattern_chains_the_leak():
        try:
            raise RuntimeError(f"failed for {TOKEN}")
        except RuntimeError as error:
            assert_no_leaks(str(error), {"token": TOKEN}, "an exception message")


    def test_the_old_introspective_assert():
        raw = TOKEN
        assert raw.startswith("not-the-prefix")
    """
)

SAFE_TESTS = [
    "test_key_with_the_wrong_format",
    "test_repr_returns_the_token",
    "test_exception_message_contains_the_key",
    "test_issued_key_in_an_exception_message",
]
OLD_PATTERNS = ["test_the_old_unsafe_pattern_chains_the_leak", "test_the_old_introspective_assert"]


def run_synthetic(tmp_path: Path, name: str, token: str, digest: str) -> str:
    module = tmp_path / "test_synthetic.py"
    module.write_text(SYNTHETIC)
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "TESTS_DIR": str(ROOT / "tests"),
        "SENTINEL_TOKEN": token,
        "SENTINEL_DIGEST": digest,
        "SYNTHETIC_MODE": name,
    }
    result = subprocess.run(
        pytest_command(
            f"{module}::{name}",
            "-vv",
            "--tb=long",
            "--showlocals",
            "--rootdir",
            str(tmp_path),
        ),
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
        env=pytest_environment(environment, tmp_path),
    )
    if result.returncode == 0:
        pytest.fail("the synthetic defect was not detected", pytrace=False)
    return result.stdout + result.stderr


def sentinel() -> tuple[str, str]:
    return "cce_tst_" + secrets.token_hex(16) + "_" + secrets.token_urlsafe(32), secrets.token_hex(
        32
    )


@pytest.mark.parametrize("name", SAFE_TESTS)
def test_synthetic_defects_are_reported_without_printing_the_key_or_the_digest(
    tmp_path: Path, name: str
) -> None:
    token, digest = sentinel()

    output = run_synthetic(tmp_path, name, token, digest)

    if token in output or digest in output or token.split("_", 3)[3] in output:
        pytest.fail("pytest printed the sentinel key, secret or digest", pytrace=False)


@pytest.mark.parametrize("name", OLD_PATTERNS)
def test_the_harness_does_see_a_leak_when_the_old_unsafe_patterns_are_used(
    tmp_path: Path, name: str
) -> None:
    """Controle: sem este, a varredura acima poderia estar passando por vazio."""
    token, digest = sentinel()

    output = run_synthetic(tmp_path, name, token, digest)

    if token not in output:
        pytest.fail(
            "the control did not reproduce the leak, so the scan above proves nothing",
            pytrace=False,
        )


# --- defeitos genéricos: qualquer função, qualquer modo -------------------------------------

GENERIC_FUNCTIONS = [
    "generate_key",
    "parse_key",
    "digest_key",
    "verify_digest",
    "pepper_bytes",
    "issue_key_material",
]
GENERIC_MODES = [
    "raises-valueerror",
    "raises-keyerror",
    "returns-none",
    "returns-empty",
    "returns-wrong-type",
]


@pytest.mark.parametrize("mode", GENERIC_MODES)
@pytest.mark.parametrize("function", GENERIC_FUNCTIONS)
def test_a_generic_defect_in_any_primitive_fails_the_tests_without_printing_any_value(
    tmp_path: Path, function: str, mode: str
) -> None:
    """Meta-teste mecânico: para cada função e modo, TODO teste do arquivo de chaves que a usa
    vê uma exceção de tipo inesperado (com os argumentos na mensagem) ou um retorno trocado.
    """
    result, recorded = run_key_tests(tmp_path, "", f"{function}:{mode}")
    output = result.stdout + result.stderr

    leaks = leaked_labels(output, recorded)
    if leaks:
        pytest.fail(
            f"pytest printed {leaks} sensitive value(s) while reporting the defect", pytrace=False
        )
    if result.returncode == 0:
        pytest.fail("the generic defect was not detected by the key tests", pytrace=False)


# --- a falha da própria regressão também não vaza -------------------------------------------

LEAKY_FAILING = textwrap.dedent(
    """
    from uuid import uuid4

    from cce.shared.security import generate_key


    def test_introspective_assert_on_a_generated_key():
        raw = generate_key(uuid4(), "test")
        assert raw.startswith("not-the-prefix")
    """
)
LEAKY_PASSING = textwrap.dedent(
    """
    import warnings
    from uuid import uuid4

    from cce.shared.security import generate_key


    def test_passes_but_warns_with_a_key():
        for _ in range(600):
            raw = generate_key(uuid4(), "test")
        warnings.warn(f"generated {raw}")
    """
)
FEW_RECORDS = textwrap.dedent(
    """
    from uuid import uuid4

    from cce.shared.security import generate_key


    def test_generates_only_one_key():
        generate_key(uuid4(), "test")
    """
)
REGRESSION_FAILURES = [
    pytest.param(
        "test_a_defect_is_reported_without and wrong-format", LEAKY_FAILING, id="accuses-a-leak"
    ),
    pytest.param("test_the_control_run", LEAKY_PASSING, id="control-sees-a-printed-value"),
    pytest.param("test_the_control_run", FEW_RECORDS, id="control-has-too-few-records"),
]


@pytest.mark.parametrize(("expression", "target_source"), REGRESSION_FAILURES)
def test_the_regression_does_not_print_the_recorded_values_when_it_fails(
    tmp_path: Path, expression: str, target_source: str
) -> None:
    """Roda a regressão contra um alvo escolhido para ela falhar, e examina a PRÓPRIA falha."""
    target = tmp_path / "test_target.py"
    target.write_text(target_source)
    records = tmp_path / "records"
    records.mkdir()
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "KEY_TEST_TARGET": str(target),
        "KEY_RECORD_DIR": str(records),
    }
    result = subprocess.run(
        pytest_command(
            "tests/unit/test_key_failure_output.py",
            "-k",
            expression,
            "-vv",
            "--tb=long",
            "--showlocals",
        ),
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
        env=pytest_environment(environment, tmp_path),
    )
    output = result.stdout + result.stderr
    recorded_file = records / "recorded.txt"
    recorded = recorded_file.read_text().splitlines() if recorded_file.exists() else []

    if result.returncode == 0:
        pytest.fail("the regression was expected to fail against this target", pytrace=False)
    if len(recorded) < 1:
        pytest.fail("no value was recorded, so the scan means nothing", pytrace=False)
    leaks = leaked_labels(output, [v for v in recorded if len(v) >= 8])
    if leaks:
        pytest.fail(
            f"the regression printed {leaks} recorded value(s) when it failed", pytrace=False
        )
