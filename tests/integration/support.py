from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PROJECT = "cce-test"
BOOTSTRAP_SCRIPT = "/docker-entrypoint-initdb.d/01-bootstrap.sh"


def compose_file() -> Path:
    """Compose do repositório; `CCE_TEST_COMPOSE_FILE` permite apontar um arquivo adulterado."""
    return Path(os.environ.get("CCE_TEST_COMPOSE_FILE", ROOT / "docker-compose.yml"))


def compose_env_file() -> Path:
    return ROOT / os.environ.get("CCE_ENV_FILE", ".env.test")


def compose_command(*args: str) -> list[str]:
    """Comando `docker compose` do projeto de teste (`cce-test`), separado do ambiente local."""
    return [
        "docker",
        "compose",
        "--env-file",
        str(compose_env_file()),
        "-p",
        COMPOSE_PROJECT,
        "--project-directory",
        str(ROOT),
        "-f",
        str(compose_file()),
        *args,
    ]


def run_compose(
    *args: str, extra_env: Mapping[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    # Variáveis CCE_* do processo sobrescreveriam o dotenv na interpolação do Compose;
    # só entram as que o chamador pede em `extra_env`.
    environment = {k: v for k, v in os.environ.items() if not k.upper().startswith("CCE_")}
    environment.update(extra_env or {})
    return subprocess.run(
        compose_command(*args),
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
        env=environment,
    )


def reapply_bootstrap(alternative_password: str) -> subprocess.CompletedProcess[str]:
    """Reexecuta o bootstrap no container com outra senha no ambiente."""
    # `-e NOME` sem `=valor`: o Compose lê o valor do ambiente do cliente e ele não aparece no argv.
    return run_compose(
        "exec",
        "-T",
        "-e",
        "CCE_APP_DB_PASSWORD",
        "-e",
        "CCE_MIGRATOR_DB_PASSWORD",
        "postgres",
        BOOTSTRAP_SCRIPT,
        extra_env={
            "CCE_APP_DB_PASSWORD": alternative_password,
            "CCE_MIGRATOR_DB_PASSWORD": alternative_password,
        },
    )


def require(condition: bool, message: str) -> None:
    """Falha com texto fixo e sem traceback.

    Use no lugar de `assert` sempre que a expressão envolver ambiente, DSN ou senha: a introspecção
    do `assert` imprimiria os valores dos operandos na saída do pytest.
    """
    if not condition:
        pytest.fail(message, pytrace=False)


def find_leaks(haystack: str, forbidden: Mapping[str, str | SecretStr]) -> list[str]:
    """Rótulos dos valores proibidos que aparecem em `haystack` (nunca os valores)."""
    leaked = []
    for label, value in forbidden.items():
        text = value.get_secret_value() if isinstance(value, SecretStr) else value
        if text and text in haystack:
            leaked.append(label)
    return leaked


def assert_no_leaks(haystack: str, forbidden: Mapping[str, str | SecretStr], where: str) -> None:
    """Falha citando só `where` e os rótulos, nunca o valor proibido."""
    leaked = find_leaks(haystack, forbidden)
    require(not leaked, f"{where} contains: {', '.join(leaked)}")


def read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        name, separator, value = line.partition("=")
        if separator and not name.startswith("#"):
            values[name] = value.strip("'")
    return values


def sqlstate(error: BaseException) -> str | None:
    """SQLSTATE de um erro do asyncpg, direto (conexão) ou embrulhado pelo SQLAlchemy."""
    wrapped = error.orig if isinstance(error, DBAPIError) else error
    state = getattr(wrapped, "sqlstate", None) or getattr(wrapped, "pgcode", None)
    return str(state) if state else None


def with_password(url: str, password: str) -> URL:
    return make_url(url).set(password=password)
