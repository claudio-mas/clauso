"""`scripts/smoke.py` reprova cada defeito de autorização que ele diz cobrir, sem mostrar chave.

Uma API falsa (só a biblioteca padrão, numa thread) responde como a do M0 e injeta um defeito por
vez: chave sem `tenant:write` que escreve, 403 fora do envelope, revogação que não vale ou que
derruba as outras chaves, resposta com dados do outro Tenant. O smoke roda de verdade, em
subprocesso, com as chaves pelo ambiente; a saída é examinada antes de qualquer outra coisa. O
smoke contra a API real (Compose) está em `tests/integration/test_smoke.py` e no `scripts/ci.sh`.
"""

from __future__ import annotations

import json
import secrets
import subprocess
import sys
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from integration.support import assert_no_leaks, require

ROOT = Path(__file__).resolve().parents[2]
SMOKE = ROOT / "scripts" / "smoke.py"

# As chaves daqui só valem na API falsa, mas seguem a regra de qualquer chave: uma falha nunca
# mostra locais nem a saída capturada.
pytestmark = pytest.mark.sensitive

TENANT_A = "ten_" + "a" * 32
TENANT_B = "ten_" + "b" * 32
ALL_SCOPES = ["tenant:read", "tenant:write"]


def new_key() -> str:
    return f"cce_dev_{secrets.token_hex(16)}_{secrets.token_urlsafe(32)}"


@dataclass
class Key:
    tenant: str
    scopes: list[str]
    revoked: bool = False


@dataclass
class FakeApi:
    """Estado da API falsa: Tenants, chaves e o defeito ativo."""

    defect: str = ""
    keys: dict[str, Key] = field(default_factory=dict)
    tenants: dict[str, dict[str, Any]] = field(
        default_factory=lambda: {
            TENANT_A: {"id": TENANT_A, "name": "Empresa A", "slug": "empresa-a"},
            TENANT_B: {"id": TENANT_B, "name": "Empresa B", "slug": "empresa-b"},
        }
    )

    def revoke(self, raw: str) -> None:
        self.keys[raw].revoked = True
        if self.defect == "revocation reaches every key of the Tenant":
            for key in self.keys.values():
                if key.tenant == self.keys[raw].tenant:
                    key.revoked = True
        if self.defect == "revocation does not apply":
            self.keys[raw].revoked = False


def error(code: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": "fixed message",
            "details": details or {},
            "request_id": "req_" + "0" * 32,
        }
    }


def handler_for(api: FakeApi) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # silencia o stderr do teste
            return

        def reply(self, status: int, body: Any, headers: dict[str, str] | None = None) -> None:
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(raw)

        def principal(self) -> Key | None:
            header = self.headers.get("Authorization", "")
            key = api.keys.get(header.removeprefix("Bearer "))
            if key is None or key.revoked:
                self.reply(401, error("AUTHENTICATION_FAILED"), {"WWW-Authenticate": "Bearer"})
                return None
            return key

        def tenant_body(self, tenant: str) -> dict[str, Any]:
            body = dict(api.tenants[tenant])
            if api.defect == "a response carries the other Tenant":
                body["related"] = [other for other in api.tenants if other != tenant]
            return body

        def do_GET(self) -> None:
            if self.path == "/health/live":
                return self.reply(200, {"status": "ok"})
            if self.path == "/health/ready":
                return self.reply(200, {"status": "ready", "checks": {}})
            key = self.principal()
            if key is None:
                return None
            if self.path == "/api/v1/auth/context":
                context = {"tenant_id": key.tenant, "key_id": "key_x", "scopes": key.scopes}
                return self.reply(200, context)
            if self.path == "/api/v1/tenant":
                return self.reply(200, self.tenant_body(key.tenant))
            return self.reply(404, error("NOT_FOUND"))

        def do_PATCH(self) -> None:
            key = self.principal()
            if key is None:
                return None
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if set(body) - {"name"}:
                return self.reply(422, error("VALIDATION_ERROR"))
            writable = "tenant:write" in key.scopes or api.defect == "a read-only key writes"
            if not writable:
                if api.defect == "the forbidden PATCH is applied":
                    api.tenants[key.tenant]["name"] = body["name"]
                if api.defect == "the 403 is outside the error envelope":
                    return self.reply(403, {"detail": "forbidden"})
                return self.reply(403, error("FORBIDDEN", {"required_scopes": ["tenant:write"]}))
            api.tenants[key.tenant]["name"] = body["name"]
            return self.reply(200, self.tenant_body(key.tenant))

    return Handler


