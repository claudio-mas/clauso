# 05: Isolamento por RLS e sessão transacional

**What to build:** o isolamento entre Tenants é provado no banco, como o role de runtime, sem HTTP e sem nenhum bypass: um Tenant não lê nem altera dados de outro, e uma conexão reutilizada do pool nunca carrega o contexto anterior.

**Blocked by:** 03

**Status:** done

**Plano:** Tarefa 5 e seção 10. **Vocabulário:** Tenant (`CONTEXT.md`); ADR-003.

- [ ] Testes escritos antes e rodados antes da migration, comprovando que a leitura cruzada evidencia o defeito
- [ ] A migration `0002` habilita e força RLS em `tenants` e `api_keys`, com policies `USING` e `WITH CHECK`, e concede ao runtime só os privilégios mínimos (nenhum acesso direto a `secret_digest`)
- [ ] Sem contexto, o runtime vê zero linhas; contexto vazio ou ausente falha fechado
- [ ] A lê só A; `UPDATE` de B afeta zero linhas; `INSERT` ou `UPDATE` para B é rejeitado por `WITH CHECK` (SQLSTATE 42501) e a transação seguinte funciona
- [ ] A prova de DML usa a tabela de teste `rls_probe`, criada só no banco de teste e removida no teardown, nunca em migration
- [ ] O owner `cce_migrator`, sem contexto, também vê zero linhas (FORCE RLS)
- [ ] Com `pool_size=1` e `max_overflow=0`: A, depois sem contexto (zero), depois B (só B), repetido após erro e após cancelamento
- [ ] `tenant_session` abre a transação, aplica o contexto local antes de qualquer acesso, faz flush e commit, e faz rollback em exceção ou cancelamento; repositories nunca fazem commit
- [ ] `check_schema` falha se faltar FORCE RLS, policy ou grant esperado, ou se `cce_app` ganhar ownership, `BYPASSRLS` ou membership
- [ ] O ADR `docs/adr/0013-transaction-tenant-context.md` registra o contexto local por transação, a sessão por operação e o commit antes da resposta HTTP

## Comments

### 2026-10-29 — Parecer do Revisor (Lupa), rodada 1: mudanças solicitadas

PR #7. Revisão com foco em segurança. Sem defeito em ENABLE/FORCE RLS, policies `USING`/`WITH CHECK`, ausência/vazio/malformado do contexto (falha fechado), grants estreitos sem `secret_digest`/`pepper_version`, owner, roles, contexto local por transação, pool (`pool_size=1`), erro e cancelamento. A Lupa rodou 159 testes de integração/migrations e 598 unitários, `ruff`, `mypy`, `uv lock --check`, `alembic current` (0002) e `alembic check`; não rodou a suíte completa de integração (imagem/Compose/serviço migrate) e não reproduziu o baseline do TDD.

Achado **importante** (só `tests/`): `tests/redaction_plugin.py` mascarava só a forma literal das credenciais; a forma escapada pelo `repr` (barra invertida duplicada) vazava em stdout com `--showlocals`; senhas com menos de 6 caracteres eram descartadas sem garantia equivalente no validador de DSN. Devolvido ao Forja.

### 2026-10-29 — Parecer do Revisor (Lupa), rodada 2: aprovado

PR #7, correção no commit `18a6b49` (`tests/redaction_plugin.py` e `tests/unit/test_failure_redaction.py`). A redação agora expande repr, repr aninhado, `unicode_escape`, JSON e URL-encoding, e o mínimo de tamanho foi removido. Contraprova independente com pytest real (`--showlocals --tb=long -rA`): cinco variantes de credencial (barra invertida, aspas simples e duplas, tab/BEL/newline, Unicode, senha curta) em cinco cenários (variável local, argumento e mensagem com repr, container com repr aninhado, stdout/stderr capturados, `asyncpg.connect` real contra porta fechada). Plugin antigo: vazamento em todas as variantes; plugin novo: nenhum vazamento. Sem achados bloqueantes, importantes ou sugestões nesse diff. `ruff`, `mypy` (64 arquivos) e `git diff --check` limpos.

Escopo da rodada 2: só a correção. A aprovação técnica não aprova o ADR 0013 (status Proposto), decisão reservada à pessoa. Desvios do Forja aceitos: sem `docs/architecture/security-model.md` (ticket 11 cobre); a 0003 (ticket 06) deve acrescentar a função de lookup ao `check_schema`; o runtime não pode usar o ORM `APIKey` inteiro (inclui `secret_digest`).
