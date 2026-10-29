"""CLI administrativa, em processo e sem banco: validação de opções, contrato de saída e erros.

As operações de banco (`bootstrap`, `tenant_session`, o engine) são substituídas; o que se testa
aqui é a CLI: o que ela aceita, o que imprime em stdout e stderr, o código de saída e o que nunca
repete. O comportamento contra o PostgreSQL real, em subprocesso, está em
`tests/integration/test_admin_cli.py`.

As chaves daqui são sintéticas. Higiene de falhas: marcador `sensitive`, `require` e
`assert_no_leaks` (texto fixo e rótulos), nenhum `assert` com a chave nos operandos.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from integration.support import assert_no_leaks, require
from pydantic import SecretStr

from cce.admin import bootstrap, cli
from cce.integrations.api_keys.service import IssuedKey, ProvisionedKey
from cce.shared.config import AdminSettings, ConfigurationError, Environment

pytestmark = pytest.mark.sensitive

TENANT_ID = uuid4()
KEY_ID = uuid4()
RAW_KEY = f"cce_tst_{KEY_ID.hex}_{secrets.token_urlsafe(32)}"
DIGEST = secrets.token_bytes(32)


def make_settings(environment: Environment = "test") -> AdminSettings:
    pepper = base64.b64encode(secrets.token_bytes(32)).decode()
    return AdminSettings(
        _env_file=None,
        env=environment,
        migrator_database_url=SecretStr(
            "postgresql+asyncpg://cce_migrator:x@127.0.0.1:5432/cce_test"
        ),
        api_key_peppers={1: SecretStr(pepper)},
    )


def provisioned_key() -> ProvisionedKey:
    return ProvisionedKey(
        issued=IssuedKey(
            key_id=KEY_ID,
            raw_key=SecretStr(RAW_KEY),
            secret_digest=DIGEST,
            pepper_version=1,
        ),
        tenant_id=TENANT_ID,
        label="initial",
        scopes=("tenant:read", "tenant:write"),
        expires_at=datetime.now(UTC) + timedelta(days=90),
    )


class FakeSession:
    pass


class World:
    """O que as operações falsas foram chamadas com, e o que devem fazer."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.failure: BaseException | None = None
        self.revoked = True
        self.committed = False


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in list(os.environ):
        if name.upper().startswith("CCE_"):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> World:
    state = World()
    monkeypatch.setattr(cli, "load_admin_settings", make_settings)

    class FakeEngine:
        async def dispose(self) -> None:
            return None

    monkeypatch.setattr(cli, "build_migration_engine", lambda settings: FakeEngine())
    monkeypatch.setattr(cli, "build_session_factory", lambda engine: "factory")

    @asynccontextmanager
    async def tenant_session(factory: object, tenant_id: UUID) -> AsyncIterator[FakeSession]:
        yield FakeSession()
        state.committed = True  # a saída normal do gerenciador é o commit

    monkeypatch.setattr(cli, "tenant_session", tenant_session)

    async def provision_tenant(factory: object, settings: object, command: Any) -> Any:
        state.calls.append(("provision", (command.slug, command.default_currency)))
        if state.failure is not None:
            raise state.failure
        return bootstrap.ProvisionedTenant(
            tenant_id=TENANT_ID,
            name=command.name,
            slug=command.slug,
            status="active",
            plan=command.plan,
            default_currency=command.default_currency,
            timezone=command.timezone,
            locale=command.locale,
            api_key=provisioned_key(),
        )

    async def issue_api_key(
        session: object, tenant_id: UUID, label: str, scopes: Any, ttl: int, settings: object
    ) -> ProvisionedKey:
        state.calls.append(("issue", (tenant_id, label, tuple(scopes), ttl)))
        if state.failure is not None:
            raise state.failure
        return provisioned_key()

    async def revoke_api_key(session: object, tenant_id: UUID, key_id: UUID) -> bool:
        state.calls.append(("revoke", (tenant_id, key_id)))
        if state.failure is not None:
            raise state.failure
        return state.revoked

    async def set_tenant_status(session: object, tenant_id: UUID, status: str) -> None:
        state.calls.append(("status", (tenant_id, status)))
        if state.failure is not None:
            raise state.failure

    monkeypatch.setattr(bootstrap, "provision_tenant", provision_tenant)
    monkeypatch.setattr(bootstrap, "issue_api_key", issue_api_key)
    monkeypatch.setattr(bootstrap, "revoke_api_key", revoke_api_key)
    monkeypatch.setattr(bootstrap, "set_tenant_status", set_tenant_status)
    return state