@dataclass
class Running:
    api: FakeApi
    base_url: str
    key_a: str
    key_b: str
    read_only: str
    revocable: str

    def environment(self) -> dict[str, str]:
        return {
            "PATH": "/usr/bin:/bin",
            "CCE_SMOKE_BASE_URL": self.base_url,
            "CCE_SMOKE_KEY_A": self.key_a,
            "CCE_SMOKE_KEY_B": self.key_b,
            "CCE_SMOKE_KEY_READ_ONLY": self.read_only,
            "CCE_SMOKE_KEY_REVOCABLE": self.revocable,
        }

    def keys(self) -> dict[str, str]:
        return {
            "key A": self.key_a,
            "key B": self.key_b,
            "read-only key": self.read_only,
            "revocable key": self.revocable,
        }


@pytest.fixture
def running() -> Iterator[Running]:
    api = FakeApi()
    key_a, key_b, read_only, revocable = new_key(), new_key(), new_key(), new_key()
    api.keys = {
        key_a: Key(TENANT_A, ALL_SCOPES),
        key_b: Key(TENANT_B, ALL_SCOPES),
        read_only: Key(TENANT_A, ["tenant:read"]),
        revocable: Key(TENANT_A, ALL_SCOPES),
    }
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(api))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        yield Running(api, f"http://127.0.0.1:{port}", key_a, key_b, read_only, revocable)
    finally:
        server.shutdown()
        server.server_close()


def smoke(running: Running, *arguments: str, **overrides: str) -> tuple[int, str]:
    environment = running.environment() | overrides
    result = subprocess.run(
        [sys.executable, str(SMOKE), *arguments],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
        timeout=60,
    )
    output = result.stdout + result.stderr
    assert_no_leaks(output, running.keys(), "the smoke output")
    return result.returncode, output


def test_a_correct_api_passes_both_phases(running: Running) -> None:
    code, output = smoke(running)
    require(code == 0 and "smoke passed" in output, f"the active phase failed:\n{output}")
    for step in (
        "ok: a key without tenant:write is 403",
        "ok: the forbidden PATCH changed nothing",
        "ok: the revocable key authenticates",
        "ok: no response carries the other Tenant",
    ):
        require(step in output, f"missing step: {step}")

    running.api.revoke(running.revocable)
    code, output = smoke(running, "--revoked")

    require(code == 0 and "smoke passed" in output, f"the revoked phase failed:\n{output}")
    require("ok: the revoked key is 401" in output, "the revocation step did not run")
    require("ok: the other keys still authenticate" in output, "the other keys were not checked")


ACTIVE_PHASE_DEFECTS = {
    "a read-only key writes": "a key without tenant:write is 403",
    "the 403 is outside the error envelope": "a key without tenant:write is 403",
    "the forbidden PATCH is applied": "the forbidden PATCH changed nothing",
    "a response carries the other Tenant": "no response carries the other Tenant",
}


@pytest.mark.parametrize("defect", list(ACTIVE_PHASE_DEFECTS))
def test_each_authorization_defect_fails_the_active_phase(running: Running, defect: str) -> None:
    running.api.defect = defect

    code, output = smoke(running)

    require(code == 1, f"the smoke passed with the defect: {defect}")
    require(f"smoke failed: {ACTIVE_PHASE_DEFECTS[defect]}" in output, f"wrong step:\n{output}")


REVOKED_PHASE_DEFECTS = {
    "revocation does not apply": "the revoked key is 401",
    "revocation reaches every key of the Tenant": "the other keys still authenticate",
}


@pytest.mark.parametrize("defect", list(REVOKED_PHASE_DEFECTS))
def test_each_revocation_defect_fails_the_revoked_phase(running: Running, defect: str) -> None:
    running.api.defect = defect
    running.api.revoke(running.revocable)

    code, output = smoke(running, "--revoked")

    require(code == 1, f"the smoke passed with the defect: {defect}")
    require(f"smoke failed: {REVOKED_PHASE_DEFECTS[defect]}" in output, f"wrong step:\n{output}")


def test_a_revocable_key_that_never_worked_fails_before_the_revocation(running: Running) -> None:
    running.api.keys[running.revocable].revoked = True

    code, output = smoke(running)

    require(code == 1, "the smoke passed with a revocable key that never authenticated")
    require("smoke failed: the revocable key authenticates" in output, f"wrong step:\n{output}")


@pytest.mark.parametrize(
    ("arguments", "missing"),
    [((), "CCE_SMOKE_KEY_READ_ONLY"), (("--revoked",), "CCE_SMOKE_KEY_REVOCABLE")],
)
def test_every_key_of_the_phase_is_required(
    running: Running, arguments: tuple[str, ...], missing: str
) -> None:
    code, output = smoke(running, *arguments, **{missing: ""})

    require(code == 1, f"the smoke ran without {missing}")
    require(missing in output, "the missing variable is not named")
