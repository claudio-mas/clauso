# 06: Autenticação por API key e bootstrap administrativo

**What to build:** um operador cria Tenants e emite chaves pela CLI administrativa, e a autenticação resolve a chave para um `Principal` imutável (Tenant, chave e scopes) sem que o cliente informe o Tenant. Duas credenciais de Tenants distintos autenticam corretamente, e a revogação vale na próxima autenticação.

**Blocked by:** 04, 05

**Status:** done

**Plano:** Tarefa 6 e seção 11. **Vocabulário:** Tenant, User (`CONTEXT.md`); o ator é a chave, não um User.

- [ ] Testes do lookup, da autenticação e do bootstrap escritos antes e falhando por função inexistente e ausência de CLI
- [ ] A migration `0003` cria a função de lookup restrita (`SECURITY DEFINER`, `search_path` fixo, `EXECUTE` só para o runtime, sem `PUBLIC`) e as policies de leitura de `cce_auth`
- [ ] O runtime não lê o digest diretamente, não assume `cce_auth` e a função não aceita tenant, padrão nem paginação
- [ ] `cce_migrator`, por ser `NOINHERIT`, não herda a leitura global
- [ ] Chave válida autentica; ausente, malformada, segredo errado, expirada, revogada, tenant suspenso e chave de outro ambiente retornam o mesmo erro uniforme
- [ ] Candidato ausente executa comparação com digest sentinela; pepper ausente invalida a chave e gera evento operacional sem segredo
- [ ] Banco fora do ar produz erro de dependência (503), nunca credencial inválida (401)
- [ ] A CLI cria Tenant e chave inicial na mesma transação e mostra a chave só depois do commit; falha no commit não deixa Tenant, chave nem exibição
- [ ] A CLI revoga chaves, rejeita revogação de chave de outro Tenant e suspende ou ativa Tenants
- [ ] Nenhum cache positivo de chaves existe; a revogação vale na autenticação seguinte
- [ ] `check_schema` passa a verificar função, owner, `search_path` e `EXECUTE`
- [ ] O ADR `docs/adr/0012-api-key-bootstrap.md` registra HMAC com pepper versionado, a função de lookup, a CLI e a rotação

## Comments

### 2026-10-29 — Parecer do Revisor (Lupa), rodada 1: aprovado

PR #8, head `f1fe7b0`. Revisão com foco em segurança. Sem achados bloqueantes ou importantes. Cobriu: função `lookup_api_key` (`SECURITY DEFINER`, owner `cce_auth` NOLOGIN, `search_path = pg_catalog, pg_temp`, `row_security = on`, objetos qualificados, `EXECUTE` sem PUBLIC), grants mínimos e `NOINHERIT`/`SET ROLE`, autenticação (erro uniforme, sentinela, pepper ausente, dependência distinta de credencial inválida, sem cache), atomicidade e saída pós-commit da CLI, revogação entre Tenants e proteção de segredos nos testes e no `check_schema`. Desvios declarados compatíveis com o escopo; o transporte HTTP permanece no ticket 07.

Evidências do Lupa (cópia extraída em /tmp): `pytest tests/migrations` 13 passed; `pytest tests/unit tests/integration --cov=cce` 1092 passed, cobertura 97,52%, com 1 falha e 6 erros só por buildx sem escrita em `~/.docker` no sandbox; repetição autorizada fora do sandbox de `test_image.py` e `test_migrate_service.py`: 7 passed (1099 confirmados em duas execuções). `ruff`, `mypy` (79 arquivos), `uv lock --check`, `alembic current` (0003), `alembic check`, `check_schema` e `git diff --check` limpos. Testes no commit `ab04a13`, anterior às implementações.

Sugestão (registrada em "Decisões e bloqueios", não bloqueia): `docs/runbooks/tenant-bootstrap.md` (seção "Se algo der errado") afirma que código 3 ou ausência de JSON significam que nada foi gravado; se a conexão cair depois do COMMIT confirmado, ou a saída falhar depois do commit, Tenant e chave podem existir sem JSON. Orientar a conferir o slug e, se preciso, emitir nova chave e revogar a emissão incerta.

Decisão da pessoa registrada na PR: ADR 0012 aceito em 2026-10-29 (commit `d059188`).

### 2026-10-29 — Re-revisão focada do Revisor (Lupa): aprovado

PR #8, commit `4890e54` (correção da sugestão do runbook, autorizada pela pessoa antes do merge; só `docs/runbooks/tenant-bootstrap.md` e uma frase do ADR 0012). Sem achados bloqueantes ou importantes; diff só nos dois documentos, `git diff --check` limpo. Runbook e ADR passam a distinguir ausência de JSON ou código 3 de rollback; conferir o slug, emitir nova chave e revogar a de entrega incerta é correto e não expõe nem reutiliza segredo.

Sugestão residual (não bloqueante, registrada em "Decisões e bloqueios"): as docstrings de `apps/api/src/cce/admin/bootstrap.py` (linhas 6-7 e 150-151) ainda afirmam que exceção no commit implica nada persistido; qualificar como falha confirmada com rollback e remeter ao resultado incerto descrito no runbook.
