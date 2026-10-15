# 11: ADRs, documentação e gate do M0

**What to build:** um engenheiro sem o histórico desta conversa consegue subir, verificar, operar e evoluir a Foundation, e o M0 só é declarado concluído depois que todos os critérios da Definition of Done foram atendidos com evidência. Este ticket é o critério de saída, e o ticket 01 do Milestone 1 depende dele.

**Blocked by:** 10

**Status:** ready-for-agent

**Plano:** Tarefa 11 e seções 16, 19 e 20.

- [ ] Os ADRs `0001` a `0010` (já escritos a partir do Blueprint) foram conferidos contra o que o M0 implementou, e os ADRs `0011` a `0017`, escritos nos tickets 01, 05, 06, 07, 08 e 10, existem e descrevem o estado real
- [ ] `docs/adr/README.md` lista todos os ADRs, de `0001` em diante, com a numeração de quatro dígitos
- [ ] O contexto de arquitetura, a visão da Foundation e o modelo de segurança descrevem o estado real implementado
- [ ] O README tem quickstart, comandos, limites e o roadmap original; os diretórios futuros têm README de escopo
- [ ] Os runbooks do M0 (local-development, tenant-bootstrap, migrations, secrets-rotation e branch-protection) foram revisados e nenhum exemplo expõe segredo; deployment e backup-restore pertencem ao ticket 12
- [ ] Cada requisito da matriz de rastreabilidade (seção 19) aponta para uma evidência
- [ ] O quickstart roda numa cópia limpa, sem venv nem volume anterior, e o smoke com dois Tenants passa
- [ ] O pipeline roda uma vez sem alterações pendentes que invalidem o resultado
- [ ] Todos os itens da Definition of Done (seção 20) estão atendidos, exceto os de publicação, deploy e restore, que foram movidos para o ticket 12; só então o status do M0 é atualizado
- [ ] Nada do Proposal Core, IA, pagamentos, frontend ou Golden Path é declarado entregue pelo M0
