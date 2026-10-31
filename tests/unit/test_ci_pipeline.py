"""O pipeline obrigatório (`scripts/ci.sh` e `.github/workflows/ci.yml`) tem todos os controles.

Estes testes leem os arquivos; a execução real do script está no relatório do ticket 10 e roda em
toda Pull Request. Eles protegem o que uma edição descuidada removeria em silêncio: uma etapa, o
`set -euo pipefail`, uma falha engolida, o pin por SHA ou as permissões mínimas.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]  # PyYAML vem do uvicorn[standard], sem stubs

ROOT = Path(__file__).resolve().parents[2]
CI_SCRIPT = ROOT / "scripts" / "ci.sh"
WORKFLOWS = ROOT / ".github" / "workflows"
CI_WORKFLOW = WORKFLOWS / "ci.yml"

# Cada controle obrigatório, na ordem em que o script os executa.
MANDATORY_STEPS = [
    ("uv pinned version", r'UV_VERSION="0\.12\.10"'),
    ("disposable dotenv", r"scripts/init_env\.py --target \"\$env_file\" --env test"),
    ("python 3.12", r"uv python install 3\.12"),
    ("lock check", r"uv lock --project apps/api --check"),
    ("locked sync", r"uv sync --project apps/api --locked --group dev"),
    ("ruff check", r"ruff check --config apps/api/pyproject\.toml apps/api/src tests scripts"),
    ("ruff format", r"ruff format --check --config apps/api/pyproject\.toml"),
    ("mypy", r"mypy --config-file apps/api/pyproject\.toml apps/api/src tests scripts"),
    ("compose config", r"compose config --quiet"),
    ("compose secrets", r"scripts/check_compose_secrets\.py"),
    ("infrastructure", r"compose up -d --wait postgres redis"),
    ("migrations to head", r"alembic -c alembic\.ini upgrade head"),
    ("migration tests", r"pytest tests/migrations"),
    (
        "suite with coverage",
        r"pytest tests/unit tests/integration tests/contracts --cov=cce "
        r"--cov-config=apps/api/pyproject\.toml",
    ),
    ("alembic check", r"alembic -c alembic\.ini check"),
    ("schema check", r"python scripts/check_schema\.py"),
    ("dependency audit", r"pip-audit"),
    ("image build", r"compose build api"),
    ("separate migration job", r"compose run --rm --no-deps migrate"),
    ("api through compose", r"compose up -d --wait --no-build --no-deps api"),
    ("read-only key", r"cce-admin api-key issue --tenant \"\$tenant_a\" .*--scope tenant:read\)\""),
    ("smoke", r"python3 scripts/smoke\.py\n"),
    ("revocation", r"cce-admin api-key revoke --tenant \"\$tenant_a\""),
    ("smoke after the revocation", r"python3 scripts/smoke\.py --revoked"),
]


def script() -> str:
    return CI_SCRIPT.read_text()


def commands() -> list[str]:
    """Comandos do script, um por linha (continuações com `\\` unidas) e sem comentários, para
    que um comentário não conte como etapa.
    """
    joined = re.sub(r"\s*\\\n\s*", " ", script())
    return [line for line in joined.splitlines() if not line.lstrip().startswith("#")]


def workflow() -> dict[Any, Any]:
    loaded: dict[Any, Any] = yaml.safe_load(CI_WORKFLOW.read_text())
    return loaded


def test_the_script_is_valid_bash() -> None:
    result = subprocess.run(["bash", "-n", str(CI_SCRIPT)], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr


def test_the_script_stops_on_the_first_failure() -> None:
    first_command = next(line for line in commands() if line.strip())

    assert script().startswith("#!/usr/bin/env bash\n")
    assert first_command == "set -euo pipefail"


@pytest.mark.parametrize(
    "pattern",
    [r"set \+[a-z]*e", r"\|\|\s*true\b", r"\|\|\s*:", r"set -[a-z]*x", r"\bprintenv\b", r"^env$"],
)
def test_the_script_never_swallows_a_failure_nor_traces_its_commands(pattern: str) -> None:
    offending = [line for line in commands() if re.search(pattern, line.strip())]

    assert offending == []


def test_every_mandatory_control_runs_in_order() -> None:
    text = "\n".join(commands())
    positions = []
    for name, pattern in MANDATORY_STEPS:
        found = re.search(pattern, text)
        assert found is not None, f"missing step: {name}"
        positions.append((found.start(), name))

    assert [name for _, name in sorted(positions)] == [name for name, _ in MANDATORY_STEPS]


def test_the_script_runs_in_the_disposable_cce_ci_project() -> None:
    text = "\n".join(commands())

    assert re.search(r'^readonly PROJECT="cce-ci"$', text, re.MULTILINE)
    assert re.search(r'^readonly DATABASE="cce_test_ci"$', text, re.MULTILINE)
    assert 'CCE_TEST_COMPOSE_PROJECT="$PROJECT"' in text
    # Toda chamada ao Compose passa pela função que fixa --env-file e -p do projeto descartável.
    raw_compose = [line for line in commands() if "docker compose" in line]
    assert len(raw_compose) == 1 and '-p "$PROJECT"' in raw_compose[0]


def test_the_teardown_is_installed_before_anything_starts_and_only_removes_cce_ci() -> None:
    lines = commands()
    trap = next(index for index, line in enumerate(lines) if line.startswith("trap teardown EXIT"))
    first_compose = next(
        index for index, line in enumerate(lines) if re.match(r"\s*compose (up|config)", line)
    )
    removals = [line for line in lines if re.search(r"\bdown\b|volume rm|\brm -[a-z]*v", line)]

    assert trap < first_compose
    assert len(removals) == 1 and re.search(r"\bcompose down --volumes", removals[0])


def test_coverage_data_is_isolated_from_the_repository() -> None:
    text = "\n".join(commands())

    assert re.search(r'export COVERAGE_FILE="\$work_dir/\.coverage"', text)


def test_the_script_never_publishes_an_image() -> None:
    text = "\n".join(commands())

    assert not re.search(r"docker (push|login)|compose push|ghcr\.io", text)


def test_the_workflow_runs_on_pull_requests_and_on_pushes_to_main() -> None:
    triggers = workflow()[True]  # o YAML 1.1 lê a chave `on` como booleano

    assert "pull_request" in triggers
    assert triggers["push"] == {"branches": ["main"]}
    assert set(triggers) <= {"pull_request", "push", "workflow_dispatch"}


def test_the_workflow_only_reads_the_repository() -> None:
    loaded = workflow()

    assert loaded["permissions"] == {"contents": "read"}
    assert all("permissions" not in job for job in loaded["jobs"].values())


def test_the_foundation_job_runs_the_ci_script() -> None:
    jobs = workflow()["jobs"]
    foundation = jobs["foundation"]
    runs = [step.get("run", "") for step in foundation["steps"]]

    assert list(jobs) == ["foundation"]
    assert foundation["runs-on"] == "ubuntu-24.04"
    assert any(run.strip() == "bash scripts/ci.sh" for run in runs)
    assert any("uv==0.12.10" in run for run in runs)


def test_every_action_is_pinned_by_full_sha_with_its_version_in_a_comment() -> None:
    uses = [line.strip() for line in CI_WORKFLOW.read_text().splitlines() if "uses:" in line]

    assert uses
    for line in uses:
        assert re.fullmatch(r"- uses: [\w.-]+/[\w.-]+@[0-9a-f]{40} # v\d+\.\d+\.\d+", line), line


def test_the_checkout_does_not_persist_credentials() -> None:
    steps = workflow()["jobs"]["foundation"]["steps"]
    checkouts = [step for step in steps if step.get("uses", "").startswith("actions/checkout@")]

    assert len(checkouts) == 1
    assert checkouts[0]["with"]["persist-credentials"] is False


def test_the_workflow_uses_no_secret_and_interpolates_no_event_data_into_shell() -> None:
    text = CI_WORKFLOW.read_text()

    assert "secrets." not in text
    assert "${{" not in text


def test_there_is_no_release_workflow_nor_image_publication() -> None:
    names = sorted(path.name for path in WORKFLOWS.iterdir())
    text = "\n".join(path.read_text() for path in WORKFLOWS.iterdir())

    assert names == ["ci.yml"]
    assert not re.search(r"packages:\s*write|docker (push|login)|ghcr\.io", text)


ADR = ROOT / "docs" / "adr" / "0017-delivery-environments.md"


@pytest.mark.parametrize(
    "required",
    [
        "**Status:** Proposto",
        "`local`, `test`, `staging` e `production`",  # os quatro ambientes
        "### Migrations em job separado",
        "### CI obrigatória antes do merge",
        "`foundation`",
        "### Promoção por digest: adiada para o ticket 12",
        "não existe `release.yml`",
    ],
)
def test_adr_0017_records_the_delivery_decisions(required: str) -> None:
    assert required in ADR.read_text()


def run_teardown(tmp_path: Path, docker_exit: int, pipeline_exit: int) -> tuple[int, str, str]:
    """Carrega as funções do `ci.sh` (sem rodar o pipeline) e sai por `teardown` com um status.

    O `docker` do PATH é um falso que registra os argumentos e devolve `docker_exit`.
    """
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    calls = tmp_path / "docker-calls"
    docker = fake_bin / "docker"
    docker.write_text(f'#!/bin/sh\necho "$@" >> "{calls}"\nexit {docker_exit}\n')
    docker.chmod(0o755)
    work_dir = tmp_path / "work"
    harness = (
        f'source "{CI_SCRIPT}"\n'
        f'work_dir="{work_dir}"\n'
        'env_file="$work_dir/.env.ci"\n'
        'mkdir -p "$work_dir" && touch "$env_file"\n'
        "trap teardown EXIT\n"
        f"exit {pipeline_exit}\n"
    )
    result = subprocess.run(
        ["bash", "-c", harness],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": f"{fake_bin}:/usr/bin:/bin", "HOME": str(tmp_path)},
    )
    assert not work_dir.exists(), "the teardown did not remove the work directory"
    return result.returncode, result.stderr, calls.read_text() if calls.exists() else ""


@pytest.mark.parametrize(
    ("docker_exit", "pipeline_exit", "expected"),
    [
        (0, 0, 0),  # tudo verde
        (1, 0, 1),  # o pipeline passou, mas o teardown falhou: a execução não pode ficar verde
        (0, 3, 3),  # o pipeline falhou e o teardown funcionou: o status original fica
        (1, 3, 3),  # os dois falharam: o status do pipeline prevalece
    ],
)
def test_the_teardown_never_turns_a_failure_into_success(
    tmp_path: Path, docker_exit: int, pipeline_exit: int, expected: int
) -> None:
    code, stderr, calls = run_teardown(tmp_path, docker_exit, pipeline_exit)

    assert code == expected
    assert re.search(r"--env-file \S+/\.env\.ci -p cce-ci .* down --volumes", calls)
    assert ("teardown of the cce-ci project failed" in stderr) == (docker_exit != 0)
