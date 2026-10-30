# Desenvolvimento local

Como subir a infraestrutura local do CCE (PostgreSQL 17, Redis 7.4 e a imagem da API) e rodar os testes. Plataforma de referência: Linux com bash. Este Compose não é um manifesto de produção.

## Estado atual

Existem a infraestrutura, o schema (migrations `0001` a `0003`: `cce.tenants`, `cce.api_keys`, a RLS e a função de lookup de API key), a CLI administrativa `cce-admin` (ver `tenant-bootstrap.md`) e a API HTTP (`/health/live`, `/health/ready`, `/api/v1/auth/context` e `/api/v1/tenant`). O formato dos erros está em `docs/api/errors.md`.

## Pré-requisitos

- Docker com Compose v2.
- uv 0.12.10 (a versão fixada no ADR-0011) e Python 3.12 (`uv python install 3.12`).

```bash
uv sync --project apps/api --locked --group dev
```

## Segredos locais

O dotenv é gerado com segredos novos e nunca é commitado. O gerador recusa sobrescrever um arquivo existente e não imprime nenhum valor.

```bash
python3 scripts/init_env.py --target .env --env local --database-name cce
python3 scripts/init_env.py --target .env.test --env test --database-name cce_test
```

## Subir e verificar

```bash
docker compose up -d --wait postgres redis
docker compose ps
```

O PostgreSQL só fica saudável depois que o bootstrap terminou (roles `cce_migrator`, `cce_app` e `cce_auth`, schemas `cce` e `cce_private`). O bootstrap roda **uma única vez, na criação do volume**.

Para derrubar, preservando os dados:

```bash
docker compose down
```

`docker compose down -v` apaga os volumes e todos os dados do ambiente. Use só em um ambiente descartável, como o de teste. Não é a forma de corrigir uma configuração.

## Migrations

O schema só é criado por migrations (Alembic), executadas como `cce_migrator` e com `NullPool`; `Base.metadata` nunca cria tabelas. O DSN vem do dotenv (`CCE_ENV_FILE`), não de argumento nem de `alembic.ini`.

```bash
CCE_ENV_FILE=.env uv run --project apps/api --locked alembic -c alembic.ini upgrade head
CCE_ENV_FILE=.env uv run --project apps/api --locked alembic -c alembic.ini check
```

Pelo Compose, o serviço `migrate` roda `alembic upgrade head` uma vez e termina; a `api` só inicia depois que ele conclui com sucesso:

```bash
docker compose run --rm --no-deps migrate
```

As tabelas pertencem a `cce_migrator`. A migration `0002` ativa a RLS e dá ao runtime o mínimo; a `0003` cria a função de lookup de API key e dá a `cce_auth` a leitura de colunas que ela usa.

## Ambiente de teste

O ambiente de teste usa o dotenv `.env.test`, o banco `cce_test` e o **projeto Compose `cce-test`**, com volumes próprios. Passe sempre `-p cce-test` junto com `--env-file .env.test`; sem o `-p`, o Compose usaria o projeto `cce` e os volumes do ambiente local.

```bash
docker compose --env-file .env.test -p cce-test up -d --wait postgres redis
CCE_ENV_FILE=.env.test uv run --project apps/api --locked pytest tests/migrations
CCE_ENV_FILE=.env.test uv run --project apps/api --locked pytest tests/integration
docker compose --env-file .env.test -p cce-test down -v
```

Os dois ambientes publicam as mesmas portas em loopback (5432 e 6379). Não suba os dois ao mesmo tempo.

Os testes de integração e de migration recusam rodar fora de `CCE_ENV=test` e de um banco `cce_test*` (o nome é validado parseando a URL, e a recusa nunca mostra o DSN). Os de migration fazem `downgrade base` e `upgrade` no banco de teste, por isso rodam antes e separados dos demais. Eles usam conexões reais como `cce_app` e `cce_migrator`. O teste da imagem constrói `cce-api:test` e o remove no fim.

## API

```bash
docker compose up -d --wait api          # sobe postgres, redis, roda migrate e espera a API
curl -s http://127.0.0.1:8000/health/live
curl -s http://127.0.0.1:8000/health/ready
```

A API escuta só em `127.0.0.1:8000`. O Swagger fica em `/docs` quando `CCE_DOCS_ENABLED=true` (padrão em `local` e `test`; a configuração recusa ativá-lo em staging e production). CORS só responde às origins exatas de `CCE_ALLOWED_ORIGINS`, sem credenciais.

### Smoke com dois Tenants

Crie dois Tenants com `cce-admin tenant create` (ver `tenant-bootstrap.md`) e passe as chaves iniciais pelo ambiente, nunca como argumento:

```bash
read -rs CCE_SMOKE_KEY_A && read -rs CCE_SMOKE_KEY_B && export CCE_SMOKE_KEY_A CCE_SMOKE_KEY_B
python3 scripts/smoke.py   # CCE_SMOKE_BASE_URL, padrão http://127.0.0.1:8000
```

O smoke confere liveness, readiness, o `401` sem chave, que as chaves são de Tenants distintos, que `X-Tenant-ID` é ignorado, que o `PATCH` de A não altera B e que um campo proibido dá `422`. Ele renomeia o Tenant A. A saída é uma linha por etapa e nunca mostra chave nem corpo de resposta. O teste `tests/integration/test_smoke.py` faz o mesmo no projeto `cce-test`: constrói a imagem, sobe a API pelo Compose, emite as chaves e roda o smoke.

## Roles e credenciais

| Role | Login | Uso |
|---|:---:|---|
| `postgres` | sim | Bootstrap e infraestrutura local; nunca a API |
| `cce_migrator` | sim | Dono dos schemas; migrations e CLI administrativa |
| `cce_app` | sim | Runtime da API |
| `cce_auth` | não | Dono da função de lookup de API key (`cce_private.lookup_api_key`, migration `0003`) |

O serviço `api` recebe só a credencial de `cce_app`. A URL do migrator e a senha do `postgres` ficam fora dele.

Alterar uma senha no dotenv **não** altera o role existente, e o bootstrap nunca rotaciona senhas. Para trocar uma senha sem apagar dados, altere o role e depois o dotenv:

```bash
docker compose exec postgres sh -c 'psql -U postgres -d "$POSTGRES_DB"'
# no prompt do psql, sem a senha na linha de comando nem no histórico do shell:
#   \password cce_app
```

Reaplicar atributos e grants do bootstrap (não altera senhas):

```bash
docker compose exec postgres /docker-entrypoint-initdb.d/01-bootstrap.sh
```

Funções criadas por `cce_migrator` e `cce_auth` não são executáveis por `PUBLIC`. Cada função que `cce_app` precise executar exige um `GRANT EXECUTE` explícito na migration que a cria.

## Imagem da API

```bash
docker compose build migrate   # a mesma imagem `cce-api:local` serve a API e o job de migration
```

A imagem roda como usuário `10001`, não contém dotenv, `uv` nem dependências de desenvolvimento, e instala o pacote `cce` sem o código-fonte. Leva também o `alembic.ini` e as migrations, para o serviço `migrate`.

## Qualidade

```bash
uv run --project apps/api --locked ruff check --config apps/api/pyproject.toml apps/api/src tests scripts
uv run --project apps/api --locked ruff format --check --config apps/api/pyproject.toml apps/api/src tests scripts
uv run --project apps/api --locked mypy --config-file apps/api/pyproject.toml apps/api/src tests scripts
uv run --project apps/api --locked pytest tests/unit
```
