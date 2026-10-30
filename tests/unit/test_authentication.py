"""Autenticação por API key, sem banco: cada caminho de falha, com o lookup substituído.

As chaves, os peppers e os digests daqui são sintéticos e gerados a cada execução. Higiene de
falhas: nada que envolva chave, segredo, pepper ou digest passa por `assert`; as verificações usam
`require` e `assert_no_leaks` (texto fixo e rótulos), e as chamadas que podem levantar passam por
`call_or_fail`/`aerror_of`, que nunca deixam a exceção original subir. Os `id` dos parâmetros são
fixos. O marcador `sensitive` esconde o traceback de qualquer falha inesperada.
"""

from __future__ import annotations

import ast
import asyncio
import base64
import dataclasses
import logging
import os
import secrets
from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from integration.support import assert_no_leaks, require
from key_support import acall_or_fail, aerror_of, call_or_fail, secret_of
from pydantic import SecretStr
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import cce.integrations.api_keys.authentication as authentication
import cce.shared.security as security
from cce.integrations.api_keys.authentication import (
    AuthenticationFailed,
    DependencyUnavailable,
    Principal,
    authenticate_api_key,
)
from cce.integrations.api_keys.repository import KeyCandidate
from cce.integrations.api_keys.service import IssuedKey, issue_key_material
from cce.shared.config import Environment, Settings
from cce.shared.security import digest_key, generate_key

pytestmark = pytest.mark.sensitive

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "apps" / "api" / "src" / "cce" / "integrations" / "api_keys"
PEPPER_1 = base64.b64encode(secrets.token_bytes(32)).decode()
PEPPER_2 = base64.b64encode(secrets.token_bytes(32)).decode()
FIXED_MESSAGE = "authentication failed"


# --- infraestrutura de teste -----------------------------------------------------------------


class FakeTransaction:
    async def __aenter__(self) -> FakeTransaction:
        return self

    async def __aexit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


class FakeSession:
    """Sessão que só registra a ordem de entrada e saída; o lookup é substituído."""

    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def __aenter__(self) -> FakeSession:
        self.events.append("open")
        return self

    async def __aexit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.events.append("close")

    def begin(self) -> FakeTransaction:
        return FakeTransaction()


class FakeFactory:
    def __init__(self) -> None:
        self.events: list[str] = []

    def __call__(self) -> FakeSession:
        return FakeSession(self.events)

    def typed(self) -> async_sessionmaker[AsyncSession]:
        return cast(async_sessionmaker[AsyncSession], self)


def make_settings(
    environment: Environment = "test", *, versions: tuple[int, ...] = (1,)
) -> Settings:
    peppers = {1: PEPPER_1, 2: PEPPER_2}
    return Settings(
        _env_file=None,
        env=environment,
        database_url=SecretStr("postgresql+asyncpg://cce_app:x@127.0.0.1:5432/cce_test"),
        redis_url=SecretStr("redis://127.0.0.1:6379/0"),
        api_key_peppers={v: SecretStr(peppers[v]) for v in versions},
        api_key_active_pepper_version=versions[0],
    )


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in list(os.environ):
        if name.upper().startswith("CCE_"):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)


@dataclasses.dataclass
class World:
    """Estado do lookup falso: o que ele devolve e quantas vezes foi chamado."""

    candidate: KeyCandidate | None = None
    failure: BaseException | None = None
    calls: int = 0
    asked: list[UUID] = dataclasses.field(default_factory=list)


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> World:
    state = World()

    async def fake_lookup(session: object, key_id: UUID) -> KeyCandidate | None:
        state.calls += 1
        state.asked.append(key_id)
        if state.failure is not None:
            raise state.failure
        return state.candidate

    monkeypatch.setattr(authentication, "lookup_candidate", fake_lookup)
    return state


def issued(settings: Settings, version: int = 1) -> IssuedKey:
    return call_or_fail(
        lambda: issue_key_material(settings.env, settings.api_key_peppers, version), "issue"
    )


