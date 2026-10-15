from __future__ import annotations

import base64
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest

from cce.shared.config import (
    AdminSettings,
    ConfigurationError,
    MigrationSettings,
    Settings,
    load_admin_settings,
    load_migration_settings,
    load_settings,
)

pytestmark = pytest.mark.usefixtures("clean_cce_env")

APP_PASSWORD = "app-password-SENTINEL"
MIGRATOR_PASSWORD = "migrator-password-SENTINEL"
REDIS_PASSWORD = "redis-password-SENTINEL"
PEPPER_B64 = base64.b64encode(bytes(range(32))).decode()
PEPPER_2_B64 = base64.b64encode(bytes(range(32, 64))).decode()
SECRET_SENTINELS = (APP_PASSWORD, MIGRATOR_PASSWORD, REDIS_PASSWORD, PEPPER_B64, PEPPER_2_B64)

Loader = Callable[[], Any]
ALL_LOADERS = [
    pytest.param(load_settings, id="settings"),
    pytest.param(load_migration_settings, id="migration"),
    pytest.param(load_admin_settings, id="admin"),
]


def local_env(**overrides: str | None) -> dict[str, str]:
    env = {
        "CCE_ENV": "local",
        "CCE_DATABASE_URL": f"postgresql+asyncpg://cce_app:{APP_PASSWORD}@127.0.0.1:5432/cce",
        "CCE_MIGRATOR_DATABASE_URL": (
            f"postgresql+asyncpg://cce_migrator:{MIGRATOR_PASSWORD}@127.0.0.1:5432/cce"
        ),
        "CCE_REDIS_URL": "redis://127.0.0.1:6379/0",
        "CCE_API_KEY_PEPPERS": json.dumps({"1": PEPPER_B64}),
    }
    return apply_overrides(env, overrides)


def remote_env(
    tmp_path: Path, environment: str = "staging", **overrides: str | None
) -> dict[str, str]:
    ca_file = tmp_path / "ca.pem"
    ca_file.write_text("not a real certificate, only has to be readable")
    env = {
        "CCE_ENV": environment,
        "CCE_DATABASE_URL": f"postgresql+asyncpg://cce_app:{APP_PASSWORD}@db.internal:5432/cce",
        "CCE_MIGRATOR_DATABASE_URL": (
            f"postgresql+asyncpg://cce_migrator:{MIGRATOR_PASSWORD}@db.internal:5432/cce"
        ),
        "CCE_DATABASE_CA_FILE": str(ca_file),
        "CCE_REDIS_URL": f"rediss://:{REDIS_PASSWORD}@redis.internal:6380/0",
        "CCE_API_KEY_PEPPERS": json.dumps({"1": PEPPER_B64}),
    }
    return apply_overrides(env, overrides)


def apply_overrides(env: dict[str, str], overrides: Mapping[str, str | None]) -> dict[str, str]:
    for name, value in overrides.items():
        if value is None:
            env.pop(name, None)
        else:
            env[name] = value
    return env


def set_env(monkeypatch: pytest.MonkeyPatch, env: Mapping[str, str]) -> None:
    for name, value in env.items():
        monkeypatch.setenv(name, value)


def assert_refused(loader: Loader, *names: str) -> str:
    """O loader recusa a configuração, cita cada campo e nunca repete um segredo."""
    with pytest.raises(ConfigurationError) as excinfo:
        loader()
    message = str(excinfo.value)
    for name in names:
        assert name in message
    for secret in SECRET_SENTINELS:
        assert secret not in message
        assert secret not in repr(excinfo.value)
    # A causa original (ValidationError) carrega o valor recebido; ela não pode viajar junto.
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__context__ is None or excinfo.value.__suppress_context__
    return message


# --- carga válida -----------------------------------------------------------------------------


def test_local_settings_load_with_documented_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    set_env(monkeypatch, local_env())

    settings = load_settings()

    assert isinstance(settings, Settings)
    assert settings.env == "local"
    assert settings.service_name == "cce-api"
    assert settings.version == "0.1.0"
    assert settings.log_level == "INFO"
    assert settings.log_format == "json"
    assert (settings.db_pool_size, settings.db_max_overflow) == (5, 5)
    assert (settings.db_pool_timeout, settings.db_command_timeout) == (5, 5)
    assert settings.allowed_origins == []
    assert settings.api_key_active_pepper_version == 1
    assert settings.api_key_ttl_days == 90
    assert settings.docs_enabled is True
    assert settings.database_ca_file is None
    assert APP_PASSWORD in settings.database_url.get_secret_value()
    assert settings.api_key_peppers[1].get_secret_value() == PEPPER_B64