Run = Callable[..., tuple[int, str, str]]


@pytest.fixture
def run(capsys: pytest.CaptureFixture[str]) -> Run:
    def invoke(*argv: str) -> tuple[int, str, str]:
        code = cli.main(list(argv))
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return invoke


def one_json(text: str, what: str) -> dict[str, Any]:
    parsed: object = None
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = None
    require(isinstance(parsed, dict), f"{what} is not a single JSON object")
    assert isinstance(parsed, dict)
    return parsed


TENANT = "ten_" + TENANT_ID.hex
KEY = "key_" + KEY_ID.hex


# --- sucesso ---------------------------------------------------------------------------------


def test_tenant_create_prints_the_tenant_and_the_key_once_after_the_commit(
    world: World, run: Run
) -> None:
    code, out, err = run("tenant", "create", "--name", "Empresa A", "--slug", "empresa-a")

    body = one_json(out, "stdout")
    require(code == 0 and err == "", "tenant create failed or wrote to stderr")
    require(
        body["tenant"]["id"] == TENANT and body["tenant"]["slug"] == "empresa-a", "wrong tenant"
    )
    require(
        body["api_key"]["key"] == RAW_KEY and out.count(RAW_KEY) == 1, "the key is not shown once"
    )
    require(body["api_key"]["id"] == KEY, "wrong key id")
    require(body["api_key"]["scopes"] == ["tenant:read", "tenant:write"], "wrong scopes")
    assert_no_leaks(out + err, {"digest": DIGEST.hex(), "digest repr": repr(DIGEST)}, "output")


@pytest.mark.parametrize(
    ("failure", "code", "kind"),
    [
        (bootstrap.TenantSlugTaken(), 1, "refused"),
        (OSError("connect failed to 10.0.0.1 as cce_migrator"), 3, "OSError"),
        (RuntimeError("boom " + RAW_KEY), 3, "RuntimeError"),
    ],
    ids=["slug-taken", "database-error", "unexpected"],
)
def test_a_failing_create_prints_nothing_in_stdout_and_a_fixed_error_in_stderr(
    world: World, run: Run, failure: BaseException, code: int, kind: str
) -> None:
    world.failure = failure

    exit_code, out, err = run("tenant", "create", "--name", "Empresa A", "--slug", "empresa-a")

    body = one_json(err, "stderr")
    require(exit_code == code and out == "", "wrong exit code or output")
    require(body["kind"] == kind, "wrong error kind")
    assert_no_leaks(
        out + err,
        {"key": RAW_KEY, "driver message": "10.0.0.1", "role": "cce_migrator"},
        "the failure output",
    )


def test_api_key_issue_passes_the_scopes_label_and_ttl_and_defaults_the_ttl(
    world: World, run: Run
) -> None:
    code, out, _ = run(
        "api-key",
        "issue",
        "--tenant",
        TENANT,
        "--label",
        "ERP",
        "--scope",
        "tenant:read",
        "--scope",
        "tenant:write",
        "--ttl-days",
        "30",
    )
    default_code, _, _ = run(
        "api-key", "issue", "--tenant", TENANT, "--label", "ERP", "--scope", "tenant:read"
    )

    body = one_json(out, "stdout")
    require(code == 0 and default_code == 0, "api-key issue failed")
    require(body["tenant_id"] == TENANT and body["api_key"]["key"] == RAW_KEY, "wrong output")
    require(
        world.calls[0] == ("issue", (TENANT_ID, "ERP", ("tenant:read", "tenant:write"), 30)),
        "the arguments were not passed through",
    )
    require(world.calls[1][1][3] == 90, "the default TTL is not the configured one")


