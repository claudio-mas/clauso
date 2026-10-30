"""Smoke da API com dois Tenants, contra uma API já em execução (por exemplo, a do Compose).

Uso (as chaves vão pelo ambiente, nunca pelo argv):

    CCE_SMOKE_BASE_URL=http://127.0.0.1:8000 CCE_SMOKE_KEY_A=... CCE_SMOKE_KEY_B=... \\
        python3 scripts/smoke.py

As duas chaves precisam ser de Tenants distintos, com `tenant:read` e `tenant:write`. O smoke
altera o nome do Tenant A. Saída: uma linha `ok: <etapa>` por etapa e `smoke passed` (código 0),
ou `smoke failed: <etapa>` (código 1). Nunca imprime chave, corpo de resposta nem mensagem de
exceção. Só usa a biblioteca padrão.
"""

from __future__ import annotations

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


def run(base_url: str, key_a: str, key_b: str) -> None:
    status, _, body = request(base_url, "GET", "/health/live")
    check(status == 200 and body == {"status": "ok"}, "liveness")
    print("ok: liveness")

    status, _, body = request(base_url, "GET", "/health/ready")
    check(status == 200 and isinstance(body, dict) and body.get("status") == "ready", "readiness")
    print("ok: readiness")

    status, headers, body = request(base_url, "GET", "/api/v1/tenant")
    check(
        status == 401
        and headers.get("www-authenticate") == "Bearer"
        and isinstance(body, dict)
        and body.get("error", {}).get("code") == "AUTHENTICATION_FAILED"
        and str(body["error"].get("request_id", "")).startswith("req_"),
        "request without a key is 401",
    )
    print("ok: request without a key is 401")

    contexts = []
    for label, key in (("A", key_a), ("B", key_b)):
        status, _, body = request(base_url, "GET", "/api/v1/auth/context", key)
        check(status == 200 and isinstance(body, dict), f"auth context of {label}")
        contexts.append(body)
    tenant_a, tenant_b = contexts[0]["tenant_id"], contexts[1]["tenant_id"]
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
    check(
        status == 422 and body.get("error", {}).get("code") == "VALIDATION_ERROR",
        "a forbidden field is 422",
    )
    print("ok: a forbidden field is 422")


def main() -> int:
    base_url = os.environ.get("CCE_SMOKE_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    key_a = os.environ.get("CCE_SMOKE_KEY_A", "")
    key_b = os.environ.get("CCE_SMOKE_KEY_B", "")
    if not key_a or not key_b:
        print("smoke failed: CCE_SMOKE_KEY_A and CCE_SMOKE_KEY_B are required", file=sys.stderr)
        return 1
    try:
        run(base_url, key_a, key_b)
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
