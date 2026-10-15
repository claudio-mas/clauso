"""Configuração por ambiente (local, test, staging, production).

Três conjuntos de settings, um por processo: a API (`Settings`), as migrations
(`MigrationSettings`) e a CLI administrativa (`AdminSettings`). A API nunca lê o DSN do migrator.

Carregar settings só valida valores: nada aqui abre conexão com banco ou Redis.
Segredos são `SecretStr`, e os erros citam o nome da variável sem nunca repetir o valor recebido.
"""

import base64
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Literal, Self
from urllib.parse import SplitResult, parse_qs, unquote, urlsplit

from pydantic import (
    AfterValidator,
    BeforeValidator,
    Field,
    SecretStr,
    ValidationError,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from pydantic_settings.exceptions import SettingsError

Environment = Literal["local", "test", "staging", "production"]

_ENV_PREFIX = "CCE_"
_REMOTE_ENVIRONMENTS = ("staging", "production")
_DEFAULT_ENV_FILE = ".env"
_TEST_DATABASE_PREFIX = "cce_test"
_MIN_PEPPER_BYTES = 32


class ConfigurationError(Exception):
    """Configuração recusada. A mensagem cita as variáveis, nunca os valores recebidos."""


def _is_remote(environment: str) -> bool:
    return environment in _REMOTE_ENVIRONMENTS


def _split_url(raw: str) -> SplitResult | None:
    try:
        return urlsplit(raw)
    except ValueError:
        return None


def _valid_port(parts: SplitResult) -> bool:
    try:
        parts.port  # noqa: B018 - a leitura levanta ValueError se a porta for inválida
    except ValueError:
        return False
    return True


def _database_name(url: SecretStr) -> str:
    parts = _split_url(url.get_secret_value())
    return parts.path.removeprefix("/") if parts else ""


def _overrides_tls(url: SecretStr) -> bool:
    parts = _split_url(url.get_secret_value())
    return parts is not None and any(
        key.lower().startswith("ssl") for key in parse_qs(parts.query, keep_blank_values=True)
    )


def _database_url(role: str) -> Callable[[SecretStr], SecretStr]:
    def validate(value: SecretStr) -> SecretStr:
        parts = _split_url(value.get_secret_value())
        if parts is None or parts.scheme != "postgresql+asyncpg":
            raise ValueError("must be a postgresql+asyncpg URL")
        if not parts.hostname or not _valid_port(parts):
            raise ValueError("must include a host and a valid port")
        if unquote(parts.username or "") != role:
            raise ValueError(f"must connect as the {role} role")
        database = parts.path.removeprefix("/")
        if not database or "/" in database:
            raise ValueError("must name a database")
        return value

    return validate


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            raise ValueError("must be valid JSON") from None
    return value


def _exact_origins(values: list[str]) -> list[str]:
    for origin in values:
        if "*" in origin:
            raise ValueError("wildcard origins are not allowed")
        parts = _split_url(origin)
        if (
            parts is None
            or parts.scheme not in ("http", "https")
            or not parts.hostname
            or "@" in parts.netloc
            or not _valid_port(parts)
            or origin != f"{parts.scheme}://{parts.netloc}"
        ):
            raise ValueError("must list exact origins such as https://app.example.com")
    return values


def _peppers(values: dict[int, SecretStr]) -> dict[int, SecretStr]:
    if not values:
        raise ValueError("must define at least one pepper version")
    for version, pepper in values.items():
        if version < 1:
            raise ValueError("pepper versions must be positive integers")
        try:
            raw = base64.b64decode(pepper.get_secret_value(), validate=True)
        except ValueError:  # inclui binascii.Error
            raise ValueError("every pepper must be base64") from None
        if len(raw) < _MIN_PEPPER_BYTES:
            raise ValueError(f"every pepper must decode to at least {_MIN_PEPPER_BYTES} bytes")
    return values


AppDatabaseUrl = Annotated[SecretStr, AfterValidator(_database_url("cce_app"))]
MigratorDatabaseUrl = Annotated[SecretStr, AfterValidator(_database_url("cce_migrator"))]
Origins = Annotated[
    list[str], NoDecode, BeforeValidator(_json_value), AfterValidator(_exact_origins)
]
Peppers = Annotated[
    dict[int, SecretStr], NoDecode, BeforeValidator(_json_value), AfterValidator(_peppers)
]


def _readable_file(path: Path) -> bool:
    return path.is_file() and os.access(path, os.R_OK)


def _raise_if_any(problems: list[str]) -> None:
    if problems:
        raise ValueError("; ".join(problems))


class _EnvironmentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix=_ENV_PREFIX, case_sensitive=False, extra="ignore", frozen=True
    )

    env: Environment = "local"
    database_ca_file: Path | None = None

    @model_validator(mode="after")
    def check_database_tls(self) -> Self:
        problems = []
        if self.database_ca_file is None:
            if _is_remote(self.env):
                problems.append(
                    f"{_ENV_PREFIX}DATABASE_CA_FILE: required in staging and production"
                )
        elif not _readable_file(self.database_ca_file):
            problems.append(f"{_ENV_PREFIX}DATABASE_CA_FILE: must be a readable file")
        _raise_if_any(problems)
        return self

    def _check_database_url(self, variable: str, url: SecretStr) -> list[str]:
        problems = []
        if self.env == "test" and not _database_name(url).startswith(_TEST_DATABASE_PREFIX):
            problems.append(
                f"{_ENV_PREFIX}{variable}: must name a database starting with "
                f"{_TEST_DATABASE_PREFIX} in test"
            )
        if _is_remote(self.env) and _overrides_tls(url):
            problems.append(f"{_ENV_PREFIX}{variable}: must not override TLS settings")
        return problems