def test_api_key_revoke_reports_the_revocation_and_never_a_key(world: World, run: Run) -> None:
    code, out, err = run("api-key", "revoke", "--tenant", TENANT, "--key", KEY)

    body = one_json(out, "stdout")
    require(code == 0 and err == "", "revoke failed")
    require(body == {"tenant_id": TENANT, "key_id": KEY, "revoked": True}, "wrong output")
    require(world.calls == [("revoke", (TENANT_ID, KEY_ID))], "wrong call")
    require(world.committed, "the revocation was reported before the commit")


def test_a_key_of_another_tenant_is_refused_with_exit_1(world: World, run: Run) -> None:
    world.revoked = False

    code, out, err = run("api-key", "revoke", "--tenant", TENANT, "--key", KEY)

    require(code == 1 and out == "", "revoking another tenant's key must exit 1 without stdout")
    require(one_json(err, "stderr")["kind"] == "refused", "wrong error kind")


@pytest.mark.parametrize(("command", "status"), [("suspend", "suspended"), ("activate", "active")])
def test_suspend_and_activate_set_the_status(
    world: World, run: Run, command: str, status: str
) -> None:
    code, out, _ = run("tenant", command, "--tenant", TENANT)

    require(code == 0, "the command failed")
    require(one_json(out, "stdout") == {"tenant_id": TENANT, "status": status}, "wrong output")
    require(world.calls == [("status", (TENANT_ID, status))], "wrong call")


def test_a_domain_error_from_a_status_change_exits_1(world: World, run: Run) -> None:
    world.failure = bootstrap.TenantNotFound()

    code, out, err = run("tenant", "suspend", "--tenant", TENANT)

    require(code == 1 and out == "", "an unknown tenant must exit 1 without stdout")
    require(one_json(err, "stderr")["error"] == "tenant not found", "wrong message")


# --- uso e configuração ----------------------------------------------------------------------


