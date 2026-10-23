"""Verifica a configuração renderizada do Compose sem imprimir nenhum segredo.

As asserções usam `pytest.fail` com mensagens fixas: o rewrite de `assert` mostraria os valores.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from .support import COMPOSE_PROJECT, compose_env_file, read_dotenv, run_compose

pytestmark = pytest.mark.integration

SERVICES = {"postgres", "redis", "migrate", "api"}


@pytest.fixture(scope="module")
def rendered() -> dict[str, Any]:
    result = run_compose("config", "--format", "json")
    if result.returncode != 0:
        pytest.fail("docker compose config failed")
    config: dict[str, Any] = json.loads(result.stdout)
    return config


@pytest.fixture(scope="module")
def secrets() -> dict[str, str]:
    values = read_dotenv(compose_env_file())
    return {
        "postgres admin password": values["CCE_POSTGRES_ADMIN_PASSWORD"],
        "migrator password": values["CCE_MIGRATOR_DB_PASSWORD"],
        "app password": values["CCE_APP_DB_PASSWORD"],
    }


def assert_absent(service: dict[str, Any], forbidden: list[str], label: str) -> None:
    serialized = json.dumps(service)
    for what in forbidden:
        if what in serialized:
            pytest.fail(f"{label} contains {what!r}-like secret or role")


def test_compose_config_is_valid() -> None:
    result = run_compose("config", "--quiet")

    assert result.returncode == 0
    assert result.stdout == "" and result.stderr == ""


def test_test_environment_uses_a_separate_project_and_database(
    rendered: dict[str, Any],
) -> None:
    assert rendered["name"] == COMPOSE_PROJECT
    assert rendered["services"]["postgres"]["environment"]["POSTGRES_DB"] == "cce_test"
    assert set(rendered["services"]) == SERVICES
    for volume in rendered["volumes"].values():
        assert volume["name"].startswith(f"{COMPOSE_PROJECT}_")


def test_api_service_has_no_migrator_url_and_no_postgres_password(
    rendered: dict[str, Any], secrets: dict[str, str]
) -> None:
    api = rendered["services"]["api"]
    environment = api["environment"]

    assert "CCE_MIGRATOR_DATABASE_URL" not in environment
    assert "POSTGRES_PASSWORD" not in environment
    assert "CCE_POSTGRES_ADMIN_PASSWORD" not in environment
    assert "env_file" not in api
    for label in ("postgres admin password", "migrator password"):
        if secrets[label] in json.dumps(api):
            pytest.fail(f"the rendered api service contains the {label}")
    if "cce_migrator" in json.dumps(api):
        pytest.fail("the rendered api service references the cce_migrator role")


def test_api_service_does_receive_its_own_runtime_credentials(
    rendered: dict[str, Any], secrets: dict[str, str]
) -> None:
    environment = rendered["services"]["api"]["environment"]

    assert environment["CCE_DATABASE_URL"].startswith("postgresql+asyncpg://cce_app:")
    if secrets["app password"] not in environment["CCE_DATABASE_URL"]:
        pytest.fail("the api DSN does not use the generated app password")
    assert environment["CCE_REDIS_URL"] == "redis://redis:6379/0"


def test_only_the_postgres_service_has_the_admin_password(
    rendered: dict[str, Any], secrets: dict[str, str]
) -> None:
    for name in ("redis", "migrate", "api"):
        if secrets["postgres admin password"] in json.dumps(rendered["services"][name]):
            pytest.fail(f"the rendered {name} service contains the postgres admin password")


def test_migrate_service_gets_only_the_migrator_credentials(
    rendered: dict[str, Any], secrets: dict[str, str]
) -> None:
    migrate = rendered["services"]["migrate"]
    environment = migrate["environment"]

    assert environment["CCE_MIGRATOR_DATABASE_URL"].startswith("postgresql+asyncpg://cce_migrator:")
    assert "CCE_DATABASE_URL" not in environment
    assert_absent(migrate, [secrets["app password"], secrets["postgres admin password"]], "migrate")


def test_migrate_is_one_shot_and_the_api_waits_for_it(rendered: dict[str, Any]) -> None:
    services = rendered["services"]

    assert services["migrate"]["restart"] == "no"
    assert services["migrate"]["command"][:2] == ["alembic", "-c"]
    assert services["migrate"]["image"] == services["api"]["image"]
    depends_on = services["api"]["depends_on"]
    assert depends_on["migrate"]["condition"] == "service_completed_successfully"
    assert depends_on["postgres"]["condition"] == "service_healthy"
    assert depends_on["redis"]["condition"] == "service_healthy"
    assert services["migrate"]["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert "depends_on" not in services["postgres"]


def test_published_ports_are_bound_to_loopback_only(rendered: dict[str, Any]) -> None:
    for name in ("postgres", "redis", "api"):
        for port in rendered["services"][name]["ports"]:
            assert port["host_ip"] == "127.0.0.1"


def test_application_containers_are_hardened(rendered: dict[str, Any]) -> None:
    for name in ("migrate", "api"):
        assert rendered["services"][name]["read_only"] is True
    assert "no-new-privileges:true" in rendered["services"]["api"]["security_opt"]


def test_api_has_a_real_healthcheck_that_is_not_faked(rendered: dict[str, Any]) -> None:
    healthcheck = rendered["services"]["api"]["healthcheck"]

    assert "/health/live" in " ".join(healthcheck["test"])
    assert "CMD-SHELL" not in healthcheck["test"] and "NONE" not in healthcheck["test"]
