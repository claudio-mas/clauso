# Migrations de dados percorrem os Tenants um a um

Todas as tabelas empresariais usam `FORCE ROW LEVEL SECURITY`, inclusive para o owner `cce_migrator`, que sem contexto de Tenant enxerga zero linhas. Por isso uma migration que lê ou altera dados de vários Tenants (backfill, normalização, mudança de formato) não roda como um único `UPDATE` global: ela lê a lista de Tenants e, para cada um, abre uma transação, aplica `set_config('app.tenant_id', ..., true)` e executa a alteração. Migrations só de schema (DDL, constraints, grants, policies) não precisam de contexto.

A lista de Tenants é obtida com o mesmo mecanismo restrito da autenticação: uma função `SECURITY DEFINER` em `cce_private`, de propriedade de um role sem login, que devolve apenas os IDs dos Tenants e é executável só pelo `cce_migrator`. Ela é criada na primeira migration de dados que precisar dela, não antes.

Rejeitamos dar `BYPASSRLS` ao migrator ou criar um role de manutenção com leitura global, porque isso abriria uma porta permanente para contornar o isolamento justamente no processo que roda com mais privilégio. Rejeitamos desligar a RLS durante a migration pelo mesmo motivo e porque o desligamento poderia ficar para trás numa falha.

Consequências:

- Migrations de dados são mais lentas e não são atômicas entre Tenants: cada Tenant é uma transação, e a migration precisa ser idempotente para poder ser retomada.
- Uma alteração que precisa ser atômica entre Tenants deve ser repensada como expand/contract.
- O runbook de migrations descreve o padrão e um exemplo.

Complementa o ADR-003 (banco compartilhado com RLS) e o ADR-013 (contexto de Tenant por transação, escrito no ticket 05 do M0).
