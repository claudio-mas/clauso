"""Os testes de Compose miram só um projeto descartável: `cce-test` local ou `cce-ci` na CI.

`scripts/ci.sh` sobe a infraestrutura no projeto `cce-ci` e exporta `CCE_TEST_COMPOSE_PROJECT`; sem
a variável, vale o `cce-test` do runbook. Qualquer outro nome (o `cce` do ambiente local, por
exemplo) é recusado antes de o `docker compose` rodar, para que um teste mutante nunca alcance
containers e volumes que não são descartáveis.
"""

from __future__ import annotations

import pytest
from integration import support


def test_the_default_project_is_cce_test(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CCE_TEST_COMPOSE_PROJECT", raising=False)

    assert support.compose_project() == "cce-test"


def test_the_ci_project_is_accepted_and_used_by_every_compose_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CCE_TEST_COMPOSE_PROJECT", "cce-ci")

    command = support.compose_command("config")

    assert support.compose_project() == "cce-ci"
    assert command[command.index("-p") + 1] == "cce-ci"


@pytest.mark.parametrize("project", ["cce", "", "cce-local", "cce-ci-other", "production"])
def test_any_other_project_is_refused_before_compose_runs(
    monkeypatch: pytest.MonkeyPatch, project: str
) -> None:
    monkeypatch.setenv("CCE_TEST_COMPOSE_PROJECT", project)

    with pytest.raises(pytest.fail.Exception, match="CCE_TEST_COMPOSE_PROJECT"):
        support.compose_command("config")
