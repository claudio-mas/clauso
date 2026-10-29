from __future__ import annotations

import pytest
from db_support import assert_disposable_database

PASSWORD = "guard-password-SENTINEL"


def dsn(database: str, *, user: str = "cce_migrator", host: str = "db.internal") -> str:
    return f"postgresql+asyncpg://{user}:{PASSWORD}@{host}:5432/{database}"


@pytest.mark.parametrize("database", ["cce_test", "cce_test_ci", "cce_test_a1"])
def test_accepts_the_test_environment_with_a_cce_test_database(database: str) -> None:
    assert_disposable_database("test", dsn(database), "migrator")


@pytest.mark.parametrize("environment", ["local", "staging", "production", "", "TEST"])
def test_refuses_any_other_environment(environment: str) -> None:
    with pytest.raises(pytest.fail.Exception):
        assert_disposable_database(environment, dsn("cce_test"), "migrator")


@pytest.mark.parametrize("database", ["cce", "postgres", "test_cce", "mycce_test", "CCE_TEST", ""])
def test_refuses_a_database_without_the_cce_test_prefix(database: str) -> None:
    with pytest.raises(pytest.fail.Exception):
        assert_disposable_database("test", dsn(database), "migrator")


@pytest.mark.parametrize(
    "url",
    [
        # o nome aparece, mas não é o banco: a validação parseia a URL em vez de buscar substring
        dsn("cce", user="cce_test"),
        dsn("cce", host="cce_test.internal"),
        dsn("cce") + "?application_name=cce_test",
        f"postgresql+asyncpg://cce_test:{PASSWORD}@db.internal:5432/cce#cce_test",
        "not a url at all " + PASSWORD,
        "",
    ],
)
def test_validates_the_parsed_database_name_not_a_substring(url: str) -> None:
    with pytest.raises(pytest.fail.Exception):
        assert_disposable_database("test", url, "migrator")


@pytest.mark.parametrize(
    ("environment", "url"),
    [
        ("local", dsn("cce")),
        ("test", dsn("cce")),
        ("test", "not a url at all " + PASSWORD),
        ("production", dsn("cce_test", host="prod-db.example.com", user="cce_app")),
    ],
)
def test_refusal_never_shows_the_dsn(environment: str, url: str) -> None:
    with pytest.raises(pytest.fail.Exception) as excinfo:
        assert_disposable_database(environment, url, "migrator")

    message = str(excinfo.value) + repr(excinfo.value)
    for fragment in (PASSWORD, "db.internal", "prod-db.example.com", "cce_app:", "@"):
        assert fragment not in message
    assert "migrator" in message


QUERY_OVERRIDES = [
    "?database=cce",
    "?database=cce_test",
    "?host=other.internal",
    "?user=postgres",
    "?dsn=postgresql://postgres@other.internal/cce",
    "?options=-c%20search_path=public",
    "?ssl=disable",
]


@pytest.mark.parametrize("query", QUERY_OVERRIDES)
@pytest.mark.parametrize("role", ["runtime", "migrator"])
def test_refuses_any_query_parameter_because_the_driver_lets_it_override_the_target(
    role: str, query: str
) -> None:
    """O asyncpg aplica a query por cima da URL: '...@h/cce_test?database=cce' conecta em `cce`."""
    with pytest.raises(pytest.fail.Exception) as excinfo:
        assert_disposable_database("test", dsn("cce_test") + query, role)

    message = str(excinfo.value)
    assert role in message and "query" in message
    for fragment in (PASSWORD, "db.internal", "cce_migrator:", "database=", "other.internal"):
        assert fragment not in message


def test_the_effective_driver_parameters_are_what_the_guard_looks_at() -> None:
    from sqlalchemy.engine import make_url

    url = make_url(dsn("cce_test") + "?database=cce")
    _, kwargs = url.get_dialect()().create_connect_args(url)

    assert url.database == "cce_test"  # o que a guarda antiga validava
    assert kwargs["database"] == "cce"  # o que o asyncpg realmente usaria
    with pytest.raises(pytest.fail.Exception):
        assert_disposable_database("test", url, "migrator")


def test_refuses_a_driver_it_cannot_inspect() -> None:
    with pytest.raises(pytest.fail.Exception):
        assert_disposable_database("test", "nosuchdriver://u:p@h/cce_test", "migrator")


def test_defense_in_depth_refuses_when_the_driver_would_use_another_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mesmo sem query, o banco que o driver vai usar é que vale (aqui, forçado a `cce`)."""
    from typing import Any

    from sqlalchemy.dialects.postgresql.asyncpg import PGDialect_asyncpg

    original: Any = PGDialect_asyncpg.create_connect_args

    def redirected(self: PGDialect_asyncpg, url: object) -> tuple[list[object], dict[str, object]]:
        arguments, kwargs = original(self, url)
        return arguments, {**kwargs, "database": "cce"}

    monkeypatch.setattr(PGDialect_asyncpg, "create_connect_args", redirected)

    with pytest.raises(pytest.fail.Exception):
        assert_disposable_database("test", dsn("cce_test"), "migrator")