class _ApiKeySettings(_EnvironmentSettings):
    api_key_peppers: Peppers
    api_key_active_pepper_version: int = Field(1, ge=1)
    api_key_ttl_days: int = Field(90, ge=1, le=365)

    @model_validator(mode="after")
    def check_active_pepper(self) -> Self:
        if self.api_key_active_pepper_version not in self.api_key_peppers:
            raise ValueError(
                f"{_ENV_PREFIX}API_KEY_ACTIVE_PEPPER_VERSION: must be a version in "
                f"{_ENV_PREFIX}API_KEY_PEPPERS"
            )
        return self


class Settings(_ApiKeySettings):
    """Settings da API: credenciais de runtime (`cce_app`), Redis e peppers de API key."""

    service_name: Literal["cce-api"] = "cce-api"
    version: str = Field("0.1.0", min_length=1, max_length=64)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "json"
    database_url: AppDatabaseUrl
    redis_url: SecretStr
    redis_ca_file: Path | None = None
    db_pool_size: int = Field(5, ge=1, le=20)
    db_max_overflow: int = Field(5, ge=0, le=20)
    db_pool_timeout: float = Field(5, gt=0)
    db_command_timeout: float = Field(5, gt=0)
    allowed_origins: Origins = []
    docs_enabled: bool = True

    @model_validator(mode="before")
    @classmethod
    def default_docs_enabled(cls, data: Any) -> Any:
        if isinstance(data, dict) and "docs_enabled" not in data:
            return {**data, "docs_enabled": data.get("env", "local") in ("local", "test")}
        return data

    @model_validator(mode="after")
    def check_runtime(self) -> Self:
        problems = self._check_database_url("DATABASE_URL", self.database_url)
        problems += self._check_redis_url()
        if self.redis_ca_file is not None and not _readable_file(self.redis_ca_file):
            problems.append(f"{_ENV_PREFIX}REDIS_CA_FILE: must be a readable file")
        if self.log_format == "console" and self.env != "local":
            problems.append(f"{_ENV_PREFIX}LOG_FORMAT: console is allowed only in local")
        if self.docs_enabled and _is_remote(self.env):
            problems.append(f"{_ENV_PREFIX}DOCS_ENABLED: must be false in staging and production")
        _raise_if_any(problems)
        return self

    def _check_redis_url(self) -> list[str]:
        expected = "rediss" if _is_remote(self.env) else "redis"
        parts = _split_url(self.redis_url.get_secret_value())
        if (
            parts is None
            or parts.scheme != expected
            or not parts.hostname
            or not _valid_port(parts)
        ):
            return [
                f"{_ENV_PREFIX}REDIS_URL: must be a {expected}:// URL with a host in {self.env}"
            ]
        if _is_remote(self.env) and _overrides_tls(self.redis_url):
            return [f"{_ENV_PREFIX}REDIS_URL: must not override TLS settings"]
        return []


