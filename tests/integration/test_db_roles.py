from __future__ import annotations

import secrets

import pytest
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from cce.shared.config import MigrationSettings, Settings

from .support import assert_no_leaks, reapply_bootstrap, require, sqlstate, with_password

pytestmark = pytest.mark.integration

INSUFFICIENT_PRIVILEGE = "42501"
INVALID_PASSWORD = "28P01"

ROLE_ATTRIBUTES = """
    select rolname, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls,
           rolinherit, rolcanlogin
    from pg_roles
    where rolname in ('cce_migrator', 'cce_app', 'cce_auth')
    order by rolname
"""


async def outcome(connection: AsyncConnection, statement: str) -> str:
    """SQLSTATE do erro, ou 'ok'. A transação é desfeita em seguida."""
    try:
        await connection.execute(text(statement))
    except DBAPIError as error:
        return sqlstate(error) or "unknown"
    finally:
        await connection.rollback()
    return "ok"


async def scalar_row(connection: AsyncConnection, statement: str) -> tuple[object, ...]:
    row = (await connection.execute(text(statement))).one()
    await connection.rollback()
    return tuple(row)


# --- roles ----------------------------------------------------------------------------------


async def test_product_roles_have_the_matrix_attributes(app_connection: AsyncConnection) -> None:
    rows = (await app_connection.execute(text(ROLE_ATTRIBUTES))).all()

    # (rolname, super, createdb, createrole, replication, bypassrls, inherit, login)
    assert [tuple(row) for row in rows] == [
        ("cce_app", False, False, False, False, False, False, True),
        ("cce_auth", False, False, False, False, False, False, False),
        ("cce_migrator", False, False, False, False, False, False, True),
    ]


async def test_cce_app_is_member_of_no_role_and_only_migrator_may_assume_cce_auth(
    app_connection: AsyncConnection,
) -> None:
    rows = (
        await app_connection.execute(
            text("""
                select granted.rolname, member.rolname, m.admin_option, m.inherit_option,
                       m.set_option
                from pg_auth_members m
                join pg_roles granted on granted.oid = m.roleid
                join pg_roles member on member.oid = m.member
                where left(granted.rolname, 4) = 'cce_' or left(member.rolname, 4) = 'cce_'
            """)
        )
    ).all()

    # A única associação é cce_migrator -> cce_auth, só com SET (sem herdar a policy global).
    assert [tuple(row) for row in rows] == [("cce_auth", "cce_migrator", False, False, True)]


async def test_cce_app_is_not_member_of_the_other_roles_even_indirectly(
    app_connection: AsyncConnection,
) -> None:
    row = await scalar_row(
        app_connection,
        """
        select pg_has_role('cce_app', 'cce_migrator', 'MEMBER'),
               pg_has_role('cce_app', 'cce_auth', 'MEMBER'),
               pg_has_role('cce_migrator', 'cce_auth', 'USAGE')
        """,
    )

    assert row == (False, False, False)


async def test_migrator_can_assume_cce_auth_without_inheriting_it(
    migrator_connection: AsyncConnection,
) -> None:
    await migrator_connection.execute(text("SET ROLE cce_auth"))
    current = (await migrator_connection.execute(text("select current_user"))).scalar_one()
    await migrator_connection.rollback()

    assert current == "cce_auth"


# --- schemas e privilégios ------------------------------------------------------------------


async def test_schemas_cce_and_cce_private_belong_to_the_migrator(
    app_connection: AsyncConnection,
) -> None:
    rows = (
        await app_connection.execute(
            text("""
                select nspname, pg_get_userbyid(nspowner)
                from pg_namespace
                where nspname in ('cce', 'cce_private')
                order by nspname
            """)
        )
    ).all()

    assert [tuple(row) for row in rows] == [
        ("cce", "cce_migrator"),
        ("cce_private", "cce_migrator"),
    ]


