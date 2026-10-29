"""`set_tenant_context` e a regra de que só `tenant_session` confirma ou desfaz transações.

Sem banco: o contexto recusa o que não é UUID e o que está fora de uma transação; e nenhum módulo
do pacote (nem os repositories futuros) chama `commit` ou `rollback` por conta própria.
"""

from __future__ import annotations

import ast
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from cce.shared.db.rls import TenantContextError, set_tenant_context

SOURCE = Path(__file__).resolve().parents[2] / "apps" / "api" / "src" / "cce"


@pytest.fixture
def session() -> AsyncSession:
    # O engine nunca conecta: a recusa acontece antes de qualquer acesso ao banco.
    return AsyncSession(create_async_engine("postgresql+asyncpg://nobody@invalid.invalid/none"))


async def test_the_context_requires_an_open_transaction(session: AsyncSession) -> None:
    with pytest.raises(TenantContextError, match="transaction"):
        await set_tenant_context(session, uuid4())


@pytest.mark.parametrize(
    "value",
    [None, "", "not-a-uuid", str(uuid4()), uuid4().hex, 0, b"x" * 16],
    ids=["none", "empty", "malformed", "uuid-as-text", "hex-as-text", "int", "bytes"],
)
async def test_the_context_accepts_only_a_uuid(session: AsyncSession, value: object) -> None:
    with pytest.raises(TypeError, match="UUID"):
        await set_tenant_context(session, value)  # type: ignore[arg-type]


def test_only_the_transactional_session_commits_or_rolls_back() -> None:
    calls: dict[str, list[str]] = {"commit": [], "rollback": []}
    for path in sorted(SOURCE.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in calls:
                    calls[node.func.attr].append(f"{path.relative_to(SOURCE)}:{node.lineno}")

    assert calls == {"commit": [], "rollback": []}