def test_settings_keys_are_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    set_env(monkeypatch, {name.lower(): v for name, v in local_env().items()})

    assert load_settings().env == "local"


def test_secrets_do_not_appear_in_repr_or_dump(monkeypatch: pytest.MonkeyPatch) -> None:
    set_env(monkeypatch, local_env())
    settings = load_settings()

    rendered = repr(settings) + str(settings) + repr(settings.model_dump())
    rendered += settings.model_dump_json()

    for secret in SECRET_SENTINELS:
        assert secret not in rendered


def test_runtime_settings_ignore_the_migrator_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    set_env(monkeypatch, local_env())

    settings = load_settings()

    assert not hasattr(settings, "migrator_database_url")
    assert "migrator_database_url" not in settings.model_dump()


def test_migration_and_admin_settings_load_and_use_the_migrator_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_env(monkeypatch, local_env())

    migration = load_migration_settings()
    admin = load_admin_settings()

    assert isinstance(migration, MigrationSettings)
    assert isinstance(admin, AdminSettings)
    assert "cce_migrator" in migration.migrator_database_url.get_secret_value()
    assert "cce_migrator" in admin.migrator_database_url.get_secret_value()
    assert admin.api_key_peppers[1].get_secret_value() == PEPPER_B64
    assert admin.api_key_active_pepper_version == 1
    assert admin.api_key_ttl_days == 90
    assert not hasattr(migration, "api_key_peppers")
    assert not hasattr(migration, "database_url")


def test_migration_settings_do_not_require_runtime_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_env(
        monkeypatch,
        local_env(CCE_DATABASE_URL=None, CCE_REDIS_URL=None, CCE_API_KEY_PEPPERS=None),
    )

    assert load_migration_settings().env == "local"


# --- variável obrigatória ausente -----------------------------------------------------------


@pytest.mark.parametrize(
    ("loader", "name"),
    [
        (load_settings, "CCE_DATABASE_URL"),
        (load_settings, "CCE_REDIS_URL"),
        (load_settings, "CCE_API_KEY_PEPPERS"),
        (load_migration_settings, "CCE_MIGRATOR_DATABASE_URL"),
        (load_admin_settings, "CCE_MIGRATOR_DATABASE_URL"),
        (load_admin_settings, "CCE_API_KEY_PEPPERS"),
    ],
)
def test_missing_required_variable_names_the_field(
    monkeypatch: pytest.MonkeyPatch, loader: Loader, name: str
) -> None:
    set_env(monkeypatch, local_env(**{name: None}))

    assert_refused(loader, name)


@pytest.mark.parametrize(
    ("name", "bad_value"),
    [
        ("CCE_DATABASE_URL", "nao-e-uma-url-SENTINEL-s3cr3t"),
        ("CCE_REDIS_URL", "nao-e-uma-url-SENTINEL-s3cr3t"),
        ("CCE_API_KEY_PEPPERS", "{SENTINEL-s3cr3t"),
        ("CCE_API_KEY_TTL_DAYS", "SENTINEL-s3cr3t"),
        ("CCE_DB_POOL_SIZE", "SENTINEL-s3cr3t"),
        ("CCE_ENV", "SENTINEL-s3cr3t"),
    ],
)
def test_invalid_value_error_cites_field_but_never_the_received_value(
    monkeypatch: pytest.MonkeyPatch, name: str, bad_value: str
) -> None:
    set_env(monkeypatch, local_env(**{name: bad_value}))

    message = assert_refused(load_settings, name)

    assert "SENTINEL" not in message
    assert "s3cr3t" not in message


