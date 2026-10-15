# 12: Publicação de imagem, deploy e recuperação

**What to build:** a imagem validada pela CI é publicada com referência imutável (digest), promovida de staging para production por um contrato de deploy, e um backup restaurado preserva dados, policies e autenticação. Saiu do caminho crítico do M0 porque depende de decisões ainda abertas (plano do M0, seção 21).

**Blocked by:** 10; decisão humana sobre plataforma de hospedagem e gerenciador de segredos

**Status:** blocked

**Plano:** seções 15.2, 15.3 e 18 (Backup/restore); partes de backup/restore que estavam na Tarefa 9. Não bloqueia o gate do M0 (ticket 11) nem o M1.

- [ ] `release.yml` roda só depois da CI aprovada, em push na `main` do próprio repositório; PR e fork nunca publicam (validado contra os dois tipos de evento)
- [ ] A imagem é construída do mesmo commit que passou na CI, publicada com label de revisão, e o digest `sha256` é registrado
- [ ] Os digests das imagens-base são resolvidos e registrados antes da primeira publicação
- [ ] O runbook `deployment.md` descreve a ordem de promoção, o job único de migration, o rollback por digest e as entradas do adapter da plataforma escolhida
- [ ] Backup e restore de dados sintéticos preservam IDs, constraints, policies e a autenticação das chaves; o runbook `backup-restore.md` distingue dados, roles e configurações
- [ ] O ADR-0017 é atualizado com a promoção por digest

## Comments

- 2026-10-15: criado na revisão dos documentos. O humano decidiu adiar release, deploy e backup/restore até existir decisão de hospedagem.
