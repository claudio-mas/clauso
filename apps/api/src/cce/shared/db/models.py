"""Registra os modelos no metadata. Importar este módulo não cria engine nem acessa o banco."""

from cce.identity.tenants.model import Tenant
from cce.integrations.api_keys.model import APIKey
from cce.shared.db.base import Base

__all__ = ["APIKey", "Base", "Tenant"]
