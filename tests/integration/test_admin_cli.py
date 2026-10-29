"""CLI administrativa `cce-admin`, executada como processo: contrato de saída, falhas e segredos.

Cada comando roda em um subprocesso com EXATAMENTE a configuração validada pelo teste (nada do
`CCE_*` do ambiente do pytest e nenhum dotenv chegam a ele), contra o banco descartável. Os
segredos (DSN, peppers) vão pelo ambiente do processo, nunca pelo argv.

Contrato: stdout traz um único objeto JSON; a chave bruta aparece só em `tenant create` e em
`api-key issue`, uma vez, depois do commit. Erros saem como um objeto JSON em stderr, com texto
fixo. Códigos de saída: 0 sucesso; 1 operação recusada (não encontrado, slug em uso, escopo
inválido); 2 uso ou configuração inválidos; 3 banco indisponível ou falha inesperada.

Higiene de falhas: marcador `sensitive`; a saída do processo, que pode conter a chave, só é
examinada por `require`, `assert_no_leaks` e `parse_json`, que nunca a repetem numa falha.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import pytest
from db_support import PASSTHROUGH, ROOT, sensitive_components
from key_support import acall_or_fail
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from cce.integrations.api_keys.authentication import (
    AuthenticationFailed,
    Principal,
    authenticate_api_key,
)
from cce.shared.config import AdminSettings, Settings
from cce.shared.ids import decode_key_id, decode_tenant_id

from .support import assert_no_leaks, require

pytestmark = [
    pytest.mark.integration,
    pytest.mark.sensitive,
    pytest.mark.usefixtures("migrated_database"),
]

Factory = async_sessionmaker[AsyncSession]
KEY_PATTERN = re.compile(r"cce_tst_[0-9a-f]{32}_[A-Za-z0-9_-]{43}")


@dataclass(frozen=True)
class Outcome:
    code: int
    out: str
    err: str


def cli_environment(
    settings: AdminSettings, extra: Mapping[str, str] | None = None
) -> dict[str, str]:
    environment = {name: os.environ[name] for name in PASSTHROUGH if name in os.environ}
    environment["CCE_ENV"] = settings.env
    environment["CCE_ENV_FILE"] = os.devnull
    environment["CCE_MIGRATOR_DATABASE_URL"] = settings.migrator_database_url.get_secret_value()
    environment["CCE_API_KEY_PEPPERS"] = json.dumps(
        {str(v): p.get_secret_value() for v, p in settings.api_key_peppers.items()}
    )
    environment["CCE_API_KEY_ACTIVE_PEPPER_VERSION"] = str(settings.api_key_active_pepper_version)
    environment["CCE_API_KEY_TTL_DAYS"] = str(settings.api_key_ttl_days)
    environment.update(extra or {})
    return environment


def run_cli(
    settings: AdminSettings, *arguments: str, extra_env: Mapping[str, str] | None = None
) -> Outcome:
    result = subprocess.run(
        [sys.executable, "-m", "cce.admin", *arguments],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
        env=cli_environment(settings, extra_env),
        timeout=60,
    )
    return Outcome(result.returncode, result.stdout, result.stderr)


def parse_json(output: str, what: str) -> dict[str, Any]:
    """Interpreta um único objeto JSON; numa falha diz só `what`, nunca o conteúdo."""
    parsed: object = None
    valid = False
    try:
        parsed = json.loads(output)
        valid = isinstance(parsed, dict)
    except ValueError:
        valid = False
    require(valid, f"{what} is not a single JSON object")
    assert isinstance(parsed, dict)
    return parsed


def raw_keys(output: str) -> list[str]:
    return KEY_PATTERN.findall(output)


@pytest.fixture
def runtime(db_settings: Settings, runtime_factory: Factory) -> tuple[Settings, Factory]:
    """O runtime que autentica as chaves que a CLI emite."""
    return db_settings, runtime_factory


async def authenticate(raw_key: str, db_settings: Settings, factory: Factory) -> Principal:
    return await acall_or_fail(
        lambda: authenticate_api_key(raw_key, db_settings, factory), "authenticate_api_key"
    )


async def rejected(raw_key: str, db_settings: Settings, factory: Factory) -> bool:
    try:
        await authenticate_api_key(raw_key, db_settings, factory)
    except AuthenticationFailed:
        return True
    except Exception:
        return False
    return False


def create_tenant(settings: AdminSettings, prefix: str, suffix: str = "a") -> tuple[UUID, str]:
    outcome = run_cli(
        settings, "tenant", "create", "--name", f"Empresa {suffix}", "--slug", f"{prefix}-{suffix}"
    )
    require(outcome.code == 0, "tenant create failed")
    body = parse_json(outcome.out, "tenant create output")
    return decode_tenant_id(body["tenant"]["id"]), body["api_key"]["key"]


# --- tenant create ---------------------------------------------------------------------------


async def test_tenant_create_prints_the_tenant_and_the_key_once_and_the_key_authenticates(
    admin_settings: AdminSettings,
    runtime: tuple[Settings, Factory],
    tenant_slug_prefix: str,
) -> None:
    db_settings, factory = runtime
    outcome = run_cli(
        admin_settings,
        "tenant",
        "create",
        "--name",
        "Empresa A",
        "--slug",
        f"{tenant_slug_prefix}-a",
    )

    require(outcome.code == 0, "tenant create failed")
    body = parse_json(outcome.out, "tenant create output")
    keys = raw_keys(outcome.out)
    require(len(keys) == 1, "the key must appear exactly once in stdout")
    assert_no_leaks(outcome.err, {"raw key": keys[0]}, "stderr of tenant create")
    require(body["tenant"]["slug"] == f"{tenant_slug_prefix}-a", "wrong slug")
    require(body["tenant"]["status"] == "active", "wrong status")
    require(
        sorted(body["api_key"]["scopes"]) == ["tenant:read", "tenant:write"],
        "the initial key must carry both scopes",
    )
    require(body["api_key"]["key"] == keys[0], "the key is not in the documented field")
    tenant_id = decode_tenant_id(body["tenant"]["id"])
    require(decode_key_id(body["api_key"]["id"]) is not None, "wrong key id")

    principal = await authenticate(keys[0], db_settings, factory)
    require(principal.tenant_id == tenant_id, "the printed key resolves to another Tenant")


async def test_two_cli_tenants_authenticate_independently(
    admin_settings: AdminSettings,
    runtime: tuple[Settings, Factory],
    tenant_slug_prefix: str,
) -> None:
    db_settings, factory = runtime
    tenant_a, key_a = create_tenant(admin_settings, tenant_slug_prefix, "a")
    tenant_b, key_b = create_tenant(admin_settings, tenant_slug_prefix, "b")

    principal_a = await authenticate(key_a, db_settings, factory)
    principal_b = await authenticate(key_b, db_settings, factory)

    require(principal_a.tenant_id == tenant_a, "key A resolves elsewhere")
    require(principal_b.tenant_id == tenant_b, "key B resolves elsewhere")
    require(tenant_a != tenant_b, "two Tenants share an id")


@pytest.mark.usefixtures("commit_failure")
async def test_tenant_create_with_a_failing_commit_shows_no_key_and_stores_nothing(
    admin_settings: AdminSettings, admin_engine: AsyncEngine, tenant_slug_prefix: str
) -> None:
    outcome = run_cli(
        admin_settings,
        "tenant",
        "create",
        "--name",
        "Empresa A",
        "--slug",
        f"{tenant_slug_prefix}-a",
    )

    require(outcome.code != 0, "the command succeeded although the commit failed")
    require(raw_keys(outcome.out + outcome.err) == [], "a key was shown although the commit failed")
    require(outcome.out.strip() == "", "stdout must be empty when the commit fails")
    async with admin_engine.connect() as connection:
        row = (
            await connection.execute(
                text("SELECT count(*) FROM cce.tenants WHERE slug LIKE :p"),
                {"p": tenant_slug_prefix + "%"},
            )
        ).scalar_one()
        await connection.rollback()
    require(row == 0, "a Tenant survived the failed commit")


async def test_a_duplicate_slug_is_refused_without_a_key(
    admin_settings: AdminSettings, tenant_slug_prefix: str
) -> None:
    create_tenant(admin_settings, tenant_slug_prefix)

    outcome = run_cli(
        admin_settings,
        "tenant",
        "create",
        "--name",
        "Outra",
        "--slug",
        f"{tenant_slug_prefix}-a",
    )

    require(outcome.code == 1, "a duplicate slug must be a refused operation (exit 1)")
    require(outcome.out.strip() == "", "stdout must be empty on failure")
    error = parse_json(outcome.err, "stderr")
    require(isinstance(error.get("error"), str), "stderr must carry an error message")


@pytest.mark.parametrize(
    "arguments",
    [
        ("--name", "Empresa", "--slug", "Not A Slug"),
        ("--name", "Empresa", "--slug", "ok-slug", "--currency", "XXX"),
        ("--name", "   ", "--slug", "ok-slug"),
        ("--slug", "ok-slug"),
    ],
    ids=["bad-slug", "bad-currency", "blank-name", "missing-name"],
)
async def test_tenant_create_validates_its_input(
    admin_settings: AdminSettings, arguments: tuple[str, ...]
) -> None:
    outcome = run_cli(admin_settings, "tenant", "create", *arguments)

    require(outcome.code == 2, "invalid input must be a usage error (exit 2)")
    require(raw_keys(outcome.out + outcome.err) == [], "a key appeared for invalid input")


# --- api-key issue / revoke ------------------------------------------------------------------


async def test_api_key_issue_prints_the_key_once_with_the_requested_scope_and_ttl(
    admin_settings: AdminSettings,
    runtime: tuple[Settings, Factory],
    tenant_slug_prefix: str,
) -> None:
    db_settings, factory = runtime
    tenant_id, _ = create_tenant(admin_settings, tenant_slug_prefix)
    public_id = "ten_" + tenant_id.hex

    outcome = run_cli(
        admin_settings,
        "api-key",
        "issue",
        "--tenant",
        public_id,
        "--label",
        "ERP",
        "--scope",
        "tenant:read",
        "--ttl-days",
        "30",
    )

    require(outcome.code == 0, "api-key issue failed")
    body = parse_json(outcome.out, "api-key issue output")
    keys = raw_keys(outcome.out)
    require(len(keys) == 1, "the key must appear exactly once")
    assert_no_leaks(outcome.err, {"raw key": keys[0]}, "stderr of api-key issue")
    require(body["api_key"]["scopes"] == ["tenant:read"], "wrong scopes")
    require(body["api_key"]["label"] == "ERP", "wrong label")
    principal = await authenticate(keys[0], db_settings, factory)
    require(principal.scopes == frozenset({"tenant:read"}), "the key does not carry the scope")
    require(principal.tenant_id == tenant_id, "the key resolves to another Tenant")


@pytest.mark.parametrize(
    "arguments",
    [
        ("--scope", "tenant:admin"),
        ("--scope", "tenant:read", "--scope", "nope"),
        (),
        ("--scope", "tenant:read", "--ttl-days", "0"),
        ("--scope", "tenant:read", "--ttl-days", "366"),
        ("--scope", "tenant:read", "--label", "   "),
    ],
    ids=["unknown-scope", "one-unknown-scope", "no-scope", "ttl-zero", "ttl-long", "blank-label"],
)
async def test_api_key_issue_validates_scopes_ttl_and_label(
    admin_settings: AdminSettings, tenant_slug_prefix: str, arguments: tuple[str, ...]
) -> None:
    tenant_id, _ = create_tenant(admin_settings, tenant_slug_prefix)
    label = () if "--label" in arguments else ("--label", "ERP")

    outcome = run_cli(
        admin_settings, "api-key", "issue", "--tenant", "ten_" + tenant_id.hex, *label, *arguments
    )

    require(outcome.code == 2, "invalid input must be a usage error (exit 2)")
    require(raw_keys(outcome.out + outcome.err) == [], "a key appeared for invalid input")


async def test_api_key_issue_for_an_unknown_tenant_is_refused(
    admin_settings: AdminSettings, tenant_slug_prefix: str
) -> None:
    outcome = run_cli(
        admin_settings,
        "api-key",
        "issue",
        "--tenant",
        "ten_" + uuid4().hex,
        "--label",
        "ERP",
        "--scope",
        "tenant:read",
    )

    require(outcome.code == 1, "an unknown Tenant must be a refused operation (exit 1)")
    require(raw_keys(outcome.out + outcome.err) == [], "a key appeared for an unknown Tenant")


async def test_api_key_revoke_revokes_is_idempotent_and_the_key_stops_authenticating(
    admin_settings: AdminSettings,
    runtime: tuple[Settings, Factory],
    tenant_slug_prefix: str,
) -> None:
    db_settings, factory = runtime
    tenant_id, raw = create_tenant(admin_settings, tenant_slug_prefix)
    principal = await authenticate(raw, db_settings, factory)
    arguments = (
        "api-key",
        "revoke",
        "--tenant",
        "ten_" + tenant_id.hex,
        "--key",
        "key_" + principal.key_id.hex,
    )

    first = run_cli(admin_settings, *arguments)
    second = run_cli(admin_settings, *arguments)

    require(first.code == 0 and second.code == 0, "revoking must succeed, twice")
    require(parse_json(first.out, "revoke output")["revoked"] is True, "revoke did not report it")
    require(raw_keys(first.out + first.err + second.out + second.err) == [], "revoke showed a key")
    require(await rejected(raw, db_settings, factory), "the revoked key still authenticates")


async def test_api_key_revoke_rejects_the_key_of_another_tenant(
    admin_settings: AdminSettings,
    runtime: tuple[Settings, Factory],
    tenant_slug_prefix: str,
) -> None:
    db_settings, factory = runtime
    tenant_a, _ = create_tenant(admin_settings, tenant_slug_prefix, "a")
    _, key_b = create_tenant(admin_settings, tenant_slug_prefix, "b")
    principal_b = await authenticate(key_b, db_settings, factory)

    outcome = run_cli(
        admin_settings,
        "api-key",
        "revoke",
        "--tenant",
        "ten_" + tenant_a.hex,
        "--key",
        "key_" + principal_b.key_id.hex,
    )

    require(outcome.code == 1, "revoking another Tenant's key must be refused (exit 1)")
    principal_after = await authenticate(key_b, db_settings, factory)
    require(principal_after.key_id == principal_b.key_id, "the other Tenant's key was revoked")


# --- tenant suspend / activate ---------------------------------------------------------------


async def test_suspend_and_activate_change_what_authenticates_next(
    admin_settings: AdminSettings,
    runtime: tuple[Settings, Factory],
    tenant_slug_prefix: str,
) -> None:
    db_settings, factory = runtime
    tenant_id, raw = create_tenant(admin_settings, tenant_slug_prefix)
    public_id = "ten_" + tenant_id.hex

    suspended = run_cli(admin_settings, "tenant", "suspend", "--tenant", public_id)
    blocked = await rejected(raw, db_settings, factory)
    activated = run_cli(admin_settings, "tenant", "activate", "--tenant", public_id)

    require(suspended.code == 0 and activated.code == 0, "suspend or activate failed")
    require(parse_json(suspended.out, "suspend output")["status"] == "suspended", "wrong status")
    require(parse_json(activated.out, "activate output")["status"] == "active", "wrong status")
    require(blocked, "a suspended Tenant's key still authenticates")
    principal = await authenticate(raw, db_settings, factory)
    require(principal.tenant_id == tenant_id, "the reactivated Tenant did not authenticate")


async def test_suspending_an_unknown_tenant_is_refused(admin_settings: AdminSettings) -> None:
    outcome = run_cli(admin_settings, "tenant", "suspend", "--tenant", "ten_" + uuid4().hex)

    require(outcome.code == 1, "an unknown Tenant must be a refused operation (exit 1)")


# --- o que a CLI nunca mostra ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("group", "command", "option"),
    [
        ("tenant", "suspend", "--tenant"),
        ("tenant", "activate", "--tenant"),
        ("api-key", "revoke", "--tenant"),
        ("api-key", "issue", "--tenant"),
    ],
    ids=["suspend", "activate", "revoke", "issue"],
)
async def test_a_malformed_id_is_a_usage_error_that_never_echoes_what_was_typed(
    admin_settings: AdminSettings,
    tenant_slug_prefix: str,
    group: str,
    command: str,
    option: str,
) -> None:
    """Quem cola uma chave bruta no lugar de um id não a vê repetida na tela nem no log."""
    _, raw = create_tenant(admin_settings, tenant_slug_prefix)
    extra = {
        ("api-key", "revoke"): ("--key", "key_" + uuid4().hex),
        ("api-key", "issue"): ("--label", "ERP", "--scope", "tenant:read"),
    }.get((group, command), ())

    outcome = run_cli(admin_settings, group, command, option, raw, *extra)

    require(outcome.code == 2, "a malformed id must be a usage error (exit 2)")
    assert_no_leaks(
        outcome.out + outcome.err,
        {"raw key": raw, "raw key without prefix": raw[4:], "secret": raw[-43:]},
        "the output for a malformed id",
    )


async def test_a_raw_key_given_as_the_key_id_is_not_echoed_either(
    admin_settings: AdminSettings, tenant_slug_prefix: str
) -> None:
    tenant_id, raw = create_tenant(admin_settings, tenant_slug_prefix)

    outcome = run_cli(
        admin_settings, "api-key", "revoke", "--tenant", "ten_" + tenant_id.hex, "--key", raw
    )

    require(outcome.code == 2, "a malformed key id must be a usage error (exit 2)")
    assert_no_leaks(outcome.out + outcome.err, {"raw key": raw, "secret": raw[-43:]}, "the output")


async def test_an_unreachable_database_exits_3_without_the_dsn_or_the_driver_message(
    admin_settings: AdminSettings, tenant_slug_prefix: str
) -> None:
    sentinel = "senha-sentinela-" + uuid4().hex
    url = make_url(admin_settings.migrator_database_url.get_secret_value()).set(
        host="127.0.0.1", port=1, password=sentinel
    )
    dsn = url.render_as_string(hide_password=False)

    outcome = run_cli(
        admin_settings,
        "tenant",
        "create",
        "--name",
        "Empresa A",
        "--slug",
        f"{tenant_slug_prefix}-a",
        extra_env={"CCE_MIGRATOR_DATABASE_URL": dsn},
    )

    assert_no_leaks(outcome.out + outcome.err, sensitive_components(dsn), "the output")
    require(outcome.code == 3, "an unreachable database must exit with 3")
    require(raw_keys(outcome.out + outcome.err) == [], "a key appeared without a database")
    require("Traceback" not in outcome.err, "the CLI printed a traceback")


async def test_an_invalid_configuration_exits_2_naming_variables_and_never_values(
    admin_settings: AdminSettings,
) -> None:
    sentinel = "pepper-curto-" + uuid4().hex
    outcome = run_cli(
        admin_settings,
        "tenant",
        "suspend",
        "--tenant",
        "ten_" + uuid4().hex,
        extra_env={"CCE_API_KEY_PEPPERS": json.dumps({"1": sentinel})},
    )

    require(outcome.code == 2, "an invalid configuration must exit with 2")
    assert_no_leaks(outcome.out + outcome.err, {"pepper": sentinel}, "the output")
    require("CCE_API_KEY_PEPPERS" in outcome.err, "the message does not name the variable")
    require("Traceback" not in outcome.err, "the CLI printed a traceback")


def test_help_works_without_configuration_or_database() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "cce.admin", "--help"],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
        env={name: os.environ[name] for name in PASSTHROUGH if name in os.environ},
        timeout=60,
    )

    require(result.returncode == 0, "--help needs configuration")
    for name in ("tenant", "api-key"):
        require(name in result.stdout, f"--help does not list {name}")
