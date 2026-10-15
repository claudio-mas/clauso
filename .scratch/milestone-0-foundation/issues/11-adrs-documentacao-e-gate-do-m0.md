# 11: ADRs, documentação e gate do M0

**What to build:** um engenheiro sem o histórico desta conversa consegue subir, verificar, operar e evoluir a Foundation, e o M0 só é declarado concluído depois que todos os critérios da Definition of Done foram atendidos com evidência. Este ticket é o critério de saída, e o ticket 01 do Milestone 1 depende dele.

**Blocked by:** 10

**Status:** ready-for-agent

**Plano:** Tarefa 11 e seções 16, 19 e 20.

- [ ] Os ADRs 001 a 010 preservam as decisões e os IDs do Blueprint, e os ADRs 011 a 017 seguem a tabela da seção 16 do plano
- [ ] Os arquivos de ADR usam a numeração de quatro dígitos já adotada em `docs/adr/` (`0001` a `0017`), de modo a coexistirem com os ADRs `0018` a `0022` existentes, sem colisão de IDs
- [ ] O contexto de arquitetura, a visão da Foundation e o modelo de segurança descrevem o estado real implementado
- [ ] O README tem quickstart, comandos, limites e o roadmap original; os diretórios futuros têm README de escopo
- [ ] Os seis runbooks foram revisados e nenhum exemplo expõe segredo
- [ ] Cada requisito da matriz de rastreabilidade (seção 19) aponta para uma evidência
- [ ] O quickstart roda numa cópia limpa, sem venv nem volume anterior, e o smoke com dois Tenants passa
- [ ] O pipeline roda uma vez sem alterações pendentes que invalidem o resultado
- [ ] Todos os itens da Definition of Done (seção 20) estão atendidos; só então o status do M0 é atualizado
- [ ] Nada do Proposal Core, IA, pagamentos, frontend ou Golden Path é declarado entregue pelo M0
