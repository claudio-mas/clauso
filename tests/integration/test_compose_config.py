"""Verifica a configuração renderizada do Compose sem imprimir nenhum segredo.

A configuração renderizada contém DSNs e senhas. Por isso nenhuma verificação aqui usa `assert`
sobre ela: a introspecção do pytest mostraria os operandos numa falha. Tudo passa por `require`
e `assert_no_leaks`, que falham com texto fixo e rótulos, nunca com o valor.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import SecretStr

from .support import (
    COMPOSE_PROJECT,
    assert_no_leaks,
    compose_env_file,
    read_dotenv,
    require,
    run_compose,
)

pytestmark = pytest.mark.integration

SERVICES = {"postgres", "redis", "migrate", "api"}


@pytest.fixture(scope="module")
def rendered() -> dict[str, Any]:
    result = run_compose("config", "--format", "json")
    require(result.returncode == 0, "docker compose config failed")
    config: dict[str, Any] = json.loads(result.stdout)
    return config


@pytest.fixture(scope="module")
def secrets() -> dict[str, SecretStr]:
    values = read_dotenv(compose_env_file())
    return {
        "postgres admin password": SecretStr(values["CCE_POSTGRES_ADMIN_PASSWORD"]),
        "migrator password": SecretStr(values["CCE_MIGRATOR_DB_PASSWORD"]),
        "app password": SecretStr(values["CCE_APP_DB_PASSWORD"]),
    }


def service_text(rendered: dict[str, Any], name: str) -> str:
    return json.dumps(rendered["services"][name])


def test_compose_config_is_valid() -> None:
    result = run_compose("config", "--quiet")

    require(result.returncode == 0, "docker compose config --quiet failed")
    require(result.stdout == "" and result.stderr == "", "docker compose config --quiet printed")


def test_test_environment_uses_a_separate_project_and_database(
    rendered: dict[str, Any],
) -> None:
    postgres_env = rendered["services"]["postgres"]["environment"]

    require(rendered["name"] == COMPOSE_PROJECT, "the Compose project is not cce-test")
    require(postgres_env["POSTGRES_DB"] == "cce_test", "the database is not cce_test")
    require(set(rendered["services"]) == SERVICES, "unexpected set of services")
    require(
        all(v["name"].startswith(f"{COMPOSE_PROJECT}_") for v in rendered["volumes"].values()),
        "volumes are not scoped to the cce-test project",
    )


def test_api_service_has_no_migrator_url_and_no_postgres_password(
    rendered: dict[str, Any], secrets: dict[str, SecretStr]
) -> None:
    api = rendered["services"]["api"]
    environment = api["environment"]
    text = service_text(rendered, "api")

    require("CCE_MIGRATOR_DATABASE_URL" not in environment, "api has CCE_MIGRATOR_DATABASE_URL")
    require("POSTGRES_PASSWORD" not in environment, "api has POSTGRES_PASSWORD")
    require("CCE_POSTGRES_ADMIN_PASSWORD" not in environment, "api has the admin password name")
    require("env_file" not in api, "api uses env_file")
    require("cce_migrator" not in text, "api references the cce_migrator role")
    assert_no_leaks(
        text,
        {
            "postgres admin password": secrets["postgres admin password"],
            "migrator password": secrets["migrator password"],
        },
        "the rendered api service",
    )


def test_api_service_does_receive_its_own_runtime_credentials(
    rendered: dict[str, Any], secrets: dict[str, SecretStr]
) -> None:
    environment = rendered["services"]["api"]["environment"]
    database_url = environment["CCE_DATABASE_URL"]

    require(
        database_url.startswith("postgresql+asyncpg://cce_app:"),
        "the api DSN does not use the cce_app role",
    )
    require(
        secrets["app password"].get_secret_value() in database_url,
        "the api DSN does not use the generated app password",
    )
    require(environment["CCE_REDIS_URL"] == "redis://redis:6379/0", "unexpected api Redis URL")


def test_only_the_postgres_service_has_the_admin_password(
    rendered: dict[str, Any], secrets: dict[str, SecretStr]
) -> None:
    for name in ("redis", "migrate", "api"):
        assert_no_leaks(
            service_text(rendered, name),
            {"postgres admin password": secrets["postgres admin password"]},
            f"the rendered {name} service",
        )


def test_migrate_service_gets_only_the_migrator_credentials(
    rendered: dict[str, Any], secrets: dict[str, SecretStr]
) -> None:
    environment = rendered["services"]["migrate"]["environment"]

    require(
        environment["CCE_MIGRATOR_DATABASE_URL"].startswith("postgresql+asyncpg://cce_migrator:"),
        "the migrate DSN does not use the cce_migrator role",
    )
    require("CCE_DATABASE_URL" not in environment, "migrate has the runtime DSN")
    assert_no_leaks(
        service_text(rendered, "migrate"),
        {
            "app password": secrets["app password"],
            "postgres admin password": secrets["postgres admin password"],
        },
        "the rendered migrate service",
    )


def test_migrate_is_one_shot_and_the_api_waits_for_it(rendered: dict[str, Any]) -> None:
    services = rendered["services"]
    depends_on = services["api"]["depends_on"]

    require(services["migrate"]["restart"] == "no", "migrate restarts")
    require(services["migrate"]["command"][:2] == ["alembic", "-c"], "migrate is not alembic")
    require(services["migrate"]["image"] == services["api"]["image"], "migrate uses another image")
    require(
        depends_on["migrate"]["condition"] == "service_completed_successfully",
        "api does not wait for migrate to complete",
    )
    require(depends_on["postgres"]["condition"] == "service_healthy", "api ignores postgres health")
    require(depends_on["redis"]["condition"] == "service_healthy", "api ignores redis health")
    require(
        services["migrate"]["depends_on"]["postgres"]["condition"] == "service_healthy",
        "migrate ignores postgres health",
    )
    require("depends_on" not in services["postgres"], "postgres depends on another service")


def test_published_ports_are_bound_to_loopback_only(rendered: dict[str, Any]) -> None:
    for name in ("postgres", "redis", "api"):
        for port in rendered["services"][name]["ports"]:
            require(port["host_ip"] == "127.0.0.1", f"{name} publishes a port beyond loopback")


def test_application_containers_are_hardened(rendered: dict[str, Any]) -> None:
    services = rendered["services"]

    require(services["migrate"]["read_only"] is True, "migrate is not read-only")
    require(services["api"]["read_only"] is True, "api is not read-only")
    require(
        "no-new-privileges:true" in services["api"]["security_opt"],
        "api may gain privileges",
    )


def test_api_has_a_real_healthcheck_that_is_not_faked(rendered: dict[str, Any]) -> None:
    command = rendered["services"]["api"]["healthcheck"]["test"]

    require("/health/live" in " ".join(command), "the api healthcheck does not probe /health/live")
    require("CMD-SHELL" not in command and "NONE" not in command, "the api healthcheck is faked")