# --- DSN ------------------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["cce_migrator", "cce_auth", "postgres", "CCE_APP", "cce_app2"])
def test_runtime_dsn_with_wrong_role_is_refused(monkeypatch: pytest.MonkeyPatch, role: str) -> None:
    dsn = f"postgresql+asyncpg://{role}:{APP_PASSWORD}@127.0.0.1:5432/cce"
    set_env(monkeypatch, local_env(CCE_DATABASE_URL=dsn))

    assert_refused(load_settings, "CCE_DATABASE_URL")


def test_runtime_dsn_without_user_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    set_env(monkeypatch, local_env(CCE_DATABASE_URL="postgresql+asyncpg://127.0.0.1:5432/cce"))

    assert_refused(load_settings, "CCE_DATABASE_URL")


@pytest.mark.parametrize("role", ["cce_app", "postgres", "cce_auth"])
@pytest.mark.parametrize("loader", [load_migration_settings, load_admin_settings])
def test_migrator_dsn_with_wrong_role_is_refused(
    monkeypatch: pytest.MonkeyPatch, loader: Loader, role: str
) -> None:
    dsn = f"postgresql+asyncpg://{role}:{MIGRATOR_PASSWORD}@127.0.0.1:5432/cce"
    set_env(monkeypatch, local_env(CCE_MIGRATOR_DATABASE_URL=dsn))

    assert_refused(loader, "CCE_MIGRATOR_DATABASE_URL")


@pytest.mark.parametrize(
    "dsn",
    [
        f"postgresql://cce_app:{APP_PASSWORD}@127.0.0.1:5432/cce",
        f"postgresql+psycopg://cce_app:{APP_PASSWORD}@127.0.0.1:5432/cce",
        f"mysql+asyncpg://cce_app:{APP_PASSWORD}@127.0.0.1:5432/cce",
        f"postgresql+asyncpg://cce_app:{APP_PASSWORD}@:5432/cce",
        f"postgresql+asyncpg://cce_app:{APP_PASSWORD}@127.0.0.1:5432",
        f"postgresql+asyncpg://cce_app:{APP_PASSWORD}@127.0.0.1:5432/",
        f"postgresql+asyncpg://cce_app:{APP_PASSWORD}@127.0.0.1:notaport/cce",
    ],
)
def test_runtime_dsn_with_wrong_shape_is_refused(monkeypatch: pytest.MonkeyPatch, dsn: str) -> None:
    set_env(monkeypatch, local_env(CCE_DATABASE_URL=dsn))

    assert_refused(load_settings, "CCE_DATABASE_URL")


@pytest.mark.parametrize("database", ["cce_test", "cce_test_ci"])
def test_test_environment_accepts_only_cce_test_databases(
    monkeypatch: pytest.MonkeyPatch, database: str
) -> None:
    env = local_env(
        CCE_ENV="test",
        CCE_DATABASE_URL=f"postgresql+asyncpg://cce_app:{APP_PASSWORD}@127.0.0.1:5432/{database}",
        CCE_MIGRATOR_DATABASE_URL=(
            f"postgresql+asyncpg://cce_migrator:{MIGRATOR_PASSWORD}@127.0.0.1:5432/{database}"
        ),
    )
    set_env(monkeypatch, env)

    assert load_settings().env == "test"
    assert load_migration_settings().env == "test"
    assert load_admin_settings().env == "test"


@pytest.mark.parametrize("loader", ALL_LOADERS)
def test_test_environment_refuses_a_non_test_database(
    monkeypatch: pytest.MonkeyPatch, loader: Loader
) -> None:
    set_env(monkeypatch, local_env(CCE_ENV="test"))  # DSNs apontam para o banco "cce"

    assert_refused(loader, "CCE_DATABASE_URL" if loader is load_settings else "CCE_MIGRATOR")


# --- TLS do banco e Redis em staging/production ---------------------------------------------


@pytest.mark.parametrize("environment", ["staging", "production"])
@pytest.mark.parametrize("loader", ALL_LOADERS)
def test_remote_environments_accept_a_complete_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, loader: Loader, environment: str
) -> None:
    set_env(monkeypatch, remote_env(tmp_path, environment))

    assert loader().env == environment


@pytest.mark.parametrize("environment", ["staging", "production"])
@pytest.mark.parametrize("loader", ALL_LOADERS)
def test_remote_environment_without_database_ca_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, loader: Loader, environment: str
) -> None:
    set_env(monkeypatch, remote_env(tmp_path, environment, CCE_DATABASE_CA_FILE=None))

    assert_refused(loader, "CCE_DATABASE_CA_FILE")


