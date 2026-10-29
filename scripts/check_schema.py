"""Verifica o que o metadata do SQLAlchemy não representa: RLS, policies, grants e roles.

`alembic check` compara tabelas e colunas; ele não enxerga FORCE RLS, policies, ACLs, ownership nem
atributos de role (plano do M0, seção 9.3). Este script lê só o catálogo, como `cce_migrator`, e
falha se faltar FORCE RLS, uma policy ou um grant esperado, ou se sobrar qualquer coisa a mais:
grant extra, policy extra, tabela inesperada, função executável por quem não deveria, `cce_app`
com ownership, `BYPASSRLS` ou associação a outro role.

Saída: `schema check passed` (código 0), uma linha por problema (código 1) ou, se o catálogo não
puder ser lido, uma mensagem fixa em stderr com o nome do tipo do erro (código 2). Nunca imprime
DSN, senha nem o texto do erro do driver.

Os conjuntos esperados abaixo crescem junto com as migrations: o ticket 06 (migration 0003)
acrescenta as policies de `cce_auth`, os grants dele e da tabela de versão do Alembic, e a função
de lookup.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from cce.shared.config import ConfigurationError, MigrationSettings, load_migration_settings
from cce.shared.db.engine import build_migration_engine

OWNER: Final = "cce_migrator"
PRODUCT_ROLES: Final = ("cce_app", "cce_auth", "cce_migrator")
SCHEMAS: Final = ("cce", "cce_private")

# Tabelas com tenant na linha: RLS habilitada, forçada e uma policy por tabela.
PROTECTED_TABLES: Final = ("api_keys", "tenants")
# Infraestrutura sem Tenant e sem RLS.
INFRASTRUCTURE_TABLES: Final = ("alembic_version",)

_CONTEXT = "NULLIF(current_setting('app.tenant_id'::text, true), ''::text)"
# Texto devolvido por pg_policies no PostgreSQL 17 (a versão fixada do projeto).
EXPECTED_POLICIES: Final = {
    ("tenants", "tenant_isolation"): {
        "permissive": "PERMISSIVE",
        "cmd": "ALL",
        "roles": ["cce_app", "cce_migrator"],
        "qual": f"(id = ({_CONTEXT})::uuid)",
        "with_check": f"(id = ({_CONTEXT})::uuid)",
    },
    ("api_keys", "tenant_isolation"): {
        "permissive": "PERMISSIVE",
        "cmd": "ALL",
        "roles": ["cce_app", "cce_migrator"],
        "qual": f"(tenant_id = ({_CONTEXT})::uuid)",
        "with_check": f"(tenant_id = ({_CONTEXT})::uuid)",
    },
}

# (tabela, coluna ou None para a tabela inteira, role, privilégio): TUDO que não é o dono.
_TENANT_UPDATABLE = ("default_currency", "locale", "name", "timezone", "updated_at")
_API_KEY_READABLE = ("created_at", "expires_at", "id", "label", "revoked_at", "scopes", "tenant_id")
EXPECTED_GRANTS: Final = frozenset(
    {("tenants", None, "cce_app", "SELECT")}
    | {("tenants", column, "cce_app", "UPDATE") for column in _TENANT_UPDATABLE}
    | {("api_keys", column, "cce_app", "SELECT") for column in _API_KEY_READABLE}
)

# Funções (schema.nome(args)) que o runtime deve executar, cada uma com GRANT EXECUTE explícito.
EXPECTED_RUNTIME_FUNCTIONS: Final[frozenset[str]] = frozenset()
FUNCTION_OWNERS: Final = (OWNER, "cce_auth")

_ROLE_ATTRIBUTES = """
    select rolname, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls,
           rolinherit, rolcanlogin
    from pg_roles
    where rolname = any(:roles)
"""
_MEMBERSHIPS = """
    select granted.rolname, member.rolname, m.admin_option, m.inherit_option, m.set_option
    from pg_auth_members m
    join pg_roles granted on granted.oid = m.roleid
    join pg_roles member on member.oid = m.member
    where granted.rolname = any(:roles) or member.rolname = any(:roles)
"""
_RELATIONS = """
    select n.nspname, c.relname, c.relkind::text, pg_get_userbyid(c.relowner),
           c.relrowsecurity, c.relforcerowsecurity
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = any(:schemas) and c.relkind in ('r', 'p', 'v', 'm', 'S', 'f')
"""
_SCHEMA_OWNERS = """
    select nspname, pg_get_userbyid(nspowner) from pg_namespace where nspname = any(:schemas)
"""
_POLICIES = """
    select tablename, policyname, permissive, cmd, roles::text[], qual, with_check
    from pg_policies
    where schemaname = 'cce'
