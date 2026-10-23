from __future__ import annotations

import os
import subprocess
from pathlib import Path

from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = ROOT / "docker-compose.yml"
COMPOSE_PROJECT = "cce-test"


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
        "-f",
        str(COMPOSE_FILE),
        *args,
    ]


def run_compose(*args: str) -> subprocess.CompletedProcess[str]:
    # Variáveis CCE_* do processo sobrescreveriam o dotenv na interpolação do Compose.
    environment = {k: v for k, v in os.environ.items() if not k.upper().startswith("CCE_")}
    return subprocess.run(
        compose_command(*args),
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
        env=environment,
    )


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
