"""Valores em memória para linhas de teste. Nada aqui acessa o banco nem guarda segredo real."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4


def tenant_values(**overrides: Any) -> dict[str, Any]:
    """Colunas obrigatórias de um Tenant; as demais usam os defaults do banco."""
    values: dict[str, Any] = {
        "id": uuid4(),
        "name": "Empresa de Teste",
        "slug": f"empresa-{uuid4().hex[:16]}",
    }
    values.update(overrides)
    return values


def api_key_values(tenant_id: UUID, **overrides: Any) -> dict[str, Any]:
    """Colunas obrigatórias de uma APIKey. O digest é aleatório, sem relação com chave real."""
    created_at = datetime.now(UTC)
    values: dict[str, Any] = {
        "id": uuid4(),
        "tenant_id": tenant_id,
        "label": "ERP",
        "secret_digest": os.urandom(32),
        "pepper_version": 1,
        "scopes": ["tenant:read", "tenant:write"],
        "created_at": created_at,
        "expires_at": created_at + timedelta(days=90),
    }
    values.update(overrides)
    return values
