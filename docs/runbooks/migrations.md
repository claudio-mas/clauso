# Migrations

Como aplicar, verificar, escrever e corrigir migrations do CCE sem depender da memória de quem as escreveu. O schema e seus controles de segurança (RLS, policies, grants, a função de lookup, os defaults de sessão dos roles) se reconstroem do zero só com o bootstrap do PostgreSQL e com as migrations. As decisões estão no [ADR-0003](../adr/0003-shared-multi-tenant-rls.md), no [ADR-0013](../adr/0013-transaction-tenant-context.md) e no [ADR-0025](../adr/0025-cross-tenant-data-migrations.md).

## Regras

- Migrations rodam num processo separado (o serviço `migrate` do Compose, ou o CLI do Alembic), como `cce_migrator`. Nunca no startup da API, e nunca por `Base.metadata.create_all`.
- O DSN vem do dotenv apontado por `CCE_ENV_FILE` (ou do ambiente do processo). Nunca vai no argumento da linha de comando, no `alembic.ini`, em log nem em ticket. Os comandos abaixo não mostram nenhuma credencial, e a saída do Alembic e do `check_schema` também não.
- RLS, policies, funções e grants são SQL explícito dentro das migrations. O autogenerate não é autoridade sobre eles.
- Uma migration aplicada em qualquer ambiente compartilhado nunca é editada. A correção é uma migration nova.
- Downgrade só existe para teste, no banco descartável (ver abaixo).

## Aplicar e verificar

```bash
export CCE_ENV_FILE=.env          # ou .env.test no ambiente de teste
uv run --project apps/api --locked alembic -c alembic.ini upgrade head
uv run --project apps/api --locked alembic -c alembic.ini current
uv run --project apps/api --locked alembic -c alembic.ini check
uv run --project apps/api --locked python scripts/check_schema.py
```

Pelo Compose, o job equivalente é `docker compose run --rm --no-deps migrate`.

Resultado esperado:

- `current` mostra a revisão head (hoje `0003 (head)`).
- `check` termina com `No new upgrade operations detected.`: tabelas, colunas, índices, FKs e uniques batem com o metadata.
- `check_schema.py` imprime `schema check passed` e sai com 0.

O `alembic check` não enxerga o que o metadata não representa. É isso que `scripts/check_schema.py` verifica, lendo só o catálogo como `cce_migrator`:

| Verificação | O que é um problema |
|---|---|
| Roles | SUPERUSER, CREATEDB, CREATEROLE, REPLICATION, BYPASSRLS ou INHERIT em `cce_app`, `cce_migrator` ou `cce_auth` (nenhum pode ter); `cce_auth` com login |
| Associações | Qualquer associação além de `cce_migrator` poder `SET ROLE cce_auth` (sem herdar, sem administrar) |
| Ownership | Schemas e relações que não sejam de `cce_migrator`; qualquer objeto de `cce_app` |
| Tabelas e RLS | Tabela protegida sem RLS habilitada e forçada; tabela inesperada em `cce` |
| Policies | Policy ausente, extra ou com expressão, comando ou roles diferentes |
| Grants | Grant ausente, extra ou com GRANT OPTION, por tabela e por coluna; `CREATE` do runtime em algum schema |
| Funções | A função de lookup com outro dono, sem SECURITY DEFINER, sem `search_path` e `row_security` fixos ou com outro corpo; qualquer outra função SECURITY DEFINER ou executável pelo runtime ou por PUBLIC |
| Constraints | Constraint ausente, extra, com outra definição (CHECK inclusive) ou não validada (`NOT VALID`) |
| Triggers, rules e relações | Trigger ou rule que não seja interno; view, sequência ou qualquer relação em `cce_private`; replica identity diferente da padrão |
| Trigger de FK | Fora do modo em que as migrations o criam (O): desligado (D), só em réplica (R) ou sempre (A) |
| Database e cluster | Publication que inclua `cce`; event trigger; GRANT SET ou ALTER SYSTEM em parâmetro para um role do produto ou PUBLIC (o runtime poderia pôr `session_replication_role` em `replica` e desligar as FKs) |
| Default privileges | Funções novas de `cce_migrator` ou `cce_auth` executáveis por PUBLIC; qualquer default que conceda algo a objetos futuros |
| Defaults de sessão | `search_path` e timeouts dos roles diferentes do bootstrap; qualquer default por database |

