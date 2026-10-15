# 10: CI e contrato de entrega

**What to build:** um pipeline reproduz o ambiente do zero e falha quando qualquer controle obrigatório falha, e a promoção para produção usa o digest da imagem validada, nunca uma tag solta.

**Blocked by:** 08, 09

**Status:** ready-for-agent

**Plano:** Tarefa 10 e seção 15. **Vocabulário:** nenhum termo novo.

- [ ] Um script de CI com falha deliberada de `check_schema` retorna código diferente de zero; depois de removida a adulteração, retorna zero
- [ ] O pipeline roda lint, tipos, testes de migration, suíte unitária e de integração com cobertura ≥ 85%, `alembic check`, `check_schema`, auditoria de dependências, build da imagem e smoke
- [ ] O pipeline roda localmente num projeto descartável e a sequência completa passa
- [ ] O pipeline não usa segredos de produção, e os segredos da composição da API são verificados sem serem exibidos
- [ ] A publicação só ocorre em push na `main`; PR e fork nunca publicam (validado contra os dois tipos de evento)
- [ ] Os digests das imagens-base são resolvidos e registrados antes da primeira publicação
- [ ] O runbook de deploy registra as limitações de hospedagem e as entradas do adapter; nenhuma plataforma é presumida e nenhum deploy real é exigido
