# 09: Robustez de migrations

**What to build:** o schema e seus controles de segurança são reconstruíveis do zero e não dependem da memória de quem implementou: adulterar o schema é detectado. O ensaio de backup e restore foi movido para o ticket 12.

**Blocked by:** 07

**Status:** done

**Plano:** Tarefa 9 e seção 9.3. O ensaio de backup/restore da seção 18 está no ticket 12.

- [ ] Em banco descartável, remover temporariamente uma policy ou um grant faz `check_schema` falhar; restabelecido o head, ele passa
- [ ] A cadeia `0001 → 0003 → base → 0003` passa em banco vazio e restaura constraints, policies e grants
- [ ] `alembic check` e a suíte completa passam depois da cadeia
- [ ] Os testes de roles e de transações cobrem as falhas restantes (cancelamento, pool reutilizado, concorrência A/B)
- [ ] O runbook de migrations está escrito, não expõe segredos e descreve o padrão de migration de dados por Tenant (ADR-0025)
- [ ] Downgrade só é permitido em banco descartável `cce_test*`; o runbook de produção descreve expand/contract

## Comments

### 2026-10-31 — Parecer do Revisor (Lupa), rodada 2: aprovado

PR #11, head `353fdaa`. Revisão com foco em segurança. Histórico: a rodada 1 (head `95b9367`) achou 1 importante: o `check_schema` só acusava trigger interno de FK com `tgenabled = D`, e o modo REPLICA (`R`) também deixa de validar escritas normais; a rodada 2 confirmou a correção e não achou bloqueantes nem importantes.

Cobriu: `scripts/check_schema.py` exige `tgenabled = O` nos triggers internos de FK de `cce.api_keys` e `cce.tenants` (REPLICA e ALWAYS reproduzidos em PostgreSQL real e acusados; os três testes novos falham contra o script da rodada 1 e passam contra o atual); verificações novas de identidade de réplica, FK deferrable, publications que incluam `cce`, `pg_parameter_acl` (produto/PUBLIC) e event triggers; mensagens só com identificadores e tipos de privilégio; falhas dos testes novos sem vazamento do segredo configurado (`-vv --showlocals --tb=long`); DSNs pelo ambiente, nunca por argv; runbook com ADR-0025, expand/contract e downgrade restrito a `CCE_ENV=test`/`cce_test*`.

Evidências do Lupa (cópia extraída em /tmp): `tests/migrations` 16 passed; `test_check_schema` + `test_runbooks` 146 passed; unit/integration/contracts sem os 3 arquivos de Docker 1368 passed; os 8 testes de Docker repetidos fora do sandbox com autorização da pessoa: 8 passed (total 1376); `ruff`, `mypy` (102 arquivos), `uv lock --check` e `git diff --check` limpos; `alembic current` = 0003, `alembic check` sem operações novas, `check_schema` passou.

Sugestão (não bloqueante), para o ticket 12: a rejeição global de event triggers é inventário estrito; um banco gerenciado legítimo (ex.: event trigger do PostgREST) falharia no check. Tratar no ticket 12 um conjunto esperado e auditado de objetos do provedor/extensões (definição, ownership, evento, estado), sem ignorar objeto só pelo nome ou por pertencer a extensão, e registrar essa premissa no runbook; o runbook não deve induzir uma migration do CCE a remover objetos legítimos do provedor.

Follow-up aceito (rodada 1, ADR-0013): o `cce_app` consegue gravar `ALTER ROLE cce_app SET statement_timeout`/`lock_timeout`/`search_path`; o PostgreSQL não tem privilégio que impeça isso e hoje só o `check_schema` detecta a deriva persistente. `SET LOCAL` por transação no `tenant_session` restauraria os valores, mas não impede SQL arbitrário (quem executa SQL como `cce_app` emite o próprio `SET`). Abrir ticket futuro com testes em conexões novas e reutilizadas e essa limitação explícita.