Códigos de saída: `0` conforme; `1` com uma linha por problema; `2` quando a configuração falta ou o catálogo não pode ser lido (mensagem fixa, sem DSN nem texto do driver).

## Quando o `check_schema` falha

Uma falha quer dizer que o banco não é o que as migrations produzem: alguém alterou o schema à mão, uma migration foi editada depois de aplicada ou o bootstrap não foi reaplicado.

1. Não corrija à mão no banco. Leia as linhas da saída: elas citam só nomes de objetos.
2. Descubra a origem (quem, quando, por quê). Uma alteração manual de grant, policy ou RLS é um incidente de segurança até prova em contrário.
3. Corrija conforme o ambiente:
   - **Banco descartável (`cce_test*`)**: reconstrua do zero. Remova as tabelas e a função que as migrations criam e rode `upgrade head` (é o que `tests/migrations/test_schema_drift.py` faz). Para um grant revogado, desfazer e reaplicar as revisões que o concedem também resolve.
   - **Qualquer outro ambiente**: escreva uma migration nova que leve o banco de volta ao esperado e passe pela revisão de segurança.
4. Um problema nos defaults de sessão de um role (por exemplo `role cce_app: setting statement_timeout is not the expected one`) se corrige reaplicando o bootstrap, que regrava `search_path` e os timeouts: `docker compose exec postgres /docker-entrypoint-initdb.d/01-bootstrap.sh`. Um default extra, que o bootstrap não conhece, sai com `ALTER ROLE <role> RESET <parâmetro>` executado pelo superusuário. O próprio `cce_app` consegue alterar os defaults dele (o PostgreSQL não tem privilégio que impeça), então trate a alteração como suspeita.
5. Rode de novo `alembic check` e `check_schema.py` até os dois passarem.

## Escrever uma migration nova

1. Crie a revisão seguinte da cadeia (`0004`, `0005`, ...), com `down_revision` apontando para o head atual e um único head.
2. Para cada tabela nova com Tenant na linha:
   - `ALTER TABLE ... ENABLE ROW LEVEL SECURITY` e `FORCE ROW LEVEL SECURITY`;
   - a policy `tenant_isolation` para `cce_app, cce_migrator`, com `USING` e `WITH CHECK` sobre `NULLIF(current_setting('app.tenant_id', true), '')::uuid`;
   - só os grants que o runtime precisa, por coluna quando der, e nenhum para PUBLIC.
3. Uma função que o runtime execute precisa de `GRANT EXECUTE` explícito. Uma função SECURITY DEFINER precisa de dono sem login, `SET search_path = pg_catalog, pg_temp` e `SET row_security = on`.
4. No mesmo Pull Request, atualize os conjuntos esperados de `scripts/check_schema.py` (tabelas protegidas, policies, grants, constraints, funções) e os testes de `tests/integration/test_check_schema.py`. Um teste confere que as constraints fixadas são as do metadata; outro, que o md5 do corpo da função de lookup é o da migration.
5. Escreva o `downgrade` e prove a ida e a volta em `tests/migrations/test_migration_chain.py`.
6. Rode a cadeia completa e a suíte (seção seguinte). A revisão de segurança é obrigatória em toda migration que mexe em RLS, grants, funções ou transações.

## Downgrade e a cadeia completa (só em teste)

`downgrade` só roda com `CCE_ENV=test` e banco cujo nome começa com `cce_test`. O `env.py` recusa qualquer outro destino antes de conectar, e também uma chamada programática em que a direção não se sabe. A recusa nunca mostra o DSN.

