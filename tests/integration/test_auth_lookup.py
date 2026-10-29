"""Função de lookup `cce_private.lookup_api_key` (migration 0003): chamada pelo runtime, sem
contexto de Tenant, devolve só o candidato de uma chave ativa e nunca amplia o que o runtime lê.

Estes testes manipulam digests (reais, gravados pelo próprio teste), por isso levam `sensitive`:
uma falha mostra só texto fixo. Nenhuma verificação usa `assert` com digest, chave ou segredo
nos operandos; elas passam por `require`, que não imprime valor. Os `id` dos parâmetros são fixos.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from db_support import OpenSession, OpenTenantSession
from seeding import SeededTenant, apply_context, seed_key
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

from cce.integrations.api_keys.model import APIKey
from cce.integrations.api_keys.repository import METADATA_COLUMNS, get_key_metadata

from .support import require, sql_outcome, sqlstate

pytestmark = [
    pytest.mark.integration,
    pytest.mark.sensitive,
    pytest.mark.usefixtures("migrated_database"),
]

INSUFFICIENT_PRIVILEGE = "42501"
UNDEFINED_FUNCTION = "42883"
LOOKUP = "SELECT * FROM cce_private.lookup_api_key(:key_id)"
QUALIFIED = "cce_private.lookup_api_key(uuid)"


async def candidates(session: AsyncSession, key_id: UUID | None) -> list[tuple[Any, ...]]:
    result = await session.execute(text(LOOKUP).bindparams(key_id=key_id))
    return [tuple(row) for row in result.all()]


async def execute_as_tenant(engine: AsyncEngine, tenant: SeededTenant, statement: str) -> None:
    async with engine.begin() as connection:
        await apply_context(connection, tenant.id)
        await connection.execute(text(statement), {"tenant": tenant.id})


async def key_with_digest(
    engine: AsyncEngine, tenant: SeededTenant, **overrides: Any
) -> tuple[SeededTenant, bytes]:
    digest = os.urandom(32)
    keyed = await seed_key(engine, tenant, secret_digest=digest, pepper_version=2, **overrides)
    return keyed, digest


# --- o que a função devolve ------------------------------------------------------------------


async def test_it_returns_the_candidate_of_each_active_key_without_tenant_context(
    migrator_engine: AsyncEngine,
    runtime_no_context: OpenSession,
    tenant_a: SeededTenant,
    tenant_b: SeededTenant,
) -> None:
    key_a, digest_a = await key_with_digest(migrator_engine, tenant_a, scopes=["tenant:read"])
    key_b, digest_b = await key_with_digest(migrator_engine, tenant_b)
    assert key_a.key_id is not None and key_b.key_id is not None

    async with runtime_no_context() as session:
        rows_a = await candidates(session, key_a.key_id)
        rows_b = await candidates(session, key_b.key_id)

    require(len(rows_a) == 1 and len(rows_b) == 1, "each lookup must return exactly one row")
    key_id, tenant_id, digest, pepper_version, scopes = rows_a[0]
    require(key_id == key_a.key_id and tenant_id == tenant_a.id, "wrong key or tenant for A")
    require(bytes(digest) == digest_a and pepper_version == 2, "wrong digest or pepper for A")
    require(list(scopes) == ["tenant:read"], "wrong scopes for A")
    require(rows_b[0][1] == tenant_b.id, "the lookup of B did not return the Tenant of B")
    require(bytes(rows_b[0][2]) == digest_b, "wrong digest for B")


async def test_it_returns_nothing_for_an_unknown_key_and_for_null(
    runtime_no_context: OpenSession,
) -> None:
    async with runtime_no_context() as session:
        unknown = await candidates(session, uuid4())
        null = await candidates(session, None)

    require(unknown == [] and null == [], "an unknown or null key id must return no rows")


async def test_it_works_whatever_search_path_and_row_security_the_caller_set(
    migrator_engine: AsyncEngine,
    runtime_no_context: OpenSession,
    tenant_a: SeededTenant,
) -> None:
    """O definer fixa o próprio `search_path` e `row_security`, e qualifica os objetos."""
    keyed, _ = await key_with_digest(migrator_engine, tenant_a)
    assert keyed.key_id is not None

    async with runtime_no_context() as session:
        await session.execute(text("SET LOCAL search_path = ''"))
        await session.execute(text("SET LOCAL row_security = off"))
        rows = await candidates(session, keyed.key_id)

    require(len(rows) == 1, "the lookup depends on the caller's search_path or row_security")


INACTIVE_STATES = {
    "revoked": "UPDATE cce.api_keys SET revoked_at = now() WHERE id = :key",
    "tenant-suspended": "UPDATE cce.tenants SET status = 'suspended' WHERE id = :tenant",
    "tenant-archived": "UPDATE cce.tenants SET status = 'archived' WHERE id = :tenant",
}


@pytest.mark.parametrize("change", list(INACTIVE_STATES.values()), ids=list(INACTIVE_STATES))
async def test_it_returns_nothing_for_a_revoked_key_or_an_inactive_tenant(
    migrator_engine: AsyncEngine,
    runtime_no_context: OpenSession,
    tenant_a: SeededTenant,
    change: str,
) -> None:
    keyed, _ = await key_with_digest(migrator_engine, tenant_a)
    assert keyed.key_id is not None
    async with migrator_engine.begin() as connection:
        await apply_context(connection, tenant_a.id)
        await connection.execute(text(change), {"key": keyed.key_id, "tenant": tenant_a.id})

    async with runtime_no_context() as session:
        rows = await candidates(session, keyed.key_id)

    require(rows == [], "an inactive key must not be a candidate")


async def test_it_returns_nothing_for_an_expired_key(
    migrator_engine: AsyncEngine,
    runtime_no_context: OpenSession,
    tenant_a: SeededTenant,
) -> None:
    created = datetime.now(UTC) - timedelta(days=2)
    keyed, _ = await key_with_digest(
        migrator_engine, tenant_a, created_at=created, expires_at=created + timedelta(days=1)
    )
    assert keyed.key_id is not None

    async with runtime_no_context() as session:
        rows = await candidates(session, keyed.key_id)

    require(rows == [], "an expired key must not be a candidate")


# --- a assinatura e a configuração da função -------------------------------------------------

FUNCTION_CATALOG = """
    select pg_get_function_arguments(p.oid), pg_get_function_result(p.oid),
           pg_get_userbyid(p.proowner), p.prosecdef, p.provolatile::text, p.proretset,
           coalesce(p.proconfig, array[]::text[]), p.proleakproof,
           (select rolcanlogin from pg_roles where oid = p.proowner)
    from pg_proc p
    where p.oid = to_regprocedure(:signature)
