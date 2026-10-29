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
    """Rede de segurança dos testes `sensitive`: o relatório nunca carrega o valor.

    Para QUALQUER relatório de item marcado, em qualquer fase (setup, call, teardown), a saída
    capturada (stdout, stderr e log) é descartada ANTES de qualquer retorno. Ela também apareceria
    em testes que passam, com `-rA`/`-rP`, e acumula as fases anteriores do mesmo item.

    Numa falha, o `longrepr` original nunca é reaproveitado, inclusive quando a exceção é um
    `pytest.fail(..., pytrace=False)`: `pytrace=False` só suprime o traceback, mas o pytest ainda
    imprime a cadeia de exceções (`__cause__`/`__context__`), e o `RuntimeError` anterior pode
    trazer a chave na mensagem. O relatório é reconstruído do zero:

    * `pytest.fail(..., pytrace=False)`: só a mensagem passada a `pytest.fail` (`Failed.msg`).
      Por contrato dos helpers (`require`, `assert_no_leaks`), ela é texto fixo e não leva valor.
      Nenhum traceback, locais ou cadeia são copiados.
    * qualquer outra falha (exceção de outro tipo, "DID NOT RAISE", `assert` introspectivo, erro
      em fixture): um texto genérico com a fase e o NOME do tipo da exceção, sem mensagem.

    Só `longrepr` e `sections` mudam: `outcome`, `when` e o resto do relatório ficam como estão,
    então o hook nunca torna verde um teste que falhou.
    """
    report = yield
    if item.get_closest_marker("sensitive") is None:
        return report
    report.sections = []
    if not report.failed:
        return report
    excinfo = call.excinfo
    if (
        excinfo is not None
        and isinstance(excinfo.value, pytest.fail.Exception)
        and not excinfo.value.pytrace
    ):
        report.longrepr = f"{excinfo.value.msg}"
        return report
    kind = excinfo.typename if excinfo is not None else "unknown"
    report.longrepr = (
        f"{item.nodeid}: failed during {report.when} ({kind}). The traceback is withheld "
        "because this test handles key material."
    )
    return report
