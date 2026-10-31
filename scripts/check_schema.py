"""Verifica o que o metadata do SQLAlchemy não representa: RLS, policies, grants e roles.

`alembic check` compara tabelas e colunas; ele não enxerga FORCE RLS, policies, ACLs, ownership nem
atributos de role (plano do M0, seção 9.3). Este script lê só o catálogo, como `cce_migrator`, e
falha se faltar FORCE RLS, uma policy ou um grant esperado, ou se sobrar qualquer coisa a mais:
grant extra, policy extra, tabela inesperada, função executável por quem não deveria, `cce_app`
com ownership, `BYPASSRLS` ou associação a outro role. Também confere o que o `alembic check` não
compara: a definição de cada constraint (CHECK inclusive) e se ela está validada, triggers e rules
fora do esperado, triggers de FK desligados, relações além das tabelas (views, sequências, qualquer
coisa em `cce_private`), default privileges que concederiam algo a objetos futuros e os defaults
de sessão dos roles (o `cce_app` pode reescrever os próprios, timeouts inclusive).

Saída: `schema check passed` (código 0), uma linha por problema (código 1) ou, se o catálogo não
puder ser lido, uma mensagem fixa em stderr com o nome do tipo do erro (código 2). Nunca imprime
DSN, senha nem o texto do erro do driver.

Os conjuntos esperados abaixo crescem junto com as migrations. A 0003 acrescentou as policies de
`cce_auth`, os grants de coluna dele, o SELECT do runtime em `alembic_version` e a função de lookup
de API key, que é verificada por inteiro: dono, SECURITY DEFINER, `search_path`, `row_security`,
volatilidade, tipo de retorno, o md5 do corpo e quem pode executá-la. Uma função SECURITY DEFINER
que não esteja na lista é um problema.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any, Final

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
EXPECTED_POLICIES: Final[dict[tuple[str, str], dict[str, Any]]] = {
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
    # Leitura global de `cce_auth`, só SELECT; é o que o definer da função de lookup usa.
    ("tenants", "auth_lookup"): {
        "permissive": "PERMISSIVE",
        "cmd": "SELECT",
        "roles": ["cce_auth"],
        "qual": "true",
        "with_check": None,
    },
    ("api_keys", "auth_lookup"): {
        "permissive": "PERMISSIVE",
        "cmd": "SELECT",
        "roles": ["cce_auth"],
        "qual": "true",
        "with_check": None,
    },
}

# (tabela, coluna ou None para a tabela inteira, role, privilégio): TUDO que não é o dono.
_TENANT_UPDATABLE = ("default_currency", "locale", "name", "timezone", "updated_at")
_API_KEY_READABLE = ("created_at", "expires_at", "id", "label", "revoked_at", "scopes", "tenant_id")
# `cce_auth` lê COLUNAS (nunca a tabela inteira), as mesmas que o lookup usa.
_AUTH_TENANT_COLUMNS = ("id", "status")
_AUTH_API_KEY_COLUMNS = (
    "expires_at",
    "id",
    "pepper_version",
    "revoked_at",
    "scopes",
    "secret_digest",
    "tenant_id",
)
EXPECTED_GRANTS: Final = frozenset(
    {("tenants", None, "cce_app", "SELECT")}
    | {("tenants", column, "cce_app", "UPDATE") for column in _TENANT_UPDATABLE}
    | {("api_keys", column, "cce_app", "SELECT") for column in _API_KEY_READABLE}
    | {("tenants", column, "cce_auth", "SELECT") for column in _AUTH_TENANT_COLUMNS}
    | {("api_keys", column, "cce_auth", "SELECT") for column in _AUTH_API_KEY_COLUMNS}
    # Readiness (ticket 07): o runtime lê a versão aplicada, só SELECT.
    | {("alembic_version", None, "cce_app", "SELECT")}
)

# Funções (schema.nome(args)) esperadas, com tudo o que importa para a segurança delas.
# `source_md5` é o md5 do corpo em `LOOKUP_BODY`, na migration 0003 (um teste confere os dois).
EXPECTED_FUNCTIONS: Final[dict[str, dict[str, Any]]] = {
    "cce_private.lookup_api_key(p_key_id uuid)": {
        "owner": "cce_auth",
        "security_definer": True,
        "volatility": "s",
        "config": ("row_security=on", "search_path=pg_catalog, pg_temp"),
        "result": "TABLE(key_id uuid, tenant_id uuid, secret_digest bytea, "
        "pepper_version smallint, scopes text[])",
        "source_md5": "1550322de697f10cd73176868f57b67e",
        # além do dono, que sempre pode executar o que possui
        "execute": ("cce_app",),
    },
}
# Funções que o runtime deve executar, cada uma com GRANT EXECUTE explícito.
EXPECTED_RUNTIME_FUNCTIONS: Final[frozenset[str]] = frozenset(
    identity for identity, spec in EXPECTED_FUNCTIONS.items() if "cce_app" in spec["execute"]
)
FUNCTION_OWNERS: Final = (OWNER, "cce_auth")

# Constraints de `cce`, com o texto de pg_get_constraintdef no PostgreSQL 17. O `alembic check`
# não compara CHECK constraints: uma regra afrouxada sob o mesmo nome só aparece aqui. Os nomes
# são os do metadata (um teste confere os dois).
_CURRENCIES = "'BRL'::character varying, 'USD'::character varying, 'EUR'::character varying"
_STATUSES = (
    "'active'::character varying, 'suspended'::character varying, 'archived'::character varying"
)
EXPECTED_CONSTRAINTS: Final[dict[tuple[str, str], str]] = {
    ("alembic_version", "alembic_version_pkc"): "PRIMARY KEY (version_num)",
    ("tenants", "pk_tenants"): "PRIMARY KEY (id)",
    ("tenants", "uq_tenants_slug"): "UNIQUE (slug)",
    ("tenants", "ck_tenants_name_not_blank"): r"CHECK (((name)::text ~ '\S'::text))",
    ("tenants", "ck_tenants_slug_format"): (
        r"CHECK (((slug)::text ~ '^[a-z0-9]+(?:-[a-z0-9]+)*$'::text))"
    ),
    ("tenants", "ck_tenants_status_allowed"): (
        f"CHECK (((status)::text = ANY ((ARRAY[{_STATUSES}])::text[])))"
    ),
    ("tenants", "ck_tenants_currency_allowed"): (
        f"CHECK (((default_currency)::text = ANY ((ARRAY[{_CURRENCIES}])::text[])))"
    ),
    ("tenants", "ck_tenants_locale_allowed"): (
        "CHECK (((locale)::text = ANY ((ARRAY['pt-BR'::character varying, "
        "'en-US'::character varying])::text[])))"
    ),
    ("tenants", "ck_tenants_timezone_known"): (
        "CHECK ((('2000-01-01 00:00:00+00'::timestamp with time zone AT TIME ZONE timezone) "
        "IS NOT NULL))"
    ),
    ("api_keys", "pk_api_keys"): "PRIMARY KEY (id)",
    ("api_keys", "fk_api_keys_tenant_id_tenants"): (
        "FOREIGN KEY (tenant_id) REFERENCES cce.tenants(id) ON DELETE RESTRICT"
    ),
    ("api_keys", "ck_api_keys_label_not_blank"): r"CHECK (((label)::text ~ '\S'::text))",
    ("api_keys", "ck_api_keys_secret_digest_length"): "CHECK ((octet_length(secret_digest) = 32))",
    ("api_keys", "ck_api_keys_pepper_version_positive"): "CHECK ((pepper_version > 0))",
    ("api_keys", "ck_api_keys_scopes_allowed"): (
        "CHECK ((scopes <@ ARRAY['tenant:read'::text, 'tenant:write'::text]))"
    ),
    ("api_keys", "ck_api_keys_expires_after_created"): "CHECK ((expires_at > created_at))",
}

# Default privileges: (role que cria, schema ou None para todos, tipo de objeto) -> o que resta
# de EXECUTE. As duas linhas do bootstrap tiram o EXECUTE de PUBLIC das funções novas; sobra só
# o do próprio dono. Qualquer outra entrada, de qualquer role nos schemas do produto, é um grant
# que nasceria sozinho em todo objeto novo.
EXPECTED_DEFAULT_PRIVILEGES: Final = frozenset(
    {(role, None, "f", role, "EXECUTE") for role in (OWNER, "cce_auth")}
)
# Defaults de sessão do bootstrap (ALTER ROLE ... SET), por role, válidos em todo database. Um role
# comum pode reescrever os próprios, então só a verificação vê o runtime desligar seus timeouts.
EXPECTED_ROLE_SETTINGS: Final[dict[str, dict[str, str]]] = {
    "cce_app": {
        "search_path": "pg_catalog, cce",
        "statement_timeout": "5s",
        "lock_timeout": "2s",
        "idle_in_transaction_session_timeout": "10s",
    },
    OWNER: {"search_path": "pg_catalog, cce"},
    "cce_auth": {},
}
_DEFAULT_OBJECT_TYPES: Final = {
    "r": "tables",
    "S": "sequences",
    "f": "functions",
    "T": "types",
    "n": "schemas",
}

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
           pg_get_userbyid(p.proowner), p.prosecdef, p.provolatile::text, p.proleakproof,
           coalesce(p.proconfig, array[]::text[]), pg_get_function_result(p.oid), md5(p.prosrc),
           has_function_privilege('cce_app', p.oid, 'EXECUTE')
    from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = any(:schemas)
"""
_FUNCTION_EXECUTE = """
    select n.nspname || '.' || p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')',
           case when a.grantee = 0 then 'PUBLIC' else pg_get_userbyid(a.grantee) end,
           a.is_grantable
    from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
    cross join lateral aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a
    where n.nspname = any(:schemas) and a.privilege_type = 'EXECUTE'
"""
_OWNED_BY_APP = """
    select count(*)
    from pg_shdepend d
    where d.refclassid = 'pg_authid'::regclass
      and d.refobjid = (select oid from pg_roles where rolname = 'cce_app')
      and d.deptype = 'o'
      and d.dbid in (0, (select oid from pg_database where datname = current_database()))
"""
_CONSTRAINTS = """
    select c.relname, k.conname, pg_get_constraintdef(k.oid), k.convalidated
    from pg_constraint k
    join pg_class c on c.oid = k.conrelid
    join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'cce'
"""
# Triggers internos (os da FK) não têm nome estável: aparecem só se estiverem desligados.
_TRIGGERS = """
    select n.nspname, c.relname, t.tgname, t.tgisinternal, t.tgenabled::text
    from pg_trigger t
    join pg_class c on c.oid = t.tgrelid
    join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = any(:schemas)
"""
_RULES = "select schemaname, tablename, rulename from pg_rules where schemaname = any(:schemas)"
_DEFAULT_PRIVILEGES = """
    select pg_get_userbyid(d.defaclrole), n.nspname, d.defaclobjtype::text,
           case when a.grantee = 0 then 'PUBLIC' else pg_get_userbyid(a.grantee) end,
           a.privilege_type
    from pg_default_acl d
    left join pg_namespace n on n.oid = d.defaclnamespace
    cross join lateral aclexplode(d.defaclacl) a
    where pg_get_userbyid(d.defaclrole) = any(:roles) or n.nspname = any(:schemas)
"""
_DEFAULT_PRIVILEGE_OWNERS = """
    select pg_get_userbyid(defaclrole) from pg_default_acl
    where defaclnamespace = 0 and defaclobjtype = 'f'
"""
# setrole 0 é o default do database inteiro; setdatabase 0 vale em todos os databases.
_SETTINGS = """
    select case when s.setrole = 0 then null else pg_get_userbyid(s.setrole) end,
           s.setdatabase <> 0, unnest(s.setconfig)
    from pg_db_role_setting s
    where s.setdatabase in (0, (select oid from pg_database where datname = current_database()))
      and (s.setrole = 0 and s.setdatabase <> 0
           or s.setrole in (select oid from pg_roles where rolname = any(:roles)))
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


def _expected_function_problems(
    identity: str,
    spec: dict[str, Any],
    row: tuple[Any, ...],
    grantees: dict[str, bool],
) -> list[str]:
    _, owner, definer, volatility, leakproof, config, result, source_md5, _ = row
    problems: list[str] = []
    where = f"function {identity}"
    if owner != spec["owner"]:
        problems.append(f"{where}: owned by {owner} instead of {spec['owner']}")
    if definer != spec["security_definer"]:
        problems.append(f"{where}: is not SECURITY DEFINER")
    if volatility != spec["volatility"]:
        problems.append(f"{where}: volatility differs from the expected one")
    if leakproof:
        problems.append(f"{where}: is LEAKPROOF")
    if "search_path=pg_catalog, pg_temp" not in config:
        problems.append(f"{where}: search_path is not pg_catalog, pg_temp")
    if "row_security=on" not in config:
        problems.append(f"{where}: row_security is not on")
    if set(config) - set(spec["config"]):
        problems.append(f"{where}: has settings other than the expected ones")
    if result != spec["result"]:
        problems.append(f"{where}: result type differs from the expected one")
    if source_md5 != spec["source_md5"]:
        problems.append(f"{where}: definition differs from the expected one")
    allowed = {owner, *spec["execute"]}
    for grantee, grantable in sorted(grantees.items()):
        if grantee not in allowed:
            problems.append(f"unexpected EXECUTE: {grantee} on {identity}")
        elif grantable and grantee != owner:
            problems.append(f"unexpected grant option: {grantee} EXECUTE on {identity}")
    for grantee in spec["execute"]:
        if grantee not in grantees:
            problems.append(f"missing EXECUTE: {grantee} on {identity}")
    return problems


async def _functions(connection: AsyncConnection) -> list[str]:
    problems: list[str] = []
    params = {"schemas": list(SCHEMAS)}
    execute: dict[str, dict[str, bool]] = {}
    for identity, grantee, grantable in (
        await connection.execute(text(_FUNCTION_EXECUTE), params)
    ).all():
        execute.setdefault(identity, {})[grantee] = bool(grantable)
    found: set[str] = set()
    for row in (await connection.execute(text(_FUNCTIONS), params)).all():
        identity, owner, definer, *_, app = row
        found.add(identity)
        if identity in EXPECTED_FUNCTIONS:
            problems += _expected_function_problems(
                identity, EXPECTED_FUNCTIONS[identity], tuple(row), execute.get(identity, {})
            )
            continue
        if owner not in FUNCTION_OWNERS:
            problems.append(f"function {identity}: owned by {owner}")
        if definer:
            problems.append(f"function {identity}: unexpected SECURITY DEFINER")
        if "PUBLIC" in execute.get(identity, {}):
            problems.append(f"unexpected EXECUTE: PUBLIC on {identity}")
        if app:
            problems.append(f"unexpected EXECUTE: cce_app on {identity}")
    for identity in sorted(set(EXPECTED_FUNCTIONS) - found):
        problems.append(f"missing function: {identity}")
    return problems


async def _constraints(connection: AsyncConnection) -> list[str]:
    problems: list[str] = []
    actual = {
        (table, name): (definition, validated)
        for table, name, definition, validated in (
            await connection.execute(text(_CONSTRAINTS))
        ).all()
    }
    for (table, name), expected in sorted(EXPECTED_CONSTRAINTS.items()):
        if (table, name) not in actual:
            problems.append(f"cce.{table}: constraint {name} is missing")
            continue
        definition, validated = actual[(table, name)]
        if definition != expected:
            problems.append(f"cce.{table}: constraint {name} differs from the expected definition")
        if not validated:
            problems.append(f"cce.{table}: constraint {name} is not validated")
    for table, name in sorted(set(actual) - set(EXPECTED_CONSTRAINTS)):
        problems.append(f"cce.{table}: unexpected constraint {name}")
    return problems


async def _triggers_rules_and_relations(connection: AsyncConnection) -> list[str]:
    """Nada de trigger, rule ou relação além das tabelas: cada um mudaria o que o DML faz."""
    problems: list[str] = []
    params = {"schemas": list(SCHEMAS)}
    for schema, table, name, internal, enabled in (
        await connection.execute(text(_TRIGGERS), params)
    ).all():
        if not internal:
            problems.append(f"{schema}.{table}: unexpected trigger {name}")
        elif enabled == "D":
            problems.append(f"{schema}.{table}: a foreign key trigger is disabled")
    for schema, table, name in (await connection.execute(text(_RULES), params)).all():
        problems.append(f"{schema}.{table}: unexpected rule {name}")
    for schema, relation, kind, *_ in (await connection.execute(text(_RELATIONS), params)).all():
        if schema != "cce" or kind not in ("r", "p"):
            problems.append(f"{schema}.{relation}: unexpected relation")
    return problems


async def _default_privileges(connection: AsyncConnection) -> list[str]:
    problems: list[str] = []
    params = {"roles": list(PRODUCT_ROLES), "schemas": list(SCHEMAS)}
    rows = {
        tuple(row) for row in (await connection.execute(text(_DEFAULT_PRIVILEGES), params)).all()
    }
    for role, _, kind, grantee, privilege in sorted(
        rows - EXPECTED_DEFAULT_PRIVILEGES, key=lambda item: tuple(map(str, item))
    ):
        objects = _DEFAULT_OBJECT_TYPES.get(kind, "objects")
        problems.append(
            f"unexpected default privilege: {role} grants {privilege} on {objects} to {grantee}"
        )
    # Sem a entrada global, vale o default do PostgreSQL: EXECUTE para PUBLIC em toda função nova.
    revoked = set((await connection.execute(text(_DEFAULT_PRIVILEGE_OWNERS))).scalars())
    for role in sorted({role for role, *_ in EXPECTED_DEFAULT_PRIVILEGES} - revoked):
        problems.append(f"missing default privilege: functions of {role} are executable by PUBLIC")
    return problems


async def _settings(connection: AsyncConnection) -> list[str]:
    """Os defaults de sessão dos roles e do database. Só nomes saem nas mensagens, nunca valores."""
    problems: list[str] = []
    actual: dict[str, dict[str, str]] = {role: {} for role in EXPECTED_ROLE_SETTINGS}
    params = {"roles": list(EXPECTED_ROLE_SETTINGS)}
    for role, in_this_database, entry in (await connection.execute(text(_SETTINGS), params)).all():
        name, _, value = str(entry).partition("=")
        if role is None:
            problems.append(f"database: unexpected setting {name}")
        elif in_this_database:
            problems.append(f"role {role}: unexpected setting {name} in this database")
        else:
            actual[role][name] = value
    for role, expected in sorted(EXPECTED_ROLE_SETTINGS.items()):
        for name in sorted(set(expected) | set(actual[role])):
            if name not in expected:
                problems.append(f"role {role}: unexpected setting {name}")
            elif actual[role].get(name) != expected[name]:
                problems.append(f"role {role}: setting {name} is not the expected one")
    return problems


async def collect_problems(connection: AsyncConnection) -> list[str]:
    """Todos os problemas encontrados no catálogo (lista vazia = esquema conforme)."""
    # pg_get_constraintdef qualifica nomes conforme o search_path; só na transação de leitura.
    await connection.execute(text("SELECT set_config('search_path', 'pg_catalog', true)"))
    problems: list[str] = []
    for check in (
        _roles,
        _memberships,
        _ownership,
        _tables_and_policies,
        _grants,
        _schema_privileges,
        _functions,
        _constraints,
        _triggers_rules_and_relations,
        _default_privileges,
        _settings,
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
