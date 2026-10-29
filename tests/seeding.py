"""Linhas de teste gravadas como o dono do schema (`cce_migrator`), no banco descartável.

A RLS é forçada também para o dono: cada gravação aplica antes o contexto do próprio Tenant, como
faria qualquer código de produção. Os digests das chaves são aleatórios e nunca são lidos de volta.
Nenhuma conexão daqui é o runtime: o runtime não tem INSERT em `tenants` nem em `api_keys`.

A tabela `cce.rls_probe` existe só enquanto um teste a pede: a fixture a cria e a remove, e ela
nunca entra em migration, metadata ou imagem (plano do M0, seção 10.4).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from factories import api_key_values, tenant_values
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

CONTEXT_STATEMENT = "SELECT set_config('app.tenant_id', :tenant_id, true)"

# Mesma expressão das policies de produção (migration 0002); um teste compara o texto das duas.
_CONTEXT = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"
PROBE_STATEMENTS = (
    "DROP TABLE IF EXISTS cce.rls_probe",
    "CREATE TABLE cce.rls_probe ("
    "id uuid PRIMARY KEY DEFAULT gen_random_uuid(), "
    "tenant_id uuid NOT NULL, "
    "value text NOT NULL)",
    "ALTER TABLE cce.rls_probe ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE cce.rls_probe FORCE ROW LEVEL SECURITY",
    "CREATE POLICY tenant_isolation ON cce.rls_probe TO cce_app, cce_migrator "
    f"USING (tenant_id = {_CONTEXT}) WITH CHECK (tenant_id = {_CONTEXT})",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON cce.rls_probe TO cce_app",
)


@dataclass(frozen=True)
class SeededTenant:
    """Um Tenant gravado no banco de teste. `key_id` só existe se uma chave foi gravada."""

    id: UUID
    slug: str
    name: str
    key_id: UUID | None = None


async def apply_context(connection: AsyncConnection, tenant_id: UUID) -> None:
    await connection.execute(text(CONTEXT_STATEMENT), {"tenant_id": str(tenant_id)})


def _insert(table: str, values: Mapping[str, Any]) -> tuple[Any, Mapping[str, Any]]:
    columns = ", ".join(values)
    placeholders = ", ".join(f":{column}" for column in values)
    return text(f"INSERT INTO cce.{table} ({columns}) VALUES ({placeholders})"), values


async def seed_tenant(engine: AsyncEngine, **overrides: Any) -> SeededTenant:
    values = tenant_values(**overrides)
    statement, parameters = _insert("tenants", values)
    async with engine.begin() as connection:
        await apply_context(connection, values["id"])
        await connection.execute(statement, dict(parameters))
    return SeededTenant(id=values["id"], slug=values["slug"], name=values["name"])


async def seed_key(engine: AsyncEngine, tenant: SeededTenant, **overrides: Any) -> SeededTenant:
    values = api_key_values(tenant.id, **overrides)
    statement, parameters = _insert("api_keys", values)
    async with engine.begin() as connection:
        await apply_context(connection, tenant.id)
        await connection.execute(statement, dict(parameters))
    return SeededTenant(tenant.id, tenant.slug, tenant.name, key_id=values["id"])


async def remove_tenant(engine: AsyncEngine, tenant_id: UUID) -> None:
    """Apaga o Tenant e suas chaves, sob o contexto dele; idempotente."""
    async with engine.begin() as connection:
        await apply_context(connection, tenant_id)
        await connection.execute(
            text("DELETE FROM cce.api_keys WHERE tenant_id = :id"), {"id": tenant_id}
        )
        await connection.execute(text("DELETE FROM cce.tenants WHERE id = :id"), {"id": tenant_id})


async def create_probe(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        for statement in PROBE_STATEMENTS:
            await connection.execute(text(statement))


async def drop_probe(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.execute(text("DROP TABLE IF EXISTS cce.rls_probe"))


async def seed_probe_row(engine: AsyncEngine, tenant_id: UUID, value: str) -> UUID:
    row_id = uuid4()
    async with engine.begin() as connection:
        await apply_context(connection, tenant_id)
        await connection.execute(
            text("INSERT INTO cce.rls_probe (id, tenant_id, value) VALUES (:id, :tenant_id, :v)"),
            {"id": row_id, "tenant_id": tenant_id, "v": value},
        )
    return row_id
