from __future__ import annotations

import json
import ssl
from pathlib import Path

import pytest
from sqlalchemy.pool import NullPool

from cce.shared.config import (
    AdminSettings,
    MigrationSettings,
    Settings,
    load_admin_settings,
    load_migration_settings,
    load_settings,
)
from cce.shared.db.engine import (
    build_engine,
    build_migration_engine,
    build_session_factory,
    migration_connect_args,
    runtime_connect_args,
)

PASSWORD = "engine-password-SENTINEL"
PEPPER = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="  # 32 bytes, só para validar o formato

# Certificado autoassinado descartável (a chave privada foi apagada): só prova que o contexto TLS
# carrega a CA configurada. Não confia em nada de verdade.
TEST_CA = """\
-----BEGIN CERTIFICATE-----
MIIDKTCCAhGgAwIBAgIUf53EE4s+JjEymXNr7ZkAc7geGRUwDQYJKoZIhvcNAQEL
BQAwIzEhMB8GA1UEAwwYY2NlLXRlc3QtY2Etbm90LWEtc2VjcmV0MCAXDTI2MTAy
OTAxNTQ0N1oYDzIxMjYxMDA1MDE1NDQ3WjAjMSEwHwYDVQQDDBhjY2UtdGVzdC1j
YS1ub3QtYS1zZWNyZXQwggEiMA0GCSqGSIb3DQEBAQUAA4IBDwAwggEKAoIBAQCu
eeyAgzIftAcyJIysyPFoq8nr3DA7MkkWU/qcZl5tdRDavi9ZHPjgB9j5CN05xn/M
t8vzqu3QdtX/MpcL1wKDhZ9ULLPyCnDI+zSVQNzgpaHZqkeNc167dDAao5ctJjNO
EAz/MZRJRqhEF24hUEKQiIsWXqTanrFsZps16E+gn3N3XGZkxe2j8Es9YS0JjY18
ANahyP12ezMwEeN09gsEzKvPpYSW9HpuAasiOP2kE0zzktGhlFqGsqy8bXb63KCK
ic1vwUpFGUt9CMoTMDRjTHGtTNje9knWsSVf3g03mHIE7Nw6ctl6SzOo96VslClO
0FTiemkw/SlhUre9p5avAgMBAAGjUzBRMB0GA1UdDgQWBBQpst/cicnCYoVmQZDb
vGtKdG7kOjAfBgNVHSMEGDAWgBQpst/cicnCYoVmQZDbvGtKdG7kOjAPBgNVHRMB
Af8EBTADAQH/MA0GCSqGSIb3DQEBCwUAA4IBAQBzRnn2GdYHdetxslVyJzQ3wmLq
y11/4aux0bXYJ32C0olxY0xOnc1O8h3VQp/43v4g2E+tzSu3GEa2hAfnvP4TDsk3
NqiLaB3uIqy6VDl8JLqeQUeY1inBmVv8YKoq0m5/TNKFlkvjsjpB/J5fAh7LM/Bu
lIKomwSgNLpaA6VPPxRDxLI9E97aBHr8W2cw3mWRVgLlNBl5+ieInq4lyYnG5SYh
Go4Tw6xQn0nXs7xDuQERCcKADnRJcvSUx+YIXQkUdAdC4qzhEudiz5fDpT3v60j1
ixG7dRMzZn0olZHJC+FGAhjgoj6fu+T8w4se4ojLQ/WvEa8Cb9FSkMpFle1L
-----END CERTIFICATE-----
"""


def base_env(**overrides: str) -> dict[str, str]:
    env = {
        "CCE_ENV": "local",
        "CCE_DATABASE_URL": f"postgresql+asyncpg://cce_app:{PASSWORD}@db.invalid:5432/cce",
        "CCE_MIGRATOR_DATABASE_URL": (
            f"postgresql+asyncpg://cce_migrator:{PASSWORD}@db.invalid:5432/cce"
        ),
        "CCE_REDIS_URL": "redis://redis.invalid:6379/0",
        "CCE_API_KEY_PEPPERS": json.dumps({"1": PEPPER}),
    }
    env.update(overrides)
    return env


