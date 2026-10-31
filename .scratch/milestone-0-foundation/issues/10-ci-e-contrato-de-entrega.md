# 10: CI obrigatória antes do merge

**What to build:** cada Pull Request roda um pipeline que reproduz o ambiente do zero e falha quando qualquer controle obrigatório falha, e a `main` só recebe merge de PR com esse pipeline aprovado. A publicação de imagem, o deploy e a promoção por digest ficaram fora do M0 e estão no ticket 12, que depende da decisão de hospedagem.

**Blocked by:** 08, 09

**Status:** in-progress

**Plano:** Tarefa 10 e seção 15.1. A seção 15.2 (release) e a 15.3 (CD) foram movidas para o ticket 12. **Vocabulário:** nenhum termo novo.

- [ ] Um script de CI com falha deliberada de `check_schema` retorna código diferente de zero; depois de removida a adulteração, retorna zero
- [ ] O pipeline roda lint, tipos, testes de migration, suíte unitária e de integração com cobertura ≥ 85%, `alembic check`, `check_schema`, auditoria de dependências, build da imagem e smoke
- [ ] O pipeline roda localmente num projeto descartável e a sequência completa passa
- [ ] O workflow roda em `pull_request` e em `push` na `main`, com `permissions: contents: read`, actions fixadas por SHA e sem segredos; os segredos da composição da API são verificados sem serem exibidos
- [ ] O `docs/runbooks/branch-protection.md` descreve a configuração da `main` que o humano aplica no GitHub: merge só por PR, job `foundation` obrigatório e branch atualizada antes do merge
- [ ] Não existe `release.yml` nem passo de publicação de imagem no M0
- [ ] O ADR `docs/adr/0017-delivery-environments.md` registra os quatro ambientes, as migrations em job separado e a CI obrigatória antes do merge; a promoção por digest fica registrada como decisão adiada para o ticket 12
