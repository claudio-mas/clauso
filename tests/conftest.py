from __future__ import annotations

import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
import sensitive_plugin
from db_support import (
    OpenSession,
    OpenTenantSession,
    assert_compose_targets_validated_database,
    assert_disposable_database,
    run_alembic,
)
from integration.support import compose_env_file, read_dotenv, require
from seeding import (
    SeededTenant,
    create_commit_failure,
    create_probe,
    create_tenant_failure,
    drop_commit_failure,
    drop_probe,
    drop_tenant_failure,
    remove_tenant,
    remove_tenants_with_slug_prefix,
    seed_key,
    seed_tenant,
)
from sensitive_plugin import pytest_runtest_makereport
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from cce.shared.config import (
    AdminSettings,
    ConfigurationError,
    MigrationSettings,
    Settings,
    load_admin_settings,
    load_migration_settings,
    load_settings,
)

if TYPE_CHECKING:
    import httpx
    from fastapi import FastAPI

ENV_FILE_HINT = "run from the repository root with CCE_ENV_FILE=.env.test"

__all__ = ["pytest_configure", "pytest_runtest_makereport"]  # hooks de sensitive_plugin


def pytest_configure(config: pytest.Config) -> None:
    sensitive_plugin.pytest_configure(config)
    # Em toda falha, a senha de um DSN que o driver exibiu nos argumentos sai como `***`.
    config.pluginmanager.import_plugin("redaction_plugin")
    # Fixture `log_output`: os logs JSON da aplicação num buffer, com a configuração restaurada.
    config.pluginmanager.import_plugin("log_support")


