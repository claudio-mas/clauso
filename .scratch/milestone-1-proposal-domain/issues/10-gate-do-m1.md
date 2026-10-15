# 10: Gate do M1: contrato, schema e migrations

**What to build:** o Milestone 1 só termina quando o contrato público, o schema e a cadeia de migrations provam, juntos, que o domínio de propostas está pronto para o M2. Este ticket é o critério de saída.

**Blocked by:** 03, 07, 08, 09

**Status:** ready-for-agent

**Spec:** milestone-1-proposal-domain/spec.md (user stories 55, 58, 61; seção Testing Decisions)

- [ ] O teste de contrato OpenAPI verifica paths exatos, scopes exigidos por operação, esquemas sem campos internos e os códigos de erro novos (`TOTAL_MISMATCH`, `CUSTOMER_MISMATCH`, `CURRENCY_MISMATCH`, `PRECONDITION_FAILED`)
- [ ] `check_schema` falha se faltar FORCE RLS, policy, grant, FK composta ou o gatilho de trava em qualquer tabela nova
- [ ] A cadeia de migrations sobe do zero ao head e desce e sobe de novo em banco descartável, restaurando constraints, policies e grants
- [ ] Para cada recurso novo existe ao menos um teste em que o Tenant A tenta ler, alterar e referenciar dados do Tenant B e falha
- [ ] Um teste exercita todos os endpoints e confirma que os logs não contêm nome, e-mail, documento, valores monetários nem conteúdo de propostas, e que audit_events não contém valores de campos pessoais
- [ ] A cobertura global continua ≥ 85% com os casos críticos de isolamento, trava de versão e auditoria obrigatórios
- [ ] A documentação da API e o `CONTEXT.md` refletem o que foi implementado, e qualquer divergência encontrada é registrada nos tickets ou nos ADRs