"""


async def test_the_function_takes_one_uuid_and_nothing_else(
    migrator_connection: AsyncConnection,
) -> None:
    row = (
        await migrator_connection.execute(text(FUNCTION_CATALOG), {"signature": QUALIFIED})
    ).one()
    await migrator_connection.rollback()

    arguments, result, *_ = row
    require(arguments == "p_key_id uuid", "the function must take only p_key_id uuid")
    require(
        result
        == "TABLE(key_id uuid, tenant_id uuid, secret_digest bytea, pepper_version smallint, "
        "scopes text[])",
        "the function must return only key_id, tenant_id, secret_digest, pepper_version, scopes",
    )


async def test_the_function_is_a_definer_of_cce_auth_with_a_fixed_search_path(
    migrator_connection: AsyncConnection,
) -> None:
    row = (
        await migrator_connection.execute(text(FUNCTION_CATALOG), {"signature": QUALIFIED})
    ).one()
    await migrator_connection.rollback()

    _, _, owner, definer, volatility, returns_set, config, leakproof, owner_can_login = row
    require(owner == "cce_auth" and owner_can_login is False, "the owner must be NOLOGIN cce_auth")
    require(definer is True, "the function must be SECURITY DEFINER")
    require(volatility == "s", "the function must be STABLE")
    require(returns_set is True and leakproof is False, "unexpected set or leakproof flags")
    require(
        sorted(config) == ["row_security=on", "search_path=pg_catalog, pg_temp"],
        "the function must fix search_path to pg_catalog, pg_temp and row_security to on",
    )


async def test_only_the_runtime_and_the_owner_can_execute_the_function(
    migrator_connection: AsyncConnection,
) -> None:
    grantees = (
        await migrator_connection.execute(
            text("""
                select case when a.grantee = 0 then 'PUBLIC' else pg_get_userbyid(a.grantee) end,
                       a.is_grantable
                from pg_proc p
                cross join lateral aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a
                where p.oid = to_regprocedure(:signature) and a.privilege_type = 'EXECUTE'
                order by 1
            """),
            {"signature": QUALIFIED},
        )
    ).all()
    flags = (
        await migrator_connection.execute(
            text("""
                select has_function_privilege('cce_app', :signature, 'EXECUTE'),
                       has_function_privilege('cce_migrator', :signature, 'EXECUTE')
            """),
            {"signature": QUALIFIED},
        )
    ).one()
    await migrator_connection.rollback()

    require(
        [tuple(row) for row in grantees] == [("cce_app", False), ("cce_auth", False)],
        "EXECUTE must belong to cce_app and the owner only, without grant option",
    )
    require(tuple(flags) == (True, False), "cce_migrator must not execute the lookup")


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT * FROM cce_private.lookup_api_key(gen_random_uuid(), gen_random_uuid())",
        "SELECT * FROM cce_private.lookup_api_key('%')",
        "SELECT * FROM cce_private.lookup_api_key(gen_random_uuid(), 10, 0)",
        "SELECT * FROM cce_private.lookup_api_key()",
    ],
    ids=["tenant-argument", "pattern-argument", "pagination-arguments", "no-argument"],
)
async def test_the_function_rejects_any_other_shape_of_call(
    app_connection: AsyncConnection, statement: str
) -> None:
    valid = await sql_outcome(app_connection, "SELECT * FROM cce_private.lookup_api_key(NULL)")
    rejected = await sql_outcome(app_connection, statement)

    require(valid == "ok", "the lookup function does not exist")
    require(
        rejected in (UNDEFINED_FUNCTION, "22P02"),
        "the lookup accepted a call with a tenant, a pattern or pagination",
    )


# --- o que o runtime e o migrator não herdam -------------------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT secret_digest FROM cce.api_keys",
        "SELECT pepper_version FROM cce.api_keys",
        "SELECT * FROM cce.api_keys",
        "SELECT secret_digest, pepper_version FROM cce.api_keys WHERE false",
        "SET ROLE cce_auth",
        "SET SESSION AUTHORIZATION cce_auth",
        "SET ROLE cce_migrator",
    ],
    ids=[
        "digest",
        "pepper",
        "star",
        "digest-and-pepper-no-rows",
        "set-role-auth",
        "session-authorization-auth",
        "set-role-migrator",
    ],
)
async def test_the_runtime_cannot_read_the_digest_or_assume_another_role(
    app_connection: AsyncConnection, statement: str
) -> None:
    require(
        await sql_outcome(app_connection, statement) == INSUFFICIENT_PRIVILEGE,
        "the runtime role reached the digest or changed role",
    )


async def test_the_runtime_executes_the_function_but_does_not_own_or_alter_it(
    app_connection: AsyncConnection,
) -> None:
    execute = await sql_outcome(app_connection, "SELECT * FROM cce_private.lookup_api_key(NULL)")
    changes = {
        "replace": await sql_outcome(
            app_connection,
            "CREATE OR REPLACE FUNCTION cce_private.lookup_api_key(uuid) "
            "RETURNS integer LANGUAGE sql AS 'select 1'",
        ),
        "drop": await sql_outcome(app_connection, f"DROP FUNCTION {QUALIFIED}"),
        "alter": await sql_outcome(
            app_connection, f"ALTER FUNCTION {QUALIFIED} SET search_path = public"
        ),
        "owner": await sql_outcome(app_connection, f"ALTER FUNCTION {QUALIFIED} OWNER TO cce_app"),
    }

    require(execute == "ok", "the runtime must be able to execute the lookup")
    require(
        changes == dict.fromkeys(changes, INSUFFICIENT_PRIVILEGE),
        "the runtime role changed the lookup function",
    )


async def test_the_migrator_does_not_inherit_the_global_read_of_cce_auth(
    migrator_engine: AsyncEngine,
    migrator_connection: AsyncConnection,
    tenant_a: SeededTenant,
    tenant_b: SeededTenant,
) -> None:
    await key_with_digest(migrator_engine, tenant_a)
    await key_with_digest(migrator_engine, tenant_b)

    visible = (
        await migrator_connection.execute(
            text("SELECT (SELECT count(*) FROM cce.tenants), (SELECT count(*) FROM cce.api_keys)")
        )
    ).one()
    digests = (
        await migrator_connection.execute(text("SELECT count(secret_digest) FROM cce.api_keys"))
    ).scalar_one()
    lookup = await sql_outcome(
        migrator_connection, "SELECT * FROM cce_private.lookup_api_key(gen_random_uuid())"
    )

    require(tuple(visible) == (0, 0), "NOINHERIT cce_migrator read rows without a tenant context")
    require(digests == 0, "NOINHERIT cce_migrator read digests without a tenant context")
    require(lookup == INSUFFICIENT_PRIVILEGE, "cce_migrator executed the lookup")


async def test_assuming_cce_auth_is_a_deliberate_migrator_only_step(
    migrator_engine: AsyncEngine,
    migrator_connection: AsyncConnection,
    tenant_a: SeededTenant,
    tenant_b: SeededTenant,
) -> None:
    """Só `SET ROLE cce_auth`, que ocorre apenas no processo de migration, abre a leitura global."""
    await key_with_digest(migrator_engine, tenant_a)
    await key_with_digest(migrator_engine, tenant_b)
    await migrator_connection.execute(text("SET ROLE cce_auth"))

    seen = (
        await migrator_connection.execute(
            text("SELECT count(*) FROM cce.tenants WHERE id = ANY(:ids)").bindparams(
                ids=[tenant_a.id, tenant_b.id]
            )
        )
    ).scalar_one()
    writes = await sql_outcome(migrator_connection, "UPDATE cce.tenants SET status = status")
    await migrator_connection.rollback()

    require(seen == 2, "cce_auth cannot read the Tenants it needs to authenticate")
    require(writes == INSUFFICIENT_PRIVILEGE, "cce_auth was able to write")


async def test_cce_auth_policies_are_select_only_and_the_runtime_policies_are_unchanged(
    migrator_connection: AsyncConnection,
) -> None:
    rows = (
        await migrator_connection.execute(
            text("""
                select tablename, policyname, cmd, roles::text[], qual, with_check
                from pg_policies where schemaname = 'cce' order by tablename, policyname
            """)
        )
    ).all()
    await migrator_connection.rollback()

    by_name: Mapping[tuple[str, str], tuple[Any, ...]] = {(r[0], r[1]): tuple(r[2:]) for r in rows}
    for table in ("tenants", "api_keys"):
        require(
            by_name[(table, "auth_lookup")] == ("SELECT", ["cce_auth"], "true", None),
            f"cce.{table}: auth_lookup must be a SELECT-only policy for cce_auth",
        )
        require(
            by_name[(table, "tenant_isolation")][:2] == ("ALL", ["cce_app", "cce_migrator"]),
            f"cce.{table}: tenant_isolation changed",
        )
    require(len(by_name) == 4, "unexpected policies on the Tenant tables")


# --- o runtime lê metadados de chave, nunca o modelo inteiro ---------------------------------


async def test_the_runtime_reads_key_metadata_by_explicit_columns_and_never_the_whole_model(
    migrator_engine: AsyncEngine,
    runtime_session: OpenTenantSession,
    tenant_a: SeededTenant,
) -> None:
    keyed, _ = await key_with_digest(migrator_engine, tenant_a, label="ERP")
    assert keyed.key_id is not None

    async with runtime_session(tenant_a.id) as session:
        metadata = await get_key_metadata(session, keyed.key_id)
    whole_model = await model_outcome(runtime_session, tenant_a)

    require(metadata is not None and metadata.label == "ERP", "the metadata was not read")
    require(
        metadata is not None and metadata.tenant_id == tenant_a.id and metadata.revoked_at is None,
        "wrong metadata",
    )
    require(whole_model == INSUFFICIENT_PRIVILEGE, "the runtime loaded the whole APIKey model")


async def model_outcome(runtime_session: OpenTenantSession, tenant: SeededTenant) -> str:
    """SQLSTATE de carregar o `APIKey` inteiro (inclui `secret_digest`) como o runtime."""
    state = "ok"
    try:
        async with runtime_session(tenant.id) as session:
            await session.execute(select(APIKey))
    except DBAPIError as error:
        state = sqlstate(error) or "unknown"
    return state


def test_the_metadata_columns_exclude_the_digest_and_the_pepper_version() -> None:
    names = {column.key for column in METADATA_COLUMNS}

    require(names.isdisjoint({"secret_digest", "pepper_version"}), "a secret column is readable")
    require(
        names == {"id", "tenant_id", "label", "scopes", "created_at", "expires_at", "revoked_at"},
        "the metadata columns changed",
    )
