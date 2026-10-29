"""O Compose que os testes mutantes usam tem de mirar o mesmo banco descartável já validado.

A fixture de settings mistura dotenv e overrides `CCE_*` do processo, mas `run_compose` descarta os
overrides e volta ao dotenv. Sem esta checagem, o teste faria `downgrade` no banco validado e o
serviço `migrate` faria `upgrade` em outro. Aqui a configuração renderizada é sintética.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest
from db_support import assert_compose_targets_validated_database, compose_target_problems
from sqlalchemy.engine import make_url

from cce.shared.config import MigrationSettings

PASSWORD = "compose-target-password-SENTINEL"
VALIDATED = make_url(f"postgresql+asyncpg://cce_migrator:{PASSWORD}@127.0.0.1:5432/cce_test")


def rendered(
    *,
    environment: str = "test",
    migrate_database: str = "cce_test",
    postgres_database: str = "cce_test",
    published: str = "5432",
    host_ip: str = "127.0.0.1",
    query: str = "",
) -> dict[str, Any]:
    return {
        "services": {
            "migrate": {
                "environment": {
                    "CCE_ENV": environment,
                    "CCE_MIGRATOR_DATABASE_URL": (
                        f"postgresql+asyncpg://cce_migrator:{PASSWORD}"
                        f"@postgres:5432/{migrate_database}{query}"
                    ),
                }
            },
            "api": {
                "environment": {
                    "CCE_ENV": environment,
                    "CCE_DATABASE_URL": (
                        f"postgresql+asyncpg://cce_app:{PASSWORD}@postgres:5432/{migrate_database}"
                    ),
                }
            },
            "postgres": {
                "environment": {"POSTGRES_DB": postgres_database},
                "ports": [
                    {"host_ip": host_ip, "target": 5432, "published": published, "mode": "ingress"}
                ],
            },
        }
    }


def test_a_coherent_test_configuration_has_no_problems() -> None:
    assert compose_target_problems(rendered(), VALIDATED) == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"environment": "local"},
        {"environment": "staging"},
        {"environment": ""},
        {"migrate_database": "cce"},
        {"migrate_database": "cce_test_other"},  # disponível, mas não é o banco validado
        {"postgres_database": "cce"},
        {"postgres_database": "cce_test_other"},
        {"published": "5433"},
        {"host_ip": "0.0.0.0"},
        {"query": "?database=cce"},
    ],
)
def test_a_divergent_configuration_is_reported_without_echoing_any_value(
    overrides: dict[str, str],
) -> None:
    problems = compose_target_problems(rendered(**overrides), VALIDATED)

    assert problems
    text = " ".join(problems)
    for fragment in (PASSWORD, "postgres:5432", "cce_migrator:", "database=", "0.0.0.0"):
        assert fragment not in text


@pytest.mark.parametrize("config", [{}, {"services": {}}, {"services": {"migrate": {}}}])
def test_a_configuration_missing_the_services_is_refused(config: dict[str, Any]) -> None:
    assert compose_target_problems(config, VALIDATED)


def test_a_validated_host_outside_loopback_is_refused() -> None:
    remote = make_url(f"postgresql+asyncpg://cce_migrator:{PASSWORD}@db.example.com:5432/cce_test")

    assert compose_target_problems(rendered(), remote)


def fake_settings(monkeypatch: pytest.MonkeyPatch, clean_cce_env: Any) -> MigrationSettings:
    monkeypatch.setenv("CCE_ENV", "test")
    monkeypatch.setenv("CCE_MIGRATOR_DATABASE_URL", VALIDATED.render_as_string(hide_password=False))
    from cce.shared.config import load_migration_settings

    return load_migration_settings()


def test_the_check_refuses_before_anything_else_when_the_compose_diverges(
    monkeypatch: pytest.MonkeyPatch, clean_cce_env: Any
) -> None:
    import db_support

    settings = fake_settings(monkeypatch, clean_cce_env)
    calls: list[tuple[str, ...]] = []

    def fake_run_compose(*args: str, **_: Any) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        diverging = rendered(environment="local", migrate_database="cce", postgres_database="cce")
        return subprocess.CompletedProcess(args, 0, json.dumps(diverging), "")

    monkeypatch.setattr(db_support, "run_compose", fake_run_compose)

    with pytest.raises(pytest.fail.Exception) as excinfo:
        assert_compose_targets_validated_database(settings)

    message = str(excinfo.value) + repr(excinfo.value)
    assert PASSWORD not in message and "postgres:5432" not in message
    assert calls == [("config", "--format", "json")]  # só renderizou; nada mutante


def test_the_check_accepts_a_coherent_compose(
    monkeypatch: pytest.MonkeyPatch, clean_cce_env: Any
) -> None:
    import db_support

    settings = fake_settings(monkeypatch, clean_cce_env)
    monkeypatch.setattr(
        db_support,
        "run_compose",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, json.dumps(rendered()), ""),
    )

    assert_compose_targets_validated_database(settings)


@pytest.mark.parametrize("stdout", ["", "not json", "[]"])
def test_the_check_refuses_when_compose_config_fails_or_is_unreadable(
    monkeypatch: pytest.MonkeyPatch, clean_cce_env: Any, stdout: str
) -> None:
    import db_support

    settings = fake_settings(monkeypatch, clean_cce_env)
    monkeypatch.setattr(
        db_support, "run_compose", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout, "")
    )

    with pytest.raises(pytest.fail.Exception):
        assert_compose_targets_validated_database(settings)

    monkeypatch.setattr(
        db_support,
        "run_compose",
        lambda *a, **k: subprocess.CompletedProcess(a, 1, json.dumps(rendered()), PASSWORD),
    )
    with pytest.raises(pytest.fail.Exception) as excinfo:
        assert_compose_targets_validated_database(settings)
    assert PASSWORD not in str(excinfo.value)


# --- endpoint interno: o banco com o mesmo nome em outra instância não conta ------------------

OTHER = "other-db.invalid"


def migrate_url(host: str = "postgres", port: str = ":5432", user: str = "cce_migrator") -> str:
    return f"postgresql+asyncpg://{user}:{PASSWORD}@{host}{port}/cce_test"


def api_url(host: str = "postgres", port: str = ":5432", user: str = "cce_app") -> str:
    return f"postgresql+asyncpg://{user}:{PASSWORD}@{host}{port}/cce_test"


def with_url(service: str, variable: str, url: str) -> dict[str, Any]:
    config = rendered()
    config["services"][service]["environment"][variable] = url
    return config


MIGRATE = ("migrate", "CCE_MIGRATOR_DATABASE_URL")
API = ("api", "CCE_DATABASE_URL")

DIVERGENT_ENDPOINTS = [
    # host
    (MIGRATE, migrate_url(host=OTHER)),
    (MIGRATE, migrate_url(host="POSTGRES")),
    (MIGRATE, migrate_url(host="Postgres")),
    (MIGRATE, migrate_url(host="postgres.example.com")),
    (MIGRATE, migrate_url(host="localhost")),
    (MIGRATE, migrate_url(host="127.0.0.1")),
    (MIGRATE, migrate_url(host="[::1]")),
    # porta interna
    (MIGRATE, migrate_url(port=":6543")),
    (MIGRATE, migrate_url(port=":5433")),
    (MIGRATE, migrate_url(port="")),
    # userinfo diferente do role esperado
    (MIGRATE, migrate_url(user="postgres")),
    (MIGRATE, migrate_url(user="cce_app")),
    (MIGRATE, migrate_url(user="cce_auth")),
    # query
    (MIGRATE, migrate_url() + "?host=" + OTHER),
    (MIGRATE, migrate_url() + "?port=6543"),
    # o mesmo vale para o DSN runtime do serviço api
    (API, api_url(host=OTHER)),
    (API, api_url(host="POSTGRES")),
    (API, api_url(host="localhost")),
    (API, api_url(port=":6543")),
    (API, api_url(port="")),
    (API, api_url(user="cce_migrator")),
    (API, api_url(user="postgres")),
    (API, api_url() + "?host=" + OTHER),
]


@pytest.mark.parametrize(("target", "url"), DIVERGENT_ENDPOINTS)
def test_a_service_that_reaches_another_endpoint_is_refused_without_echoing_it(
    target: tuple[str, str], url: str
) -> None:
    problems = compose_target_problems(with_url(*target, url), VALIDATED)

    assert problems
    text = " ".join(problems)
    for fragment in (PASSWORD, OTHER, "6543", "5433", "localhost", "cce_app:", "cce_migrator:"):
        assert fragment not in text


def test_the_internal_endpoint_is_exactly_postgres_5432_for_every_service() -> None:
    assert compose_target_problems(rendered(), VALIDATED) == []


def test_any_other_service_with_a_database_url_is_refused() -> None:
    config = rendered()
    config["services"]["worker"] = {
        "environment": {"CCE_DATABASE_URL": api_url(host=OTHER), "UNRELATED": "x"}
    }

    assert compose_target_problems(config, VALIDATED)

    config["services"]["worker"] = {"environment": {"SOME_DB": api_url()}}
    assert compose_target_problems(config, VALIDATED)  # nome de variável desconhecido


def test_a_service_without_database_urls_is_ignored() -> None:
    config = rendered()
    config["services"]["redis"] = {"environment": {"CCE_REDIS_URL": "redis://redis:6379/0"}}

    assert compose_target_problems(config, VALIDATED) == []
