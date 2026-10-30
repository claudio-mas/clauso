"""Erros do banco no envelope HTTP, sem SQL, nomes internos nem mensagens do driver."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import (
    DBAPIError,
    IntegrityError,
    InterfaceError,
    OperationalError,
    ProgrammingError,
)
from sqlalchemy.exc import TimeoutError as PoolTimeout

from cce.api.errors import ApiError, from_database_error

SECRET_SQL = "UPDATE cce.tenants SET name = 'segredo-SENTINEL'"


class DriverError(Exception):
    """Imita o erro do asyncpg, que carrega `constraint_name` e o SQLSTATE."""

    def __init__(self, sqlstate: str, constraint_name: str | None = None) -> None:
        super().__init__("driver message with segredo-SENTINEL")
        self.sqlstate = sqlstate
        self.constraint_name = constraint_name


class AdaptedError(Exception):
    """Imita o erro adaptado do dialeto asyncpg do SQLAlchemy: a causa é o erro do driver."""

    def __init__(self, cause: DriverError) -> None:
        super().__init__("adapted message with segredo-SENTINEL")
        self.sqlstate = cause.sqlstate
        self.__cause__ = cause


def integrity(sqlstate: str, constraint: str | None) -> IntegrityError:
    return IntegrityError(
        SECRET_SQL, {"name": "segredo-SENTINEL"}, AdaptedError(DriverError(sqlstate, constraint))
    )


def describe(error: ApiError) -> str:
    return f"{error.status_code} {error.code} {error.message} {error.details}"


@pytest.mark.parametrize(
    ("constraint", "field"),
    [
        ("uq_tenants_slug", "slug"),
        ("ck_tenants_name_not_blank", "name"),
        ("ck_tenants_currency_allowed", "default_currency"),
        ("ck_tenants_locale_allowed", "locale"),
        ("ck_tenants_timezone_known", "timezone"),
    ],
)
def test_a_known_constraint_is_a_conflict_naming_only_the_public_field(
    constraint: str, field: str
) -> None:
    error = from_database_error(integrity("23505", constraint))

    assert error.status_code == 409
    assert error.code == "CONFLICT"
    assert error.details == {"field": field}
    assert "SENTINEL" not in describe(error)
    assert constraint not in describe(error)


@pytest.mark.parametrize("constraint", ["ck_unknown_rule", "pk_tenants", None])
def test_an_unknown_constraint_is_an_internal_error(constraint: str | None) -> None:
    error = from_database_error(integrity("23514", constraint))

    assert error.status_code == 500
    assert error.code == "INTERNAL_ERROR"
    assert error.details == {}


@pytest.mark.parametrize(
    "database_error",
    [
        OperationalError(SECRET_SQL, None, Exception("connection refused")),
        InterfaceError(SECRET_SQL, None, Exception("connection closed")),
        PoolTimeout("QueuePool limit reached"),
        OSError("connection refused to db.invalid"),
        ConnectionRefusedError("refused"),
        TimeoutError(),
    ],
    ids=["operational", "interface", "pool-timeout", "os-error", "refused", "timeout"],
)
def test_an_unreachable_database_is_dependency_unavailable(database_error: Exception) -> None:
    error = from_database_error(database_error)

    assert error.status_code == 503
    assert error.code == "DEPENDENCY_UNAVAILABLE"
    assert "SENTINEL" not in describe(error)


def test_an_invalidated_connection_is_dependency_unavailable() -> None:
    error = DBAPIError(SECRET_SQL, None, Exception("terminated"), connection_invalidated=True)

    assert from_database_error(error).status_code == 503


@pytest.mark.parametrize(
    "database_error",
    [
        ProgrammingError(SECRET_SQL, None, Exception("syntax error at segredo-SENTINEL")),
        DBAPIError(SECRET_SQL, None, Exception("raise exception segredo-SENTINEL")),
    ],
    ids=["programming", "generic"],
)
def test_any_other_database_error_is_an_internal_error(database_error: Exception) -> None:
    error = from_database_error(database_error)

    assert error.status_code == 500
    assert error.code == "INTERNAL_ERROR"
    assert "SENTINEL" not in describe(error)
