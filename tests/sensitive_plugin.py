"""Marcador `sensitive`: uma falha em teste que manipula chave nunca mostra traceback nem saída.

Os hooks são importados por `tests/conftest.py`; os meta-testes também carregam este módulo como
plugin (`-p sensitive_plugin`) em execuções sintéticas.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "sensitive: o teste manipula chave, segredo, pepper ou digest; uma falha nunca mostra "
        "traceback, locais nem saída capturada, só um texto fixo",
    )


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    """Rede de segurança dos testes `sensitive`.

    Qualquer falha que não seja um `pytest.fail(..., pytrace=False)` (exceção inesperada de outro
    tipo, "DID NOT RAISE", `assert` introspectivo, erro em fixture) mostraria o valor nos locais
    do traceback ou na mensagem da exceção. Aqui o relatório é trocado por um texto fixo com o
    tipo da exceção, e a saída capturada é descartada.
    """
    report = yield
    if not report.failed or item.get_closest_marker("sensitive") is None:
        return report
    excinfo = call.excinfo
    if (
        excinfo is not None
        and isinstance(excinfo.value, pytest.fail.Exception)
        and not excinfo.value.pytrace
    ):
        return report  # já é uma falha de texto fixo
    kind = excinfo.typename if excinfo is not None else "unknown"
    report.longrepr = (
        f"{item.nodeid}: failed during {report.when} ({kind}). The traceback is withheld "
        "because this test handles key material."
    )
    report.sections = []
    return report
