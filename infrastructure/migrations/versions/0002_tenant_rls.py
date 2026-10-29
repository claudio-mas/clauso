"""tenant row level security and runtime grants

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-29

Habilita e FORÇA a RLS em `cce.tenants` e `cce.api_keys` (plano do M0, seção 10.1; ADR-003) e
concede ao runtime (`cce_app`) só o mínimo. Sem contexto de Tenant a policy não casa nenhuma
linha: `NULLIF(..., '')` cobre o contexto ausente e o vazio, e um UUID malformado falha em vez de
liberar tudo. A policy vale para `cce_app` e para o dono (`cce_migrator`), que é recusado como
qualquer outro porque o FORCE o inclui.

Grants de `cce_app`:
  * `tenants`: SELECT; UPDATE só em name, default_currency, timezone, locale e updated_at;
  * `api_keys`: SELECT só em id, tenant_id, label, scopes, created_at, expires_at e revoked_at.
Nenhum acesso, de nenhum tipo, a `secret_digest` e `pepper_version`; nenhum INSERT, DELETE nem
TRUNCATE; nenhuma concessão a `cce_auth` nem a PUBLIC. Esta migration não cria funções, então não
há EXECUTE a conceder: os default privileges de `cce_migrator` não dão EXECUTE a PUBLIC, e cada
função que o runtime precisar executar exige o próprio `GRANT EXECUTE` (não há `IN SCHEMA`).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONTEXT = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"

_TENANT_UPDATABLE = "name, default_currency, timezone, locale, updated_at"
_API_KEY_READABLE = "id, tenant_id, label, scopes, created_at, expires_at, revoked_at"

# (tabela, coluna que carrega o Tenant)
_PROTECTED = (("tenants", "id"), ("api_keys", "tenant_id"))


def upgrade() -> None:
    for table, column in _PROTECTED:
        op.execute(f"ALTER TABLE cce.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE cce.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON cce.{table} "
            "TO cce_app, cce_migrator "
            f"USING ({column} = {_CONTEXT}) "
            f"WITH CHECK ({column} = {_CONTEXT})"
        )
    op.execute("GRANT SELECT ON cce.tenants TO cce_app")
    op.execute(f"GRANT UPDATE ({_TENANT_UPDATABLE}) ON cce.tenants TO cce_app")
    op.execute(f"GRANT SELECT ({_API_KEY_READABLE}) ON cce.api_keys TO cce_app")


def downgrade() -> None:
    op.execute(f"REVOKE SELECT ({_API_KEY_READABLE}) ON cce.api_keys FROM cce_app")
    op.execute(f"REVOKE UPDATE ({_TENANT_UPDATABLE}) ON cce.tenants FROM cce_app")
    op.execute("REVOKE SELECT ON cce.tenants FROM cce_app")
    for table, _ in reversed(_PROTECTED):
        op.execute(f"DROP POLICY tenant_isolation ON cce.{table}")
        op.execute(f"ALTER TABLE cce.{table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE cce.{table} DISABLE ROW LEVEL SECURITY")