def candidate_of(
    key: IssuedKey, tenant_id: UUID, scopes: tuple[str, ...] = ("tenant:read",)
) -> KeyCandidate:
    return KeyCandidate(
        key_id=key.key_id,
        tenant_id=tenant_id,
        secret_digest=key.secret_digest,
        pepper_version=key.pepper_version,
        scopes=scopes,
    )


async def authenticate(raw_key: Any, settings: Settings, factory: FakeFactory) -> Principal:
    return await acall_or_fail(
        lambda: authenticate_api_key(raw_key, settings, factory.typed()), "authenticate_api_key"
    )


def key_forbidden(key: IssuedKey) -> dict[str, str]:
    raw = key.raw_key.get_secret_value()
    return {
        "key": raw,
        "secret": secret_of(raw),
        "digest": key.secret_digest.hex(),
        "digest repr": repr(key.secret_digest),
        "pepper 1": PEPPER_1,
        "pepper 2": PEPPER_2,
    }


def describe(error: BaseException) -> tuple[str, str, str, tuple[object, ...]]:
    return (type(error).__name__, str(error), repr(error), error.args)


# --- chave válida ----------------------------------------------------------------------------


async def test_a_valid_key_authenticates_to_an_immutable_principal(world: World) -> None:
    settings, tenant_id = make_settings(), uuid4()
    key = issued(settings)
    world.candidate = candidate_of(key, tenant_id, ("tenant:read", "tenant:write"))

    principal = await authenticate(key.raw_key.get_secret_value(), settings, FakeFactory())

    require(isinstance(principal, Principal), "the result is not a Principal")
    require(principal.tenant_id == tenant_id and principal.key_id == key.key_id, "wrong identity")
    require(principal.scopes == frozenset({"tenant:read", "tenant:write"}), "wrong scopes")
    require(isinstance(principal.scopes, frozenset), "the scopes must be a frozenset")
    require(world.asked == [key.key_id], "the lookup must receive only the key id")


async def test_the_principal_is_frozen_hashable_and_carries_no_secret(world: World) -> None:
    settings = make_settings()
    key = issued(settings)
    world.candidate = candidate_of(key, uuid4())
    principal = await authenticate(key.raw_key.get_secret_value(), settings, FakeFactory())

    fields = sorted(field.name for field in dataclasses.fields(principal))
    error = None
    try:
        principal.tenant_id = uuid4()  # type: ignore[misc]
    except dataclasses.FrozenInstanceError as raised:
        error = raised
    hash(principal)

    require(fields == ["key_id", "scopes", "tenant_id"], "the Principal has unexpected fields")
    require(error is not None, "the Principal can be mutated")
    assert_no_leaks(repr(principal) + str(principal), key_forbidden(key), "repr of the Principal")


async def test_the_pepper_version_of_the_candidate_selects_the_pepper(world: World) -> None:
    settings = make_settings(versions=(1, 2))
    key = issued(settings, version=2)
    world.candidate = candidate_of(key, uuid4())

    principal = await authenticate(key.raw_key.get_secret_value(), settings, FakeFactory())

    require(principal.key_id == key.key_id, "a key under pepper version 2 did not authenticate")


# --- falhas uniformes ------------------------------------------------------------------------


