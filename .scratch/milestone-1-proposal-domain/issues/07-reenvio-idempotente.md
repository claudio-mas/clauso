# 07: Reenvio idempotente do Quote

**What to build:** um Host reenvia o mesmo Quote quantas vezes precisar. O CCE nunca duplica a Proposal: devolve a existente se o conteúdo não mudou, cria uma nova ProposalVersion se mudou, atualiza no lugar validade, título e metadata de uma versão ainda em draft, e recusa o reenvio apontando para outro cliente ou outra moeda (ADR-0020). A comparação ignora `valid_until`, porque o Host costuma recalcular a validade a cada envio.

**Blocked by:** 05, 06

**Status:** ready-for-agent

**Spec:** milestone-1-proposal-domain/spec.md (user stories 20–23)

- [ ] `(tenant, external_source, external_id)` é único para Proposals; um segundo `POST` com o mesmo par não cria outra Proposal
- [ ] Conteúdo, preços e termos idênticos aos da versão corrente (forma canônica sem `valid_until`), com validade, título e metadata também iguais, devolvem 200 com `result: unchanged` e não criam versão nem AuditEvent
- [ ] Conteúdo, preços e termos idênticos, mas `valid_until`, título ou metadata diferentes, atualizam esses campos no lugar, sem criar versão (o checksum é recalculado quando a validade muda), e devolvem 200 com `result: updated` e AuditEvent
- [ ] Reenviar o mesmo Quote em dias seguidos com a validade recalculada ("hoje + 30 dias") nunca cria versão nova
- [ ] Conteúdo, preços ou termos diferentes devolvem 200 com `result: version_created` e criam a versão seguinte, que passa a ser a corrente; o histórico completo permanece consultável
- [ ] A primeira criação devolve 201 com `result: created`; os casos de reenvio devolvem 200 e o campo `result` distingue o que aconteceu
- [ ] Reenviar com outro Customer retorna 422 `CUSTOMER_MISMATCH` e nada é gravado
- [ ] A moeda diferente da original retorna 422 `CURRENCY_MISMATCH` e nada é gravado
- [ ] Dois reenvios concorrentes com o mesmo par resultam em uma só Proposal e sem números de versão repetidos
- [ ] Os pontos de extensão para "reenvio rejeitado após o Acceptance" (M5) e "reenvio que só muda a validade de uma versão já publicada" (M2) estão documentados, sem código de erro implementado
