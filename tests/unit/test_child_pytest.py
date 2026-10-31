"""Os meta-testes que rodam pytest em subprocesso não tocam na cobertura do processo pai.

Sem o isolamento, um filho que herdasse `PYTEST_ADDOPTS=--cov` ou `COVERAGE_PROCESS_START`
gravaria por cima do arquivo de cobertura do pai (o `.coverage` padrão ou o `COVERAGE_FILE` da
CI), e o relatório final mostraria só o que o filho mediu.
"""

from __future__ import annotations

import re
import subprocess
import textwrap
from pathlib import Path

from child_pytest import pytest_command, pytest_environment
from db_support import ROOT

INNER = textwrap.dedent(
    """
    from uuid import uuid4

    from cce.shared import ids


    def test_inner() -> None:
        assert ids.encode_tenant_id(uuid4()).startswith("ten_")
    """
)


def coverage_inheriting_environment(parent_data: Path) -> dict[str, str]:
    """Um ambiente de pai que mede cobertura e que um filho ingênuo herdaria."""
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": str(parent_data.parent),
        "COVERAGE_FILE": str(parent_data),
        "COVERAGE_RCFILE": str(ROOT / "apps" / "api" / "pyproject.toml"),
        "COVERAGE_PROCESS_START": str(ROOT / "apps" / "api" / "pyproject.toml"),
        "COV_CORE_SOURCE": "cce",
        "COV_CORE_DATAFILE": str(parent_data),
        "PYTEST_ADDOPTS": "--cov=cce --cov-config=apps/api/pyproject.toml",
        "PYTEST_PLUGINS": "pytest_cov",
    }


def test_the_child_environment_drops_every_coverage_and_addopts_variable(tmp_path: Path) -> None:
    parent_data = tmp_path / "parent" / ".coverage"

    child = pytest_environment(coverage_inheriting_environment(parent_data), tmp_path)

    inherited = sorted(
        name
        for name in child
        if name.startswith(("COV_CORE_", "PYTEST_")) or name in ("COVERAGE_PROCESS_START",)
    )
    assert inherited == []
    assert child["COVERAGE_FILE"] != str(parent_data)
    assert Path(child["COVERAGE_FILE"]).parent == tmp_path
    assert child["PATH"] == "/usr/bin:/bin"


def test_the_child_command_disables_the_coverage_plugin() -> None:
    command = pytest_command("tests/unit")

    assert " ".join(command[1:3]) == "-m pytest"
    assert "no:pytest_cov" in command
    assert command[-1] == "tests/unit"


def test_a_child_pytest_never_writes_the_parent_coverage_data(tmp_path: Path) -> None:
    parent_data = tmp_path / "parent" / ".coverage"
    parent_data.parent.mkdir()
    inner = tmp_path / "inner"
    inner.mkdir()
    (inner / "test_inner.py").write_text(INNER)
    environment = coverage_inheriting_environment(parent_data)
    environment["PATH"] = str(Path(pytest_command()[0]).parent) + ":/usr/bin:/bin"

    result = subprocess.run(
        pytest_command(str(inner), "-q", "--rootdir", str(inner)),
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
        env=pytest_environment(environment, tmp_path),
    )

    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    assert not parent_data.exists()
    assert not (ROOT / ".coverage.child").exists()


def test_every_child_pytest_goes_through_the_isolated_helper() -> None:
    """Um `subprocess` com `-m pytest` escrito à mão voltaria a herdar a cobertura do pai."""
    raw_child = re.compile(r"""["']-m["'],\s*["']pytest["']""")
    offenders = [
        str(path.relative_to(ROOT))
        for path in sorted((ROOT / "tests").rglob("*.py"))
        if path.name != "child_pytest.py" and raw_child.search(path.read_text())
    ]

    assert offenders == []