```bash
docker compose --env-file .env.test -p cce-test up -d --wait postgres redis
export CCE_ENV_FILE=.env.test
uv run --project apps/api --locked alembic -c alembic.ini downgrade base
uv run --project apps/api --locked alembic -c alembic.ini upgrade 0001
uv run --project apps/api --locked alembic -c alembic.ini upgrade head
uv run --project apps/api --locked alembic -c alembic.ini check
uv run --project apps/api --locked python scripts/check_schema.py
uv run --project apps/api --locked pytest tests/migrations
uv run --project apps/api --locked pytest tests/unit tests/integration tests/contracts
```

Os testes de `tests/migrations` fazem `downgrade base` e reconstroem o schema, por isso rodam antes e separados dos demais.

## Produção: expand/contract, sem downgrade

Em produção não há downgrade. Um rollback da imagem da API nunca implica desfazer o schema: a versão anterior da API precisa continuar funcionando com o schema novo. Toda mudança incompatível vira expand/contract, em releases separados:

1. **Expand**: a migration só acrescenta (coluna nula ou com default, tabela nova, índice criado sem travar a tabela). A API em produção ignora o que é novo.
2. **Migrar o código**: a API nova grava nos dois formatos e lê o novo com fallback para o antigo.
3. **Backfill**: uma migration de dados preenche o formato novo, Tenant a Tenant (seção seguinte).
4. **Contract**: num release posterior, quando nenhuma versão em produção depende do formato antigo, uma migration remove o que sobrou (coluna, policy, grant).

Se uma migration aplicada estiver errada, a correção é outra migration para frente. Uma mudança que precise ser atômica entre Tenants também é repensada como expand/contract.

## Migrations de dados: um Tenant por vez (ADR-0025)

A RLS é forçada também para o dono das tabelas: sem contexto de Tenant, `cce_migrator` enxerga zero linhas. Por isso um backfill não é um `UPDATE` global. Também não se resolve dando BYPASSRLS ao migrator, nem desligando a RLS durante a migration, nem criando um role de leitura global: nunca faça nenhum dos três.

O padrão:

1. **Listar os Tenants** com uma função SECURITY DEFINER em `cce_private`, de dono sem login, que devolve só os IDs dos Tenants e é executável só por `cce_migrator`. Ela ainda não existe: é criada na primeira migration de dados que precisar dela, junto com sua entrada em `check_schema.py` (o EXECUTE de `cce_migrator` como esperado).
2. **Para cada Tenant, uma transação**, com o contexto aplicado só nela:

   ```sql
   BEGIN;
   SELECT set_config('app.tenant_id', :tenant_id, true);   -- true: vale só nesta transação
   UPDATE cce.<tabela>
      SET <coluna_nova> = <expressão>
    WHERE <coluna_nova> IS NULL;                            -- idempotente: só o que falta
   COMMIT;
   ```

   O `tenant_id` vai sempre como parâmetro, nunca concatenado no SQL.
3. **Idempotente e retomável**: cada Tenant é commitado sozinho, então uma falha no meio deixa parte dos Tenants migrados. A migration precisa poder rodar de novo e só completar o que falta (o `WHERE` acima). Um Tenant grande se processa em lotes, cada lote na sua transação e com o contexto aplicado de novo.
4. **Sem contexto residual**: o contexto se aplica com `set_config(..., true)` dentro de cada transação, nunca com `SET` de sessão.
5. **Verificar**: depois do backfill, conferir Tenant a Tenant (sob o contexto de cada um) que nada ficou para trás, antes do passo de contract.

A primeira migration de dados fixa o mecanismo em código (o laço com uma transação por Tenant dentro do job de migration) e prova com um teste que um Tenant que falha não desfaz os já migrados e que uma segunda execução completa o resto.

## Segredos

- Nenhum comando deste runbook leva senha ou DSN no argumento. A conexão vem de `CCE_ENV_FILE` ou do ambiente do processo.
- A saída do Alembic e do `check_schema` não mostra DSN, senha nem o texto de erro do driver. Se algum dia mostrar, trate como incidente e troque a credencial (ver `local-development.md`, "Roles e credenciais").
- Não cole saída de migration em ticket ou Pull Request sem lê-la antes.