class MigrationSettings(_EnvironmentSettings):
    """Settings das migrations: só o DSN do migrator e o TLS do banco."""

    migrator_database_url: MigratorDatabaseUrl

    @model_validator(mode="after")
    def check_migrator(self) -> Self:
        _raise_if_any(self._check_database_url("MIGRATOR_DATABASE_URL", self.migrator_database_url))
        return self


class AdminSettings(_ApiKeySettings):
    """Settings da CLI administrativa: DSN do migrator e material de emissão de chaves."""

    migrator_database_url: MigratorDatabaseUrl

    @model_validator(mode="after")
    def check_migrator(self) -> Self:
        _raise_if_any(self._check_database_url("MIGRATOR_DATABASE_URL", self.migrator_database_url))
        return self


class _EnvironmentProbe(BaseSettings):
    """Lê só o ambiente e o arquivo dotenv escolhido, para decidir como carregar o resto."""

    model_config = SettingsConfigDict(env_prefix=_ENV_PREFIX, case_sensitive=False, extra="ignore")

    env: str | None = None
    env_file: str | None = None


def _resolve_env_file() -> str | None:
    """Devolve o dotenv a carregar, ou None. Recusa dotenv em staging e production."""
    process = _EnvironmentProbe(_env_file=None)
    if process.env is not None and _is_remote(process.env):
        if process.env_file:
            raise ConfigurationError(
                f"{_ENV_PREFIX}ENV_FILE: dotenv files are not allowed in staging and production"
            )
        return None
    env_file = process.env_file or _DEFAULT_ENV_FILE
    if process.env is None:
        with_dotenv = _EnvironmentProbe(_env_file=env_file)
        if with_dotenv.env is not None and _is_remote(with_dotenv.env):
            raise ConfigurationError(
                f"{_ENV_PREFIX}ENV: staging and production must be set by the process "
                "environment, not by a dotenv file"
            )
    return env_file


def _describe(error: ValidationError) -> str:
    problems: list[str] = []
    for item in error.errors(include_url=False, include_context=False, include_input=False):
        message = item["msg"].removeprefix("Value error, ")
        location = item["loc"]
        if location and isinstance(location[0], str):
            message = f"{_ENV_PREFIX}{location[0].upper()}: {message}"
        if message not in problems:
            problems.append(message)
    return "invalid configuration: " + "; ".join(problems)


def _load[S: _EnvironmentSettings](settings_class: type[S]) -> S:
    env_file = _resolve_env_file()
    try:
        return settings_class(_env_file=env_file)
    except ValidationError as error:
        # `from None`: o ValidationError original carrega os valores recebidos.
        raise ConfigurationError(_describe(error)) from None
    except SettingsError:
        raise ConfigurationError(
            "invalid configuration: a configuration source could not be read"
        ) from None


def load_settings() -> Settings:
    return _load(Settings)


def load_migration_settings() -> MigrationSettings:
    return _load(MigrationSettings)


def load_admin_settings() -> AdminSettings:
    return _load(AdminSettings)
