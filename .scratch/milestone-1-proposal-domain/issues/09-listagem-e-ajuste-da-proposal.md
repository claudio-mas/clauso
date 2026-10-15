# 09: Listagem e ajuste da Proposal

**What to build:** um Host monta as telas do seu ERP listando e filtrando as Proposals do Tenant, e ajusta título e metadata de uma Proposal sem criar versão.

**Blocked by:** 04

**Status:** ready-for-agent

**Spec:** milestone-1-proposal-domain/spec.md (user stories 28, 35)

- [ ] `GET /proposals` pagina por cursor no mesmo contrato do ticket 02 e filtra por `external_id`, `customer_id`, status e período de criação
- [ ] Filtros inválidos (UUID malformado, período invertido) retornam 422
- [ ] A listagem nunca inclui Proposals de outro Tenant, mesmo com filtro por `customer_id` de outro Tenant (retorna vazio)
- [ ] `PATCH /proposals/{id}` altera só `title` e `metadata`, sem criar versão; tentar alterar `customer_id`, `currency`, `external_id` ou status é recusado (422)
- [ ] O `PATCH` grava AuditEvent com valores antigo e novo e atualiza `updated_at`
