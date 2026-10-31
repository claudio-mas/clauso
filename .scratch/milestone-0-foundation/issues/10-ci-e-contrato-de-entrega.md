# 10: CI obrigatória antes do merge

**What to build:** cada Pull Request roda um pipeline que reproduz o ambiente do zero e falha quando qualquer controle obrigatório falha, e a `main` só recebe merge de PR com esse pipeline aprovado. A publicação de imagem, o deploy e a promoção por digest ficaram fora do M0 e estão no ticket 12, que depende da decisão de hospedagem.

**Blocked by:** 08, 09

**Status:** done

**Plano:** Tarefa 10 e seção 15.1. A seção 15.2 (release) e a 15.3 (CD) foram movidas para o ticket 12. **Vocabulário:** nenhum termo novo.

- [ ] Um script de CI com falha deliberada de `check_schema` retorna código diferente de zero; depois de removida a adulteração, retorna zero
- [ ] O pipeline roda lint, tipos, testes de migration, suíte unitária e de integração com cobertura ≥ 85%, `alembic check`, `check_schema`, auditoria de dependências, build da imagem e smoke
- [ ] O pipeline roda localmente num projeto descartável e a sequência completa passa
- [ ] O workflow roda em `pull_request` e em `push` na `main`, com `permissions: contents: read`, actions fixadas por SHA e sem segredos; os segredos da composição da API são verificados sem serem exibidos
- [ ] O `docs/runbooks/branch-protection.md` descreve a configuração da `main` que o humano aplica no GitHub: merge só por PR, job `foundation` obrigatório e branch atualizada antes do merge
- [ ] Não existe `release.yml` nem passo de publicação de imagem no M0
- [ ] O ADR `docs/adr/0017-delivery-environments.md` registra os quatro ambientes, as migrations em job separado e a CI obrigatória antes do merge; a promoção por digest fica registrada como decisão adiada para o ticket 12

## Comments

### 2026-10-31 — Parecer do Revisor (Lupa), rodada 2: aprovado

PR #12, head de aprovação `768643f`. Histórico: a rodada 1 (head `4171dd3`) achou 2 importantes: (1) `scripts/ci.sh` engolia falha de teardown (`compose down` falhando mantinha exit 0); (2) o smoke não cobria scopes nem revogação (plano 15.1, item 13); a rodada 2 confirmou as correções (commits `f227cc4`, `6ecc72e`, `e22727a`) e não achou bloqueantes nem importantes.

Cobriu: workflow com `pull_request`, `push` na `main`, `workflow_dispatch`, `permissions: contents: read`, `persist-credentials: false`, checkout fixado por SHA (conferido pela API como v7.0.1), sem `${{ }}` nem segredos; pipeline com todos os controles obrigatórios; sem `release.yml` nem publicação de imagem; `check_compose_secrets` e `child_pytest` sem vazamento; teardown testado por função real e docker falso nos quatro cenários; smoke com leitura `tenant:read`, PATCH 403 com envelope, isolamento entre Tenants, 401 após revogação e sobrevivência das demais chaves; chaves pelo ambiente, nunca por argv; testes `sensitive`; runbook de branch protection; ADR-0017 (Proposto na revisão; aceito pela pessoa em 31/10/2026, com a adequação PEP 668 registrada no ADR).

Evidências: Forja, `bash scripts/ci.sh` exit 0 em 247 s, 16 testes de migration, 1463 testes, cobertura 96,97%, pip-audit limpo, smoke nas duas fases; GitHub, `foundation` verde no run 41168110371 (`e22727a`). Lupa: 41 + 6 testes focados, ruff, mypy (109 arquivos), `bash -n`, `git diff --check` ok; não repetiu o `ci.sh` completo por causa do `cce-test` ativo.

Observações: `pip install --user --break-system-packages uv==0.12.10` mantém a decisão do ADR-0011 (PEP 668); registrar a adequação no aceite do ADR-0017. Esperar o `foundation` verde do head final antes do merge.
