# 03: Tenant e APIKey persistidos (migration 0001)

**What to build:** o schema com `Tenant` e `APIKey` é reproduzível por migration, em banco descartável, com constraints e defaults documentados. Ainda não há autenticação HTTP.

**Blocked by:** 02

**Status:** in-progress

**Plano:** Tarefa 3 e seções 8 e 9. **Vocabulário:** Tenant (`CONTEXT.md`).

- [ ] Testes de constraints (slug, status, moeda, locale, timezone, expiração posterior à criação, digest de 32 bytes, scopes permitidos) escritos antes e falhando por tabelas ausentes
- [ ] A migration `0001` sobe do zero e `empty → 0001 → base → 0001` passa em banco descartável
- [ ] O owner das tabelas é `cce_migrator`; `cce_app` não tem DDL
- [ ] Datas são sempre com timezone e em UTC; JSON usa ISO 8601 com offset
- [ ] Os schemas de PATCH de Tenant recusam `id`, `tenant_id`, `status`, `plan` e `slug` (`extra="forbid"`)
- [ ] As fixtures só rodam com `CCE_ENV=test` e banco com prefixo `cce_test`, validado parseando a URL; qualquer outro caso aborta sem mostrar o DSN
- [ ] `alembic check` passa depois de alinhar a metadata; `create_all` nunca é usado
- [ ] Migrations rodam com `NullPool` e sem DSN em logs

## Comments

### 2026-10-28 — Parecer do Revisor (Lupa), rodada 1: mudanças necessárias

PR #5, head `ae59663`. Revisão com foco em segurança, pelo git local, em cópia nova via `git archive`. Constraints, cadeia `empty → 0001 → base → 0001`, `alembic check`, owner `cce_migrator`, ausência de DDL e de privilégios de tabela para `cce_app`/`cce_auth`/`PUBLIC`, timestamps UTC, PATCH com `extra="forbid"`, `NullPool` e ausência de `create_all` foram conferidos e estão corretos. Escopo, `COPY` do Dockerfile, `CONTEXT.md` e ADRs também estão corretos.

Problemas:
1. **[bloqueante]** `tests/db_support.py:36-39` (consumido por `tests/conftest.py:59,91` e `infrastructure/migrations/env.py:72`): a guarda valida só `URL.database`, mas o dialect asyncpg deixa a query sobrescrever o banco. Reprodução mínima com DSN sintético e sem conexão: `postgresql+asyncpg://cce_migrator@localhost/cce_test?database=cce` passa em `assert_disposable_database` e em `MigrationSettings`, e o engine efetivo usa o banco `cce`. Vale para o DSN runtime e para o do migrator; `run_alembic(..., "downgrade", "base")` passa pela mesma guarda e `env.py` abre o engine vulnerável, podendo executar testes, escritas e downgrade no banco de desenvolvimento. Corrigir recusando query/parâmetros nos DSNs de teste (também na validação carregada por `env.py`) ou validando os parâmetros finais efetivos do driver e impedindo overrides de banco, role e destino. Acrescentar regressões para runtime, migrator e downgrade, provando recusa antes de conectar e sem DSN na saída.
2. **[importante]** `tests/db_support.py:53-62` e `infrastructure/migrations/env.py:71-75`: a configuração validada não é a executada. `run_alembic` valida o objeto recebido, mas o subprocesso herda `os.environ` e `env.py` recarrega settings/dotenv sem conferir que correspondem ao validado (contraprova: objeto `test`/`cce_test` com ambiente herdado `local`/`cce`; o subprocesso carregou `local`/`cce` sem nova recusa). Além disso, o comando direto do Alembic aceita `downgrade` em `local`/`cce`: `env.py` não implementa a restrição da seção 9.3 do plano (downgrade só em banco descartável `cce_test*`). Corrigir vinculando explicitamente o subprocesso aos settings validados por ambiente controlado (segredo nunca no argv) e guardando o downgrade no próprio processo Alembic contra a configuração efetiva, exigindo `test` e banco descartável. Testar divergência entre dotenv, ambiente e settings, e `downgrade` fora de `test`.
3. **[importante]** `tests/db_support.py:64-69` e `tests/migrations/test_migration_chain.py:135-137`: a proteção de falhas cobre só o DSN completo. Se o stderr contém só a senha, um fragmento sensível ou uma URL normalizada, a função imprime os últimos 1500 caracteres (contraprova: o `pytest.fail` contém uma sentinela). O teste offline faz asserts introspectivos sobre stdout antes de `assert_no_leaks`, e a falha imprime a sentinela. Corrigir evitando saída bruta em `pytest.fail`, com mensagens fixas ou saída sanitizada por componentes sensíveis; verificar vazamentos antes de quaisquer asserts e usar `require` nas verificações de saída. Acrescentar regressões de falha com senha isolada e pytest real, verificando stdout, stderr, traceback e argv sem imprimir sentinelas.

Opiniões sobre os pontos submetidos: (1) `0001` sem nenhum `GRANT` a `cce_app`/`cce_auth` é correto e suficiente nesta etapa, e não substitui a RLS do ADR-0003 para o runtime futuro; (2) o CHECK de timezone com validação estrita na aplicação é aceitável, mas `ZoneInfo` aceita alguns aliases que existem na tzdata, então não promete só nomes `Area/Location`; (3) os defaults de banco são coerentes, documentados e testados; (4) o regex `\S` é adequado, com sugestão de incluir tab e newline nos testes negativos de `label`; (5) há caminhos para outro banco, conforme os itens 1 e 2.

Limites declarados pelo Revisor: 157 testes de integração passaram, 1 falha e 6 erros (os sete testes de imagem e `migrate` ficaram limitados pelo sandbox dele, que não consegue escrever em `~/.docker/buildx/activity`); esses sete vêm do Regente (suíte completa: 164 passed). Migrations (7 passed), unit (360), Ruff, mypy estrito e lock passaram. O ticket volta a `in-progress`.
