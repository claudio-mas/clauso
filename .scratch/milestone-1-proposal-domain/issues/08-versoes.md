# 08: Versões: listar, criar e editar em draft

**What to build:** um Host acompanha o histórico de uma Proposal, registra uma revisão comercial deliberada como nova versão e corrige uma versão em draft no lugar, sem sobrescrever a edição de outro processo.

**Blocked by:** 05

**Status:** ready-for-agent

**Spec:** milestone-1-proposal-domain/spec.md (user stories 29–34)

- [ ] `GET /proposals/{id}/versions` lista as versões em ordem com número, checksum e data; `GET` de uma versão devolve o conteúdo completo e o Total daquele momento
- [ ] `POST /proposals/{id}/versions` cria a versão seguinte, que passa a ser a corrente, com numeração sequencial sem lacunas
- [ ] `PUT` de uma versão em draft substitui conteúdo, preços e termos, recalcula Total e checksum e não cria versão
- [ ] `GET` devolve o checksum como `ETag`; `PUT` com `If-Match` divergente retorna 412 `PRECONDITION_FAILED` e nada é gravado; sem o cabeçalho a edição prossegue
- [ ] `TOTAL_MISMATCH` e as validações de Sections e itens valem em `POST` e `PUT`
- [ ] Duas criações concorrentes de versão nunca produzem número repetido nem lacuna
- [ ] Editar ou criar versão grava AuditEvent com valores antigo e novo
- [ ] Versão com `published_at` ou `accepted_at` preenchido não pode ser editada (o gatilho do banco responde e a API devolve erro padronizado)
- [ ] Versão de outra Proposal ou de outro Tenant é 404
