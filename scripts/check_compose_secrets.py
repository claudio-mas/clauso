"""Verifica os segredos da composição da API sem exibir nenhum valor.

Renderiza o `docker-compose.yml` com o dotenv indicado (`docker compose config --format json`, lido
em memória e nunca impresso) e confere:

- as três senhas do PostgreSQL (`postgres`, `cce_app`, `cce_migrator`) existem, não são marcadores
  do `.env.example`, têm ao menos 32 caracteres e são distintas entre si;
- o serviço `api` recebe só a credencial de runtime: o DSN conecta como `cce_app` com a senha do
  `cce_app`, e nenhuma variável nem valor do serviço carrega a URL do migrator, a senha do
  `postgres` ou a do `cce_migrator`;
- o ambiente do serviço `api` passa na validação de `Settings` da própria API (peppers em base64
  com 32 bytes ou mais, versão ativa, URLs, TTL), como o container faria ao iniciar.

Uso (os segredos ficam no dotenv; o argv só leva caminhos e o nome do projeto):

    python scripts/check_compose_secrets.py --env-file .env.ci --project-name cce-ci

Saída: `compose secrets check passed` (código 0), uma linha `compose secrets: <problema>` por
problema (código 1) ou, se a composição não puder ser renderizada, uma mensagem fixa (código 2).
As mensagens citam só serviços e nomes de variável; a saída do `docker compose` nunca é repassada.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping
from itertools import combinations
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from cce.shared.config import Settings

ROOT = Path(__file__).resolve().parent.parent
COMPOSE_FILE = ROOT / "docker-compose.yml"
MIN_PASSWORD_LENGTH = 32
PLACEHOLDER = re.compile(r"__[A-Z0-9_]+__")
# Variáveis do serviço postgres que guardam as senhas, e o rótulo de cada uma nas mensagens.
PASSWORDS = {
    "POSTGRES_PASSWORD": "the postgres admin password",
    "CCE_APP_DB_PASSWORD": "the cce_app password",
    "CCE_MIGRATOR_DB_PASSWORD": "the cce_migrator password",
}
# Nenhuma destas chega ao serviço api: são do bootstrap ou do job de migration.
FORBIDDEN_IN_API = ("CCE_MIGRATOR_DATABASE_URL", "POSTGRES_PASSWORD", "CCE_POSTGRES_ADMIN_PASSWORD")


def _environment(service: Mapping[str, Any]) -> dict[str, str]:
    raw = service.get("environment") or {}
    return {str(name): "" if value is None else str(value) for name, value in raw.items()}


def _password_problems(postgres: Mapping[str, str]) -> list[str]:
    problems = []
    for variable in PASSWORDS:
        value = postgres.get(variable, "")
        if not value:
            problems.append(f"the postgres service {variable} is empty")
        elif PLACEHOLDER.search(value):
            problems.append(f"the postgres service {variable} is a placeholder")
        elif len(value) < MIN_PASSWORD_LENGTH:
            problems.append(
                f"the postgres service {variable} is shorter than {MIN_PASSWORD_LENGTH} characters"
            )
    for first, second in combinations(PASSWORDS, 2):
        if postgres.get(first) and postgres.get(first) == postgres.get(second):
            problems.append(f"the postgres service {first} and {second} share a value")
    return problems


def _runtime_url_problems(api: Mapping[str, str], app_password: str) -> list[str]:
    try:
        url = make_url(api.get("CCE_DATABASE_URL", ""))
    except ArgumentError:
        return ["the api service CCE_DATABASE_URL cannot be parsed"]
    problems = []
    if url.username != "cce_app":
        problems.append("the api service CCE_DATABASE_URL does not connect as cce_app")
    if not url.password or url.password != app_password:
        problems.append("the api service CCE_DATABASE_URL password is not the cce_app password")
    return problems


class _ApiServiceSettings(Settings):
    """O `Settings` da API alimentado só pelo ambiente renderizado do serviço `api`.

    Sem isto, uma variável que faltasse no serviço viria do ambiente de quem roda o script.
    """

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings,)


def _settings_problems(api: Mapping[str, str]) -> list[str]:
    """Valida o ambiente do serviço api com o `Settings` da API, citando só as variáveis."""
    fields = {
        name.removeprefix("CCE_").lower(): value
        for name, value in api.items()
        if name.startswith("CCE_")
    }
    try:
        _ApiServiceSettings.model_validate(fields)
    except ValidationError as error:
        details = error.errors(include_url=False, include_context=False, include_input=False)
        names = sorted(
            {f"CCE_{item['loc'][0]}".upper() if item["loc"] else "a field" for item in details}
        )
        return [f"the api service settings are invalid: {name}" for name in names]
    return []


def problems(config: Mapping[str, Any]) -> list[str]:
    """Problemas da composição renderizada. Só frases fixas: nenhum valor entra nelas."""
    services = config.get("services") or {}
    missing = [name for name in ("postgres", "api") if name not in services]
    if missing:
        return [f"the composition has no {name} service" for name in missing]
    postgres = _environment(services["postgres"])
    api = _environment(services["api"])
    found = _password_problems(postgres)

    found += [f"the api service receives {name}" for name in FORBIDDEN_IN_API if name in api]
    api_text = json.dumps(services["api"])
    for variable in ("POSTGRES_PASSWORD", "CCE_MIGRATOR_DB_PASSWORD"):
        value = postgres.get(variable, "")
        if value and value in api_text:
            found.append(f"the api service carries {PASSWORDS[variable]}")

    found += _runtime_url_problems(api, postgres.get("CCE_APP_DB_PASSWORD", ""))
    found += _settings_problems(api)
    return found


def render(env_file: Path, project: str) -> dict[str, Any] | None:
    """`docker compose config` em memória; None se não renderizar. A saída nunca é impressa."""
    # Variáveis CCE_* do processo sobrescreveriam o dotenv na interpolação do Compose.
    environment = {k: v for k, v in os.environ.items() if not k.upper().startswith("CCE_")}
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(env_file),
            "-p",
            project,
            "--project-directory",
            str(ROOT),
            "-f",
            str(COMPOSE_FILE),
            "config",
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )
    if result.returncode != 0:
        return None
    try:
        config = json.loads(result.stdout)
    except ValueError:
        return None
    return config if isinstance(config, dict) else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check_compose_secrets.py",
        description="Check the API composition secrets without printing them.",
    )
    parser.add_argument("--env-file", required=True, type=Path, help="dotenv of the composition")
    parser.add_argument("--project-name", required=True, help="Compose project name")
    arguments = parser.parse_args(argv)

    config = render(arguments.env_file, arguments.project_name)
    if config is None:
        print(
            "compose secrets: docker compose config failed or is unreadable; its output is "
            "withheld, so re-run it by hand with --quiet to read the error",
            file=sys.stderr,
        )
        return 2
    found = problems(config)
    for problem in found:
        print(f"compose secrets: {problem}", file=sys.stderr)
    if found:
        return 1
    print("compose secrets check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