"""
_GRANTS = """
    select table_name, column_name, grantee, privilege, grantable
    from (
        select c.relname as table_name, null::text as column_name,
               case when a.grantee = 0 then 'PUBLIC' else pg_get_userbyid(a.grantee) end as grantee,
               a.privilege_type as privilege, a.is_grantable as grantable
        from pg_class c
        join pg_namespace n on n.oid = c.relnamespace
        cross join lateral aclexplode(c.relacl) a
        where n.nspname = 'cce' and c.relkind in ('r', 'p') and a.grantee <> c.relowner
        union all
        select c.relname, t.attname,
               case when a.grantee = 0 then 'PUBLIC' else pg_get_userbyid(a.grantee) end,
               a.privilege_type, a.is_grantable
        from pg_attribute t
        join pg_class c on c.oid = t.attrelid
        join pg_namespace n on n.oid = c.relnamespace
        cross join lateral aclexplode(t.attacl) a
        where n.nspname = 'cce' and c.relkind in ('r', 'p') and not t.attisdropped
    ) entries
"""
_SCHEMA_PRIVILEGES = """
    select s.name,
           has_schema_privilege('cce_app', s.name, 'USAGE'),
           has_schema_privilege('cce_app', s.name, 'CREATE')
    from (values ('cce'), ('cce_private'), ('public')) as s(name)
"""
_FUNCTIONS = """
    select n.nspname || '.' || p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')',
           pg_get_userbyid(p.proowner),
           exists (
               select 1
               from aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a
               where a.grantee = 0 and a.privilege_type = 'EXECUTE'
           ),
           has_function_privilege('cce_app', p.oid, 'EXECUTE')
    from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = any(:schemas)
"""
_OWNED_BY_APP = """
    select count(*)
    from pg_shdepend d
    where d.refclassid = 'pg_authid'::regclass
      and d.refobjid = (select oid from pg_roles where rolname = 'cce_app')
      and d.deptype = 'o'
      and d.dbid in (0, (select oid from pg_database where datname = current_database()))