async def test_create_on_public_is_revoked_for_everyone(app_connection: AsyncConnection) -> None:
    row = await scalar_row(
        app_connection,
        """
        select has_schema_privilege('cce_app', 'public', 'CREATE'),
               has_schema_privilege('cce_migrator', 'public', 'CREATE'),
               has_schema_privilege('cce_auth', 'public', 'CREATE'),
               exists (
                   select 1
                   from pg_namespace n,
                        aclexplode(coalesce(n.nspacl, acldefault('n', n.nspowner))) a
                   where n.nspname = 'public' and a.grantee = 0 and a.privilege_type = 'CREATE'
               )
        """,
    )

    assert row == (False, False, False, False)


async def test_public_has_no_privilege_on_the_database(app_connection: AsyncConnection) -> None:
    row = await scalar_row(
        app_connection,
        """
        select exists (
            select 1
            from pg_database d,
                 aclexplode(coalesce(d.datacl, acldefault('d', d.datdba))) a
            where d.datname = current_database() and a.grantee = 0
        )
        """,
    )

    assert row == (False,)


async def test_schema_grants_follow_the_matrix(app_connection: AsyncConnection) -> None:
    row = await scalar_row(
        app_connection,
        """
        select has_schema_privilege('cce_app', 'cce', 'USAGE'),
               has_schema_privilege('cce_app', 'cce', 'CREATE'),
               has_schema_privilege('cce_app', 'cce_private', 'USAGE'),
               has_schema_privilege('cce_app', 'cce_private', 'CREATE'),
               has_schema_privilege('cce_auth', 'cce', 'USAGE'),
               has_schema_privilege('cce_auth', 'cce', 'CREATE'),
               has_schema_privilege('cce_auth', 'cce_private', 'USAGE'),
               has_schema_privilege('cce_auth', 'cce_private', 'CREATE')
        """,
    )

    assert row == (True, False, True, False, True, False, True, True)


async def test_cce_app_cannot_create_objects(app_connection: AsyncConnection) -> None:
    for statement in (
        "CREATE TABLE cce.should_fail (id integer)",
        "CREATE TABLE cce_private.should_fail (id integer)",
        "CREATE TABLE public.should_fail (id integer)",
        "CREATE SCHEMA should_fail",
    ):
        assert await outcome(app_connection, statement) == INSUFFICIENT_PRIVILEGE, statement


async def test_cce_app_cannot_assume_the_other_roles(app_connection: AsyncConnection) -> None:
    for role in ("cce_migrator", "cce_auth"):
        assert await outcome(app_connection, f"SET ROLE {role}") == INSUFFICIENT_PRIVILEGE


async def test_migrator_creates_objects_in_cce_but_not_in_public(
    migrator_connection: AsyncConnection,
) -> None:
    assert await outcome(migrator_connection, "CREATE TABLE cce.probe (id integer)") == "ok"
    assert (
        await outcome(migrator_connection, "CREATE TABLE public.probe (id integer)")
        == INSUFFICIENT_PRIVILEGE
    )


# --- configuração de sessão -----------------------------------------------------------------


async def test_runtime_role_has_search_path_and_timeouts(app_connection: AsyncConnection) -> None:
    row = await scalar_row(
        app_connection,
        """
        select current_setting('search_path'),
               current_setting('statement_timeout'),
               current_setting('lock_timeout'),
               current_setting('idle_in_transaction_session_timeout')
        """,
    )

    assert row == ("pg_catalog, cce", "5s", "2s", "10s")


async def test_migrator_has_the_search_path_but_no_runtime_timeouts(
    migrator_connection: AsyncConnection,
) -> None:
    row = await scalar_row(
        migrator_connection,
        """
        select current_setting('search_path'),
               current_setting('statement_timeout'),
               current_setting('lock_timeout'),
               current_setting('idle_in_transaction_session_timeout')
        """,
    )

    assert row == ("pg_catalog, cce", "0", "0", "0")


QUERY_CANCELED = "57014"
LOCK_NOT_AVAILABLE = "55P03"


async def test_the_runtime_statement_timeout_cancels_a_slow_query_and_the_connection_survives(
    app_connection: AsyncConnection,
) -> None:
    assert await outcome(app_connection, "SELECT pg_sleep(6)") == QUERY_CANCELED

    assert await scalar_row(app_connection, "SELECT 1") == (1,)


