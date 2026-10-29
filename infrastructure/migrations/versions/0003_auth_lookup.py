"""api key lookup for authentication

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-29

A autenticação acontece ANTES de existir um Tenant: a chave apresentada é a única informação
(plano do M0, seção 11.2). Esta migration dá ao role sem login `cce_auth` a leitura global mínima
e a empacota numa função `SECURITY DEFINER` que o runtime consegue executar e nada mais:

  * policies `auth_lookup` (SELECT, só `cce_auth`) em `cce.tenants` e `cce.api_keys`;
  * `GRANT SELECT` de COLUNAS para `cce_auth`: `tenants(id, status)` e `api_keys(id, tenant_id,
    secret_digest, pepper_version, scopes, expires_at, revoked_at)`; nenhuma tabela inteira;
  * `cce_private.lookup_api_key(p_key_id uuid)`: criada e DONA de `cce_auth` (NOLOGIN, sem
    BYPASSRLS, sem ownership das tabelas), com `search_path = pg_catalog, pg_temp`,
    `row_security = on` e todos os objetos qualificados. Recebe só o id da chave: não há Tenant,
    padrão nem paginação, e devolve no máximo uma linha (a chave primária);
  * `REVOKE ALL ... FROM PUBLIC` e `GRANT EXECUTE` explícito, só para `cce_app` (os default
    privileges de `cce_auth` já não dão EXECUTE a PUBLIC, mas não confiamos neles sozinhos);
  * `GRANT SELECT` em `cce.alembic_version` para `cce_app`, para a readiness (ticket 07).

`cce_migrator` é NOINHERIT: não herda a leitura de `cce_auth` e não executa a função. Só esta
migration assume o role, com `SET LOCAL ROLE cce_auth` (o bootstrap lhe deu SET sem INHERIT), para
criar a função já com o dono certo, sem ALTER OWNER e sem uma janela em que o migrator a possua.
`SET LOCAL` e o `RESET ROLE` ficam dentro da transação da migration; a API nunca muda de role.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_COLUMNS = "id, status"
_API_KEY_COLUMNS = "id, tenant_id, secret_digest, pepper_version, scopes, expires_at, revoked_at"

# Corpo exato da função. `scripts/check_schema.py` fixa o md5 dele: mudar este texto exige uma
# nova migration e a atualização do hash (um teste compara os dois).
LOOKUP_BODY = """
    SELECT k.id, k.tenant_id, k.secret_digest, k.pepper_version, k.scopes
    FROM cce.api_keys AS k
    JOIN cce.tenants AS t ON t.id = k.tenant_id
    WHERE k.id = p_key_id
      AND k.revoked_at IS NULL
      AND k.expires_at > pg_catalog.now()
      AND t.status = 'active'
"""

_SIGNATURE = "cce_private.lookup_api_key(uuid)"


def upgrade() -> None:
    for table in ("tenants", "api_keys"):
        op.execute(
            f"CREATE POLICY auth_lookup ON cce.{table} AS PERMISSIVE FOR SELECT TO cce_auth "
            "USING (true)"
        )
    op.execute(f"GRANT SELECT ({_TENANT_COLUMNS}) ON cce.tenants TO cce_auth")
    op.execute(f"GRANT SELECT ({_API_KEY_COLUMNS}) ON cce.api_keys TO cce_auth")
    op.execute("GRANT SELECT ON cce.alembic_version TO cce_app")

    op.execute("SET LOCAL ROLE cce_auth")
    op.execute(
        "CREATE FUNCTION cce_private.lookup_api_key(p_key_id uuid) "
        "RETURNS TABLE (key_id uuid, tenant_id uuid, secret_digest bytea, "
        "pepper_version smallint, scopes text[]) "
        "LANGUAGE sql STABLE SECURITY DEFINER "
        "SET search_path = pg_catalog, pg_temp "
        "SET row_security = on "
        f"AS $function${LOOKUP_BODY}$function$"
    )
    op.execute(f"REVOKE ALL ON FUNCTION {_SIGNATURE} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {_SIGNATURE} TO cce_app")
    op.execute("RESET ROLE")


def downgrade() -> None:
    # A função pertence a cce_auth: só ele (ou um superusuário) a remove.
    op.execute("SET LOCAL ROLE cce_auth")
    op.execute(f"DROP FUNCTION {_SIGNATURE}")
    op.execute("RESET ROLE")

    op.execute("REVOKE SELECT ON cce.alembic_version FROM cce_app")
    op.execute(f"REVOKE SELECT ({_API_KEY_COLUMNS}) ON cce.api_keys FROM cce_auth")
    op.execute(f"REVOKE SELECT ({_TENANT_COLUMNS}) ON cce.tenants FROM cce_auth")
    for table in ("api_keys", "tenants"):
        op.execute(f"DROP POLICY auth_lookup ON cce.{table}")
