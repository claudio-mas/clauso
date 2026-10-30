"""Contrato público da API no OpenAPI (plano do M0, Tarefa 8; ADR-0014).

O documento é gerado pela aplicação de produção (`create_app`), sem banco: paths e métodos exatos,
o esquema Bearer, os scopes exigidos por operação, os códigos de erro com o envelope padronizado,
os cabeçalhos de correlação e nenhum campo de segredo em lugar nenhum do documento.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from api_support import BASE_URL
from log_support import synthetic_settings

from cce.integrations.api_keys.scopes import ALLOWED_SCOPES
from cce.main import create_app

pytestmark = pytest.mark.usefixtures("clean_cce_env")

AUTH_CONTEXT = "/api/v1/auth/context"
TENANT = "/api/v1/tenant"
OPERATIONS = {
    "/health/live": {"get"},
    "/health/ready": {"get"},
    AUTH_CONTEXT: {"get"},
    TENANT: {"get", "patch"},
}
REQUIRED_SCOPES = {
    ("get", AUTH_CONTEXT): [],
    ("get", TENANT): ["tenant:read"],
    ("patch", TENANT): ["tenant:write"],
}
STATUSES = {
    ("get", AUTH_CONTEXT): {"200", "401", "500", "503"},
    ("get", TENANT): {"200", "401", "403", "404", "500", "503"},
    ("patch", TENANT): {"200", "401", "403", "404", "409", "422", "500", "503"},
}
ERROR_CODES = {
    "401": "AUTHENTICATION_FAILED",
    "403": "FORBIDDEN",
    "404": "NOT_FOUND",
    "405": "METHOD_NOT_ALLOWED",
    "409": "CONFLICT",
    "422": "VALIDATION_ERROR",
    "500": "INTERNAL_ERROR",
    "503": "DEPENDENCY_UNAVAILABLE",
}
SECRET_NAME = re.compile(
    r"secret|digest|pepper|password|passwd|token|raw_?key|api_?key|authorization|cookie|dsn",
    re.IGNORECASE,
)


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    return create_app(synthetic_settings()).openapi()


def operations(schema: dict[str, Any]) -> Iterator[tuple[str, str, dict[str, Any]]]:
    for path, item in schema["paths"].items():
        for method, operation in item.items():
            yield method, path, operation


def resolve(schema: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    reference = node.get("$ref")
    if reference is None:
        return node
    name = reference.removeprefix("#/components/schemas/")
    resolved: dict[str, Any] = schema["components"]["schemas"][name]
    return resolved


def names_in(node: Any) -> Iterator[str]:
    """Todo nome de propriedade, parâmetro, schema e exemplo do documento."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ("properties", "schemas", "example", "examples") and isinstance(value, dict):
                yield from value
            if key == "parameters" and isinstance(value, list):
                yield from (parameter.get("name", "") for parameter in value)
            if key == "required" and isinstance(value, list):
                yield from (item for item in value if isinstance(item, str))
            yield from names_in(value)
    elif isinstance(node, list):
        for value in node:
            yield from names_in(value)


def test_the_paths_and_methods_are_exactly_the_public_ones(schema: dict[str, Any]) -> None:
    found = {path: set(item) for path, item in schema["paths"].items()}

    assert found == OPERATIONS


def test_the_api_key_is_a_bearer_scheme(schema: dict[str, Any]) -> None:
    schemes = schema["components"]["securitySchemes"]

    assert set(schemes) == {"ApiKey"}
    assert schemes["ApiKey"]["type"] == "http"
    assert schemes["ApiKey"]["scheme"] == "bearer"


def test_every_api_operation_requires_the_bearer_scheme_and_health_does_not(
    schema: dict[str, Any],
) -> None:
    for method, path, operation in operations(schema):
        if path.startswith("/api/v1/"):
            assert operation.get("security") == [{"ApiKey": []}], (method, path)
        else:
            assert "security" not in operation, (method, path)


def test_every_api_operation_lists_its_required_scopes(schema: dict[str, Any]) -> None:
    found = {
        (method, path): operation.get("x-cce-required-scopes")
        for method, path, operation in operations(schema)
        if path.startswith("/api/v1/")
    }

    assert found == REQUIRED_SCOPES
    for scopes in found.values():
        assert set(scopes or []) <= set(ALLOWED_SCOPES)


def test_every_api_operation_lists_its_status_codes(schema: dict[str, Any]) -> None:
    found = {
        (method, path): set(operation["responses"])
        for method, path, operation in operations(schema)
        if path.startswith("/api/v1/")
    }

    assert found == STATUSES


def test_every_error_response_is_the_envelope_with_its_code(schema: dict[str, Any]) -> None:
    for method, path, operation in operations(schema):
        for status, response in operation["responses"].items():
            if status not in ERROR_CODES or path.startswith("/health/"):
                continue
            content = response["content"]["application/json"]
            envelope = resolve(schema, content["schema"])
            assert set(envelope["required"]) == {"error"}, (method, path, status)
            body = resolve(schema, envelope["properties"]["error"])
            assert set(body["required"]) == {"code", "message", "details", "request_id"}
            assert content["example"]["error"]["code"] == ERROR_CODES[status], (path, status)


def test_the_error_codes_are_an_enumeration(schema: dict[str, Any]) -> None:
    body = schema["components"]["schemas"]["ErrorBody"]

    assert set(body["properties"]["code"]["enum"]) == set(ERROR_CODES.values())


def test_a_401_documents_the_bearer_challenge(schema: dict[str, Any]) -> None:
    for method, path, operation in operations(schema):
        if "401" in operation["responses"]:
            assert "WWW-Authenticate" in operation["responses"]["401"].get("headers", {}), (
                method,
                path,
            )


def test_every_response_documents_the_correlation_headers(schema: dict[str, Any]) -> None:
    for method, path, operation in operations(schema):
        for status, response in operation["responses"].items():
            headers = response.get("headers", {})
            assert {"X-Request-ID", "X-Trace-ID"} <= set(headers), (method, path, status)


def test_the_successful_responses_use_the_public_schemas(schema: dict[str, Any]) -> None:
    def success(method: str, path: str) -> str:
        operation = schema["paths"][path][method]
        return str(operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"])

    assert success("get", AUTH_CONTEXT).endswith("/AuthContext")
    assert success("get", TENANT).endswith("/TenantRead")
    assert success("patch", TENANT).endswith("/TenantRead")


def test_the_patch_body_refuses_unknown_fields(schema: dict[str, Any]) -> None:
    body = schema["paths"][TENANT]["patch"]["requestBody"]["content"]["application/json"]

    patch = resolve(schema, body["schema"])
    assert patch["additionalProperties"] is False
    assert set(patch["properties"]) == {"name", "default_currency", "timezone", "locale"}


def test_no_name_in_the_document_is_a_secret_field(schema: dict[str, Any]) -> None:
    secrets = sorted({name for name in names_in(schema) if SECRET_NAME.search(name)})

    assert secrets == []


def test_fastapis_own_validation_error_schema_is_not_exposed(schema: dict[str, Any]) -> None:
    # O 422 padrão do FastAPI descreve `input` e `ctx`, que a API nunca devolve.
    assert not {"HTTPValidationError", "ValidationError"} & set(schema["components"]["schemas"])


async def test_the_served_document_is_the_same_contract() -> None:
    app = create_app(synthetic_settings())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        response = await client.get("/openapi.json")

    assert response.status_code == 200
    assert response.json() == app.openapi()