@pytest.mark.parametrize("loader", ALL_LOADERS)
def test_database_ca_must_be_a_readable_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, loader: Loader
) -> None:
    for bad in (str(tmp_path / "missing.pem"), str(tmp_path)):
        set_env(monkeypatch, remote_env(tmp_path, CCE_DATABASE_CA_FILE=bad))
        assert_refused(loader, "CCE_DATABASE_CA_FILE")


def test_redis_ca_is_optional_but_must_be_readable_when_given(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env = remote_env(tmp_path)
    set_env(monkeypatch, env)
    assert load_settings().redis_ca_file is None

    set_env(monkeypatch, {"CCE_REDIS_CA_FILE": env["CCE_DATABASE_CA_FILE"]})
    assert load_settings().redis_ca_file == Path(env["CCE_DATABASE_CA_FILE"])

    set_env(monkeypatch, {"CCE_REDIS_CA_FILE": str(tmp_path / "missing.pem")})
    assert_refused(load_settings, "CCE_REDIS_CA_FILE")


@pytest.mark.parametrize(
    ("environment", "redis_url"),
    [
        ("staging", "redis://redis.internal:6379/0"),
        ("production", "redis://redis.internal:6379/0"),
        ("local", "rediss://127.0.0.1:6380/0"),
        ("test", "rediss://127.0.0.1:6380/0"),
        ("local", "http://127.0.0.1:6379/0"),
    ],
)
def test_redis_scheme_must_match_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, environment: str, redis_url: str
) -> None:
    env = (
        remote_env(tmp_path, environment, CCE_REDIS_URL=redis_url)
        if environment in {"staging", "production"}
        else local_env(
            CCE_ENV=environment,
            CCE_REDIS_URL=redis_url,
            CCE_DATABASE_URL=f"postgresql+asyncpg://cce_app:{APP_PASSWORD}@127.0.0.1:5432/cce_test",
        )
    )
    set_env(monkeypatch, env)

    assert_refused(load_settings, "CCE_REDIS_URL")


@pytest.mark.parametrize(
    ("name", "url"),
    [
        (
            "CCE_DATABASE_URL",
            f"postgresql+asyncpg://cce_app:{APP_PASSWORD}@db.internal:5432/cce?ssl=disable",
        ),
        (
            "CCE_DATABASE_URL",
            f"postgresql+asyncpg://cce_app:{APP_PASSWORD}@db.internal:5432/cce?sslmode=disable",
        ),
        ("CCE_REDIS_URL", f"rediss://:{REDIS_PASSWORD}@redis.internal:6380/0?ssl_cert_reqs=none"),
    ],
)
def test_remote_environments_refuse_urls_that_weaken_tls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str, url: str
) -> None:
    set_env(monkeypatch, remote_env(tmp_path, **{name: url}))

    assert_refused(load_settings, name)


# --- dotenv ---------------------------------------------------------------------------------


def write_dotenv(path: Path, env: Mapping[str, str]) -> None:
    path.write_text("".join(f"{name}='{value}'\n" for name, value in env.items()))


def test_local_environment_reads_dotenv_from_the_working_directory(
    clean_cce_env: Path,
) -> None:
    write_dotenv(clean_cce_env / ".env", local_env())

    settings = load_settings()

    assert settings.env == "local"
    assert APP_PASSWORD in settings.database_url.get_secret_value()


def test_cce_env_file_selects_another_dotenv(
    monkeypatch: pytest.MonkeyPatch, clean_cce_env: Path
) -> None:
    write_dotenv(clean_cce_env / ".env", local_env(CCE_DATABASE_URL="ignored"))
    write_dotenv(clean_cce_env / "other.env", local_env())
    monkeypatch.setenv("CCE_ENV_FILE", "other.env")

    assert load_settings().env == "local"


def test_process_environment_wins_over_dotenv(
    monkeypatch: pytest.MonkeyPatch, clean_cce_env: Path
) -> None:
    write_dotenv(clean_cce_env / ".env", local_env(CCE_API_KEY_TTL_DAYS="10"))
    set_env(monkeypatch, {"CCE_API_KEY_TTL_DAYS": "30"})

    assert load_settings().api_key_ttl_days == 30