async def test_the_runtime_lock_timeout_gives_up_on_a_held_lock_and_works_once_released(
    app_connection: AsyncConnection, migrator_engine: AsyncEngine
) -> None:
    async with migrator_engine.connect() as holder:
        await holder.execute(text("LOCK TABLE cce.tenants IN ACCESS EXCLUSIVE MODE"))
        try:
            blocked = await outcome(app_connection, "SELECT count(*) FROM cce.tenants")
        finally:
            await holder.rollback()

    assert blocked == LOCK_NOT_AVAILABLE
    assert await scalar_row(app_connection, "SELECT count(*) FROM cce.tenants") == (0,)


async def test_functions_are_not_executable_by_public_by_default(
    migrator_connection: AsyncConnection,
) -> None:
    probe = """
        select has_function_privilege('cce_app', 'cce_private.{name}()', 'EXECUTE'),
               exists (
                   select 1
                   from pg_proc p,
                        aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a
                   where p.proname = '{name}' and a.grantee = 0 and a.privilege_type = 'EXECUTE'
               )
    """
    await migrator_connection.execute(
        text(
            "CREATE FUNCTION cce_private.probe_migrator() "
            "RETURNS integer LANGUAGE sql AS 'select 1'"
        )
    )
    created_by_migrator = (
        await migrator_connection.execute(text(probe.format(name="probe_migrator")))
    ).one()
    await migrator_connection.rollback()

    await migrator_connection.execute(text("SET ROLE cce_auth"))
    await migrator_connection.execute(
        text("CREATE FUNCTION cce_private.probe_auth() RETURNS integer LANGUAGE sql AS 'select 1'")
    )
    created_by_auth = (
        await migrator_connection.execute(text(probe.format(name="probe_auth")))
    ).one()
    await migrator_connection.rollback()

    assert tuple(created_by_migrator) == (False, False)
    assert tuple(created_by_auth) == (False, False)


# --- bootstrap reaplicável ------------------------------------------------------------------


@pytest.mark.usefixtures("compose_targets_validated_database")
async def test_bootstrap_is_reapplicable_and_never_rotates_passwords(
    db_settings: Settings, migrator_settings: MigrationSettings
) -> None:
    other_password = secrets.token_hex(32)
    original_urls = {
        "app": db_settings.database_url.get_secret_value(),
        "migrator": migrator_settings.migrator_database_url.get_secret_value(),
    }
    forbidden = {"alternative password": other_password}
    before = await _role_snapshot(db_settings)

    for _ in range(2):
        result = reapply_bootstrap(other_password)
        require(result.returncode == 0, "bootstrap reapplication failed")
        assert_no_leaks(" ".join(result.args), forbidden, "the docker compose argv")
        assert_no_leaks(result.stdout + result.stderr, forbidden, "the bootstrap output")

    assert await _role_snapshot(db_settings) == before
    # As senhas originais continuam valendo; a "nova" não foi aplicada a nenhum role.
    # Os DSNs ficam em variáveis: um `assert` com eles na expressão os mostraria numa falha.
    for role, url in original_urls.items():
        rejected = await _login_outcome(with_password(url, other_password))
        accepted = await _login_outcome(url)
        require(rejected == INVALID_PASSWORD, f"the alternative password works for {role}")
        require(accepted == "ok", f"the original password no longer works for {role}")


async def _role_snapshot(settings: Settings) -> list[tuple[object, ...]]:
    engine = create_async_engine(settings.database_url.get_secret_value(), poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            rows = (await connection.execute(text(ROLE_ATTRIBUTES))).all()
            return [tuple(row) for row in rows]
    finally:
        await engine.dispose()


async def _login_outcome(url: str | URL) -> str:
    engine = create_async_engine(url, poolclass=NullPool, connect_args={"timeout": 10})
    try:
        async with engine.connect() as connection:
            await connection.execute(text("select 1"))
    except Exception as error:  # falha de conexão chega como erro do asyncpg, sem embrulho
        return sqlstate(error) or "unknown"
    finally:
        await engine.dispose()
    return "ok"