def build_failures(settings: Settings) -> dict[str, Callable[[World], Any]]:
    """Cada caso prepara o mundo e devolve a chave apresentada."""
    tenant_id = uuid4()

    def valid_key() -> IssuedKey:
        return issued(settings)

    def absent(world: World) -> Any:
        return None

    def empty(world: World) -> Any:
        return ""

    def malformed(world: World) -> Any:
        return "not-a-key"

    def not_text(world: World) -> Any:
        return b"bytes"

    def wrong_secret(world: World) -> Any:
        key = valid_key()
        world.candidate = candidate_of(key, tenant_id)
        return generate_key(key.key_id, settings.env)  # mesmo id, outro segredo

    def unknown_id(world: World) -> Any:
        world.candidate = None
        return valid_key().raw_key.get_secret_value()

    def other_environment(world: World) -> Any:
        return generate_key(uuid4(), "production")

    def unconfigured_pepper(world: World) -> Any:
        key = valid_key()
        world.candidate = dataclasses.replace(candidate_of(key, tenant_id), pepper_version=9)
        return key.raw_key.get_secret_value()

    def wrong_digest_length(world: World) -> Any:
        key = valid_key()
        world.candidate = dataclasses.replace(candidate_of(key, tenant_id), secret_digest=b"short")
        return key.raw_key.get_secret_value()

    return {
        "absent": absent,
        "empty": empty,
        "malformed": malformed,
        "not-text": not_text,
        "wrong-secret": wrong_secret,
        "unknown-id": unknown_id,
        "other-environment": other_environment,
        "unconfigured-pepper": unconfigured_pepper,
        "wrong-digest-length": wrong_digest_length,
    }


CASES = list(build_failures(make_settings()))


@pytest.mark.parametrize("case", CASES, ids=CASES)
async def test_every_rejection_is_the_same_error_with_the_same_message(
    world: World, case: str
) -> None:
    settings = make_settings()
    presented = build_failures(settings)[case](world)

    error = await aerror_of(
        lambda: authenticate_api_key(presented, settings, FakeFactory().typed()),
        AuthenticationFailed,
        f"authenticating ({case})",
    )

    require(str(error) == FIXED_MESSAGE and error.args == (), "the message is not the fixed one")
    require(error.__cause__ is None, "the error chains its cause")
    require(error.__suppress_context__ is True, "the error would print its context")


async def test_all_rejections_are_indistinguishable(world: World) -> None:
    settings = make_settings()
    described = set()
    for name, build in build_failures(settings).items():
        world.candidate = None
        presented = build(world)

        async def present(presented: Any = presented) -> Principal:
            return await authenticate_api_key(presented, settings, FakeFactory().typed())

        error = await aerror_of(present, AuthenticationFailed, f"authenticating ({name})")
        described.add(describe(error))

    require(len(described) == 1, "two rejections differ in type, message, repr or arguments")


# --- candidato ausente e pepper ausente ------------------------------------------------------


@pytest.fixture
def spy_on_comparison(monkeypatch: pytest.MonkeyPatch) -> list[tuple[bool, bool]]:
    """Cada comparação: (candidato tem 32 bytes, o armazenado é o digest sentinela)."""
    calls: list[tuple[bool, bool]] = []
    real = security.verify_digest

    def spy(candidate: bytes, stored: bytes) -> bool:
        calls.append((len(candidate) == 32, stored == authentication.SENTINEL_DIGEST))
        return real(candidate, stored)

    monkeypatch.setattr(authentication, "verify_digest", spy)
    return calls


async def test_an_absent_candidate_still_runs_one_comparison_against_the_sentinel(
    world: World, spy_on_comparison: list[tuple[bool, bool]]
) -> None:
    settings = make_settings()
    world.candidate = None

    await aerror_of(
        lambda: authenticate_api_key(
            issued(settings).raw_key.get_secret_value(), settings, FakeFactory().typed()
        ),
        AuthenticationFailed,
        "authenticating an unknown key",
    )

    require(spy_on_comparison == [(True, True)], "no sentinel comparison for an absent candidate")


async def test_a_present_candidate_is_compared_with_its_stored_digest_not_the_sentinel(
    world: World, spy_on_comparison: list[tuple[bool, bool]]
) -> None:
    settings = make_settings()
    key = issued(settings)
    world.candidate = candidate_of(key, uuid4())

    await authenticate(key.raw_key.get_secret_value(), settings, FakeFactory())

    require(spy_on_comparison == [(True, False)], "the stored digest was not compared")