@pytest.mark.parametrize("environment", ["staging", "production"])
@pytest.mark.parametrize("loader", ALL_LOADERS)
def test_dotenv_that_selects_a_remote_environment_is_refused(
    clean_cce_env: Path, loader: Loader, environment: str
) -> None:
    write_dotenv(clean_cce_env / ".env", remote_env(clean_cce_env, environment))

    assert_refused(loader, "CCE_ENV")


@pytest.mark.parametrize("environment", ["staging", "production"])
@pytest.mark.parametrize("loader", ALL_LOADERS)
def test_cce_env_file_is_refused_in_remote_environments(
    monkeypatch: pytest.MonkeyPatch, clean_cce_env: Path, loader: Loader, environment: str
) -> None:
    write_dotenv(clean_cce_env / "prod.env", local_env())
    set_env(monkeypatch, remote_env(clean_cce_env, environment, CCE_ENV_FILE="prod.env"))

    assert_refused(loader, "CCE_ENV_FILE")


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_remote_environments_never_read_a_stray_dotenv(
    monkeypatch: pytest.MonkeyPatch, clean_cce_env: Path, environment: str
) -> None:
    write_dotenv(clean_cce_env / ".env", local_env())  # a .env esquecido no diretório
    set_env(monkeypatch, remote_env(clean_cce_env, environment, CCE_REDIS_URL=None))

    assert_refused(load_settings, "CCE_REDIS_URL")  # o Redis do .env não preenche a lacuna


# --- origens CORS ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "origins",
    [
        '["*"]',
        '["https://app.example.com", "*"]',
        '["https://*.example.com"]',
        '["https://app.example.com/"]',
        '["https://app.example.com/path"]',
        '["app.example.com"]',
        '["ftp://app.example.com"]',
        '["https://app.example.com?x=1"]',
        '["https://user@app.example.com"]',
        '["null"]',
        '[""]',
        "[1]",
        '"*"',
        "*",
        '{"a": "https://app.example.com"}',
    ],
)
def test_allowed_origins_must_be_exact_origins_without_wildcard(
    monkeypatch: pytest.MonkeyPatch, origins: str
) -> None:
    set_env(monkeypatch, local_env(CCE_ALLOWED_ORIGINS=origins))

    assert_refused(load_settings, "CCE_ALLOWED_ORIGINS")


def test_allowed_origins_accepts_exact_origins(monkeypatch: pytest.MonkeyPatch) -> None:
    origins = ["https://app.example.com", "http://localhost:3000"]
    set_env(monkeypatch, local_env(CCE_ALLOWED_ORIGINS=json.dumps(origins)))

    assert load_settings().allowed_origins == origins


# --- peppers --------------------------------------------------------------------------------


@pytest.mark.parametrize("loader", [load_settings, load_admin_settings])
@pytest.mark.parametrize(
    "peppers",
    [
        "{}",
        "[]",
        "nao-e-json",
        '{"1": 5}',
        json.dumps({"1": "__PEPPER_BASE64__"}),  # marcador do .env.example nunca substituído
        json.dumps({"1": "not base64!!"}),
        json.dumps({"1": base64.b64encode(bytes(31)).decode()}),  # 31 bytes < 32
        json.dumps({"1": ""}),
        json.dumps({"um": PEPPER_B64}),
        json.dumps({"0": PEPPER_B64}),
        json.dumps({"-1": PEPPER_B64}),
        json.dumps({"1": PEPPER_B64, "2": "curto"}),
    ],
)
def test_invalid_pepper_map_is_refused(
    monkeypatch: pytest.MonkeyPatch, loader: Loader, peppers: str
) -> None:
    set_env(monkeypatch, local_env(CCE_API_KEY_PEPPERS=peppers))

    assert_refused(loader, "CCE_API_KEY_PEPPERS")