@pytest.fixture
def clean_cce_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Isola o teste do ambiente real: sem variáveis CCE_* e com um diretório de trabalho vazio.

    Devolve o diretório de trabalho, onde um `.env` de teste pode ser criado.
    """
    for name in list(os.environ):
        if name.upper().startswith("CCE_"):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def db_settings() -> Settings:
    """Settings de runtime. Só roda com CCE_ENV=test e banco `cce_test*`; nunca mostra o DSN."""
    try:
        settings = load_settings()
    except ConfigurationError as error:
        pytest.fail(f"cannot load settings ({ENV_FILE_HINT}): {error}", pytrace=False)
    assert_disposable_database(settings.env, settings.database_url.get_secret_value(), "runtime")
    return settings


@pytest.fixture
def migrator_settings() -> MigrationSettings:
    """Settings do migrator, com o mesmo guard do banco descartável."""
    return _guarded_migration_settings()


def _guarded_migration_settings() -> MigrationSettings:
    try:
        settings = load_migration_settings()
    except ConfigurationError as error:
        pytest.fail(f"cannot load migration settings ({ENV_FILE_HINT}): {error}", pytrace=False)
    assert_disposable_database(
        settings.env, settings.migrator_database_url.get_secret_value(), "migrator"
    )
    return settings


@pytest.fixture
def admin_settings() -> AdminSettings:
    """Settings da CLI administrativa, com o mesmo guard do banco descartável."""
    try:
        settings = load_admin_settings()
    except ConfigurationError as error:
        pytest.fail(f"cannot load admin settings ({ENV_FILE_HINT}): {error}", pytrace=False)
    assert_disposable_database(
        settings.env, settings.migrator_database_url.get_secret_value(), "migrator"
    )
    return settings


@pytest.fixture(scope="module")
def migrated_database() -> None:
    """Leva o banco descartável ao head das migrations (idempotente)."""
    run_alembic(_guarded_migration_settings(), "upgrade", "head")


@pytest.fixture
def compose_targets_validated_database(migrator_settings: MigrationSettings) -> None:
    """Exige que o Compose mire o banco já validado. Peça esta fixture ANTES de qualquer conexão
    ou etapa mutante via Compose (build, run, exec) e antes de `downgrade`.
    """
    assert_compose_targets_validated_database(migrator_settings)


def _engine(url: str | URL) -> AsyncEngine:
    # hide_parameters: um erro de SQL nunca mostra os valores ligados (digests, por exemplo).
    return create_async_engine(
        url, poolclass=NullPool, hide_parameters=True, connect_args={"timeout": 10}
    )


@pytest.fixture
async def app_connection(db_settings: Settings) -> AsyncIterator[AsyncConnection]:
    """Conexão real como `cce_app`, o role de runtime."""
    engine = _engine(db_settings.database_url.get_secret_value())
    try:
        async with engine.connect() as connection:
            yield connection
    finally:
        await engine.dispose()


@pytest.fixture
async def migrator_connection(
    migrator_settings: MigrationSettings,
) -> AsyncIterator[AsyncConnection]:
    """Conexão real como `cce_migrator`, o dono do schema."""
    engine = _engine(migrator_settings.migrator_database_url.get_secret_value())
    try:
        async with engine.connect() as connection:
            yield connection
    finally:
        await engine.dispose()


@pytest.fixture
async def migrator_engine(
    migrator_settings: MigrationSettings, migrated_database: None
) -> AsyncIterator[AsyncEngine]:
    """Engine do dono do schema, para gravar e limpar dados de teste (sempre sob contexto)."""
    engine = _engine(migrator_settings.migrator_database_url.get_secret_value())
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def admin_engine(migrator_settings: MigrationSettings) -> AsyncIterator[AsyncEngine]:
    """Superusuário do banco descartável: defeitos de role, limpeza e conferências que a RLS
    esconderia do dono. Nunca é usado por código de produção.
    """
    password = read_dotenv(compose_env_file()).get("CCE_POSTGRES_ADMIN_PASSWORD", "")
    require(password != "", "CCE_POSTGRES_ADMIN_PASSWORD is not set in the test dotenv")
    url = make_url(migrator_settings.migrator_database_url.get_secret_value()).set(
        username="postgres", password=password
    )
    assert_disposable_database(migrator_settings.env, url, "admin")
    engine = _engine(url)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def admin_connection(admin_engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    """Conexão do superusuário do banco descartável (defeitos sempre desfeitos)."""
    async with admin_engine.connect() as connection:
        yield connection
        await connection.rollback()


@pytest.fixture
async def commit_failure(migrator_engine: AsyncEngine) -> AsyncIterator[None]:
    """Faz todo COMMIT que inseriu uma chave falhar no PostgreSQL (removido no fim)."""
    await create_commit_failure(migrator_engine)
    try:
        yield
    finally:
        await drop_commit_failure(migrator_engine)


@pytest.fixture
async def tenant_slug_prefix(
    admin_engine: AsyncEngine, migrated_database: None
) -> AsyncIterator[str]:
    """Prefixo único de slug para Tenants criados pelo código sob teste; limpos no fim."""
    prefix = f"t-{uuid4().hex[:12]}"
    try:
        yield prefix
    finally:
        await remove_tenants_with_slug_prefix(admin_engine, prefix)


@pytest.fixture
async def runtime_engine(
    db_settings: Settings, migrated_database: None
) -> AsyncIterator[AsyncEngine]:
    """Engine do runtime montado pela fábrica de produção, com pool de UMA conexão.

    `pool_size=1` e `max_overflow=0` fazem toda sessão reutilizar a mesma conexão física, que é
    o cenário em que um contexto residual apareceria.
    """
    from cce.shared.db.engine import build_engine

    settings = db_settings.model_copy(
        update={"db_pool_size": 1, "db_max_overflow": 0, "db_pool_timeout": 5}
    )
    engine = build_engine(settings)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
def runtime_factory(runtime_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    from cce.shared.db.engine import build_session_factory

    return build_session_factory(runtime_engine)


@pytest.fixture
def runtime_session(runtime_factory: async_sessionmaker[AsyncSession]) -> OpenTenantSession:
    """`runtime_session(tenant_id)`: a sessão transacional de produção, como `cce_app`."""
    from cce.shared.db.session import tenant_session

    def open_session(tenant_id: UUID) -> AbstractAsyncContextManager[AsyncSession]:
        return tenant_session(runtime_factory, tenant_id)

    return open_session


@pytest.fixture
def runtime_no_context(runtime_factory: async_sessionmaker[AsyncSession]) -> OpenSession:
    """Transação do runtime que NUNCA aplica contexto de Tenant."""

    @asynccontextmanager
    async def open_session() -> AsyncIterator[AsyncSession]:
        async with runtime_factory() as session, session.begin():
            yield session

    return open_session


@pytest.fixture
async def tenant_a(migrator_engine: AsyncEngine) -> AsyncIterator[SeededTenant]:
    tenant = await seed_tenant(migrator_engine, name="Empresa A")
    try:
        yield tenant
    finally:
        await remove_tenant(migrator_engine, tenant.id)


@pytest.fixture
async def tenant_b(migrator_engine: AsyncEngine) -> AsyncIterator[SeededTenant]:
    tenant = await seed_tenant(migrator_engine, name="Empresa B")
    try:
        yield tenant
    finally:
        await remove_tenant(migrator_engine, tenant.id)


@pytest.fixture
async def keyed_a(migrator_engine: AsyncEngine, tenant_a: SeededTenant) -> SeededTenant:
    """O Tenant A com uma chave. Só testes `sensitive` pedem esta fixture (há digest no banco)."""
    return await seed_key(migrator_engine, tenant_a, label="chave A")


@pytest.fixture
async def keyed_b(migrator_engine: AsyncEngine, tenant_b: SeededTenant) -> SeededTenant:
    return await seed_key(migrator_engine, tenant_b, label="chave B")


@pytest.fixture
async def rls_probe(migrator_engine: AsyncEngine) -> AsyncIterator[None]:
    """Cria `cce.rls_probe` só para o teste e a remove no fim, mesmo se o teste falhar."""
    await create_probe(migrator_engine)
    try:
        yield
    finally:
        await drop_probe(migrator_engine)


@pytest.fixture
async def tenant_failure(
    migrator_engine: AsyncEngine,
) -> AsyncIterator[Callable[[str, UUID], Awaitable[None]]]:
    """`await tenant_failure(kind, tenant_id)`: faz falhar a alteração do Tenant até o fim."""

    async def inject(kind: str, tenant_id: UUID) -> None:
        await create_tenant_failure(migrator_engine, kind, tenant_id)

    try:
        yield inject
    finally:
        await drop_tenant_failure(migrator_engine)


@pytest.fixture
def api_settings(db_settings: Settings, migrated_database: None) -> Settings:
    """Settings da API de teste, com pool de UMA conexão: toda requisição reutiliza a mesma."""
    return db_settings.model_copy(
        update={"db_pool_size": 1, "db_max_overflow": 0, "db_pool_timeout": 5}
    )


@pytest.fixture
async def api(api_settings: Settings) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient]]:
    """A aplicação de produção com lifespan, contra o banco e o Redis de teste."""
    from api_support import running_app

    async with running_app(api_settings) as running:
        yield running