@pytest.fixture
def local_settings(monkeypatch: pytest.MonkeyPatch, clean_cce_env: Path) -> Settings:
    for name, value in base_env(CCE_DB_POOL_SIZE="7", CCE_DB_MAX_OVERFLOW="3").items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("CCE_DB_POOL_TIMEOUT", "4")
    monkeypatch.setenv("CCE_DB_COMMAND_TIMEOUT", "6")
    return load_settings()


@pytest.fixture
def staging_env(monkeypatch: pytest.MonkeyPatch, clean_cce_env: Path) -> None:
    ca_file = clean_cce_env / "ca.pem"
    ca_file.write_text(TEST_CA)
    env = base_env(
        CCE_ENV="staging",
        CCE_DATABASE_CA_FILE=str(ca_file),
        CCE_REDIS_URL="rediss://redis.invalid:6380/0",
    )
    for name, value in env.items():
        monkeypatch.setenv(name, value)


def test_runtime_engine_uses_the_pool_settings_without_connecting(
    local_settings: Settings,
) -> None:
    engine = build_engine(local_settings)  # o host não resolve: criar o engine não conecta

    pool = engine.sync_engine.pool
    assert pool.size() == 7  # type: ignore[attr-defined]
    assert pool._max_overflow == 3  # type: ignore[attr-defined]
    assert pool.timeout() == 4  # type: ignore[attr-defined]
    assert pool._pre_ping is True  # type: ignore[attr-defined]
    assert engine.echo is False
    assert engine.sync_engine.hide_parameters is True


def test_engine_never_exposes_the_password(local_settings: Settings) -> None:
    engine = build_engine(local_settings)

    assert PASSWORD not in repr(engine) + str(engine.url) + repr(engine.sync_engine)


def test_runtime_connect_args_set_the_command_timeout_and_no_tls_locally(
    local_settings: Settings,
) -> None:
    arguments = runtime_connect_args(local_settings)

    assert arguments == {"command_timeout": 6}


def test_remote_environments_verify_the_server_certificate_and_hostname(
    staging_env: None,
) -> None:
    arguments = runtime_connect_args(load_settings())

    context = arguments["ssl"]
    assert isinstance(context, ssl.SSLContext)
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert arguments["command_timeout"] == 5


def test_remote_tls_context_trusts_only_the_configured_ca(staging_env: None) -> None:
    context = runtime_connect_args(load_settings())["ssl"]

    certificates = context.get_ca_certs()
    assert len(certificates) == 1
    assert dict(item[0] for item in certificates[0]["subject"])["commonName"] == (
        "cce-test-ca-not-a-secret"
    )


@pytest.mark.parametrize("loader", [load_migration_settings, load_admin_settings])
def test_migration_connect_args_have_tls_in_remote_environments_and_no_command_timeout(
    staging_env: None, loader: object
) -> None:
    settings = loader()  # type: ignore[operator]

    arguments = migration_connect_args(settings)

    assert set(arguments) == {"ssl"}
    assert arguments["ssl"].check_hostname is True


def test_migration_connect_args_are_empty_locally(
    monkeypatch: pytest.MonkeyPatch, clean_cce_env: Path
) -> None:
    for name, value in base_env().items():
        monkeypatch.setenv(name, value)

    assert migration_connect_args(load_migration_settings()) == {}


@pytest.mark.parametrize("loader", [load_migration_settings, load_admin_settings])
def test_migration_engine_never_pools_connections(
    monkeypatch: pytest.MonkeyPatch, clean_cce_env: Path, loader: object
) -> None:
    for name, value in base_env().items():
        monkeypatch.setenv(name, value)
    settings: MigrationSettings | AdminSettings = loader()  # type: ignore[operator]

    engine = build_migration_engine(settings)

    assert isinstance(engine.sync_engine.pool, NullPool)
    assert engine.echo is False
    assert engine.sync_engine.hide_parameters is True
    assert PASSWORD not in repr(engine) + str(engine.url)
    assert engine.url.username == "cce_migrator"


def test_runtime_engine_connects_as_the_runtime_role(local_settings: Settings) -> None:
    assert build_engine(local_settings).url.username == "cce_app"


def test_session_factory_does_not_expire_on_commit_nor_autoflush(
    local_settings: Settings,
) -> None:
    factory = build_session_factory(build_engine(local_settings))

    assert factory.kw["expire_on_commit"] is False
    assert factory.kw["autoflush"] is False