"""
_DATABASE_OWNER = (
    "select pg_get_userbyid(datdba) from pg_database where datname = current_database()"
)


def _describe(table: str, column: str | None) -> str:
    return f"cce.{table}" if column is None else f"cce.{table}.{column}"


async def _roles(connection: AsyncConnection) -> list[str]:
    problems: list[str] = []
    rows = (await connection.execute(text(_ROLE_ATTRIBUTES), {"roles": list(PRODUCT_ROLES)})).all()
    found = {row[0]: row for row in rows}
    for role in PRODUCT_ROLES:
        if role not in found:
            problems.append(f"role {role} does not exist")
            continue
        _, superuser, createdb, createrole, replication, bypassrls, inherit, login = found[role]
        for flag, message in (
            (superuser, f"role {role} is a superuser"),
            (createdb, f"role {role} can create databases"),
            (createrole, f"role {role} can create roles"),
            (replication, f"role {role} has REPLICATION"),
            (bypassrls, f"role {role} has BYPASSRLS"),
            (inherit, f"role {role} inherits privileges"),
        ):
            if flag:
                problems.append(message)
        if role == "cce_auth" and login:
            problems.append("role cce_auth can log in")
        if role != "cce_auth" and not login:
            problems.append(f"role {role} cannot log in")
    return problems


async def _memberships(connection: AsyncConnection) -> list[str]:
    """Só `cce_migrator` pode assumir `cce_auth` (SET), sem herdar e sem administrar."""
    problems: list[str] = []
    rows = (await connection.execute(text(_MEMBERSHIPS), {"roles": list(PRODUCT_ROLES)})).all()
    migrator_can_set_auth = False
    for granted, member, admin, inherit, set_option in rows:
        if (granted, member) != ("cce_auth", OWNER):
            problems.append(f"unexpected membership: {member} is a member of {granted}")
            continue
        if admin:
            problems.append(f"unexpected membership: {member} administers {granted}")
        if inherit:
            problems.append(f"unexpected membership: {member} inherits {granted}")
        migrator_can_set_auth = migrator_can_set_auth or bool(set_option)
    if not migrator_can_set_auth:
        problems.append("missing membership: cce_migrator cannot SET ROLE cce_auth")
    return problems


async def _ownership(connection: AsyncConnection) -> list[str]:
    problems: list[str] = []
    params = {"schemas": list(SCHEMAS)}
    for schema, owner in (await connection.execute(text(_SCHEMA_OWNERS), params)).all():
        if owner != OWNER:
            problems.append(f"schema {schema}: owned by {owner} instead of {OWNER}")
    for schema, relation, _, owner, _, _ in (
        await connection.execute(text(_RELATIONS), params)
    ).all():
        if owner != OWNER:
            problems.append(f"{schema}.{relation}: owned by {owner} instead of {OWNER}")
    owned = (await connection.execute(text(_OWNED_BY_APP))).scalar_one()
    if owned:
        problems.append(f"role cce_app owns {owned} object(s)")
    if (await connection.execute(text(_DATABASE_OWNER))).scalar_one() == "cce_app":
        problems.append("role cce_app owns the database")
    return problems


async def _tables_and_policies(connection: AsyncConnection) -> list[str]:
    problems: list[str] = []
    relations = (await connection.execute(text(_RELATIONS), {"schemas": ["cce"]})).all()
    tables = {row[1]: row for row in relations if row[2] in ("r", "p")}
    for table in PROTECTED_TABLES:
        if table not in tables:
            problems.append(f"cce.{table}: table is missing")
            continue
        _, _, _, _, enabled, forced = tables[table]
        if not enabled:
            problems.append(f"cce.{table}: row level security is not enabled")
        if not forced:
            problems.append(f"cce.{table}: row level security is not forced")
    for table in sorted(set(tables) - set(PROTECTED_TABLES) - set(INFRASTRUCTURE_TABLES)):
        problems.append(f"cce.{table}: unexpected table")

    actual = {
        (table, name): (permissive, cmd, sorted(roles), qual, with_check)
        for table, name, permissive, cmd, roles, qual, with_check in (
            await connection.execute(text(_POLICIES))
        ).all()
        if table in tables
    }
    for (table, name), expected in EXPECTED_POLICIES.items():
        if table not in tables:
            continue
        if (table, name) not in actual:
            problems.append(f"cce.{table}: policy {name} is missing")
        elif actual[(table, name)] != (
            expected["permissive"],
            expected["cmd"],
            expected["roles"],
            expected["qual"],
            expected["with_check"],
        ):
            problems.append(f"cce.{table}: policy {name} differs from the expected definition")
    for table, name in sorted(set(actual) - set(EXPECTED_POLICIES)):
        problems.append(f"cce.{table}: unexpected policy {name}")
    return problems


async def _grants(connection: AsyncConnection) -> list[str]:
    problems: list[str] = []
    actual: dict[tuple[str, str | None, str, str], bool] = {}
    for table, column, grantee, privilege, grantable in (
        await connection.execute(text(_GRANTS))
    ).all():
        actual[(table, column, grantee, privilege)] = bool(grantable)
    for table, column, grantee, privilege in sorted(
        set(actual) - EXPECTED_GRANTS, key=lambda item: tuple(map(str, item))
    ):
        problems.append(f"unexpected grant: {grantee} {privilege} on {_describe(table, column)}")
    for table, column, grantee, privilege in sorted(
        EXPECTED_GRANTS - set(actual), key=lambda item: tuple(map(str, item))
    ):
        problems.append(f"missing grant: {grantee} {privilege} on {_describe(table, column)}")
    for (table, column, grantee, privilege), grantable in sorted(
        actual.items(), key=lambda item: tuple(map(str, item[0]))
    ):
        if grantable and (table, column, grantee, privilege) in EXPECTED_GRANTS:
            problems.append(
                f"unexpected grant option: {grantee} {privilege} on {_describe(table, column)}"
            )
    return problems


async def _schema_privileges(connection: AsyncConnection) -> list[str]:
    problems: list[str] = []
    for schema, usage, create in (await connection.execute(text(_SCHEMA_PRIVILEGES))).all():
        if create:
            problems.append(f"cce_app can CREATE in schema {schema}")
        if schema in SCHEMAS and not usage:
            problems.append(f"cce_app cannot USE schema {schema}")
    return problems


async def _functions(connection: AsyncConnection) -> list[str]:
    problems: list[str] = []
    executable_by_app: set[str] = set()
    for identity, owner, public, app in (
        await connection.execute(text(_FUNCTIONS), {"schemas": list(SCHEMAS)})
    ).all():
        if owner not in FUNCTION_OWNERS:
            problems.append(f"function {identity}: owned by {owner}")
        if public:
            problems.append(f"unexpected EXECUTE: PUBLIC on {identity}")
        if app:
            executable_by_app.add(identity)
            if identity not in EXPECTED_RUNTIME_FUNCTIONS:
                problems.append(f"unexpected EXECUTE: cce_app on {identity}")
    for identity in sorted(EXPECTED_RUNTIME_FUNCTIONS - executable_by_app):
        problems.append(f"missing EXECUTE: cce_app on {identity}")
    return problems


async def collect_problems(connection: AsyncConnection) -> list[str]:
    """Todos os problemas encontrados no catálogo (lista vazia = esquema conforme)."""
    problems: list[str] = []
    for check in (
        _roles,
        _memberships,
        _ownership,
        _tables_and_policies,
        _grants,
        _schema_privileges,
        _functions,
    ):
        problems += await check(connection)
    return sorted(set(problems))


async def _check(settings: MigrationSettings) -> int:
    engine = build_migration_engine(settings)
    try:
        async with engine.connect() as connection:
            problems = await collect_problems(connection)
            await connection.rollback()
    except Exception as error:  # a mensagem do driver pode citar host e usuário: só o tipo sai
        print(f"check_schema: cannot read the schema ({type(error).__name__})", file=sys.stderr)
        return 2
    finally:
        await engine.dispose()
    if problems:
        for problem in problems:
            print(problem)
        return 1
    print("schema check passed")
    return 0


def main() -> int:
    try:
        settings = load_migration_settings()
    except ConfigurationError as error:
        print(f"check_schema: {error}", file=sys.stderr)  # cita variáveis, nunca valores
        return 2
    return asyncio.run(_check(settings))


if __name__ == "__main__":
    sys.exit(main())
