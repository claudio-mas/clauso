"""tenants and api keys

Revision ID: 0001
Revises:
Create Date: 2026-10-28

Cria `cce.tenants` e `cce.api_keys` (plano do M0, seções 8.2 e 8.3), com constraints nomeadas.
As tabelas pertencem a `cce_migrator`, que roda a migration. Nenhum GRANT: sem RLS ainda
(migration 0002), uma leitura do runtime enxergaria todos os Tenants.

Defaults do banco em `tenants`: status `active`, plan `development`, default_currency `BRL`,
timezone `America/Sao_Paulo`, locale `pt-BR`, created_at e updated_at em now(). Os instantes são
`timestamptz`; o fuso do Tenant é só de apresentação.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=63), nullable=False),
        sa.Column(
            "status", sa.String(length=16), server_default=sa.text("'active'"), nullable=False
        ),
        sa.Column(
            "plan", sa.String(length=50), server_default=sa.text("'development'"), nullable=False
        ),
        sa.Column(
            "default_currency", sa.String(length=3), server_default=sa.text("'BRL'"), nullable=False
        ),
        sa.Column(
            "timezone",
            sa.String(length=64),
            server_default=sa.text("'America/Sao_Paulo'"),
            nullable=False,
        ),
        sa.Column(
            "locale", sa.String(length=16), server_default=sa.text("'pt-BR'"), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenants")),
        sa.UniqueConstraint("slug", name=op.f("uq_tenants_slug")),
        sa.CheckConstraint(r"name ~ '\S'", name=op.f("ck_tenants_name_not_blank")),
        sa.CheckConstraint(
            r"slug ~ '^[a-z0-9]+(?:-[a-z0-9]+)*$'", name=op.f("ck_tenants_slug_format")
        ),
        sa.CheckConstraint(
            "status IN ('active', 'suspended', 'archived')", name=op.f("ck_tenants_status_allowed")
        ),
        sa.CheckConstraint(
            "default_currency IN ('BRL', 'USD', 'EUR')", name=op.f("ck_tenants_currency_allowed")
        ),
        sa.CheckConstraint("locale IN ('pt-BR', 'en-US')", name=op.f("ck_tenants_locale_allowed")),
        sa.CheckConstraint(
            "(TIMESTAMPTZ '2000-01-01 00:00:00+00' AT TIME ZONE timezone) IS NOT NULL",
            name=op.f("ck_tenants_timezone_known"),
        ),
        schema="cce",
    )
    op.create_table(
        "api_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("secret_digest", sa.LargeBinary(), nullable=False),
        sa.Column("pepper_version", sa.SmallInteger(), nullable=False),
        sa.Column("scopes", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_keys")),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["cce.tenants.id"],
            name=op.f("fk_api_keys_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(r"label ~ '\S'", name=op.f("ck_api_keys_label_not_blank")),
        sa.CheckConstraint(
            "octet_length(secret_digest) = 32", name=op.f("ck_api_keys_secret_digest_length")
        ),
        sa.CheckConstraint("pepper_version > 0", name=op.f("ck_api_keys_pepper_version_positive")),
        sa.CheckConstraint(
            "scopes <@ ARRAY['tenant:read', 'tenant:write']::text[]",
            name=op.f("ck_api_keys_scopes_allowed"),
        ),
        sa.CheckConstraint(
            "expires_at > created_at", name=op.f("ck_api_keys_expires_after_created")
        ),
        schema="cce",
    )
    op.create_index(
        op.f("ix_api_keys_tenant_id"), "api_keys", ["tenant_id"], unique=False, schema="cce"
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_api_keys_tenant_id"), table_name="api_keys", schema="cce")
    op.drop_table("api_keys", schema="cce")
    op.drop_table("tenants", schema="cce")
