# Publicação por API key com o scope `proposal:publish`

Publicar uma ProposalVersion exige uma ação explícita de um ator autorizado: um User ou uma API key com o scope `proposal:publish`. Esse scope é separado de `proposal:write`, de modo que uma chave pode criar e editar drafts sem conseguir publicá-los. A revisão humana obrigatória vale só para versões cujo conteúdo foi gerado pela IA do CCE: elas nascem *draft* e só podem ser publicadas depois de marcadas como *ready* por um ator autorizado, e a marcação registra quem a fez. Conteúdo enviado pelo Host já foi revisado no Host, e o CCE não tem como verificar isso.

Rejeitamos exigir sempre um User para publicar porque o User só existe com a sessão embedded (Blueprint §37), que não está em nenhum milestone, e isso impediria o Golden Path "o ERP cria e publica pela API" (§85, M2 e M3) e contrariaria o ADR-004 (API first). Rejeitamos permitir que `proposal:write` publique porque o Tenant perderia o meio de exigir aprovação humana: quem quiser essa garantia simplesmente não emite chaves com `proposal:publish`.

Consequências:

- A revisão (*draft* ou *ready*) é atributo da ProposalVersion, não estado da Proposal. A Proposal fica com o ciclo comercial: `DRAFT` (nunca publicada), `PUBLISHED`, `NEGOTIATING`, `REJECTED` e `ACCEPTED`; `READY` sai da Proposal (Blueprint §15.1).
- A ProposalVersion ganhará a origem do conteúdo (`host` ou `ai`) quando a geração por IA existir (M6); até lá toda versão tem origem `host`.
- O scope `proposal:publish` é criado no M2, junto com a Publication. O M1 não o implementa.
- O ator da publicação (User ou API key) é registrado no AuditEvent e, depois, no Evidence Package.

Complementa o ADR-004 (API first) e o ADR-006 (LLM não é autoridade sobre valores comerciais).
