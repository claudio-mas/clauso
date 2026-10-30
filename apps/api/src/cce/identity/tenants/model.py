"""Tenant: a empresa vendedora, fronteira do isolamento. Tabela `cce.tenants`.

A raiz Tenant não tem `tenant_id`: a própria coluna `id` é a fronteira da policy (ticket 05).
Defaults do banco: status `active`, plano `development`, moeda `BRL`, fuso `America/Sao_Paulo`,
locale `pt-BR`, e `created_at`/`updated_at` em `now()`. O `updated_at` é atualizado de forma
explícita pelo serviço. Fuso e locale são só de apresentação; o armazenamento é sempre UTC.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, String, func, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from cce.shared.db.base import Base

STATUSES = ("active", "suspended", "archived")
CURRENCIES = ("BRL", "USD", "EUR")
LOCALES = ("pt-BR", "en-US")
SLUG_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"

# Constraints cuja violação é um conflito esperado (HTTP 409), com o campo público que ela protege.
# Os nomes seguem a convenção de `Base` e a migration 0001; qualquer outra violação é erro interno.
CONFLICT_FIELDS = {
    "uq_tenants_slug": "slug",
    "ck_tenants_name_not_blank": "name",
    "ck_tenants_slug_format": "slug",
    "ck_tenants_status_allowed": "status",
    "ck_tenants_currency_allowed": "default_currency",
    "ck_tenants_locale_allowed": "locale",
    "ck_tenants_timezone_known": "timezone",
}


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


class Tenant(Base):
    __tablename__ = "tenants"
    __table_args__ = (
        CheckConstraint(r"name ~ '\S'", name="name_not_blank"),
        CheckConstraint(f"slug ~ '{SLUG_PATTERN}'", name="slug_format"),
        CheckConstraint(_one_of("status", STATUSES), name="status_allowed"),
        CheckConstraint(_one_of("default_currency", CURRENCIES), name="currency_allowed"),
        CheckConstraint(_one_of("locale", LOCALES), name="locale_allowed"),
        # Recusa nome de fuso que o PostgreSQL desconhece (a expressão falha com 22023).
        CheckConstraint(
            "(TIMESTAMPTZ '2000-01-01 00:00:00+00' AT TIME ZONE timezone) IS NOT NULL",
            name="timezone_known",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(63), unique=True)
    status: Mapped[str] = mapped_column(String(16), server_default=text("'active'"))
    plan: Mapped[str] = mapped_column(String(50), server_default=text("'development'"))
    default_currency: Mapped[str] = mapped_column(String(3), server_default=text("'BRL'"))
    timezone: Mapped[str] = mapped_column(String(64), server_default=text("'America/Sao_Paulo'"))
    locale: Mapped[str] = mapped_column(String(16), server_default=text("'pt-BR'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
