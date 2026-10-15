# 07: Reenvio idempotente do Quote

**What to build:** um Host reenvia o mesmo Quote quantas vezes precisar. O CCE nunca duplica a Proposal: devolve a existente se o conteúdo não mudou, cria uma nova ProposalVersion se mudou, e recusa o reenvio apontando para outro cliente (ADR-0020).

**Blocked by:** 05, 06

**Status:** ready-for-agent

**Spec:** milestone-1-proposal-domain/spec.md (user stories 20–23)

- [ ] `(tenant, external_source, external_id)` é único para Proposals; um segundo `POST` com o mesmo par não cria outra Proposal
- [ ] Conteúdo idêntico (mesmo checksum da versão corrente) devolve 200 com a Proposal existente e não cria versão nem AuditEvent de alteração
- [ ] Conteúdo diferente devolve 200 e cria a versão seguinte, que passa a ser a corrente; o histórico completo permanece consultável
- [ ] A primeira criação devolve 201; os dois casos de reenvio devolvem 200 e o corpo permite distinguir o que aconteceu
- [ ] Reenviar com outro Customer retorna 422 `CUSTOMER_MISMATCH` e nada é gravado
- [ ] A moeda diferente da original retorna 422 e nada é gravado
- [ ] Dois reenvios concorrentes com o mesmo par resultam em uma só Proposal e sem números de versão repetidos
- [ ] O ponto de extensão para "reenvio rejeitado após o Acceptance" está documentado, sem código de erro implementado