def test_pepper_map_accepts_several_versions_and_selects_the_active_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    peppers = json.dumps({"1": PEPPER_B64, "2": PEPPER_2_B64})
    set_env(
        monkeypatch,
        local_env(CCE_API_KEY_PEPPERS=peppers, CCE_API_KEY_ACTIVE_PEPPER_VERSION="2"),
    )

    for settings in (load_settings(), load_admin_settings()):
        assert sorted(settings.api_key_peppers) == [1, 2]
        assert settings.api_key_active_pepper_version == 2


@pytest.mark.parametrize("loader", [load_settings, load_admin_settings])
@pytest.mark.parametrize("version", ["2", "0", "abc"])
def test_active_pepper_version_must_exist_in_the_map(
    monkeypatch: pytest.MonkeyPatch, loader: Loader, version: str
) -> None:
    set_env(monkeypatch, local_env(CCE_API_KEY_ACTIVE_PEPPER_VERSION=version))

    assert_refused(loader, "CCE_API_KEY_ACTIVE_PEPPER_VERSION")


# --- limites numéricos e opções por ambiente ------------------------------------------------


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("CCE_API_KEY_TTL_DAYS", "0"),
        ("CCE_API_KEY_TTL_DAYS", "366"),
        ("CCE_DB_POOL_SIZE", "0"),
        ("CCE_DB_POOL_SIZE", "21"),
        ("CCE_DB_MAX_OVERFLOW", "-1"),
        ("CCE_DB_MAX_OVERFLOW", "21"),
        ("CCE_DB_POOL_TIMEOUT", "0"),
        ("CCE_DB_POOL_TIMEOUT", "-1"),
        ("CCE_DB_COMMAND_TIMEOUT", "0"),
        ("CCE_LOG_LEVEL", "TRACE"),
        ("CCE_LOG_FORMAT", "xml"),
        ("CCE_SERVICE_NAME", "outra-coisa"),
        ("CCE_VERSION", ""),
    ],
)
def test_out_of_range_values_are_refused(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    set_env(monkeypatch, local_env(**{name: value}))

    assert_refused(load_settings, name)


@pytest.mark.parametrize(
    ("name", "value", "expected"),
    [
        ("CCE_API_KEY_TTL_DAYS", "1", 1),
        ("CCE_API_KEY_TTL_DAYS", "365", 365),
        ("CCE_DB_POOL_SIZE", "20", 20),
        ("CCE_DB_MAX_OVERFLOW", "0", 0),
    ],
)
def test_boundary_values_are_accepted(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str, expected: int
) -> None:
    set_env(monkeypatch, local_env(**{name: value}))

    settings = load_settings()

    assert getattr(settings, name.removeprefix("CCE_").lower()) == expected


def test_console_log_format_is_local_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    set_env(monkeypatch, local_env(CCE_LOG_FORMAT="console"))
    assert load_settings().log_format == "console"

    set_env(
        monkeypatch,
        local_env(
            CCE_ENV="test",
            CCE_LOG_FORMAT="console",
            CCE_DATABASE_URL=f"postgresql+asyncpg://cce_app:{APP_PASSWORD}@127.0.0.1:5432/cce_test",
        ),
    )
    assert_refused(load_settings, "CCE_LOG_FORMAT")

    set_env(monkeypatch, remote_env(tmp_path, CCE_LOG_FORMAT="console"))
    assert_refused(load_settings, "CCE_LOG_FORMAT")


def test_docs_are_enabled_by_default_only_outside_remote_environments(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    set_env(monkeypatch, local_env())
    assert load_settings().docs_enabled is True

    set_env(monkeypatch, remote_env(tmp_path))
    assert load_settings().docs_enabled is False


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_docs_cannot_be_enabled_in_remote_environments(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, environment: str
) -> None:
    set_env(monkeypatch, remote_env(tmp_path, environment, CCE_DOCS_ENABLED="true"))

    assert_refused(load_settings, "CCE_DOCS_ENABLED")


def test_unknown_environment_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    set_env(monkeypatch, local_env(CCE_ENV="dev"))

    assert_refused(load_settings, "CCE_ENV")


def test_all_problems_are_reported_together(monkeypatch: pytest.MonkeyPatch) -> None:
    set_env(monkeypatch, local_env(CCE_DATABASE_URL=None, CCE_API_KEY_TTL_DAYS="0"))

    assert_refused(load_settings, "CCE_DATABASE_URL", "CCE_API_KEY_TTL_DAYS")
