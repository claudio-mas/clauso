"""Redação de credenciais na saída de falhas: um erro de conexão nunca imprime a senha do DSN.

Quando uma conexão com o banco falha (senha errada, banco indisponível), o pytest imprime os
argumentos dos frames do driver, e o asyncpg os recebe com a senha em texto. Isso acontece mesmo
com `--tb` padrão. Este plugin lê as credenciais da configuração de teste (variáveis `CCE_*` do
processo e o dotenv de `CCE_ENV_FILE`) e, em toda falha, troca cada valor encontrado no relatório
e na saída capturada (de testes que falham ou passam) por `***`. Relatórios sem credencial ficam
intactos.

Complementa o marcador `sensitive`, que esconde o traceback inteiro de quem manipula chaves; aqui
só os valores somem, e o diagnóstico continua legível. Os hooks são registrados por
`tests/conftest.py`, e os meta-testes o carregam com `-p redaction_plugin`.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Generator, Iterable
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

import pytest

MASK = "***"
_SECRET_NAMES = ("PASSWORD", "PEPPER")

_secrets: tuple[str, ...] = ()


def _dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return values
    for line in lines:
        name, separator, value = line.partition("=")
        if separator and not name.startswith("#"):
            values[name.strip()] = value.strip().strip("'\"")
    return values


def _from_url(value: str) -> list[str]:
    try:
        password = urlsplit(value).password or ""
    except ValueError:
        return []
    decoded = unquote(password)
    return [password, decoded, quote(decoded, safe="")] if password else []


def _from_json(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except ValueError:
        return []
    return [str(item) for item in parsed.values()] if isinstance(parsed, dict) else []


def _inner(text: str) -> str:
    """Conteúdo entre as aspas do `repr`, que o pytest mostra em locais e argumentos."""
    return repr(text)[1:-1]


def _renderings(text: str) -> set[str]:
    """Formas em que o pytest e os drivers podem exibir `text`, além da literal.

    Barra invertida, aspas e caracteres de controle aparecem escapados (`\\`, `\\'`, `\\x07`,
    `\\n`); o JSON e a URL têm escapes próprios. A saída de um repr entra de novo em outro repr
    (um objeto dentro de um dict dentro de uma mensagem), então as formas são aplicadas duas vezes.
    """
    transforms: tuple[Callable[[str], str], ...] = (
        _inner,
        lambda t: t.encode("unicode_escape").decode("ascii"),
        lambda t: _inner(t).replace("\\'", "'"),  # repr entre aspas duplas deixa a simples crua
        lambda t: _inner(t).replace('"', '\\"'),
        lambda t: json.dumps(t)[1:-1],
        lambda t: quote(t, safe=""),
    )
    forms = {text}
    for _ in range(2):
        forms |= {transform(form) for form in forms for transform in transforms}
    return forms


def collect_secrets(environment: dict[str, str], dotenv: Path | None) -> tuple[str, ...]:
    """Valores (e suas formas escapadas) que nenhuma saída de teste pode conter."""
    values = dict(_dotenv(dotenv)) if dotenv is not None else {}
    values.update(environment)
    found: set[str] = set()
    for name, value in values.items():
        upper = name.upper()
        if not upper.startswith("CCE_") or not value:
            continue
        # `CCE_API_KEY_ACTIVE_PEPPER_VERSION` é um número, não um segredo: mascarar o valor dela
        # trocaria todo "1" do relatório (inclusive em "401") por `***`.
        if any(part in upper for part in _SECRET_NAMES) and not upper.endswith("_VERSION"):
            found.add(value)
            found.update(_from_json(value))
        if upper.endswith("_URL") and "://" in value:
            found.update(_from_url(value))
    # Sem tamanho mínimo: o validador de DSN não impede senhas curtas, e mascarar texto comum a
    # mais é melhor que deixar uma credencial. O mais longo primeiro evita restos de uma troca.
    expanded = {form for secret in found for form in _renderings(secret) if form}
    return tuple(sorted(expanded, key=len, reverse=True))


def scrub(text: str, secrets: Iterable[str]) -> str:
    for secret in secrets:
        text = text.replace(secret, MASK)
    return text


def pytest_configure(config: pytest.Config) -> None:
    global _secrets
    env_file = os.environ.get("CCE_ENV_FILE")
    dotenv = Path(config.rootpath, env_file) if env_file else None
    _secrets = collect_secrets(dict(os.environ), dotenv)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    report = yield
    if not _secrets:
        return report
    # A saída capturada também aparece para testes que passam (`-rA`, `-rP`).
    report.sections = [(name, scrub(content, _secrets)) for name, content in report.sections]
    if report.failed and report.longrepr is not None:
        text = str(report.longrepr)
        scrubbed = scrub(text, _secrets)
        if scrubbed != text:
            report.longrepr = scrubbed
    return report