def test_a_configuration_error_exits_2_with_the_variable_and_no_value(
    world: World, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken() -> AdminSettings:
        raise ConfigurationError("invalid configuration: CCE_API_KEY_PEPPERS: must be base64")

    monkeypatch.setattr(cli, "load_admin_settings", broken)

    code, out, err = run("tenant", "suspend", "--tenant", TENANT)

    require(code == 2 and out == "", "a configuration error must exit 2")
    require("CCE_API_KEY_PEPPERS" in err, "the message does not name the variable")


@pytest.mark.parametrize(
    "argv",
    [
        ("tenant", "suspend", "--tenant", RAW_KEY),
        ("tenant", "suspend", "--tenant", KEY),
        ("tenant", "suspend", "--tenant", "ten_" + "A" * 32),
        ("api-key", "revoke", "--tenant", TENANT, "--key", RAW_KEY),
        ("api-key", "revoke", "--tenant", TENANT, "--key", TENANT),
        ("api-key", "issue", "--tenant", RAW_KEY, "--label", "x", "--scope", "tenant:read"),
        ("api-key", "issue", "--tenant", TENANT, "--label", "x", "--scope", RAW_KEY),
        ("api-key", "issue", "--tenant", TENANT, "--label", RAW_KEY * 3, "--scope", "tenant:read"),
        (
            "api-key",
            "issue",
            "--tenant",
            TENANT,
            "--label",
            "x",
            "--scope",
            "tenant:read",
            "--ttl-days",
            RAW_KEY,
        ),
        ("api-key", "revoke", "--tenant", TENANT, "--key", KEY, RAW_KEY),
        ("api-key", "revoke", "--tenant", TENANT, "--key", KEY, "--bogus", RAW_KEY),
        (RAW_KEY,),
        ("tenant", RAW_KEY),
    ],
    ids=[
        "tenant-is-a-raw-key",
        "tenant-is-a-key-id",
        "tenant-uppercase",
        "key-is-a-raw-key",
        "key-is-a-tenant-id",
        "issue-tenant-raw-key",
        "scope-is-a-raw-key",
        "label-too-long",
        "ttl-is-a-raw-key",
        "extra-argument",
        "unknown-option",
        "key-as-command",
        "key-as-subcommand",
    ],
)
def test_a_bad_value_is_a_usage_error_that_never_echoes_what_was_typed(
    world: World, run: Run, argv: tuple[str, ...]
) -> None:
    code, out, err = run(*argv)

    require(code == 2 and out == "", "a bad value must be a usage error (exit 2) without stdout")
    require(one_json(err, "stderr")["kind"] == "usage", "wrong error kind")
    assert_no_leaks(
        out + err,
        {"raw key": RAW_KEY, "secret": RAW_KEY[-43:], "key without prefix": RAW_KEY[4:]},
        "the usage error",
    )
    require(world.calls == [], "an invalid command reached the database layer")


@pytest.mark.parametrize(
    "argv",
    [
        ("tenant", "create", "--slug", "ok-slug"),
        ("tenant", "create", "--name", "x", "--slug", "Not A Slug"),
        ("tenant", "create", "--name", "   ", "--slug", "ok-slug"),
        ("tenant", "create", "--name", "x", "--slug", "ok-slug", "--currency", "XXX"),
        ("tenant", "create", "--name", "x", "--slug", "ok-slug", "--timezone", "Mars/Base"),
        ("api-key", "issue", "--tenant", TENANT, "--label", "x"),
        ("api-key", "issue", "--tenant", TENANT, "--label", "x", "--scope", "tenant:admin"),
        ("api-key", "issue", "--tenant", TENANT, "--label", "  ", "--scope", "tenant:read"),
        (
            "api-key",
            "issue",
            "--tenant",
            TENANT,
            "--label",
            "x",
            "--scope",
            "tenant:read",
            "--ttl-days",
            "0",
        ),
        (
            "api-key",
            "issue",
            "--tenant",
            TENANT,
            "--label",
            "x",
            "--scope",
            "tenant:read",
            "--ttl-days",
            "366",
        ),
        (
            "api-key",
            "issue",
            "--tenant",
            TENANT,
            "--label",
            "x",
            "--scope",
            "tenant:read",
            "--ttl-days",
            "-1",
        ),
        ("api-key", "revoke", "--tenant", TENANT),
        ("tenant", "suspend"),
        ("nonsense",),
    ],
    ids=[
        "missing-name",
        "bad-slug",
        "blank-name",
        "bad-currency",
        "bad-timezone",
        "missing-scope",
        "unknown-scope",
        "blank-label",
        "ttl-zero",
        "ttl-too-long",
        "ttl-negative",
        "missing-key",
        "missing-tenant",
        "unknown-command",
    ],
)
def test_invalid_input_exits_2_before_any_database_work(
    world: World, run: Run, argv: tuple[str, ...]
) -> None:
    code, out, err = run(*argv)

    require(code == 2 and out == "", "invalid input must exit 2 without stdout")
    require(one_json(err, "stderr")["kind"] == "usage", "wrong error kind")
    require(world.calls == [], "an invalid command reached the database layer")


def test_without_arguments_it_prints_the_help_and_exits_2(world: World, run: Run) -> None:
    code, out, _ = run()

    require(code == 2, "no arguments must exit 2")
    require("tenant" in out and "api-key" in out, "the help does not list the command groups")


@pytest.mark.parametrize(
    "argv", [("--help",), ("tenant", "--help"), ("api-key", "issue", "--help")]
)
def test_help_works_and_exits_0_without_configuration(
    run: Run, argv: tuple[str, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden() -> AdminSettings:
        raise AssertionError("--help must not load the configuration")

    monkeypatch.setattr(cli, "load_admin_settings", forbidden)

    code, out, _ = run(*argv)

    require(code == 0 and out != "", "--help failed")


def test_the_module_entry_point_calls_main() -> None:
    source = (Path(cli.__file__).parent / "__main__.py").read_text()

    require("raise SystemExit(main())" in source, "python -m cce.admin does not exit with main()")


def test_the_console_script_points_at_main() -> None:
    pyproject = (Path(cli.__file__).parents[3] / "pyproject.toml").read_text()

    require('cce-admin = "cce.admin.cli:main"' in pyproject, "cce-admin does not call main()")
