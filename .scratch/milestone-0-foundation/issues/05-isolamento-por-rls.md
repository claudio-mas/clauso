# 05: Isolamento por RLS e sessão transacional

**What to build:** o isolamento entre Tenants é provado no banco, como o role de runtime, sem HTTP e sem nenhum bypass: um Tenant não lê nem altera dados de outro, e uma conexão reutilizada do pool nunca carrega o contexto anterior.

**Blocked by:** 03

**Status:** ready-for-agent

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
