"""Quando a autenticação tem um defeito, os testes o acusam sem imprimir chave nenhuma.

Roda `pytest` de verdade (`-vv --tb=long --showlocals`, o pior caso de saída) sobre os testes de
autenticação (`tests/unit/test_authentication.py`, sem banco, e `tests/integration/
test_authentication.py`, contra o PostgreSQL de teste) com defeitos injetados por um plugin. O
plugin grava, num arquivo temporário, toda chave, segredo, digest e pepper que o processo interno
gera; depois procuramos cada um deles em stdout, stderr e traceback. Tudo é sintético.

Cada defeito faz o teste interno FALHAR: a falha precisa ser detectada (o código de saída não é 0)
e a saída do relatório não pode conter nenhum dos valores gravados.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from db_support import ROOT
from integration.support import require

pytestmark = pytest.mark.sensitive

PLUGIN = textwrap.dedent(
    """
    import base64
    import logging
    import os
    from uuid import uuid4

    import cce.integrations.api_keys.authentication as authentication
    import cce.integrations.api_keys.repository as repository
    import cce.integrations.api_keys.service as service
    import cce.shared.security as security

    RECORD = os.environ["AUTH_RECORD_FILE"]
    DEFECT = os.environ.get("AUTH_DEFECT", "")
    LAST = {"raw": "", "digest": b""}


    def record(*values):
        with open(RECORD, "a", encoding="utf-8") as handle:
            for value in values:
                if value:
                    handle.write(str(value) + "\\n")


    original_generate = security.generate_key
    original_digest = security.digest_key
    original_pepper = security.pepper_bytes


    def generate_key(key_id, environment):
        raw = original_generate(key_id, environment)
        record(raw, raw.rsplit("_", 1)[-1])
        return raw


    def digest_key(raw_key, pepper):
        digest = original_digest(raw_key, pepper)
        LAST["raw"], LAST["digest"] = raw_key, digest
        record(raw_key, digest.hex(), repr(digest), pepper.hex(), base64.b64encode(pepper).decode())
        return digest


    def pepper_bytes(peppers, version):
        value = original_pepper(peppers, version)
        record(value.hex(), base64.b64encode(value).decode(), repr(value))
        return value


    for module in (security, authentication, service):
        for name, replacement in (
            ("generate_key", generate_key),
            ("digest_key", digest_key),
            ("pepper_bytes", pepper_bytes),
        ):
            if hasattr(module, name):
                setattr(module, name, replacement)

    original_authenticate = authentication.authenticate_api_key


    class LeakyFailure(authentication.AuthenticationFailed):
        def __str__(self):
            return "authentication failed for " + LAST["raw"]


    async def authenticate(raw_key, settings, session_factory):
        if isinstance(raw_key, str) and len(raw_key) > 60:
            record(raw_key, raw_key.rsplit("_", 1)[-1])
        if DEFECT == "unexpected-exception":
            raise RuntimeError("boom " + str(raw_key) + " " + repr(settings.api_key_peppers))
        try:
            principal = await original_authenticate(raw_key, settings, session_factory)
        except authentication.AuthenticationFailed:
            if DEFECT == "error-echoes-key":
                LAST["raw"] = str(raw_key)
                raise LeakyFailure() from None
            raise
        except authentication.DependencyUnavailable:
            if DEFECT == "dependency-error-chains-the-key":
                raise authentication.DependencyUnavailable() from RuntimeError(str(raw_key))
            raise
        if DEFECT == "wrong-principal":
            return authentication.Principal(uuid4(), principal.key_id, principal.scopes)
        return principal


    authentication.authenticate_api_key = authenticate

    if DEFECT == "candidate-repr-leaks":
        def leaky_candidate(self):
            return f"KeyCandidate({self.secret_digest.hex()} {self.secret_digest!r})"

        repository.KeyCandidate.__repr__ = leaky_candidate
        repository.KeyCandidate.__str__ = leaky_candidate

    if DEFECT == "event-logs-the-key":
        real_error = authentication._logger.error

        def noisy_error(message, *args, **kwargs):
            extra = dict(kwargs.get("extra") or {})
            extra["raw_key"] = LAST["raw"]
            extra["digest"] = LAST["digest"].hex()
            kwargs["extra"] = extra
            return real_error(message + " " + LAST["raw"] + " " + LAST["digest"].hex(), **kwargs)

        authentication._logger.error = noisy_error
    """
)

DEFECTS = [
    "error-echoes-key",
    "unexpected-exception",
    "dependency-error-chains-the-key",
    "wrong-principal",
    "candidate-repr-leaks",
    "event-logs-the-key",
]
TARGETS = ["tests/unit/test_authentication.py", "tests/integration/test_authentication.py"]


def run_auth_tests(
    tmp_path: Path, defect: str
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    plugin = tmp_path / "authdefects.py"
    plugin.write_text(PLUGIN)
    record = tmp_path / "recorded.txt"
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "PYTHONPATH": str(tmp_path),
        "AUTH_RECORD_FILE": str(record),
        "AUTH_DEFECT": defect,
    }
    for name in ("CCE_ENV_FILE", "CCE_ENV"):
        if name in os.environ:
            environment[name] = os.environ[name]
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            *TARGETS,
            "-p",
            "authdefects",
            "-p",
            "no:cacheprovider",
            "-vv",
            "--tb=long",
            "--showlocals",
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
        env=environment,
    )
    values = record.read_text().splitlines() if record.exists() else []
    return result, [v for v in values if len(v) >= 8 and len(set(v)) >= 12]


def leaked_count(output: str, recorded: list[str]) -> int:
    return sum(1 for value in set(recorded) if value in output)


def test_the_control_run_passes_and_records_the_sensitive_values(tmp_path: Path) -> None:
    result, recorded = run_auth_tests(tmp_path, "")

    require(result.returncode == 0, "the authentication tests fail without any injected defect")
    require(len(recorded) > 100, "the control run recorded too few sensitive values")
    require(
        leaked_count(result.stdout + result.stderr, recorded) == 0,
        "a passing run printed a sensitive value",
    )


@pytest.mark.parametrize("defect", DEFECTS)
def test_a_defect_is_reported_without_printing_any_key_secret_digest_or_pepper(
    tmp_path: Path, defect: str
) -> None:
    result, recorded = run_auth_tests(tmp_path, defect)
    output = result.stdout + result.stderr

    require(
        leaked_count(output, recorded) == 0,
        "pytest printed a sensitive value while reporting the defect",
    )
    require(
        result.returncode != 0 and " failed" in output,
        "the injected defect was not detected by the authentication tests",
    )
    require(len(recorded) > 50, "the plugin recorded too few values for the scan to mean anything")
