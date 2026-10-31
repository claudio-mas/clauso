"""Smoke da API com dois Tenants, contra uma API já em execução (por exemplo, a do Compose).

Roda em duas fases, com as chaves pelo ambiente (nunca pelo argv):

    CCE_SMOKE_BASE_URL=http://127.0.0.1:8000 CCE_SMOKE_KEY_A=... CCE_SMOKE_KEY_B=... \\
        CCE_SMOKE_KEY_READ_ONLY=... CCE_SMOKE_KEY_REVOCABLE=... python3 scripts/smoke.py
    # revogue a CCE_SMOKE_KEY_REVOCABLE (cce-admin api-key revoke) e então:
    CCE_SMOKE_BASE_URL=... CCE_SMOKE_KEY_A=... CCE_SMOKE_KEY_REVOCABLE=... \\
        python3 scripts/smoke.py --revoked

- `CCE_SMOKE_KEY_A` e `CCE_SMOKE_KEY_B`: Tenants distintos, com `tenant:read` e `tenant:write`.
- `CCE_SMOKE_KEY_READ_ONLY`: do Tenant A, só com `tenant:read`.
- `CCE_SMOKE_KEY_REVOCABLE`: do Tenant A; autentica na primeira fase e é 401 depois de revogada,
  enquanto a `CCE_SMOKE_KEY_A` continua valendo.

A primeira fase confere health, o 401 sem chave, o isolamento entre A e B (`X-Tenant-ID` ignorado,
`PATCH` só no próprio Tenant, nenhuma resposta com o ID do outro), o 422 de campo proibido e o 403
de uma chave sem `tenant:write`, sem efeito no banco. Ela altera o nome do Tenant A. Saída: uma
linha `ok: <etapa>` por etapa e `smoke passed` (código 0), ou `smoke failed: <etapa>` (código 1).
Nunca imprime chave, corpo de resposta nem mensagem de exceção. Só usa a biblioteca padrão.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from typing import Any

TIMEOUT_SECONDS = 10


class SmokeFailure(Exception):
    """Uma etapa falhou. A mensagem é o nome da etapa, sem valores."""


def request(
    base_url: str, method: str, path: str, key: str | None = None, **options: Any
) -> tuple[int, dict[str, str], Any]:
    headers = {"Accept": "application/json", **options.get("headers", {})}
    if key is not None:
        headers["Authorization"] = f"Bearer {key}"
    data = None
    if "json" in options:
        data = json.dumps(options["json"]).encode()
        headers["Content-Type"] = "application/json"
    prepared = urllib.request.Request(base_url + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(prepared, timeout=TIMEOUT_SECONDS) as response:
            status, response_headers, raw = response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        status, response_headers, raw = error.code, dict(error.headers), error.read()
    try:
        body = json.loads(raw) if raw else None
    except ValueError:
        body = None
    return status, {name.lower(): value for name, value in response_headers.items()}, body


def check(condition: bool, step: str) -> None:
    if not condition:
        raise SmokeFailure(step)


def is_error(body: Any, code: str) -> bool:
    """O envelope de erro do CCE com `code` e um `request_id` público."""
    error = body.get("error") if isinstance(body, dict) else None
    return (
        isinstance(error, dict)
        and error.get("code") == code
        and str(error.get("request_id", "")).startswith("req_")
    )


def is_authentication_failure(status: int, headers: dict[str, str], body: Any) -> bool:
    return (
        status == 401
        and headers.get("www-authenticate") == "Bearer"
        and is_error(body, "AUTHENTICATION_FAILED")
    )


def context_of(base_url: str, key: str, step: str) -> dict[str, Any]:
    status, _, body = request(base_url, "GET", "/api/v1/auth/context", key)
    check(status == 200 and isinstance(body, dict), step)
    return dict(body)


def run(base_url: str, key_a: str, key_b: str, read_only: str, revocable: str) -> None:
    status, _, body = request(base_url, "GET", "/health/live")
    check(status == 200 and body == {"status": "ok"}, "liveness")
    print("ok: liveness")

    status, _, body = request(base_url, "GET", "/health/ready")
    check(status == 200 and isinstance(body, dict) and body.get("status") == "ready", "readiness")
    print("ok: readiness")

    status, headers, body = request(base_url, "GET", "/api/v1/tenant")
    check(is_authentication_failure(status, headers, body), "request without a key is 401")
    print("ok: request without a key is 401")

    tenant_a = context_of(base_url, key_a, "auth context of A")["tenant_id"]
    tenant_b = context_of(base_url, key_b, "auth context of B")["tenant_id"]
    check(tenant_a != tenant_b, "the two keys belong to distinct Tenants")
    print("ok: two Tenants authenticated")

    status, _, body = request(
        base_url, "GET", "/api/v1/tenant", key_a, headers={"X-Tenant-ID": tenant_b}
    )
    check(status == 200 and body.get("id") == tenant_a, "X-Tenant-ID does not change the Tenant")
    print("ok: X-Tenant-ID is ignored")

    status, _, before_b = request(base_url, "GET", "/api/v1/tenant", key_b)
    check(status == 200 and before_b.get("id") == tenant_b, "Tenant B reads itself")

    name = f"Smoke {uuid.uuid4().hex[:8]}"
    status, _, body = request(
        base_url,
        "PATCH",
        "/api/v1/tenant",
        key_a,
        headers={"X-Tenant-ID": tenant_b},
        json={"name": name},
    )
    check(status == 200 and body.get("id") == tenant_a and body.get("name") == name, "PATCH A")
    status, _, after_a = request(base_url, "GET", "/api/v1/tenant", key_a)
    check(status == 200 and after_a.get("name") == name, "the change of A was committed")
    status, _, after_b = request(base_url, "GET", "/api/v1/tenant", key_b)
    check(status == 200 and after_b == before_b, "Tenant B is unchanged")
    print("ok: PATCH changes only its own Tenant")

    status, _, body = request(
        base_url, "PATCH", "/api/v1/tenant", key_a, json={"tenant_id": tenant_b}
    )
    check(status == 422 and is_error(body, "VALIDATION_ERROR"), "a forbidden field is 422")
    print("ok: a forbidden field is 422")

    # Nenhuma resposta de um Tenant carrega o ID do outro, nem com o X-Tenant-ID do outro.
    for key, own, other in ((key_a, tenant_a, tenant_b), (key_b, tenant_b, tenant_a)):
        for path in ("/api/v1/auth/context", "/api/v1/tenant"):
            status, _, body = request(base_url, "GET", path, key, headers={"X-Tenant-ID": other})
            text = json.dumps(body)
            check(
                status == 200 and own in text and other not in text,
                "no response carries the other Tenant",
            )
    print("ok: no response carries the other Tenant")

    read_only_context = context_of(base_url, read_only, "auth context of the read-only key")
    check(
        read_only_context["tenant_id"] == tenant_a
        and read_only_context.get("scopes") == ["tenant:read"],
        "the read-only key belongs to A and has only tenant:read",
    )
    status, _, body = request(base_url, "GET", "/api/v1/tenant", read_only)
    check(status == 200 and body.get("id") == tenant_a, "the read-only key reads its Tenant")
    print("ok: the read-only key reads its Tenant")

    status, _, body = request(
        base_url, "PATCH", "/api/v1/tenant", read_only, json={"name": f"Forbidden {name}"}
    )
    check(
        status == 403
        and is_error(body, "FORBIDDEN")
        and body["error"].get("details") == {"required_scopes": ["tenant:write"]},
        "a key without tenant:write is 403",
    )
    print("ok: a key without tenant:write is 403")
    status, _, after_forbidden = request(base_url, "GET", "/api/v1/tenant", key_a)
    check(
        status == 200 and after_forbidden.get("name") == name,
        "the forbidden PATCH changed nothing",
    )
    print("ok: the forbidden PATCH changed nothing")

    check(
        context_of(base_url, revocable, "the revocable key authenticates")["tenant_id"] == tenant_a,
        "the revocable key authenticates",
    )
    print("ok: the revocable key authenticates")


def run_revoked(base_url: str, key_a: str, revocable: str) -> None:
    """Depois da revogação: a chave revogada é 401, e a outra chave do mesmo Tenant ainda vale."""
    for path in ("/api/v1/auth/context", "/api/v1/tenant"):
        status, headers, body = request(base_url, "GET", path, revocable)
        check(is_authentication_failure(status, headers, body), "the revoked key is 401")
        check("tenant_id" not in json.dumps(body), "the revoked key is 401")
    print("ok: the revoked key is 401")

    context_of(base_url, key_a, "the other keys still authenticate")
    status, _, _ = request(base_url, "GET", "/api/v1/tenant", key_a)
    check(status == 200, "the other keys still authenticate")
    print("ok: the other keys still authenticate")


ACTIVE_KEYS = ("CCE_SMOKE_KEY_A", "CCE_SMOKE_KEY_B", "CCE_SMOKE_KEY_READ_ONLY")
REVOKED_KEYS = ("CCE_SMOKE_KEY_A",)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="smoke.py", description="Two-Tenant smoke of the API.")
    parser.add_argument(
        "--revoked", action="store_true", help="check CCE_SMOKE_KEY_REVOCABLE after its revocation"
    )
    arguments = parser.parse_args(argv)
    base_url = os.environ.get("CCE_SMOKE_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    required = (*(REVOKED_KEYS if arguments.revoked else ACTIVE_KEYS), "CCE_SMOKE_KEY_REVOCABLE")
    keys = {name: os.environ.get(name, "") for name in required}
    missing = [name for name, value in keys.items() if not value]
    if missing:
        print(f"smoke failed: {', '.join(missing)} required", file=sys.stderr)
        return 1
    try:
        if arguments.revoked:
            run_revoked(base_url, keys["CCE_SMOKE_KEY_A"], keys["CCE_SMOKE_KEY_REVOCABLE"])
        else:
            run(
                base_url,
                keys["CCE_SMOKE_KEY_A"],
                keys["CCE_SMOKE_KEY_B"],
                keys["CCE_SMOKE_KEY_READ_ONLY"],
                keys["CCE_SMOKE_KEY_REVOCABLE"],
            )
    except SmokeFailure as failure:
        print(f"smoke failed: {failure}", file=sys.stderr)
        return 1
    except Exception as error:  # noqa: BLE001 - só o tipo sai; a mensagem pode trazer valores
        print(f"smoke failed: unexpected {type(error).__name__}", file=sys.stderr)
        return 1
    print("smoke passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
