"""`scripts/check_schema.py`: verifica o que o metadata não representa (RLS, policies, grants).

O banco real passa limpo. Cada defeito é introduzido DENTRO de uma transação que sempre é desfeita
(o DDL do PostgreSQL é transacional): os de tabela, policy e grant, pelo dono do schema; os de role
(atributos, associação, ownership), por um superusuário só do banco descartável, porque o migrator
não pode alterá-los. Nada fica gravado depois do teste.

As mensagens do verificador são texto fixo com nomes de objetos; nenhum DSN ou valor entra nelas.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from types import ModuleType

import pytest
from db_support import ROOT, alembic_environment, assert_disposable_database, sensitive_components
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from cce.shared.config import MigrationSettings
from integration.support import assert_no_leaks, compose_env_file, read_dotenv, require

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("migrated_database")]

SCRIPT = ROOT / "scripts" / "check_schema.py"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_schema", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def check_schema() -> ModuleType:
    return load_script()


@pytest.fixture
async def admin_connection(migrator_settings: MigrationSettings) -> AsyncIterator[AsyncConnection]:
    """Superusuário do banco descartável (só para introduzir defeitos de role, sempre desfeitos)."""
    password = read_dotenv(compose_env_file()).get("CCE_POSTGRES_ADMIN_PASSWORD", "")
    require(password != "", "CCE_POSTGRES_ADMIN_PASSWORD is not set in the test dotenv")
    url = make_url(migrator_settings.migrator_database_url.get_secret_value()).set(
        username="postgres", password=password
    )
    assert_disposable_database(migrator_settings.env, url, "admin")
    engine: AsyncEngine = create_async_engine(
        url, poolclass=NullPool, hide_parameters=True, connect_args={"timeout": 10}
    )
    try:
        async with engine.connect() as connection:
            yield connection
            await connection.rollback()
    finally:
        await engine.dispose()


async def problems_after(
    check_schema: ModuleType, connection: AsyncConnection, *statements: str
) -> list[str]:
    """Aplica os comandos, roda o verificador na mesma transação e a desfaz."""
    try:
        for statement in statements:
            await connection.execute(text(statement))
        found = await check_schema.collect_problems(connection)
    finally:
        await connection.rollback()
    return list(found)


async def test_a_migrated_database_has_no_problems(
    check_schema: ModuleType, migrator_connection: AsyncConnection
) -> None:
    assert await problems_after(check_schema, migrator_connection) == []


# --- defeitos de tabela, policy e grant (pelo dono do schema) --------------------------------

TABLE_DEFECTS = {
    "tenants-not-forced": (
        "ALTER TABLE cce.tenants NO FORCE ROW LEVEL SECURITY",
        "cce.tenants: row level security is not forced",
    ),
    "api-keys-not-forced": (
        "ALTER TABLE cce.api_keys NO FORCE ROW LEVEL SECURITY",
        "cce.api_keys: row level security is not forced",
    ),
    "tenants-rls-disabled": (
        "ALTER TABLE cce.tenants DISABLE ROW LEVEL SECURITY",
        "cce.tenants: row level security is not enabled",
    ),
    "api-keys-rls-disabled": (
        "ALTER TABLE cce.api_keys DISABLE ROW LEVEL SECURITY",
        "cce.api_keys: row level security is not enabled",
    ),
    "tenants-policy-dropped": (
        "DROP POLICY tenant_isolation ON cce.tenants",
        "cce.tenants: policy tenant_isolation is missing",
    ),
    "api-keys-policy-dropped": (
        "DROP POLICY tenant_isolation ON cce.api_keys",
        "cce.api_keys: policy tenant_isolation is missing",
    ),
    "extra-open-policy": (
        "CREATE POLICY open ON cce.tenants USING (true)",
        "cce.tenants: unexpected policy open",
    ),
    "unexpected-table": (
        "CREATE TABLE cce.rls_probe (id integer)",
        "cce.rls_probe: unexpected table",
    ),
}


@pytest.mark.parametrize(
    ("statement", "expected"), list(TABLE_DEFECTS.values()), ids=list(TABLE_DEFECTS)
)
async def test_it_reports_table_and_policy_defects(
    check_schema: ModuleType,
    migrator_connection: AsyncConnection,
    statement: str,
    expected: str,
) -> None:
    found = await problems_after(check_schema, migrator_connection, statement)

    assert expected in found


POLICY_BODY = "TO cce_app, cce_migrator"
POLICY_DEFECTS = {
    "using-true": (
        f"CREATE POLICY tenant_isolation ON cce.tenants {POLICY_BODY} "
        "USING (true) WITH CHECK (true)"
    ),
    "no-with-check": (
        f"CREATE POLICY tenant_isolation ON cce.tenants {POLICY_BODY} "
        "USING (id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    ),
    "other-expression": (
        f"CREATE POLICY tenant_isolation ON cce.tenants {POLICY_BODY} "
        "USING (id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (id = current_setting('app.tenant_id', true)::uuid)"
    ),
    "public-role": (
        "CREATE POLICY tenant_isolation ON cce.tenants "
        "USING (id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
        "WITH CHECK (id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    ),
    "select-only": (
        f"CREATE POLICY tenant_isolation ON cce.tenants FOR SELECT {POLICY_BODY} "
        "USING (id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    ),
}


@pytest.mark.parametrize("statement", list(POLICY_DEFECTS.values()), ids=list(POLICY_DEFECTS))
async def test_it_reports_a_policy_that_differs_from_the_expected_definition(
    check_schema: ModuleType, migrator_connection: AsyncConnection, statement: str
) -> None:
    found = await problems_after(
        check_schema,
        migrator_connection,
        "DROP POLICY tenant_isolation ON cce.tenants",
        statement,
    )

    assert "cce.tenants: policy tenant_isolation differs from the expected definition" in found


GRANT_DEFECTS = {
    "digest-column": (
        "GRANT SELECT (secret_digest) ON cce.api_keys TO cce_app",
        "unexpected grant: cce_app SELECT on cce.api_keys.secret_digest",
    ),
    "pepper-column": (
        "GRANT SELECT (pepper_version) ON cce.api_keys TO cce_app",
        "unexpected grant: cce_app SELECT on cce.api_keys.pepper_version",
    ),
    "api-keys-table-select": (
        "GRANT SELECT ON cce.api_keys TO cce_app",
        "unexpected grant: cce_app SELECT on cce.api_keys",
    ),
    "api-keys-update": (
        "GRANT UPDATE (revoked_at) ON cce.api_keys TO cce_app",
        "unexpected grant: cce_app UPDATE on cce.api_keys.revoked_at",
    ),
    "tenants-insert": (
        "GRANT INSERT ON cce.tenants TO cce_app",
        "unexpected grant: cce_app INSERT on cce.tenants",
    ),
    "tenants-delete": (
        "GRANT DELETE ON cce.tenants TO cce_app",
        "unexpected grant: cce_app DELETE on cce.tenants",
    ),
    "tenants-truncate": (
        "GRANT TRUNCATE ON cce.tenants TO cce_app",
        "unexpected grant: cce_app TRUNCATE on cce.tenants",
    ),
    "tenants-slug-update": (
        "GRANT UPDATE (slug) ON cce.tenants TO cce_app",
        "unexpected grant: cce_app UPDATE on cce.tenants.slug",
    ),
    "public-select": (
        "GRANT SELECT ON cce.api_keys TO PUBLIC",
        "unexpected grant: PUBLIC SELECT on cce.api_keys",
    ),
    "auth-select": (
        "GRANT SELECT ON cce.tenants TO cce_auth",
        "unexpected grant: cce_auth SELECT on cce.tenants",
    ),
    "alembic-version": (
        "GRANT SELECT ON cce.alembic_version TO cce_app",
        "unexpected grant: cce_app SELECT on cce.alembic_version",
    ),
    "grant-option": (
        "GRANT SELECT ON cce.tenants TO cce_app WITH GRANT OPTION",
        "unexpected grant option: cce_app SELECT on cce.tenants",
    ),
    "missing-select": (
        "REVOKE SELECT ON cce.tenants FROM cce_app",
        "missing grant: cce_app SELECT on cce.tenants",
    ),
    "missing-update": (
        "REVOKE UPDATE (name) ON cce.tenants FROM cce_app",
        "missing grant: cce_app UPDATE on cce.tenants.name",
    ),
    "missing-key-column": (
        "REVOKE SELECT (scopes) ON cce.api_keys FROM cce_app",
        "missing grant: cce_app SELECT on cce.api_keys.scopes",
    ),
    "create-on-schema": (
        "GRANT CREATE ON SCHEMA cce TO cce_app",
        "cce_app can CREATE in schema cce",
    ),
    "create-on-private": (
        "GRANT CREATE ON SCHEMA cce_private TO cce_app",
        "cce_app can CREATE in schema cce_private",
    ),
}


@pytest.mark.parametrize(
    ("statement", "expected"), list(GRANT_DEFECTS.values()), ids=list(GRANT_DEFECTS)
)
async def test_it_reports_missing_and_unexpected_grants(
    check_schema: ModuleType,
    migrator_connection: AsyncConnection,
    statement: str,
    expected: str,
) -> None:
    found = await problems_after(check_schema, migrator_connection, statement)

    assert expected in found


CREATE_FUNCTION = "CREATE FUNCTION cce_private.{name}() RETURNS integer LANGUAGE sql AS 'select 1'"
FUNCTION_DEFECTS = {
    "execute-to-app": (
        "GRANT EXECUTE ON FUNCTION cce_private.probe() TO cce_app",
        "unexpected EXECUTE: cce_app on cce_private.probe()",
    ),
    "execute-to-public": (
        "GRANT EXECUTE ON FUNCTION cce_private.probe() TO PUBLIC",
        "unexpected EXECUTE: PUBLIC on cce_private.probe()",
    ),
}


@pytest.mark.parametrize(
    ("statement", "expected"), list(FUNCTION_DEFECTS.values()), ids=list(FUNCTION_DEFECTS)
)
async def test_it_reports_a_function_the_runtime_or_public_can_execute(
    check_schema: ModuleType,
    migrator_connection: AsyncConnection,
    statement: str,
    expected: str,
) -> None:
    found = await problems_after(
        check_schema, migrator_connection, CREATE_FUNCTION.format(name="probe"), statement
    )

    assert expected in found


async def test_a_function_nobody_was_granted_is_not_a_problem(
    check_schema: ModuleType, migrator_connection: AsyncConnection
) -> None:
    """Os default privileges de cce_migrator tiram o EXECUTE de PUBLIC; sem GRANT, nada executa."""
    found = await problems_after(
        check_schema, migrator_connection, CREATE_FUNCTION.format(name="probe")
    )

    assert found == []


# --- defeitos de role (pelo superusuário do banco descartável) -------------------------------

ROLE_DEFECTS = {
    "app-bypassrls": ("ALTER ROLE cce_app BYPASSRLS", "role cce_app has BYPASSRLS"),
    "app-superuser": ("ALTER ROLE cce_app SUPERUSER", "role cce_app is a superuser"),
    "app-createrole": ("ALTER ROLE cce_app CREATEROLE", "role cce_app can create roles"),
    "app-createdb": ("ALTER ROLE cce_app CREATEDB", "role cce_app can create databases"),
    "app-replication": ("ALTER ROLE cce_app REPLICATION", "role cce_app has REPLICATION"),
    "app-inherit": ("ALTER ROLE cce_app INHERIT", "role cce_app inherits privileges"),
    "migrator-bypassrls": (
        "ALTER ROLE cce_migrator BYPASSRLS",
        "role cce_migrator has BYPASSRLS",
    ),
    "auth-login": ("ALTER ROLE cce_auth LOGIN", "role cce_auth can log in"),
    "auth-bypassrls": ("ALTER ROLE cce_auth BYPASSRLS", "role cce_auth has BYPASSRLS"),
    "app-member-of-migrator": (
        "GRANT cce_migrator TO cce_app",
        "unexpected membership: cce_app is a member of cce_migrator",
    ),
    "app-member-of-auth": (
        "GRANT cce_auth TO cce_app",
        "unexpected membership: cce_app is a member of cce_auth",
    ),
    "auth-inherited-by-migrator": (
        "GRANT cce_auth TO cce_migrator WITH INHERIT TRUE",
        "unexpected membership: cce_migrator inherits cce_auth",
    ),
    "migrator-member-of-app": (
        "GRANT cce_app TO cce_migrator",
        "unexpected membership: cce_migrator is a member of cce_app",
    ),
    "app-owns-tenants": (
        "ALTER TABLE cce.tenants OWNER TO cce_app",
        "cce.tenants: owned by cce_app instead of cce_migrator",
    ),
    "app-owns-api-keys": (
        "ALTER TABLE cce.api_keys OWNER TO cce_app",
        "cce.api_keys: owned by cce_app instead of cce_migrator",
    ),
    "app-owns-schema": (
        "ALTER SCHEMA cce OWNER TO cce_app",
        "schema cce: owned by cce_app instead of cce_migrator",
    ),
    "app-owns-private-schema": (
        "ALTER SCHEMA cce_private OWNER TO cce_app",
        "schema cce_private: owned by cce_app instead of cce_migrator",
    ),
}


@pytest.mark.parametrize(
    ("statement", "expected"), list(ROLE_DEFECTS.values()), ids=list(ROLE_DEFECTS)
)
async def test_it_reports_role_attribute_membership_and_ownership_defects(
    check_schema: ModuleType,
    admin_connection: AsyncConnection,
    statement: str,
    expected: str,
) -> None:
    found = await problems_after(check_schema, admin_connection, statement)

    assert expected in found


async def test_it_reports_a_function_owned_by_the_runtime_role(
    check_schema: ModuleType, admin_connection: AsyncConnection
) -> None:
    found = await problems_after(
        check_schema,
        admin_connection,
        CREATE_FUNCTION.format(name="probe"),
        "ALTER FUNCTION cce_private.probe() OWNER TO cce_app",
    )

    assert "function cce_private.probe(): owned by cce_app" in found


# --- a linha de comando ---------------------------------------------------------------------


def run_script(
    settings: MigrationSettings, extra: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    environment = alembic_environment(settings, os.environ)
    environment.update(extra or {})
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
        env=environment,
    )


def test_the_command_passes_on_the_migrated_database_and_prints_no_dsn(
    migrator_settings: MigrationSettings,
) -> None:
    result = run_script(migrator_settings)
    dsn = migrator_settings.migrator_database_url.get_secret_value()

    assert_no_leaks(result.stdout + result.stderr, sensitive_components(dsn), "check_schema output")
    require(result.returncode == 0, "check_schema failed on the migrated database")
    require("schema check passed" in result.stdout, "check_schema did not report success")


def test_the_command_fails_without_the_dsn_when_the_database_is_unreachable(
    migrator_settings: MigrationSettings,
) -> None:
    sentinel = "senha-sentinela-" + os.urandom(8).hex()
    url = make_url(migrator_settings.migrator_database_url.get_secret_value()).set(
        host="127.0.0.1", port=1, password=sentinel
    )
    result = run_script(
        migrator_settings,
        {"CCE_MIGRATOR_DATABASE_URL": url.render_as_string(hide_password=False)},
    )

    forbidden = sensitive_components(url.render_as_string(hide_password=False))
    assert_no_leaks(result.stdout + result.stderr, forbidden, "check_schema output")
    require(result.returncode == 2, "check_schema did not exit with 2 on a connection failure")
    require("cannot read the schema" in result.stderr, "check_schema gave no fixed message")


def test_the_command_exits_1_and_lists_the_problems(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = load_script()

    async def fake(connection: AsyncConnection) -> list[str]:
        return ["cce.tenants: row level security is not forced"]

    monkeypatch.setattr(module, "collect_problems", fake)

    code = module.main()

    captured = capsys.readouterr()
    assert code == 1
    assert "cce.tenants: row level security is not forced" in captured.out
    assert "schema check passed" not in captured.out
