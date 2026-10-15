"""Gera um dotenv local a partir de `.env.example`, com segredos novos.

Troca somente os marcadores explícitos (`__APP_PASSWORD__` etc.) e os três valores que dependem
dos argumentos: `CCE_ENV`, `CCE_POSTGRES_DB` e o banco no final dos DSNs. Aceita apenas os
ambientes `local` e `test`, recusa sobrescrever um arquivo existente e nunca imprime segredos.

    python scripts/init_env.py --target .env --env local --database-name cce
    python scripts/init_env.py --target .env.test --env test --database-name cce_test
"""

from __future__ import annotations

import argparse
import base64
import os
import re
import secrets
import sys
from collections.abc import Callable
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent.parent / ".env.example"
TEMPLATE_DATABASE = "cce"
DATABASE_URL_NAMES = ("CCE_DATABASE_URL", "CCE_MIGRATOR_DATABASE_URL")
ENVIRONMENTS = ("local", "test")
TEST_DATABASE_PREFIX = "cce_test"
DATABASE_NAME = re.compile(r"[a-z][a-z0-9_]{0,62}")
LEFTOVER_MARKER = re.compile(r"__[A-Z0-9_]+__")


def _password() -> str:
    return secrets.token_hex(32)


def _pepper() -> str:
    return base64.b64encode(secrets.token_bytes(32)).decode("ascii")


SECRET_MARKERS: dict[str, Callable[[], str]] = {
    "__POSTGRES_PASSWORD__": _password,
    "__APP_PASSWORD__": _password,
    "__MIGRATOR_PASSWORD__": _password,
    "__PEPPER_BASE64__": _pepper,
}


class Refusal(Exception):
    pass


def render(template: str, environment: str, database: str) -> str:
    secret_values = {marker: generate() for marker, generate in SECRET_MARKERS.items()}
    lines = []
    for line in template.splitlines():
        name = line.partition("=")[0]
        for marker, value in secret_values.items():
            line = line.replace(marker, value)
        if name == "CCE_ENV":
            line = f"CCE_ENV={environment}"
        elif name == "CCE_POSTGRES_DB":
            line = f"CCE_POSTGRES_DB={database}"
        elif name in DATABASE_URL_NAMES:
            if not line.endswith(f"/{TEMPLATE_DATABASE}"):
                raise Refusal(f"{name} in the template does not end with /{TEMPLATE_DATABASE}")
            line = line.removesuffix(TEMPLATE_DATABASE) + database
        lines.append(line)
    rendered = "\n".join(lines) + "\n"
    if LEFTOVER_MARKER.search(rendered):
        raise Refusal("the template has a marker this script does not know how to replace")
    return rendered


def write_new_file(target: Path, content: str) -> None:
    if not target.parent.is_dir():
        raise Refusal("the target directory does not exist")
    # O_EXCL recusa arquivo existente (e symlink) sem corrida entre verificar e criar.
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(target, flags, 0o600)
    except FileExistsError:
        raise Refusal("the target already exists; refusing to overwrite it") from None
    except OSError as error:
        raise Refusal(f"cannot create the target: {error.strerror}") from None
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def parse_arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="init_env.py", description="Generate a local dotenv file with fresh secrets."
    )
    parser.add_argument("--target", required=True, type=Path, help="file to create")
    parser.add_argument("--env", required=True, choices=ENVIRONMENTS, help="local or test only")
    parser.add_argument("--database-name", required=True, help="PostgreSQL database name")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_arguments(argv)
    environment: str = arguments.env
    database: str = arguments.database_name
    target: Path = arguments.target
    try:
        if not DATABASE_NAME.fullmatch(database):
            raise Refusal("the database name must match [a-z][a-z0-9_]{0,62}")
        if environment == "test" and not database.startswith(TEST_DATABASE_PREFIX):
            raise Refusal(f"the test database name must start with {TEST_DATABASE_PREFIX}")
        content = render(TEMPLATE.read_text(encoding="utf-8"), environment, database)
        write_new_file(target, content)
    except Refusal as refusal:
        print(f"init_env: {refusal}", file=sys.stderr)
        return 1
    print(f"init_env: created {target} for {environment} (database {database})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
