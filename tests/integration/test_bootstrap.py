"""Bootstrap administrativo: criar Tenant e chave na mesma transação, revogar, suspender e ativar.

Roda como `cce_migrator` (o dono do schema) pela fábrica de produção, sempre sob o contexto de um
Tenant, e confere o resultado com o runtime (`authenticate_api_key`), que é quem vale. A falha de
COMMIT é real: uma constraint trigger adiada recusa a inserção da chave quando a transação termina.

Higiene de falhas: marcador `sensitive`; chaves e digests só passam por `require` e
`assert_no_leaks`; as chamadas que podem levantar passam por `acall_or_fail`/`aerror_of`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from key_support import acall_or_fail, aerror_of
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from cce.admin.bootstrap import (
    BootstrapError,
    ProvisionedTenant,
    TenantNotFound,
    create_tenant,
    issue_api_key,
    provision_tenant,
    revoke_api_key,
    set_tenant_status,
)
from cce.identity.tenants.schemas import TenantCreate
from cce.integrations.api_keys.authentication import (
    AuthenticationFailed,
    Principal,
    authenticate_api_key,
)
from cce.shared.config import AdminSettings, Settings
from cce.shared.db.engine import build_migration_engine, build_session_factory
from cce.shared.db.session import tenant_session
from cce.shared.security import digest_key, pepper_bytes, verify_digest

from .support import assert_no_leaks, require

pytestmark = [
    pytest.mark.integration,
    pytest.mark.sensitive,
    pytest.mark.usefixtures("migrated_database"),
]

Factory = async_sessionmaker[AsyncSession]


@pytest.fixture
async def admin_factory(admin_settings: AdminSettings) -> AsyncIterator[Factory]:
    engine = build_migration_engine(admin_settings)
    try:
        yield build_session_factory(engine)
    finally:
        await engine.dispose()


def command(prefix: str, suffix: str = "a") -> TenantCreate:
    return TenantCreate(name=f"Empresa {suffix}", slug=f"{prefix}-{suffix}")


async def provision(
    factory: Factory, settings: AdminSettings, prefix: str, suffix: str = "a"
) -> ProvisionedTenant:
    return await acall_or_fail(
        lambda: provision_tenant(factory, settings, command(prefix, suffix)), "provision_tenant"
    )


async def counts(admin_engine: AsyncEngine, prefix: str) -> tuple[int, int]:
    """(Tenants, chaves) cujo slug começa com `prefix`, lidos pelo superusuário (sem RLS)."""
    async with admin_engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT (SELECT count(*) FROM cce.tenants WHERE slug LIKE :p), "
                    "(SELECT count(*) FROM cce.api_keys WHERE tenant_id IN "
                    "(SELECT id FROM cce.tenants WHERE slug LIKE :p))"
                ),
                {"p": prefix + "%"},
            )
        ).one()
        await connection.rollback()
    return int(row[0]), int(row[1])


async def authenticate(raw_key: str, settings: Settings, factory: Factory) -> Principal:
    return await acall_or_fail(
        lambda: authenticate_api_key(raw_key, settings, factory), "authenticate_api_key"
    )


async def reject(raw_key: str, settings: Settings, factory: Factory, what: str) -> None:
    await aerror_of(
        lambda: authenticate_api_key(raw_key, settings, factory), AuthenticationFailed, what
    )


# --- criação atômica de Tenant e chave --------------------------------------------------------


async def test_it_creates_the_tenant_and_its_initial_key_and_the_key_authenticates(
    admin_factory: Factory,
    admin_settings: AdminSettings,
    db_settings: Settings,
    runtime_factory: Factory,
    tenant_slug_prefix: str,
) -> None:
    provisioned = await provision(admin_factory, admin_settings, tenant_slug_prefix)

    principal = await authenticate(
        provisioned.api_key.issued.raw_key.get_secret_value(), db_settings, runtime_factory
    )

    require(principal.tenant_id == provisioned.tenant_id, "the key resolves to another Tenant")
    require(
        principal.key_id == provisioned.api_key.issued.key_id, "the Principal has another key id"
    )
    require(principal.scopes == frozenset({"tenant:read", "tenant:write"}), "wrong initial scopes")
    require(provisioned.slug == f"{tenant_slug_prefix}-a", "wrong slug")


async def test_the_stored_digest_is_the_hmac_of_the_key_with_the_active_pepper(
    admin_factory: Factory,
    admin_settings: AdminSettings,
    tenant_slug_prefix: str,
) -> None:
    provisioned = await provision(admin_factory, admin_settings, tenant_slug_prefix)

    async with tenant_session(admin_factory, provisioned.tenant_id) as session:
        row = (
            await session.execute(
                text("SELECT secret_digest, pepper_version, label FROM cce.api_keys")
            )
        ).one()
    pepper = pepper_bytes(
        admin_settings.api_key_peppers, admin_settings.api_key_active_pepper_version
    )
    expected = digest_key(provisioned.api_key.issued.raw_key.get_secret_value(), pepper)

    require(verify_digest(bytes(row[0]), expected), "the stored digest is not the key's HMAC")
    require(row[1] == admin_settings.api_key_active_pepper_version, "wrong pepper version")
    require(row[2] == "initial", "wrong label")
    assert_no_leaks(
        f"{row[2]}",
        {"raw key": provisioned.api_key.issued.raw_key.get_secret_value()},
        "the stored label",
    )


async def test_two_provisioned_tenants_have_independent_keys_that_resolve_to_their_own_tenant(
    admin_factory: Factory,
    admin_settings: AdminSettings,
    db_settings: Settings,
    runtime_factory: Factory,
    tenant_slug_prefix: str,
) -> None:
    first = await provision(admin_factory, admin_settings, tenant_slug_prefix, "a")
    second = await provision(admin_factory, admin_settings, tenant_slug_prefix, "b")

    principal_first = await authenticate(
        first.api_key.issued.raw_key.get_secret_value(), db_settings, runtime_factory
    )
    principal_second = await authenticate(
        second.api_key.issued.raw_key.get_secret_value(), db_settings, runtime_factory
    )

    require(first.tenant_id != second.tenant_id, "two Tenants share an id")
    require(principal_first.tenant_id == first.tenant_id, "the first key resolves elsewhere")
    require(principal_second.tenant_id == second.tenant_id, "the second key resolves elsewhere")


@pytest.mark.usefixtures("commit_failure")
async def test_a_commit_failure_leaves_no_tenant_no_key_and_returns_no_key(
    admin_factory: Factory,
    admin_settings: AdminSettings,
    admin_engine: AsyncEngine,
    tenant_slug_prefix: str,
) -> None:
    returned: ProvisionedTenant | None = None
    failure = ""
    try:
        returned = await provision_tenant(
            admin_factory, admin_settings, command(tenant_slug_prefix)
        )
    except Exception as error:
        failure = type(error).__name__

    require(returned is None, "the key material was returned although the commit failed")
    require(failure != "", "provisioning did not fail although the commit failed")
    require(
        await counts(admin_engine, tenant_slug_prefix) == (0, 0),
        "a Tenant or a key survived the failed commit",
    )


async def test_a_duplicate_slug_is_a_clean_error_and_creates_nothing_more(
    admin_factory: Factory,
    admin_settings: AdminSettings,
    admin_engine: AsyncEngine,
    tenant_slug_prefix: str,
) -> None:
    first = await provision(admin_factory, admin_settings, tenant_slug_prefix)

    error = await aerror_of(
        lambda: provision_tenant(admin_factory, admin_settings, command(tenant_slug_prefix)),
        BootstrapError,
        "provisioning a duplicate slug",
    )

    require(await counts(admin_engine, tenant_slug_prefix) == (1, 1), "a second Tenant appeared")
    assert_no_leaks(
        str(error), {"raw key": first.api_key.issued.raw_key.get_secret_value()}, "the error"
    )


async def test_a_tenant_cannot_be_created_under_the_context_of_another(
    admin_factory: Factory, admin_engine: AsyncEngine, tenant_slug_prefix: str
) -> None:
    other = uuid4()

    await aerror_of(
        lambda: _create_under_context(admin_factory, other, uuid4(), tenant_slug_prefix),
        DBAPIError,
        "creating a Tenant under another Tenant's context",
    )

    require(await counts(admin_engine, tenant_slug_prefix) == (0, 0), "a Tenant was created")


async def _create_under_context(
    factory: Factory, context: UUID, tenant_id: UUID, prefix: str
) -> None:
    async with tenant_session(factory, context) as session:
        await create_tenant(session, tenant_id, command(prefix))


# --- emissão de novas chaves -----------------------------------------------------------------


async def test_it_issues_a_key_with_the_requested_scopes_label_and_ttl(
    admin_factory: Factory,
    admin_settings: AdminSettings,
    db_settings: Settings,
    runtime_factory: Factory,
    tenant_slug_prefix: str,
) -> None:
    tenant = await provision(admin_factory, admin_settings, tenant_slug_prefix)

    async with tenant_session(admin_factory, tenant.tenant_id) as session:
        granted = await issue_api_key(
            session, tenant.tenant_id, "ERP", ("tenant:read",), 30, admin_settings
        )

    principal = await authenticate(
        granted.issued.raw_key.get_secret_value(), db_settings, runtime_factory
    )
    require(principal.scopes == frozenset({"tenant:read"}), "wrong scopes")
    require(granted.label == "ERP" and granted.scopes == ("tenant:read",), "wrong label or scopes")
    lifetime = granted.expires_at - datetime.now(UTC)
    require(
        timedelta(days=29) < lifetime <= timedelta(days=30), "the key does not expire in 30 days"
    )


@pytest.mark.parametrize(
    "arguments",
    [
        {"scopes": ("tenant:admin",)},
        {"scopes": ()},
        {"scopes": ("tenant:read", "bogus")},
        {"ttl_days": 0},
        {"ttl_days": 366},
        {"label": "   "},
        {"label": "x" * 101},
    ],
    ids=[
        "unknown-scope",
        "no-scope",
        "one-unknown-scope",
        "ttl-zero",
        "ttl-too-long",
        "blank-label",
        "long-label",
    ],
)
async def test_it_rejects_invalid_requests_before_touching_the_database(
    admin_factory: Factory,
    admin_settings: AdminSettings,
    admin_engine: AsyncEngine,
    tenant_slug_prefix: str,
    arguments: dict[str, Any],
) -> None:
    tenant = await provision(admin_factory, admin_settings, tenant_slug_prefix)
    request: dict[str, Any] = {"label": "ERP", "scopes": ("tenant:read",), "ttl_days": 30}
    request.update(arguments)

    async def attempt() -> None:
        async with tenant_session(admin_factory, tenant.tenant_id) as session:
            await issue_api_key(
                session,
                tenant.tenant_id,
                request["label"],
                request["scopes"],
                request["ttl_days"],
                admin_settings,
            )

    await aerror_of(attempt, BootstrapError, "issuing an invalid request")

    require(await counts(admin_engine, tenant_slug_prefix) == (1, 1), "a key was stored")


async def test_it_cannot_issue_a_key_for_an_unknown_tenant_or_for_another_tenants_context(
    admin_factory: Factory,
    admin_settings: AdminSettings,
    admin_engine: AsyncEngine,
    tenant_slug_prefix: str,
) -> None:
    mine = await provision(admin_factory, admin_settings, tenant_slug_prefix, "a")
    theirs = await provision(admin_factory, admin_settings, tenant_slug_prefix, "b")

    async def unknown() -> None:
        ghost = uuid4()
        async with tenant_session(admin_factory, ghost) as session:
            await issue_api_key(session, ghost, "ERP", ("tenant:read",), 30, admin_settings)

    async def crossed() -> None:
        async with tenant_session(admin_factory, mine.tenant_id) as session:
            await issue_api_key(
                session, theirs.tenant_id, "ERP", ("tenant:read",), 30, admin_settings
            )

    await aerror_of(unknown, TenantNotFound, "issuing for an unknown Tenant")
    await aerror_of(crossed, TenantNotFound, "issuing for another Tenant")

    require(await counts(admin_engine, tenant_slug_prefix) == (2, 2), "an extra key was stored")


@pytest.mark.usefixtures("commit_failure")
async def test_a_commit_failure_when_issuing_returns_no_key_and_stores_none(
    admin_factory: Factory,
    admin_settings: AdminSettings,
    admin_engine: AsyncEngine,
    migrator_engine: AsyncEngine,
    tenant_slug_prefix: str,
) -> None:
    # O Tenant é gravado sem chave (a trigger só dispara em INSERT de chave).
    tenant_id = uuid4()
    async with tenant_session(admin_factory, tenant_id) as session:
        await create_tenant(session, tenant_id, command(tenant_slug_prefix))

    returned = None
    failure = ""
    try:
        async with tenant_session(admin_factory, tenant_id) as session:
            returned = await issue_api_key(
                session, tenant_id, "ERP", ("tenant:read",), 30, admin_settings
            )
    except Exception as error:
        failure = type(error).__name__

    require(failure != "", "issuing did not fail although the commit failed")
    require(await counts(admin_engine, tenant_slug_prefix) == (1, 0), "a key survived the commit")
    del returned  # o objeto só existe dentro da transação desfeita; nunca é mostrado


# --- revogação -------------------------------------------------------------------------------


async def revoked_at(factory: Factory, tenant_id: UUID, key_id: UUID) -> datetime | None:
    async with tenant_session(factory, tenant_id) as session:
        return (
            await session.execute(
                text("SELECT revoked_at FROM cce.api_keys WHERE id = :id"), {"id": key_id}
            )
        ).scalar_one()


async def test_it_revokes_a_key_and_the_next_authentication_fails(
    admin_factory: Factory,
    admin_settings: AdminSettings,
    db_settings: Settings,
    runtime_factory: Factory,
    tenant_slug_prefix: str,
) -> None:
    tenant = await provision(admin_factory, admin_settings, tenant_slug_prefix)
    raw = tenant.api_key.issued.raw_key.get_secret_value()
    await authenticate(raw, db_settings, runtime_factory)

    async with tenant_session(admin_factory, tenant.tenant_id) as session:
        done = await revoke_api_key(session, tenant.tenant_id, tenant.api_key.issued.key_id)

    require(done is True, "the revocation was not reported")
    await reject(raw, db_settings, runtime_factory, "authenticating a revoked key")


async def test_revoking_twice_is_idempotent_and_keeps_the_first_timestamp(
    admin_factory: Factory, admin_settings: AdminSettings, tenant_slug_prefix: str
) -> None:
    tenant = await provision(admin_factory, admin_settings, tenant_slug_prefix)
    key_id = tenant.api_key.issued.key_id

    async with tenant_session(admin_factory, tenant.tenant_id) as session:
        first = await revoke_api_key(session, tenant.tenant_id, key_id)
    stamp = await revoked_at(admin_factory, tenant.tenant_id, key_id)
    async with tenant_session(admin_factory, tenant.tenant_id) as session:
        second = await revoke_api_key(session, tenant.tenant_id, key_id)

    require(first is True and second is True, "revoking again must still succeed")
    require(stamp is not None, "the key has no revocation timestamp")
    require(
        await revoked_at(admin_factory, tenant.tenant_id, key_id) == stamp,
        "the second revocation moved the timestamp",
    )


async def test_it_does_not_revoke_the_key_of_another_tenant_or_an_unknown_key(
    admin_factory: Factory,
    admin_settings: AdminSettings,
    db_settings: Settings,
    runtime_factory: Factory,
    tenant_slug_prefix: str,
) -> None:
    mine = await provision(admin_factory, admin_settings, tenant_slug_prefix, "a")
    theirs = await provision(admin_factory, admin_settings, tenant_slug_prefix, "b")

    async with tenant_session(admin_factory, mine.tenant_id) as session:
        crossed = await revoke_api_key(session, mine.tenant_id, theirs.api_key.issued.key_id)
    async with tenant_session(admin_factory, mine.tenant_id) as session:
        wrong_context = await revoke_api_key(
            session, theirs.tenant_id, theirs.api_key.issued.key_id
        )
    async with tenant_session(admin_factory, mine.tenant_id) as session:
        unknown = await revoke_api_key(session, mine.tenant_id, uuid4())

    require(
        crossed is False and wrong_context is False and unknown is False, "it reported a revocation"
    )
    require(
        await revoked_at(admin_factory, theirs.tenant_id, theirs.api_key.issued.key_id) is None,
        "the key of another Tenant was revoked",
    )
    principal = await authenticate(
        theirs.api_key.issued.raw_key.get_secret_value(), db_settings, runtime_factory
    )
    require(principal.tenant_id == theirs.tenant_id, "the other Tenant's key stopped working")


# --- suspender e ativar ----------------------------------------------------------------------


async def test_suspending_a_tenant_blocks_its_keys_and_activating_restores_them(
    admin_factory: Factory,
    admin_settings: AdminSettings,
    db_settings: Settings,
    runtime_factory: Factory,
    tenant_slug_prefix: str,
) -> None:
    tenant = await provision(admin_factory, admin_settings, tenant_slug_prefix)
    raw = tenant.api_key.issued.raw_key.get_secret_value()

    async with tenant_session(admin_factory, tenant.tenant_id) as session:
        await set_tenant_status(session, tenant.tenant_id, "suspended")
    await reject(raw, db_settings, runtime_factory, "authenticating for a suspended Tenant")
    async with tenant_session(admin_factory, tenant.tenant_id) as session:
        await set_tenant_status(session, tenant.tenant_id, "active")

    principal = await authenticate(raw, db_settings, runtime_factory)
    require(principal.tenant_id == tenant.tenant_id, "the reactivated Tenant did not authenticate")


async def test_it_only_accepts_active_and_suspended_and_knows_its_tenants(
    admin_factory: Factory, admin_settings: AdminSettings, tenant_slug_prefix: str
) -> None:
    tenant = await provision(admin_factory, admin_settings, tenant_slug_prefix)

    async def invalid() -> None:
        async with tenant_session(admin_factory, tenant.tenant_id) as session:
            await set_tenant_status(session, tenant.tenant_id, "archived")

    async def unknown() -> None:
        ghost = uuid4()
        async with tenant_session(admin_factory, ghost) as session:
            await set_tenant_status(session, ghost, "suspended")

    await aerror_of(invalid, BootstrapError, "setting an unsupported status")
    await aerror_of(unknown, TenantNotFound, "setting the status of an unknown Tenant")


async def test_changing_the_status_touches_updated_at(
    admin_factory: Factory,
    admin_settings: AdminSettings,
    tenant_slug_prefix: str,
) -> None:
    tenant = await provision(admin_factory, admin_settings, tenant_slug_prefix)

    async def stamp() -> datetime:
        async with tenant_session(admin_factory, tenant.tenant_id) as session:
            return (await session.execute(text("SELECT updated_at FROM cce.tenants"))).scalar_one()

    before = await stamp()
    async with tenant_session(admin_factory, tenant.tenant_id) as session:
        await set_tenant_status(session, tenant.tenant_id, "suspended")

    require(await stamp() > before, "updated_at did not move")
