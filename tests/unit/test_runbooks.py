"""Os runbooks não carregam segredos e o de migrations cobre o que o ticket 09 exige."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RUNBOOKS = sorted((ROOT / "docs" / "runbooks").glob("*.md"))
MIGRATIONS = ROOT / "docs" / "runbooks" / "migrations.md"

# Uma URL de banco com usuário E senha, uma senha passada por variável do libpq ou por argumento.
SECRET_PATTERNS = {
    "a database URL with a password": re.compile(
        r"postgres(?:ql)?(?:\+\w+)?://[^\s/:@]+:[^\s/@]+@"
    ),
    "PGPASSWORD": re.compile(r"PGPASSWORD\s*="),
    "a password argument": re.compile(r"--password[ =]\S"),
}


@pytest.mark.parametrize("runbook", RUNBOOKS, ids=[path.name for path in RUNBOOKS])
def test_no_runbook_carries_a_secret(runbook: Path) -> None:
    content = runbook.read_text()

    assert [label for label, pattern in SECRET_PATTERNS.items() if pattern.search(content)] == []


def test_the_migrations_runbook_exists() -> None:
    assert MIGRATIONS in RUNBOOKS


@pytest.mark.parametrize(
    "required",
    [
        "check_schema.py",  # a verificação do que o metadata não representa
        "alembic -c alembic.ini check",
        "cce_test",  # downgrade só no banco descartável
        "CCE_ENV=test",
        "expand/contract",
        "ADR-0025",  # migrations de dados por Tenant
        "set_config('app.tenant_id'",
        "SECURITY DEFINER",
        "idempotente",
        "CCE_ENV_FILE",  # o DSN vem do dotenv, nunca do argv
    ],
)
def test_the_migrations_runbook_covers(required: str) -> None:
    assert required in MIGRATIONS.read_text()


def test_the_migrations_runbook_never_suggests_bypassing_rls() -> None:
    lines = [
        line
        for line in MIGRATIONS.read_text().splitlines()
        if re.search(r"BYPASSRLS|DISABLE ROW LEVEL SECURITY|row_security\s*=\s*off", line)
    ]

    # Esses termos só aparecem como o que NÃO fazer.
    assert all(re.search(r"\b(?:nunca|não|sem|nenhum)\b", line, re.IGNORECASE) for line in lines), (
        lines
    )


BRANCH_PROTECTION = ROOT / "docs" / "runbooks" / "branch-protection.md"
LOCAL_DEVELOPMENT = ROOT / "docs" / "runbooks" / "local-development.md"


@pytest.mark.parametrize(
    "required",
    [
        "Require a pull request before merging",  # merge só por PR
        "Bypass list",  # ninguém contorna, nem administradores
        "`foundation`",  # o job obrigatório
        "Require branches to be up to date before merging",
        "Block force pushes",
        "Restrict deletions",
        "Allow merge commits",
        "ADR-0017",
    ],
)
def test_the_branch_protection_runbook_covers(required: str) -> None:
    assert required in BRANCH_PROTECTION.read_text()


@pytest.mark.parametrize(
    "required",
    ["bash scripts/ci.sh", "cce-ci", "cce_test_ci", "CCE_TEST_COMPOSE_PROJECT", "COVERAGE_FILE"],
)
def test_the_local_development_runbook_explains_the_full_verification(required: str) -> None:
    assert required in LOCAL_DEVELOPMENT.read_text()
