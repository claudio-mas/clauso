"""Pytest em subprocesso para os meta-testes, isolado da cobertura do processo pai.

Os meta-testes rodam testes reais num pytest filho para examinar a saída de uma falha. O filho não
pode medir cobertura nem gravar dados dela: se herdasse `PYTEST_ADDOPTS=--cov`,
`COVERAGE_PROCESS_START` ou o `COVERAGE_FILE` do pai, gravaria por cima do arquivo de cobertura da
suíte (o `.coverage` padrão ou o da CI) e o relatório final mostraria só o que o filho mediu. Todo
`-m pytest` em subprocesso passa por aqui (`tests/unit/test_child_pytest.py` confere).
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path

# Variáveis que ligam a cobertura, injetam opções ou plugins no pytest filho.
_DROPPED_PREFIXES = ("COV_CORE_", "COVERAGE_", "PYTEST_")


def pytest_command(*arguments: str) -> list[str]:
    """`python -m pytest` sem o plugin de cobertura e sem gravar cache no repositório."""
    return [
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "no:pytest_cov",
        "-p",
        "no:cacheprovider",
        *arguments,
    ]


def pytest_environment(environment: Mapping[str, str], scratch: Path) -> dict[str, str]:
    """`environment` sem nada de cobertura nem de opções do pytest do pai.

    `COVERAGE_FILE` aponta para `scratch` (o `tmp_path` do teste): mesmo que algo ligue a
    cobertura no filho, os dados nunca caem no arquivo do pai.
    """
    child = {
        name: value
        for name, value in environment.items()
        if not name.upper().startswith(_DROPPED_PREFIXES)
    }
    child["COVERAGE_FILE"] = str(scratch / ".coverage.child")
    return child