async def test_the_sentinel_never_authenticates(world: World) -> None:
    """Se o digest guardado fosse o sentinel, nenhuma chave real ainda assim o igualaria."""
    settings = make_settings()
    key = issued(settings)
    world.candidate = dataclasses.replace(
        candidate_of(key, uuid4()), secret_digest=authentication.SENTINEL_DIGEST
    )

    await aerror_of(
        lambda: authenticate_api_key(
            key.raw_key.get_secret_value(), settings, FakeFactory().typed()
        ),
        AuthenticationFailed,
        "authenticating against the sentinel digest",
    )


async def test_a_missing_pepper_invalidates_the_key_and_emits_an_event_without_secrets(
    world: World,
    spy_on_comparison: list[tuple[bool, bool]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = make_settings(versions=(1,))
    other = make_settings(
        versions=(2,)
    )  # a chave foi emitida sob o pepper 2, que não está em `settings`
    key = issued(other, version=2)
    world.candidate = candidate_of(key, uuid4())

    with caplog.at_level(logging.DEBUG):
        await aerror_of(
            lambda: authenticate_api_key(
                key.raw_key.get_secret_value(), settings, FakeFactory().typed()
            ),
            AuthenticationFailed,
            "authenticating without the pepper",
        )

    events = [record for record in caplog.records if record.name == authentication.LOGGER_NAME]
    require(len(events) == 1, "a missing pepper must emit exactly one operational event")
    event = events[0]
    require(event.levelno == logging.ERROR, "the missing pepper event must be an error")
    require(
        getattr(event, "pepper_version", None) == 2
        and getattr(event, "key_id", None) == str(key.key_id),
        "the event must say which pepper version and key id",
    )
    everything = " ".join(
        [
            caplog.text,
            event.getMessage(),
            repr(event.__dict__),
            str(event.args),
            str(event.exc_info),
        ]
    )
    assert_no_leaks(everything, key_forbidden(key), "the log of a missing pepper")
    require(spy_on_comparison == [(True, True)], "a missing pepper must still compare a sentinel")


async def test_ordinary_rejections_emit_no_operational_event(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    settings = make_settings()
    world.candidate = None

    with caplog.at_level(logging.DEBUG):
        await aerror_of(
            lambda: authenticate_api_key(
                issued(settings).raw_key.get_secret_value(), settings, FakeFactory().typed()
            ),
            AuthenticationFailed,
            "authenticating an unknown key",
        )

    require(
        [r for r in caplog.records if r.name == authentication.LOGGER_NAME] == [],
        "an ordinary rejection must not emit an event",
    )


# --- dependência indisponível ----------------------------------------------------------------

DEPENDENCY_FAILURES: dict[str, Callable[[], BaseException]] = {
    "connection-refused": lambda: ConnectionRefusedError("connect call failed"),
    "os-error": lambda: OSError("network is unreachable"),
    "timeout": lambda: TimeoutError(),
    "operational-error": lambda: OperationalError("SELECT 1", None, Exception("server closed")),
    "pool-timeout": lambda: PoolTimeoutError("QueuePool limit reached"),
}


@pytest.mark.parametrize("name", list(DEPENDENCY_FAILURES), ids=list(DEPENDENCY_FAILURES))
async def test_a_database_failure_is_a_dependency_error_never_an_invalid_credential(
    world: World, name: str
) -> None:
    settings = make_settings()
    world.failure = DEPENDENCY_FAILURES[name]()

    error = await aerror_of(
        lambda: authenticate_api_key(
            issued(settings).raw_key.get_secret_value(), settings, FakeFactory().typed()
        ),
        DependencyUnavailable,
        f"authenticating with the database failing ({name})",
    )

    require(not isinstance(error, AuthenticationFailed), "a dependency error is a 401")
    require(error.__cause__ is None and error.__suppress_context__ is True, "the cause is chained")
    require(error.args == (), "the dependency error must carry no driver message")


async def test_a_failure_that_is_not_the_database_is_not_reported_as_a_credential_problem(
    world: World,
) -> None:
    settings = make_settings()
    world.failure = ValueError("programming error")

    raised = None
    try:
        await authenticate_api_key(
            issued(settings).raw_key.get_secret_value(), settings, FakeFactory().typed()
        )
    except Exception as error:
        raised = type(error)

    require(raised is ValueError, "an unexpected failure must not become a 401 or a 503")


async def test_cancellation_is_never_swallowed(world: World) -> None:
    settings = make_settings()
    world.failure = asyncio.CancelledError()

    cancelled = False
    try:
        await authenticate_api_key(
            issued(settings).raw_key.get_secret_value(), settings, FakeFactory().typed()
        )
    except asyncio.CancelledError:
        cancelled = True

    require(cancelled, "the cancellation was swallowed")


# --- sessão curta, sem tenant e sem cache ----------------------------------------------------


async def test_the_lookup_runs_and_closes_before_any_comparison(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = make_settings()
    key = issued(settings)
    world.candidate = candidate_of(key, uuid4())
    factory = FakeFactory()
    real = security.verify_digest

    def spy(candidate: bytes, stored: bytes) -> bool:
        factory.events.append("compare")
        return real(candidate, stored)

    monkeypatch.setattr(authentication, "verify_digest", spy)

    await authenticate(key.raw_key.get_secret_value(), settings, factory)

    require(factory.events == ["open", "close", "compare"], "the session outlives the lookup")


async def test_there_is_no_positive_cache_each_authentication_asks_the_database(
    world: World,
) -> None:
    settings = make_settings()
    key = issued(settings)
    world.candidate = candidate_of(key, uuid4())

    for _ in range(3):
        await authenticate(key.raw_key.get_secret_value(), settings, FakeFactory())
    world.candidate = None
    error = await aerror_of(
        lambda: authenticate_api_key(
            key.raw_key.get_secret_value(), settings, FakeFactory().typed()
        ),
        AuthenticationFailed,
        "authenticating after the key disappeared",
    )

    require(world.calls == 4 and error is not None, "a result was served without a lookup")


def identifiers(source: str) -> set[str]:
    """Nomes, atributos e módulos importados do código; docstrings e comentários ficam de fora."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            found.add(node.module or "")
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            found.add(node.name)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            found.add(node.func.id)
    return {name.lower() for name in found}


def test_the_authentication_sources_hold_no_cache_and_no_tenant_context() -> None:
    banned = ("cache", "lru", "functools", "set_tenant_context", "tenant_session", "fastapi")
    found = sorted(
        f"{path.name}: {word}"
        for path in (SOURCE / "authentication.py", SOURCE / "repository.py")
        for name in identifiers(path.read_text())
        for word in banned
        if word in name
    )

    require(found == [], "authentication uses a cache, a tenant context or FastAPI")


def test_the_key_candidate_hides_the_digest_everywhere() -> None:
    digest = secrets.token_bytes(32)
    candidate = KeyCandidate(
        key_id=uuid4(), tenant_id=uuid4(), secret_digest=digest, pepper_version=1, scopes=()
    )

    text = call_or_fail(lambda: repr(candidate) + str(candidate), "repr of the candidate")

    assert_no_leaks(
        text, {"digest hex": digest.hex(), "digest repr": repr(digest)}, "repr of the candidate"
    )


def test_hmac_with_the_active_pepper_is_what_the_absent_path_would_compute() -> None:
    """O caminho sem candidato calcula um HMAC real, como o caminho com candidato."""
    settings = make_settings()
    pepper = base64.b64decode(PEPPER_1)
    raw = generate_key(uuid4(), settings.env)

    digest = call_or_fail(lambda: digest_key(raw, pepper), "digest_key")

    require(len(digest) == 32 and digest != authentication.SENTINEL_DIGEST, "bad sentinel or HMAC")
