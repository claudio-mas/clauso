"""Constraints, defaults e tipos de cce.tenants e cce.api_keys, contra PostgreSQL real.

As linhas são inseridas como `cce_migrator` (dono das tabelas) e cada tentativa é desfeita em
seguida. A RLS vale também para o dono (FORCE, migration 0002) e dispararia antes das constraints
(`WITH CHECK` vem primeiro), então cada transação começa suspendendo a RLS das duas tabelas com
DDL transacional: o rollback a restaura. O isolamento tem os próprios testes (`test_rls.py`).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from factories import api_key_values, tenant_values
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from .support import sqlstate

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("migrated_database")]

OK = "ok"
CHECK_VIOLATION = "23514"
NOT_NULL_VIOLATION = "23502"
UNIQUE_VIOLATION = "23505"
FOREIGN_KEY_VIOLATION = "23503"
STRING_TOO_LONG = "22001"
INVALID_TIME_ZONE = "22023"  # o PostgreSQL recusa o nome na própria expressão do CHECK

Statement = tuple[str, Mapping[str, Any]]


async def lift_rls(connection: AsyncConnection) -> None:
    """Suspende a RLS de `tenants` e `api_keys` até o fim da transação (o rollback a restaura)."""
    for table in ("tenants", "api_keys"):
        await connection.execute(text(f"ALTER TABLE cce.{table} NO FORCE ROW LEVEL SECURITY"))
        await connection.execute(text(f"ALTER TABLE cce.{table} DISABLE ROW LEVEL SECURITY"))


def insert_statement(table: str, values: Mapping[str, Any]) -> Statement:
    columns = ", ".join(values)
    placeholders = ", ".join(f":{column}" for column in values)
    return f"INSERT INTO cce.{table} ({columns}) VALUES ({placeholders})", values


async def attempt(connection: AsyncConnection, *statements: Statement) -> str:
    """Executa os comandos na mesma transação e devolve o SQLSTATE do primeiro que falhar.

    A transação é sempre desfeita, então nada fica no banco.
    """
    try:
        await lift_rls(connection)
        for sql, parameters in statements:
            await connection.execute(text(sql), dict(parameters))
    except DBAPIError as error:
        return sqlstate(error) or "unknown"
    finally:
        await connection.rollback()
    return OK


async def insert(connection: AsyncConnection, table: str, values: Mapping[str, Any]) -> str:
    return await attempt(connection, insert_statement(table, values))


async def insert_key(connection: AsyncConnection, **overrides: Any) -> str:
    """Cria um Tenant e uma chave dele na mesma transação; devolve o resultado."""
    tenant = tenant_values()
    key = api_key_values(tenant["id"], **overrides)
    return await attempt(
        connection, insert_statement("tenants", tenant), insert_statement("api_keys", key)
    )


# --- Tenant ---------------------------------------------------------------------------------


async def test_tenant_row_with_only_required_columns_gets_the_documented_defaults(
    migrator_connection: AsyncConnection,
) -> None:
    values = tenant_values()
    sql, parameters = insert_statement("tenants", values)
    await lift_rls(migrator_connection)
    await migrator_connection.execute(text(sql), dict(parameters))
    row = (
        await migrator_connection.execute(
            text(
                "SELECT status, plan, default_currency, timezone, locale, created_at, updated_at "
                "FROM cce.tenants WHERE id = :id"
            ),
            {"id": values["id"]},
        )
    ).one()
    await migrator_connection.rollback()

    assert tuple(row[:5]) == ("active", "development", "BRL", "America/Sao_Paulo", "pt-BR")
    for moment in row[5:]:
        assert moment.utcoffset() == timedelta(0)
        assert abs(datetime.now(UTC) - moment) < timedelta(minutes=5)


@pytest.mark.parametrize("slug", ["a", "empresa-a", "a1-b2-c3", "0", "x" * 63, "abc-123"])
async def test_tenant_accepts_valid_slugs(migrator_connection: AsyncConnection, slug: str) -> None:
    assert await insert(migrator_connection, "tenants", tenant_values(slug=slug)) == OK


@pytest.mark.parametrize(
    "slug",
    ["", "Empresa", "-a", "a-", "a--b", "a_b", "a b", "a.b", "á", "a/b", "A", "a\n", "-"],
)
async def test_tenant_rejects_invalid_slugs(
    migrator_connection: AsyncConnection, slug: str
) -> None:
    result = await insert(migrator_connection, "tenants", tenant_values(slug=slug))

    assert result == CHECK_VIOLATION


async def test_tenant_slug_is_limited_to_63_characters(
    migrator_connection: AsyncConnection,
) -> None:
    result = await insert(migrator_connection, "tenants", tenant_values(slug="x" * 64))

    assert result == STRING_TOO_LONG


async def test_tenant_slug_is_unique(migrator_connection: AsyncConnection) -> None:
    first = insert_statement("tenants", tenant_values(slug="slug-duplicado"))
    second = insert_statement("tenants", tenant_values(slug="slug-duplicado"))

    assert await attempt(migrator_connection, first, second) == UNIQUE_VIOLATION


async def test_tenant_primary_key_is_required_and_unique(
    migrator_connection: AsyncConnection,
) -> None:
    first = tenant_values()
    duplicate = tenant_values(id=first["id"])

    assert await insert(migrator_connection, "tenants", tenant_values(id=None)) == (
        NOT_NULL_VIOLATION
    )
    assert (
        await attempt(
            migrator_connection,
            insert_statement("tenants", first),
            insert_statement("tenants", duplicate),
        )
        == UNIQUE_VIOLATION
    )


@pytest.mark.parametrize("status", ["active", "suspended", "archived"])
async def test_tenant_accepts_the_three_statuses(
    migrator_connection: AsyncConnection, status: str
) -> None:
    assert await insert(migrator_connection, "tenants", tenant_values(status=status)) == OK


@pytest.mark.parametrize("status", ["deleted", "ACTIVE", "", "Active", "inactive"])
async def test_tenant_rejects_unknown_statuses(
    migrator_connection: AsyncConnection, status: str
) -> None:
    result = await insert(migrator_connection, "tenants", tenant_values(status=status))

    assert result == CHECK_VIOLATION


@pytest.mark.parametrize("currency", ["BRL", "USD", "EUR"])
async def test_tenant_accepts_the_three_currencies(
    migrator_connection: AsyncConnection, currency: str
) -> None:
    result = await insert(migrator_connection, "tenants", tenant_values(default_currency=currency))

    assert result == OK


@pytest.mark.parametrize("currency", ["GBP", "brl", "", "BR"])
async def test_tenant_rejects_other_currencies(
    migrator_connection: AsyncConnection, currency: str
) -> None:
    result = await insert(migrator_connection, "tenants", tenant_values(default_currency=currency))

    assert result == CHECK_VIOLATION


async def test_tenant_currency_is_limited_to_3_characters(
    migrator_connection: AsyncConnection,
) -> None:
    result = await insert(migrator_connection, "tenants", tenant_values(default_currency="BRLL"))

    assert result == STRING_TOO_LONG


@pytest.mark.parametrize("locale", ["pt-BR", "en-US"])
async def test_tenant_accepts_the_two_locales(
    migrator_connection: AsyncConnection, locale: str
) -> None:
    assert await insert(migrator_connection, "tenants", tenant_values(locale=locale)) == OK


@pytest.mark.parametrize("locale", ["fr-FR", "pt-br", "en", "", "pt_BR"])
async def test_tenant_rejects_other_locales(
    migrator_connection: AsyncConnection, locale: str
) -> None:
    result = await insert(migrator_connection, "tenants", tenant_values(locale=locale))

    assert result == CHECK_VIOLATION


@pytest.mark.parametrize("zone", ["America/Sao_Paulo", "UTC", "Europe/Lisbon", "Asia/Tokyo"])
async def test_tenant_accepts_known_time_zones(
    migrator_connection: AsyncConnection, zone: str
) -> None:
    assert await insert(migrator_connection, "tenants", tenant_values(timezone=zone)) == OK


@pytest.mark.parametrize("zone", ["Mars/Olympus", "", "America/Sao Paulo", "../etc/passwd", "x"])
async def test_tenant_rejects_unknown_time_zones(
    migrator_connection: AsyncConnection, zone: str
) -> None:
    result = await insert(migrator_connection, "tenants", tenant_values(timezone=zone))

    assert result in {CHECK_VIOLATION, INVALID_TIME_ZONE}


@pytest.mark.parametrize("name", ["", " ", "   ", "\t", "\n", " \t\n "])
async def test_tenant_rejects_blank_names(migrator_connection: AsyncConnection, name: str) -> None:
    result = await insert(migrator_connection, "tenants", tenant_values(name=name))

    assert result == CHECK_VIOLATION


async def test_tenant_name_is_limited_to_200_characters(
    migrator_connection: AsyncConnection,
) -> None:
    assert await insert(migrator_connection, "tenants", tenant_values(name="n" * 200)) == OK
    assert await insert(migrator_connection, "tenants", tenant_values(name="n" * 201)) == (
        STRING_TOO_LONG
    )


@pytest.mark.parametrize(
    "column", ["name", "slug", "status", "plan", "default_currency", "timezone", "locale"]
)
async def test_tenant_columns_are_not_null(
    migrator_connection: AsyncConnection, column: str
) -> None:
    result = await insert(migrator_connection, "tenants", tenant_values(**{column: None}))

    assert result == NOT_NULL_VIOLATION


# --- APIKey ---------------------------------------------------------------------------------


async def test_api_key_with_valid_values_is_accepted(
    migrator_connection: AsyncConnection,
) -> None:
    assert await insert_key(migrator_connection) == OK


@pytest.mark.parametrize("length", [0, 1, 31, 33, 64])
async def test_api_key_digest_must_have_exactly_32_bytes(
    migrator_connection: AsyncConnection, length: int
) -> None:
    result = await insert_key(migrator_connection, secret_digest=b"\x01" * length)

    assert result == CHECK_VIOLATION


@pytest.mark.parametrize("version", [0, -1, -32768])
async def test_api_key_pepper_version_must_be_positive(
    migrator_connection: AsyncConnection, version: int
) -> None:
    assert await insert_key(migrator_connection, pepper_version=version) == CHECK_VIOLATION


@pytest.mark.parametrize(
    "scopes",
    [["tenant:read"], ["tenant:write"], ["tenant:read", "tenant:write"], ["tenant:read"] * 2],
)
async def test_api_key_accepts_allowed_scopes(
    migrator_connection: AsyncConnection, scopes: list[str]
) -> None:
    assert await insert_key(migrator_connection, scopes=scopes) == OK


@pytest.mark.parametrize(
    "scopes",
    [
        ["admin"],
        ["tenant:read", "tenant:admin"],
        ["TENANT:READ"],
        ["tenant:*"],
        [""],
        [None],
        ["tenant:read", None],
        ["proposal:write"],
    ],
)
async def test_api_key_rejects_scopes_outside_the_allowed_set(
    migrator_connection: AsyncConnection, scopes: list[str | None]
) -> None:
    assert await insert_key(migrator_connection, scopes=scopes) == CHECK_VIOLATION


async def test_api_key_must_expire_after_it_was_created(
    migrator_connection: AsyncConnection,
) -> None:
    created = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)

    later = await insert_key(
        migrator_connection, created_at=created, expires_at=created + timedelta(seconds=1)
    )
    same = await insert_key(migrator_connection, created_at=created, expires_at=created)
    earlier = await insert_key(
        migrator_connection, created_at=created, expires_at=created - timedelta(days=1)
    )

    assert (later, same, earlier) == (OK, CHECK_VIOLATION, CHECK_VIOLATION)


@pytest.mark.parametrize("label", ["", " ", "   ", "\t", "\n", " \t\n "])
async def test_api_key_rejects_blank_labels(
    migrator_connection: AsyncConnection, label: str
) -> None:
    assert await insert_key(migrator_connection, label=label) == CHECK_VIOLATION


async def test_api_key_label_is_limited_to_100_characters(
    migrator_connection: AsyncConnection,
) -> None:
    assert await insert_key(migrator_connection, label="l" * 100) == OK
    assert await insert_key(migrator_connection, label="l" * 101) == STRING_TOO_LONG


@pytest.mark.parametrize(
    "column", ["tenant_id", "label", "secret_digest", "pepper_version", "scopes", "expires_at"]
)
async def test_api_key_columns_are_not_null(
    migrator_connection: AsyncConnection, column: str
) -> None:
    assert await insert_key(migrator_connection, **{column: None}) == NOT_NULL_VIOLATION


async def test_api_key_revoked_at_is_optional(migrator_connection: AsyncConnection) -> None:
    assert await insert_key(migrator_connection, revoked_at=None) == OK
    assert await insert_key(migrator_connection, revoked_at=datetime.now(UTC)) == OK


async def test_api_key_requires_an_existing_tenant(migrator_connection: AsyncConnection) -> None:
    result = await insert(migrator_connection, "api_keys", api_key_values(uuid4()))

    assert result == FOREIGN_KEY_VIOLATION


async def test_deleting_a_tenant_with_keys_is_restricted(
    migrator_connection: AsyncConnection,
) -> None:
    tenant = tenant_values()
    result = await attempt(
        migrator_connection,
        insert_statement("tenants", tenant),
        insert_statement("api_keys", api_key_values(tenant["id"])),
        ("DELETE FROM cce.tenants WHERE id = :id", {"id": tenant["id"]}),
    )

    assert result == FOREIGN_KEY_VIOLATION


# --- datas ----------------------------------------------------------------------------------


async def test_every_timestamp_column_is_timestamptz(
    migrator_connection: AsyncConnection,
) -> None:
    rows = (
        await migrator_connection.execute(
            text("""
                select table_name, column_name, data_type
                from information_schema.columns
                where table_schema = 'cce' and table_name in ('tenants', 'api_keys')
                  and (column_name like '%\\_at' or data_type like 'timestamp%')
                order by table_name, column_name
            """)
        )
    ).all()
    await migrator_connection.rollback()

    assert [tuple(row) for row in rows] == [
        ("api_keys", "created_at", "timestamp with time zone"),
        ("api_keys", "expires_at", "timestamp with time zone"),
        ("api_keys", "revoked_at", "timestamp with time zone"),
        ("tenants", "created_at", "timestamp with time zone"),
        ("tenants", "updated_at", "timestamp with time zone"),
    ]


async def test_instants_come_back_in_utc_whatever_the_session_time_zone(
    migrator_connection: AsyncConnection,
) -> None:
    await lift_rls(migrator_connection)
    await migrator_connection.execute(text("SET TIME ZONE 'America/Sao_Paulo'"))
    tenant = tenant_values()
    created = datetime(2029, 1, 1, 12, 0, tzinfo=UTC)
    expires = datetime(2030, 1, 1, 12, 0, tzinfo=UTC)
    key = api_key_values(tenant["id"], created_at=created, expires_at=expires)
    for sql, parameters in (insert_statement("tenants", tenant), insert_statement("api_keys", key)):
        await migrator_connection.execute(text(sql), dict(parameters))
    row = (
        await migrator_connection.execute(
            text("SELECT created_at, expires_at FROM cce.api_keys WHERE id = :id"),
            {"id": key["id"]},
        )
    ).one()
    await migrator_connection.rollback()

    assert (row[0], row[1]) == (created, expires)
    assert row[0].utcoffset() == timedelta(0)
    assert row[1].utcoffset() == timedelta(0)


# --- estrutura ------------------------------------------------------------------------------


async def test_foreign_key_is_restrict_and_tenant_id_is_indexed(
    migrator_connection: AsyncConnection,
) -> None:
    foreign_key = (
        await migrator_connection.execute(
            text("""
                select conname, confdeltype::text
                from pg_constraint
                where conrelid = 'cce.api_keys'::regclass and contype = 'f'
            """)
        )
    ).one()
    index = (
        await migrator_connection.execute(
            text("""
                select indexname from pg_indexes
                where schemaname = 'cce' and tablename = 'api_keys'
                  and indexname = 'ix_api_keys_tenant_id'
            """)
        )
    ).scalar_one_or_none()
    await migrator_connection.rollback()

    assert tuple(foreign_key) == ("fk_api_keys_tenant_id_tenants", "r")
    assert index == "ix_api_keys_tenant_id"
