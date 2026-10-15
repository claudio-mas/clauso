# Banco compartilhado com tenant_id e Row Level Security

Todos os Tenants compartilham o mesmo banco e os mesmos schemas. Toda tabela empresarial tem `tenant_id` obrigatório e RLS habilitada e forçada (inclusive para o owner), com policies que comparam a linha ao contexto de Tenant aplicado na transação. A API roda com um role sem ownership, sem `BYPASSRLS` e sem DDL. A única leitura global é a do lookup de API key, feita por uma função restrita de um role sem login.

Rejeitamos banco por Tenant e schema por Tenant pelo custo operacional (migrations multiplicadas, pools fragmentados, analytics difícil). Rejeitamos isolamento só por filtro na aplicação porque uma consulta sem filtro vazaria dados; a RLS é a defesa em profundidade contra essa omissão.

Consequências: o isolamento precisa ser provado com testes em PostgreSQL real, como o role de runtime; migrations de dados percorrem os Tenants um a um (ADR-0025).

Implementação: a partir do M0 (Blueprint §38; plano do M0 §7 e §10).
