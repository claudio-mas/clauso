# Technical Implementation Plan — Milestone 0: Foundation

> **Para execução assistida por agente:** implementar tarefa por tarefa com superpowers:executing-plans; usar superpowers:subagent-driven-development somente se o usuário escolher subagentes. Caixas de seleção registram trabalho futuro, não executado.

**Goal:** entregar uma base reproduzível do Commercial Closing Engine, com FastAPI, PostgreSQL/RLS, tenants isolados, API keys revogáveis, Redis, observabilidade inicial, testes reais e pipeline de entrega.

**Architecture:** monólito modular no monorepositório do Blueprint, com backend Python em apps/api. A API usa credenciais limitadas; migrations e provisionamento usam processo administrativo separado. Toda operação empresarial ocorre numa transação com contexto local e autorização derivada da API key verificada.

**Tech Stack:** Python 3.12; FastAPI; Pydantic 2; SQLAlchemy 2 async; asyncpg; Alembic; PostgreSQL 17; Redis 7.4; uv; pytest; Ruff; mypy; Docker Compose; GitHub Actions.

**Spec:** [Product & Technical Blueprint v1](Product_Technical_Blueprint_v1.md), especialmente seções 38, 41–55, 69–76 e 83–93.

**Versão:** 1.0  
**Data:** 14/10/2026  
**Status:** plano pronto para implementação e revisão; código e pipelines ainda não executados.

> **Nota de revisão (15/10/2026).** Este plano não foi reescrito. Onde ele diverge dos tickets em `.scratch/milestone-0-foundation/issues/`, **os tickets prevalecem**. Divergências conhecidas:
>
> 1. **Raiz do repositório.** A árvore da seção 3 é criada na raiz deste repositório, ao lado de `CONTEXT.md` e `docs/`, e não numa pasta `commercial-closing-engine/`.
> 2. **ADRs.** Os arquivos usam quatro dígitos (`docs/adr/0001-modular-monolith.md`, não `001-...`). Os ADRs `0001`–`0010` já existem, escritos a partir do Blueprint. Os `0011`–`0017` da seção 16 são escritos junto com o ticket que toma cada decisão (01, 05, 06, 07, 08 e 10), não todos no ticket 11. Existem também os ADRs `0018`–`0025`.
> 3. **Escopo reduzido.** O release com publicação no GHCR (seção 15.2), o contrato de CD (seção 15.3), os runbooks `deployment.md` e `backup-restore.md`, o ensaio de restore (Tarefa 9 e seção 18) e os itens correspondentes da Definition of Done (seção 20) saíram do M0 e foram para o ticket 12, bloqueado pela decisão de hospedagem. No M0 não existe `.github/workflows/release.yml`, e há um runbook novo, `branch-protection.md`.
> 4. **Fluxo por Pull Request.** Toda mudança chega à `main` por PR, com a CI rodando na PR antes do merge (`docs/agents/workflow.md`). O merge local com `git merge --no-ff` não é mais usado.
> 5. **Plataforma de referência.** Linux com bash, igual à CI. Os comandos em PowerShell deste plano valem como equivalentes para Windows; scripts e runbooks novos são escritos para bash.
> 6. **Migrations de dados.** Como a RLS é forçada também para o `cce_migrator`, backfills percorrem os Tenants um a um (ADR-0025). O runbook de migrations (ticket 09) descreve o padrão.
> 7. **Versões.** As faixas da seção 4.1 são piso. Em 15/10/2026, `redis>=6,<8` excluía a versão atual do redis-py (8.x) e o uv já estava na 0.13. A Tarefa 1 decide se amplia as faixas e registra a escolha no ADR-011.

## Global Constraints

- Preservar a estrutura do Blueprint: apps, packages, infrastructure, docs e tests.
- Backend Python/FastAPI; domínio separado de transporte e providers.
- Shared DB + tenant_id + PostgreSQL RLS; sem database/schema por empresa.
- Prefixo HTTP canônico /api/v1. Exemplos /v1 ou sem prefixo são abreviações.
- IDs públicos com prefixos; UUID interno. M0 usa UUID4 e ten_<32 hex>; UUIDv7 é opção futura.
- Ambientes local, test, staging e production.
- Migrations executadas por job separado, nunca no startup nem por create_all.
- API keys servidor a servidor, scopes, expiração e revogação; sem segredos nos logs.
- Testes RLS em PostgreSQL real, como role runtime não proprietária e sem BYPASSRLS.
- Propostas, frontend, LLM, PSP, assinatura, SDK, outbox e workers de negócio ficam fora do M0.
- Redis é auxiliar; estado comercial e autorização durável pertencem ao PostgreSQL.
- Intervalos de dependências abaixo são decisões do projeto, não alegações de versões mais recentes. O lockfile gerado na implementação fixa versões exatas.

## Review Focus

1. **Contexto residual no pool:** request B, erro e cancelamento não podem reutilizar tenant A; tarefas 5 e 7 testam pool de uma conexão e concorrência.
2. **Autenticação antes do tenant:** lookup não exige tenant enviado pelo cliente nem libera listagem global; tarefa 6 verifica função e privilégios.
3. **Falso positivo de isolamento:** superusuário e SQLite mascaram falhas; tarefas 3 e 5 verificam roles, ownership, policies e DML real.
4. **Commit após resposta de sucesso:** HTTP só responde depois do término da transação; tarefa 7 injeta falha de flush/commit.
5. **Configuração indevida:** chave de outro ambiente, segredo ausente, CORS aberto e DSN administrativo no container API precisam falhar; tarefas 1, 2, 6 e 10 cobrem esses casos.

---

## 1. Resultado verificável e fronteira

Ao final do M0, um desenvolvedor consegue:

1. Clonar o repositório e gerar segredos locais automaticamente.
2. Subir PostgreSQL, Redis e API com Docker Compose.
3. Aplicar migrations por um serviço de execução única.
4. Provisionar empresas A/B e suas chaves pela CLI administrativa.
5. Consultar e alterar somente os dados permitidos da própria empresa.
6. Demonstrar isolamento com ausência/troca indevida de contexto.
7. Revogar uma chave e observar a próxima autenticação recusada.
8. Correlacionar respostas e logs por request_id e trace_id.
9. Executar análise de código, testes reais e build na CI.
10. Produzir imagem versionada e procedimento de promoção.

O M0 entrega uma fatia operacional da infraestrutura, não o Golden Path comercial. Preservar os marcos do Blueprint: M1 Proposal Domain; M2 Publication; M3 Reference Integration; M4 Engagement; M5 Closing; M6 AI. MVPs agrupam capacidades e não substituem essa numeração.

A composição local completa com MinIO, Mailpit, workers e frontend será expandida quando surgirem consumidores. Nenhum desses serviços é necessário no Foundation.

## 2. Decisões de implementação

| Tema | Decisão M0 |
|---|---|
| Python | Um pacote instalável cce-api em apps/api; import cce |
| Dependências | uv; pyproject.toml e uv.lock em apps/api |
| Runtime | CPython 3.12 em local, CI e imagem |
| Banco | PostgreSQL 17; schema técnico cce compartilhado |
| Schema privado | cce_private contém funções; não é schema por tenant |
| I/O | AsyncEngine e AsyncSession com asyncpg |
| Transação | Uma transação de dados por request; repository não commita |
| Autenticação | Authorization: Bearer <api_key> |
| Provisionamento | CLI; sem signup ou chave global aceita pela API |
| Scopes | tenant:read e tenant:write |
| API | GET/PATCH /api/v1/tenant; GET /api/v1/auth/context |
| IDs | UUID4 no banco; ten_ e key_ na serialização |
| Tempo | UTC no banco; timezone do tenant para apresentação |
| Redis | Health/readiness; namespaces reservados para cache/broker futuro |
| Logs | JSON com structlog e contexto por request |
| Entrega | CI, imagem imutável e job de migration |

Tenant representa a empresa isolada. Um fabricante de ERP com várias empresas independentes normalmente integra um tenant CCE por empresa. Uma hierarquia de parceiros será decisão posterior; nenhum header pode selecionar outra empresa.

UUID4 atende ao UUID interno permitido pelo Blueprint sem biblioteca adicional para UUIDv7 no Python 3.12. A API não promete a versão interna do UUID.

## 3. Estrutura exata do repositório ao final do M0

Diretórios futuros possuem README de escopo. Não criar módulos de negócio vazios.

~~~text
commercial-closing-engine/
├── .editorconfig
├── .gitignore
├── .dockerignore
├── .env.example
├── .python-version
├── README.md
├── docker-compose.yml
├── alembic.ini
├── pytest.ini
├── .github/workflows/
│   ├── ci.yml
│   └── release.yml
├── apps/
│   ├── api/
│   │   ├── pyproject.toml
│   │   ├── uv.lock
│   │   └── src/cce/
│   │       ├── __init__.py
│   │       ├── main.py
│   │       ├── api/
│   │       │   ├── __init__.py
│   │       │   ├── router.py
│   │       │   ├── dependencies.py
│   │       │   ├── errors.py
│   │       │   ├── middleware.py
│   │       │   └── health.py
│   │       ├── shared/
│   │       │   ├── __init__.py
│   │       │   ├── config.py
│   │       │   ├── ids.py
│   │       │   ├── logging.py
│   │       │   ├── security.py
│   │       │   ├── redis.py
│   │       │   └── db/
│   │       │       ├── __init__.py
│   │       │       ├── base.py
│   │       │       ├── engine.py
│   │       │       ├── models.py
│   │       │       ├── rls.py
│   │       │       └── session.py
│   │       ├── identity/
│   │       │   ├── __init__.py
│   │       │   └── tenants/
│   │       │       ├── __init__.py
│   │       │       ├── model.py
│   │       │       ├── schemas.py
│   │       │       ├── repository.py
│   │       │       ├── service.py
│   │       │       └── routes.py
│   │       ├── integrations/
│   │       │   ├── __init__.py
│   │       │   └── api_keys/
│   │       │       ├── __init__.py
│   │       │       ├── model.py
│   │       │       ├── scopes.py
│   │       │       ├── repository.py
│   │       │       ├── service.py
│   │       │       └── authentication.py
│   │       └── admin/
│   │           ├── __init__.py
│   │           ├── cli.py
│   │           └── bootstrap.py
│   ├── admin-web/README.md
│   └── proposal-web/README.md
├── packages/
│   ├── sdk-python/README.md
│   ├── sdk-js/README.md
│   ├── ui/README.md
│   └── schemas/README.md
├── infrastructure/
│   ├── docker/
│   │   ├── api.Dockerfile
│   │   └── postgres/
│   │       ├── 01-bootstrap.sh
│   │       └── bootstrap.sql
│   └── migrations/
│       ├── env.py
│       ├── script.py.mako
│       └── versions/
│           ├── 0001_tenants_api_keys.py
│           ├── 0002_tenant_rls.py
│           └── 0003_auth_lookup.py
├── scripts/
│   ├── init_env.py
│   ├── check_schema.py
│   ├── smoke.py
│   └── ci.sh
├── tests/
│   ├── conftest.py
│   ├── factories.py
│   ├── unit/
│   │   ├── test_config.py
│   │   ├── test_ids.py
│   │   ├── test_key_security.py
│   │   ├── test_logging.py
│   │   └── test_tenant_validation.py
│   ├── integration/
│   │   ├── test_db_roles.py
│   │   ├── test_rls.py
│   │   ├── test_auth_lookup.py
│   │   ├── test_authentication.py
│   │   ├── test_transactions.py
│   │   ├── test_tenant_api.py
│   │   ├── test_health.py
│   │   └── test_bootstrap.py
│   ├── contracts/test_openapi.py
│   └── migrations/test_migration_chain.py
└── docs/
    ├── architecture/
    │   ├── context.md
    │   ├── foundation.md
    │   └── security-model.md
    ├── api/
    │   ├── authentication.md
    │   └── errors.md
    ├── adr/
    │   ├── README.md
    │   ├── 001-modular-monolith.md
    │   ├── 002-postgresql.md
    │   ├── 003-shared-multi-tenant-rls.md
    │   ├── 004-api-first.md
    │   ├── 005-immutable-accepted-proposal.md
    │   ├── 006-structured-ai.md
    │   ├── 007-provider-abstraction.md
    │   ├── 008-transactional-outbox.md
    │   ├── 009-react-next-renderer.md
    │   ├── 010-fastapi-core.md
    │   ├── 011-python-uv.md
    │   ├── 012-api-key-bootstrap.md
    │   ├── 013-transaction-tenant-context.md
    │   ├── 014-public-ids-api-errors.md
    │   ├── 015-redis-role.md
    │   ├── 016-observability.md
    │   └── 017-delivery-environments.md
    └── runbooks/
        ├── local-development.md
        ├── tenant-bootstrap.md
        ├── migrations.md
        ├── secrets-rotation.md
        ├── deployment.md
        └── backup-restore.md
~~~

Gerados e ignorados: .env, .env.test, .env.ci, apps/api/.venv, coverage.xml, .coverage, htmlcov, dist e caches. uv.lock é gerado e versionado. Segredos nunca são commitados.

O import cce pertence ao backend no M0. O SDK futuro deve decidir nome de distribuição/import antes de coexistir no mesmo ambiente.

## 4. Python e dependências

### 4.1 apps/api/pyproject.toml

~~~toml
[project]
name = "cce-api"
version = "0.1.0"
description = "Commercial Closing Engine API"
requires-python = ">=3.12,<3.13"
dependencies = [
  "fastapi>=0.121,<1",
  "uvicorn[standard]>=0.34,<1",
  "pydantic>=2.11,<3",
  "pydantic-settings>=2.8,<3",
  "sqlalchemy[asyncio]>=2.0.40,<2.1",
  "alembic>=1.15,<2",
  "asyncpg>=0.30,<1",
  "redis>=6,<8",
  "structlog>=25,<27",
  "typer>=0.15,<1",
  "tzdata>=2025.2",
]

[project.scripts]
cce-admin = "cce.admin.cli:app"

[dependency-groups]
dev = [
  "pytest>=8.3,<10",
  "pytest-asyncio>=1,<2",
  "pytest-cov>=6,<8",
  "httpx>=0.28,<1",
  "asgi-lifespan>=2.1,<3",
  "ruff>=0.11,<1",
  "mypy>=1.15,<2",
  "pip-audit>=2.9,<3",
]

[build-system]
requires = ["hatchling>=1.27,<2"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/cce"]

[tool.ruff]
target-version = "py312"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "ASYNC"]

[tool.mypy]
python_version = "3.12"
strict = true
plugins = ["pydantic.mypy"]

[tool.coverage.run]
branch = true
source = ["cce"]

[tool.coverage.report]
fail_under = 85
show_missing = true
~~~

Não instalar Celery, SDKs de IA/pagamento, Next.js ou Playwright no M0. Argon2id será incluído quando houver senhas humanas; digest de API key aleatória é outro mecanismo.

.python-version contém 3.12. Baseline de ferramenta: uv 0.12.10 em local, Docker e CI, registrada no ADR-011 e atualizada somente em alteração revisada.

Comandos na raiz:

~~~powershell
uv python install 3.12
uv lock --project apps/api
uv sync --project apps/api --locked --group dev
uv run --project apps/api --locked python -c "import cce; import fastapi"
~~~

A primeira resolução gera apps/api/uv.lock. CI usa uv lock --check e uv sync --locked, sem reescrever o lock. Ver [lockfile do uv](https://docs.astral.sh/uv/guides/projects/) e [locked versus frozen](https://docs.astral.sh/uv/concepts/projects/sync/).

### 4.2 pytest.ini

~~~ini
[pytest]
testpaths = tests
asyncio_mode = auto
asyncio_default_fixture_loop_scope = function
addopts = -ra --strict-markers
markers =
    integration: requer PostgreSQL e Redis reais
    migration: altera schema do banco descartavel
~~~

Fixtures async de escopo function evitam compartilhar pools entre event loops. Não usar pytest-xdist no M0; migration tests rodam numa etapa própria.

## 5. Ambientes e configuração

### 5.1 Interfaces de settings

Em shared/config.py definir:

- Environment = Literal["local", "test", "staging", "production"].
- Settings: API, DSN runtime, Redis, logs e peppers de API key.
- MigrationSettings: ambiente, DSN migrator e configuração de CA/TLS do banco.
- AdminSettings: ambiente, DSN administrativo, CA/TLS e material de emissão de chaves.
- load_settings() -> Settings.
- load_migration_settings() -> MigrationSettings.
- load_admin_settings() -> AdminSettings.

Usar BaseSettings e SettingsConfigDict com env_prefix="CCE_", case_sensitive=False e extra="ignore" para o dotenv local compartilhado. Validar os campos críticos conhecidos. A API instancia somente Settings, sem engine migrator.

Segredos são SecretStr, incluindo valores do mapa de peppers. Erros de settings mostram nomes dos campos sem o valor recebido. Não imprimir ValidationError completo nem model_dump com segredos.

### 5.2 Variáveis

| Variável | Tipo / default | Validação |
|---|---|---|
| CCE_ENV | local | Enum dos quatro ambientes |
| CCE_ENV_FILE | .env em local/test | Dotenv proibido em staging/production |
| CCE_SERVICE_NAME | cce-api | Texto fixo |
| CCE_VERSION | 0.1.0 | Release/commit do build |
| CCE_LOG_LEVEL | INFO | DEBUG/INFO/WARNING/ERROR |
| CCE_LOG_FORMAT | json | console permitido somente local |
| CCE_DATABASE_URL | obrigatório | PostgreSQL asyncpg; usuário cce_app |
| CCE_MIGRATOR_DATABASE_URL | somente admin/migration | Usuário cce_migrator |
| CCE_DATABASE_CA_FILE | staging/production | Arquivo de CA legível; validação de hostname |
| CCE_REDIS_URL | obrigatório | redis:// local/test; rediss:// staging/production |
| CCE_REDIS_CA_FILE | opcional | CA privada de Redis, se a plataforma não usar CA pública |
| CCE_DB_POOL_SIZE | 5 | 1–20 por processo |
| CCE_DB_MAX_OVERFLOW | 5 | 0–20 |
| CCE_DB_POOL_TIMEOUT | 5 | Segundos positivos |
| CCE_DB_COMMAND_TIMEOUT | 5 | Segundos positivos |
| CCE_ALLOWED_ORIGINS | [] | JSON com origins exatas; wildcard proibido |
| CCE_API_KEY_PEPPERS | obrigatório | JSON version→base64 de ≥32 bytes aleatórios |
| CCE_API_KEY_ACTIVE_PEPPER_VERSION | 1 | Precisa existir no mapa |
| CCE_API_KEY_TTL_DAYS | 90 | 1–365 |
| CCE_DOCS_ENABLED | true local/test | false staging/production no M0 |
| CCE_POSTGRES_DB | cce | Testes exigem prefixo cce_test |
| CCE_POSTGRES_ADMIN_PASSWORD | gerado | Somente container PostgreSQL |
| CCE_APP_DB_PASSWORD | gerado | Bootstrap runtime |
| CCE_MIGRATOR_DB_PASSWORD | gerado | Bootstrap migrator |

Os quatro últimos são configuração de infraestrutura, não campos runtime. init_env.py gera DSNs coerentes com as senhas.

Em staging/production criar SSLContext com CA confiável e check_hostname=True, passado a asyncpg por connect_args. Não desativar verificação TLS. A plataforma monta a CA; a imagem não contém certificados privados nem segredos. Local/test permitem conexão sem TLS na rede Compose/loopback.

O loader verifica o ambiente final após carregar configurações: se staging/production tiver sido obtido de dotenv, recusar a configuração. Para Redis rediss, exigir validação de certificado e hostname; usar a CA privada configurada quando necessária. Não passar opções SSL a conexões redis:// locais.

### 5.3 .env.example

~~~dotenv
CCE_ENV=local
CCE_SERVICE_NAME=cce-api
CCE_VERSION=0.1.0
CCE_LOG_LEVEL=INFO
CCE_LOG_FORMAT=json
CCE_POSTGRES_DB=cce
CCE_POSTGRES_ADMIN_PASSWORD=__POSTGRES_PASSWORD__
CCE_APP_DB_PASSWORD=__APP_PASSWORD__
CCE_MIGRATOR_DB_PASSWORD=__MIGRATOR_PASSWORD__
CCE_DATABASE_URL=postgresql+asyncpg://cce_app:__APP_PASSWORD__@127.0.0.1:5432/cce
CCE_MIGRATOR_DATABASE_URL=postgresql+asyncpg://cce_migrator:__MIGRATOR_PASSWORD__@127.0.0.1:5432/cce
CCE_REDIS_URL=redis://127.0.0.1:6379/0
CCE_ALLOWED_ORIGINS=[]
CCE_API_KEY_PEPPERS='{"1":"__PEPPER_BASE64__"}'
CCE_API_KEY_ACTIVE_PEPPER_VERSION=1
CCE_API_KEY_TTL_DAYS=90
CCE_DOCS_ENABLED=true
~~~

scripts/init_env.py troca somente os marcadores explícitos usando secrets.token_hex(32) para cada senha e base64 de secrets.token_bytes(32) para pepper. Recebe --target, --env e --database-name; aceita somente local/test; recusa sobrescrever arquivo existente; não imprime valores. Em POSIX criar com 0600; em Windows manter no diretório privado do usuário.

~~~powershell
python scripts/init_env.py --target .env --env local --database-name cce
python scripts/init_env.py --target .env.test --env test --database-name cce_test
~~~

.env.test usa projeto Compose separado e banco cce_test. Não iniciar simultaneamente duas composições nas mesmas portas. Staging/production recebem segredos da plataforma, sem reutilizar dotenv local.

## 6. Docker e Compose

### 6.1 infrastructure/docker/api.Dockerfile

~~~dockerfile
FROM ghcr.io/astral-sh/uv:0.12.10 AS uvbin
FROM python:3.12-slim-bookworm AS builder
COPY --from=uvbin /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=0 UV_COMPILE_BYTECODE=1
WORKDIR /app
COPY apps/api/pyproject.toml apps/api/uv.lock ./apps/api/
RUN uv sync --project apps/api --frozen --no-dev --no-install-project
COPY apps/api/src ./apps/api/src
RUN uv sync --project apps/api --frozen --no-dev --no-editable

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PATH="/app/apps/api/.venv/bin:$PATH"
RUN groupadd --gid 10001 cce \
    && useradd --uid 10001 --gid 10001 --no-create-home cce
WORKDIR /app
COPY --from=builder --chown=10001:10001 /app/apps/api/.venv ./apps/api/.venv
COPY --chown=10001:10001 alembic.ini ./
COPY --chown=10001:10001 infrastructure/migrations ./infrastructure/migrations
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "cce.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
~~~

.dockerignore exclui .git, .env*, .venv, caches, testes e outputs. Não copiar dotenv para imagem. A imagem contém pacote e Alembic, sem uv ou dependências dev; o mesmo artefato roda como API ou job de migration com configurações distintas.

Tags acima são baseline local. Antes da primeira publicação, resolver os digests das imagens-base e registrá-los na alteração revisada do pipeline. Deploy promove digest da imagem construída, nunca latest.

### 6.2 docker-compose.yml

Todas as variáveis abaixo são fornecidas pelo .env gerado. Não usar env_file: .env no serviço API.

~~~yaml
name: cce
services:
  postgres:
    image: postgres:17-bookworm
    environment:
      POSTGRES_DB: $CCE_POSTGRES_DB
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: $CCE_POSTGRES_ADMIN_PASSWORD
      CCE_APP_DB_PASSWORD: $CCE_APP_DB_PASSWORD
      CCE_MIGRATOR_DB_PASSWORD: $CCE_MIGRATOR_DB_PASSWORD
    ports:
      - "127.0.0.1:5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./infrastructure/docker/postgres/01-bootstrap.sh:/docker-entrypoint-initdb.d/01-bootstrap.sh:ro
      - ./infrastructure/docker/postgres/bootstrap.sql:/opt/cce/bootstrap.sql:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres -d \"$$POSTGRES_DB\""]
      interval: 3s
      timeout: 3s
      retries: 20

  redis:
    image: redis:7.4-bookworm
    command: ["redis-server", "--appendonly", "yes"]
    ports:
      - "127.0.0.1:6379:6379"
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 3s
      timeout: 2s
      retries: 20

  migrate:
    image: cce-api:local
    build:
      context: .
      dockerfile: infrastructure/docker/api.Dockerfile
    environment:
      CCE_ENV: $CCE_ENV
      CCE_MIGRATOR_DATABASE_URL: postgresql+asyncpg://cce_migrator:$CCE_MIGRATOR_DB_PASSWORD@postgres:5432/$CCE_POSTGRES_DB
    command: ["alembic", "-c", "alembic.ini", "upgrade", "head"]
    depends_on:
      postgres:
        condition: service_healthy
    restart: "no"
    read_only: true
    tmpfs: ["/tmp"]

  api:
    image: cce-api:local
    build:
      context: .
      dockerfile: infrastructure/docker/api.Dockerfile
    environment:
      CCE_ENV: $CCE_ENV
      CCE_VERSION: $CCE_VERSION
      CCE_LOG_LEVEL: $CCE_LOG_LEVEL
      CCE_LOG_FORMAT: json
      CCE_DATABASE_URL: postgresql+asyncpg://cce_app:$CCE_APP_DB_PASSWORD@postgres:5432/$CCE_POSTGRES_DB
      CCE_REDIS_URL: redis://redis:6379/0
      CCE_ALLOWED_ORIGINS: $CCE_ALLOWED_ORIGINS
      CCE_API_KEY_PEPPERS: $CCE_API_KEY_PEPPERS
      CCE_API_KEY_ACTIVE_PEPPER_VERSION: $CCE_API_KEY_ACTIVE_PEPPER_VERSION
      CCE_API_KEY_TTL_DAYS: $CCE_API_KEY_TTL_DAYS
      CCE_DOCS_ENABLED: $CCE_DOCS_ENABLED
    ports:
      - "127.0.0.1:8000:8000"
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
      migrate:
        condition: service_completed_successfully
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2)"]
      interval: 10s
      timeout: 3s
      retries: 3
    read_only: true
    tmpfs: ["/tmp"]
    security_opt: ["no-new-privileges:true"]

volumes:
  postgres_data:
  redis_data:
~~~

O serviço migrate termina com código 0 antes de iniciar api. O [Docker Compose](https://docs.docker.com/compose/how-tos/startup-order/) diferencia readiness das dependências e conclusão de serviços de execução única. Não executar migrations no startup do FastAPI.

O bootstrap PostgreSQL roda somente na criação do volume. Alterar arquivo/senha em dotenv não modifica roles existentes. O runbook orienta alteração explícita de credenciais, sem recomendar apagar dados para corrigir configuração.

Redis local não tem autenticação e é publicado somente em loopback para testes. Staging/production exigem rede privada, ACL mínima, TLS e segredo dedicado; este Compose não é manifesto de produção.

## 7. PostgreSQL: roles, schemas e bootstrap

### 7.1 Privilégios

| Role | LOGIN | Owner de tabelas | BYPASSRLS | Uso |
|---|:---:|:---:|:---:|---|
| postgres | sim, infraestrutura local | administração | sim | Bootstrap; nunca API |
| cce_migrator | sim | sim | não | DDL, migrations e CLI |
| cce_app | sim | não | não | Runtime |
| cce_auth | não | não | não | Função de lookup SECURITY DEFINER |

Roles do produto: NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT. cce_auth também NOLOGIN. cce_app não é membro de nenhuma das outras roles. cce_migrator pode assumir cce_auth para administrar a função, mas não herda sua policy global.

cce_auth recebe SELECT somente nas colunas necessárias à autenticação. Sua leitura global é uma exceção pequena, sem login e acessível ao runtime apenas por função parametrizada. Nenhum role usa BYPASSRLS para resolver o bootstrap de credenciais.

### 7.2 Bootstrap

01-bootstrap.sh: LF, shebang /bin/sh, set -eu e psql com ON_ERROR_STOP=1. Usar POSTGRES_DB e variáveis psql app_password, migrator_password e db_name, sem concatenar segredos no SQL.

bootstrap.sql deve:

1. Criar roles ausentes via SELECT format(...) seguido de \gexec.
2. Usar interpolação psql segura :'app_password', :'migrator_password' e :"db_name"; não interpolar dentro de DO dollar-quoted.
3. Aplicar os atributos da matriz; conceder cce_auth somente a cce_migrator.
4. Criar schemas cce e cce_private com owner cce_migrator.
5. Revogar CREATE em public e privilégios do database de PUBLIC.
6. Conceder CONNECT a cce_app/cce_migrator, USAGE em cce a cce_app/cce_auth e USAGE em cce_private a cce_app.
7. Conceder USAGE, CREATE em cce_private a cce_auth para ownership da função; nunca ao cce_app.
8. Retirar EXECUTE de PUBLIC nos default privileges de funções de cce_migrator e cce_auth em cce_private.
9. Fixar search_path como pg_catalog,cce; queries de infraestrutura qualificam seus objetos.
10. Definir statement_timeout=5s, lock_timeout=2s e idle_in_transaction_session_timeout=10s no role runtime.

Bootstrap pode reaplicar atributos/grants, mas não rotaciona senhas silenciosamente. Staging/production provisionam roles por administrador/IaC. Migrations não recebem superusuário.

Aceite: pg_roles e pg_auth_members confirmam a matriz; SET ROLE cce_migrator e CREATE TABLE falham como cce_app com insufficient_privilege.


## 8. SQLAlchemy: modelos e engine

### 8.1 Convenções

shared/db/base.py define Base(DeclarativeBase), metadata com schema="cce" e naming_convention:

~~~python
{
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
~~~

Declarar constraints CHECK com nome explícito. Usar Mapped[T], mapped_column, UUID(as_uuid=True), DateTime(timezone=True), ARRAY(Text) e LargeBinary. Evitar lazy loading em respostas.

shared/db/models.py importa Tenant e APIKey para registrar ambos no metadata; não cria engine nem acessa banco ao importar. Alembic usa esse registro. Não usar Base.metadata.create_all para criar ambiente ou testes de integração.

### 8.2 Tenant

Arquivo identity/tenants/model.py; tabela cce.tenants:

| Campo | Tipo | Regra |
|---|---|---|
| id | UUID | PK; uuid4 na aplicação |
| name | varchar(200) | NOT NULL; 1–200 após strip |
| slug | varchar(63) | NOT NULL, UNIQUE; minúsculo com hífens |
| status | varchar(16) | active, suspended ou archived |
| plan | varchar(50) | development no bootstrap; sem billing no M0 |
| default_currency | varchar(3) | BRL; M0 aceita BRL/USD/EUR |
| timezone | varchar(64) | America/Sao_Paulo; validar ZoneInfo |
| locale | varchar(16) | pt-BR; M0 aceita pt-BR/en-US |
| created_at | timestamptz | NOT NULL; server default now() |
| updated_at | timestamptz | NOT NULL; atualização explícita no serviço |

Slug: regex ^[a-z0-9]+(?:-[a-z0-9]+)*$ e tamanho 1–63. Status tem CHECK no banco. Moeda tem CHECK da lista inicial. Locale/timezone são configurações de apresentação, não mudam o armazenamento UTC.

A raiz Tenant não possui tenant_id: sua própria coluna id é a fronteira da policy. Todas as entidades empresariais dependentes, começando por APIKey, possuem tenant_id obrigatório.

API schemas:

- TenantRead: id público ten_..., name, slug, status, plan, default_currency, timezone, locale, created_at, updated_at.
- TenantPatch: somente name, default_currency, timezone e locale opcionais; pelo menos um campo; extra="forbid".
- TenantCreate: usado somente pela CLI; name e slug obrigatórios, demais defaults acima; status inicial active.
- Não aceitar id, tenant_id, status, plan ou slug via PATCH.
- Datas JSON em ISO 8601 com offset UTC; nunca datetime sem timezone.

### 8.3 APIKey

Arquivo integrations/api_keys/model.py; tabela cce.api_keys:

| Campo | Tipo | Regra |
|---|---|---|
| id | UUID | PK; uuid4 |
| tenant_id | UUID | FK tenants.id, NOT NULL, ON DELETE RESTRICT |
| label | varchar(100) | NOT NULL |
| secret_digest | bytea | NOT NULL; CHECK octet_length(...)=32 |
| pepper_version | smallint | >0 |
| scopes | text[] | NOT NULL; subconjunto de tenant:read, tenant:write |
| created_at | timestamptz | now() |
| expires_at | timestamptz | NOT NULL; posterior a created_at |
| revoked_at | timestamptz | NULL enquanto válida |

Índice ix_api_keys_tenant_id. Scopes têm CHECK usando contenção ARRAY; não permitir string livre. Não armazenar segredo bruto, token completo ou URL que o contenha. Não expor digest em schema HTTP.

Roles runtime recebem somente leitura de colunas não secretas da APIKey; lifecycle de emissão/revogação ocorre por CLI. O lookup privilegiado da seção 11 acessa o digest por função restrita.

### 8.4 Engine e sessão

shared/db/engine.py:

- build_engine(settings: Settings) -> AsyncEngine.
- build_migration_engine(settings: MigrationSettings | AdminSettings) -> AsyncEngine.
- build_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession].

Runtime usa pool_pre_ping=True, pool_size/max_overflow/pool_timeout dos settings, echo=False e hide_parameters=True. connect_args configura command_timeout e TLS quando aplicável.

Session factory: expire_on_commit=False, autoflush=False. Cada request e cada autenticação recebem sessões próprias; nunca compartilhar AsyncSession entre tarefas concorrentes. A documentação de [asyncio do SQLAlchemy](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html) descreve o ciclo assíncrono e os limites de compartilhamento de sessão.

A migration engine usa NullPool, não pool runtime. Dispose explícito no término do processo.

## 9. Alembic e cadeia de migrations

### 9.1 Configuração

alembic.ini:

~~~ini
[alembic]
script_location = %(here)s/infrastructure/migrations
prepend_sys_path = .
~~~

Não escrever sqlalchemy.url com credenciais no arquivo. env.py lê MigrationSettings, importa shared/db/models.py e usa Base.metadata como target_metadata.

Este alembic.ini não tem seções de logging: não chamar logging.config.fileConfig do template padrão sem adicionar essas seções. Logs de migrations devem omitir parâmetros e DSNs, como o runtime.

Online: asyncio.run em uma coroutine que cria AsyncEngine com NullPool e chama connection.run_sync(do_run_migrations). O callback configura:

- include_schemas=True.
- version_table_schema="cce".
- compare_type=True.
- include_name que inclui somente schema cce, evitando objetos internos/privados fora do metadata.
- transação por migration, com DDL transacional PostgreSQL.

Offline: configurar URL diretamente, literal_binds=True, dialect_opts={"paramstyle": "named"} e o mesmo version_table_schema. Offline apenas gera SQL; não prova execução.

Nunca interpolar DSN via ConfigParser que expanda porcentagens de senha; passar a URL pela criação do engine/configuração em memória. Ver o [cookbook assíncrono do Alembic](https://alembic.sqlalchemy.org/en/latest/cookbook.html).

### 9.2 Revisões exatas

| Revision ID | Arquivo | Upgrade | Downgrade |
|---|---|---|---|
| 0001 | 0001_tenants_api_keys.py | Tabelas, constraints e índices | Remove api_keys antes de tenants |
| 0002 | 0002_tenant_rls.py | ENABLE/FORCE RLS, policies e grants limitados | Revoga grants e remove policies/RLS |
| 0003 | 0003_auth_lookup.py | Função privada, policies auth e grant da versão Alembic | Revoga EXECUTE e remove função/policies auth |

0001 tem down_revision=None; 0002 aponta para 0001; 0003 para 0002. Revisões posteriores nunca editam migrations já aplicadas em ambientes compartilhados.

cce.alembic_version é metadado de infraestrutura, sem tenant/RLS; runtime recebe somente SELECT para readiness. Grants referentes à tabela são aplicados em 0003, quando ela já existe.

RLS, funções e grants são SQL explícito dentro das migrations. Autogenerate não é autoridade sobre esses controles. O [Alembic documenta os limites de autogeração e alembic check](https://alembic.sqlalchemy.org/en/latest/autogenerate.html); scripts/check_schema.py verifica o que metadata não representa.

### 9.3 Verificação

~~~powershell
uv run --project apps/api --locked alembic -c alembic.ini upgrade head
uv run --project apps/api --locked alembic -c alembic.ini current
uv run --project apps/api --locked alembic -c alembic.ini check
uv run --project apps/api --locked python scripts/check_schema.py
~~~

current deve apontar para 0003 (head); check sem operações novas. check_schema falha se faltar FORCE RLS, policy, grant esperado ou se cce_app ganhar ownership/BYPASSRLS/membership indevido.

Downgrade de teste é permitido apenas em banco descartável cce_test*. Em produção, expand/contract e correção por nova migration; rollback de imagem não implica downgrade destrutivo.

## 10. PostgreSQL RLS e contexto transacional

### 10.1 Policies de dados

A policy é aplicada a cce_app e cce_migrator, que tem NOINHERIT. A role proprietária também está sujeita à RLS por FORCE.

~~~sql
ALTER TABLE cce.tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE cce.tenants FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON cce.tenants
TO cce_app, cce_migrator
USING (
    id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
)
WITH CHECK (
    id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
);

ALTER TABLE cce.api_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE cce.api_keys FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON cce.api_keys
TO cce_app, cce_migrator
USING (
    tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
)
WITH CHECK (
    tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
);
~~~

NULLIF cobre contexto ausente e string vazia após término de SET LOCAL. Sem contexto, SELECT não retorna dados e escrita sujeita à policy é recusada. UUID malformado causa erro e falha fechada, nunca fallback global.

Policies não substituem GRANT. Conceder ao cce_app:

- SELECT em cce.tenants.
- UPDATE somente em name, default_currency, timezone, locale e updated_at de cce.tenants.
- SELECT em id, tenant_id, label, scopes, created_at, expires_at e revoked_at de cce.api_keys.
- Sem INSERT/DELETE em tenants/api_keys; sem UPDATE de credenciais.
- Sem TRUNCATE, DDL, CREATE em schemas ou acesso direto a secret_digest.

Os detalhes de ENABLE/FORCE e exceções de owner/superusuário são descritos em [Row Security Policies](https://www.postgresql.org/docs/17/ddl-rowsecurity.html). A combinação de [USING e WITH CHECK](https://www.postgresql.org/docs/17/sql-createpolicy.html) controla visibilidade e linhas novas.

### 10.2 API de contexto

shared/db/rls.py define set_tenant_context(session: AsyncSession, tenant_id: UUID) -> None assíncrona, exigindo session.in_transaction().

~~~python
await session.execute(
    text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
    {"tenant_id": str(tenant_id)},
)
~~~

O terceiro argumento true limita a configuração à transação. Ver [set_config/current_setting](https://www.postgresql.org/docs/17/functions-admin.html). Não usar SET de sessão, SQL concatenado ou configuração apenas no checkout do pool.

shared/db/session.py define tenant_session(factory, tenant_id) como async context manager que:

1. Abre sessão.
2. Abre session.begin().
3. Aplica set_tenant_context antes de qualquer acesso a tabelas empresariais.
4. Entrega a sessão.
5. Executa flush final e deixa o context manager commitar.
6. Em exceção ou cancelamento, desfaz a transação e devolve/descarta a conexão.
7. Fecha a sessão; o reset do pool permanece habilitado.

Repositories podem flush, nunca commit/rollback. Casos de uso não abrem transações aninhadas para trocar de tenant. Trabalho de background futuro terá sessão e contexto próprios, sem capturar sessão do request.

### 10.3 Limite real da proteção

RLS protege contra omissão de filtros e acessos acidentais. Uma aplicação comprometida capaz de emitir SQL arbitrário também pode chamar set_config com outro UUID; o GUC não é prova criptográfica de identidade. O backend é uma fronteira de confiança: SQL parametrizado, API sem execução de SQL livre e autorização continuam obrigatórios.

TRUNCATE, ownership e verificações de integridade têm semânticas próprias. Não prometer sigilo absoluto contra inferência por constraints globais. M0 não expõe busca global nem criação pública de slug.

### 10.4 Prova de DML

Tenant tem grants deliberadamente estreitos. Para testar INSERT/UPDATE/DELETE sob RLS sem ampliar a produção, a fixture cria **somente no banco de teste** cce.rls_probe(id UUID, tenant_id UUID NOT NULL, value TEXT), replica a policy de tenant e concede DML ao cce_app.

Testar também as tabelas reais e seus grants. O probe prova WITH CHECK; uma operação recusada por falta de GRANT não é evidência de que a policy de escrita funcionou. Remover o probe no teardown. Ele nunca integra migrations, metadata ou imagem de produção.

## 11. API keys e autenticação antes do tenant

### 11.1 Formato e digest

Formato: cce_<environment_marker>_<uuid_hex>_<secret_base64url>.

- Marker: local→dev, test→tst, staging→stg, production→prd.
- UUID hex: 32 caracteres.
- Secret: secrets.token_urlsafe(32), 43 caracteres base64url.
- Parser por regex ancorada; não dividir irrestritamente por underscore, que pode integrar o secret.
- Cabeçalho único Authorization: Bearer; query strings e cookies não autenticam a API M0.
- Transporte HTTPS fora de local/test.

shared/security.py define:

- generate_key(key_id: UUID, environment: Environment) -> str.
- parse_key(raw_key: str, environment: Environment) -> ParsedKey.
- digest_key(raw_key: str, pepper: bytes) -> bytes.
- verify_digest(candidate: bytes, stored: bytes) -> bool.

ParsedKey contém key_id e raw_key como SecretStr; representação não expõe token. Digest: HMAC-SHA256(pepper, b"cce-api-key:v1\0" + raw_key.encode("ascii")). Comparação por hmac.compare_digest, sempre com bytes de comprimento fixo.

Peppers têm versões. O lookup retorna pepper_version, que seleciona o segredo apropriado do mapa. Pepper ausente torna a chave inválida e produz evento operacional sem segredo. Manter versões anteriores durante rotação; não é possível recalcular digest de chave antiga sem seu segredo bruto.

Chaves não são senhas humanas: segredo com 256 bits permite digest HMAC rápido. Não utilizar bcrypt/Argon2 para essa finalidade nem apresentar HMAC como mecanismo de hash de senha de usuário.

### 11.2 Solução do bootstrap de autenticação

Não buscar APIKey por tenant enviado num header. A migration 0003 concede ao role sem login cce_auth uma policy SELECT explícita e privilégios de coluna:

~~~sql
CREATE POLICY auth_lookup ON cce.tenants
FOR SELECT TO cce_auth USING (true);

CREATE POLICY auth_lookup ON cce.api_keys
FOR SELECT TO cce_auth USING (true);

GRANT SELECT (id, status) ON cce.tenants TO cce_auth;
GRANT SELECT (
    id, tenant_id, secret_digest, pepper_version,
    scopes, expires_at, revoked_at
) ON cce.api_keys TO cce_auth;

CREATE FUNCTION cce_private.lookup_api_key(p_key_id uuid)
RETURNS TABLE (
    key_id uuid,
    tenant_id uuid,
    secret_digest bytea,
    pepper_version smallint,
    scopes text[]
)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
SET row_security = on
AS $function$
    SELECT k.id, k.tenant_id, k.secret_digest, k.pepper_version, k.scopes
    FROM cce.api_keys AS k
    JOIN cce.tenants AS t ON t.id = k.tenant_id
    WHERE k.id = p_key_id
      AND k.revoked_at IS NULL
      AND k.expires_at > now()
      AND t.status = 'active';
$function$;

REVOKE ALL ON FUNCTION cce_private.lookup_api_key(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION cce_private.lookup_api_key(uuid) TO cce_app;
ALTER FUNCTION cce_private.lookup_api_key(uuid) OWNER TO cce_auth;
~~~

Ajustar ACL antes de transferir ownership, pois cce_migrator usa NOINHERIT. No downgrade da revisão 0003, executar SET LOCAL ROLE cce_auth, remover a função e executar RESET ROLE antes de remover policies/grants das tabelas como cce_migrator. Essa mudança de role ocorre somente no processo de migration; nunca na API.

A função retorna no máximo um candidato por PK; não aceita SQL, tenant, pattern ou paginação. Retorna digest somente para uso interno da autenticação; nunca criar rota HTTP que o exponha.

O definer não é superusuário, não tem BYPASSRLS nem ownership das tabelas. A leitura global vem de duas policies SELECT específicas. cce_app não pode SET ROLE cce_auth. Usar nomes qualificados e search_path fixo, conforme orientações de [SECURITY DEFINER](https://www.postgresql.org/docs/17/sql-createfunction.html).

A operação consulta um identificador de chave com entropia alta; acesso SQL arbitrário ao runtime permanece fora da fronteira protegida, conforme seção 10.3.

### 11.3 Fluxo de autenticação

authentication.py define Principal imutável: tenant_id: UUID, key_id: UUID, scopes: frozenset[str].

authenticate_api_key(raw_key, settings, session_factory) -> Principal, assíncrona:

1. Valida cabeçalho, formato e ambiente.
2. Abre sessão/transação curta, sem contexto de tenant, somente para chamar a função.
3. Busca candidato, fecha a transação e não guarda essa sessão.
4. Calcula HMAC com pepper_version e compara digest em tempo constante.
5. Para candidato ausente, executa comparação com digest sentinela; retorna o mesmo erro público.
6. Retorna Principal somente após comparação válida.
7. O request de dados abre outra sessão, aplica tenant do Principal e executa o caso de uso.

Nenhum cache positivo de chaves no M0: revogação vale na próxima autenticação. Request já autenticado pode concluir; não prometer cancelamento instantâneo de operações em andamento.

Banco indisponível → 503 DEPENDENCY_UNAVAILABLE, não 401. Ausência, formato inválido, ambiente errado, chave inexistente, expirada, revogada ou tenant suspenso → 401 AUTHENTICATION_FAILED com mensagem uniforme.

scopes.py: Scope(StrEnum) com TENANT_READ="tenant:read" e TENANT_WRITE="tenant:write". require_scope(scope: Scope) produz dependência que retorna Principal ou 403 FORBIDDEN.

Scope fica em integrations/api_keys/scopes.py; a factory de dependência require_scope é definida em api/dependencies.py. O material de autenticação não importa FastAPI.

### 11.4 CLI administrativa e rotação

admin/bootstrap.py fornece operações assíncronas:

- create_tenant(session, command: TenantCreate) -> Tenant.
- issue_api_key(session, tenant_id, label, scopes, ttl_days, settings) -> IssuedKey.
- revoke_api_key(session, tenant_id, key_id) -> bool.
- set_tenant_status(session, tenant_id, status) -> None.

Todas operam como cce_migrator em uma transação com set_tenant_context. Para criar Tenant, gerar UUID antes do INSERT e aplicar esse UUID ao contexto. Criar tenant e chave inicial na mesma transação; só mostrar a chave após commit.

admin/cli.py usa Typer; callbacks síncronos chamam asyncio.run. Comandos:

~~~powershell
uv run --project apps/api --locked cce-admin tenant create --name "Empresa A" --slug empresa-a
uv run --project apps/api --locked cce-admin api-key issue --tenant ten_<id> --label "ERP" --scope tenant:read --scope tenant:write --ttl-days 90
uv run --project apps/api --locked cce-admin api-key revoke --tenant ten_<id> --key key_<id>
uv run --project apps/api --locked cce-admin tenant suspend --tenant ten_<id>
uv run --project apps/api --locked cce-admin tenant activate --tenant ten_<id>
~~~

tenant create emite chave inicial com ambos os scopes. issue aceita somente scopes conhecidos. Retorno JSON em stdout para operação; logs JSON redigidos em stderr. Chave bruta aparece somente no resultado de emissão, uma única vez; nunca no log.

Suspender/revogar não apaga registros. Repetir revoke retorna sucesso idempotente quando já revogada; chave de outro tenant não é alterada. CLI não é uma forma de autenticação HTTP.

Rotação: emitir chave nova → atualizar ERP e testar → revogar anterior. Para pepper: distribuir mapa com versões antiga/nova → tornar nova a ativa → reemitir chaves → revogar antigas → remover pepper sem consumidores.

## 12. FastAPI e contratos HTTP

### 12.1 Application factory

main.py define create_app(settings: Settings | None = None) -> FastAPI.

lifespan cria engine, session factory, Redis e heads esperados do Alembic; armazena em app.state. No shutdown chama await redis.aclose() e await engine.dispose(), inclusive após falhas de startup parcial. Não abre conexões globais na importação.

Usar lifespan, conforme [documentação FastAPI](https://fastapi.tiangolo.com/advanced/events/). shared/redis.py cria redis.asyncio.Redis com socket_connect_timeout=1, socket_timeout=1, decode_responses=True; fechamento explícito é descrito em [redis-py asyncio](https://redis.io/docs/latest/develop/clients/redis-py/async/).

Middleware ASGI puro em api/middleware.py gera request_id="req_"+uuid4().hex e trace_id de 32 hex não zero; limpa contextvars antes e no finally. Não aceitar tenant de headers. No M0 trace_id é correlação local; exportação OpenTelemetry/distributed tracing fica documentada como evolução.

Desabilitar Swagger/OpenAPI externo em staging/production. CORS somente origins explícitas, allow_credentials=False. Como não há cookies nem UI no M0, CSRF/CSP das interfaces futuras não são simulados na API.

### 12.2 Dependência transacional

api/dependencies.py define get_principal(request), require_scope(scope) e get_tenant_session(request, principal).

get_principal usa HTTPBearer(auto_error=False) para documentar o esquema HTTP Bearer no OpenAPI e controlar o 401 uniforme. Recusar múltiplos headers Authorization. Declarar os scopes exigidos na descrição e na extensão OpenAPI x-cce-required-scopes de cada operação; não apresentar API key como fluxo OAuth2. A dependência é cacheada somente dentro do mesmo request.

As rotas usam Depends(get_tenant_session, scope="function"). Isso termina a transação antes de enviar resposta. A baseline FastAPI ≥0.121 contém esse recurso, conforme [Advanced Dependencies](https://fastapi.tiangolo.com/advanced/advanced-dependencies/).

Erro em flush/commit precisa sair como erro HTTP padronizado, sem sucesso previamente enviado. Retornar TenantRead já materializado; nenhuma lazy query após fechamento da sessão. Não usar response streaming em rotas com essa dependência.

### 12.3 Endpoints

| Método / path | Auth | Resultado |
|---|---|---|
| GET /health/live | Não | 200 se processo responde, sem checar dependências |
| GET /health/ready | Não | 200 se PostgreSQL, Redis e schema estão prontos; 503 caso contrário |
| GET /api/v1/auth/context | Chave válida | Tenant público, key ID e scopes; sem digest |
| GET /api/v1/tenant | tenant:read | Dados da empresa autenticada |
| PATCH /api/v1/tenant | tenant:write | Altera os quatro campos permitidos |

Readiness usa runtime para SELECT 1 e leitura de cce.alembic_version; compara set de revisions com ScriptDirectory.get_heads(). Redis faz PING. Timeout de 750 ms por dependência. Não retornar URLs, hostnames, senhas ou mensagens brutas de exceção.

GET /health/live: {"status":"ok"}. Ready: {"status":"ready","checks":{"postgres":"ok","redis":"ok","schema":"ok"}}; em falha usar status="not_ready", check="unavailable" e HTTP 503.

A API não possui GET /tenants nem rota por tenant_id no M0. X-Tenant-ID é ignorado e nunca muda o Principal. Campo tenant_id no PATCH é rejeitado por extra="forbid".

PATCH retorna 200 com representação atualizada. Sem campo ou com campo proibido → 422. Nome vazio, timezone inválido, moeda/locale fora da lista → 422. Conflito esperado de integridade → 409; falha inesperada → 500.

### 12.4 Repositories e serviços

identity/tenants/repository.py:

- get_current(session: AsyncSession, tenant_id: UUID) -> Tenant | None.
- update_current(session, tenant_id, changes: TenantPatch) -> Tenant | None.

Usar filtro por ID mais RLS: defesa em profundidade. Serviço valida e executa flush; repository não faz commit. TenantService.get_current / patch_current aceitam session e Principal e retornam TenantRead.

integrations/api_keys/repository.py contém lookup_candidate(session, key_id) -> KeyCandidate | None e funções de persistência usadas pela CLI. Não expõe candidate em schemas HTTP.

integrations/api_keys/service.py cuida do lifecycle, geração e digest; authentication.py cuida da verificação e Principal. shared/security.py não acessa banco.

### 12.5 Erros

Modelo da seção 69 do Blueprint:

~~~json
{
  "error": {
    "code": "AUTHENTICATION_FAILED",
    "message": "Authentication failed.",
    "details": {},
    "request_id": "req_0123456789abcdef0123456789abcdef"
  }
}
~~~

| HTTP | code |
|---|---|
| 401 | AUTHENTICATION_FAILED |
| 403 | FORBIDDEN |
| 404 | NOT_FOUND |
| 409 | CONFLICT |
| 422 | VALIDATION_ERROR |
| 500 | INTERNAL_ERROR |
| 503 | DEPENDENCY_UNAVAILABLE |

401 inclui WWW-Authenticate: Bearer. Middleware devolve X-Request-ID e X-Trace-ID inclusive em erros. Handler de validação remove input/contexto que contenha dados recebidos; details pode informar somente campo e tipo da violação.

IntegrityError é traduzido por constraint conhecida; não retornar SQL, nomes internos de tabelas ou parâmetros. Erros inesperados registram tipo e frames de stack sem locals/mensagem arbitrária.

## 13. Logging, segurança operacional e Redis

### 13.1 Logging

shared/logging.py define configure_logging(settings) e processor de redação recursiva. Campos padrão: timestamp UTC, level, event, service, version, environment, request_id, trace_id, tenant_id quando autenticado, key_id, route template, status_code e duration_ms.

Não registrar Authorization, cookies, API keys, peppers, DSNs, bodies, query string, nome/documento/e-mail de clientes ou URLs públicas completas. Desabilitar access log padrão do Uvicorn para evitar registrar path/token livre. Usar template da rota, ou "unmatched" para 404.

Logs de falhas não usam repr de settings, secret objects ou exceções de banco. Processar chaves sensíveis como password, token, secret, authorization, api_key e secret_digest, mas não confiar só em nomes: eventos aceitam campos por allowlist.

Registrar tenant.created, tenant.suspended, tenant.activated, api_key.issued e api_key.revoked com IDs, actor operacional e resultado; sem valores secretos. São registros operacionais de segurança. O AuditEvent de domínio com old/new values e retenção própria será implementado no Proposal Core, conforme Blueprint; logs não o substituem.

Definir retenção operacional inicial de 30 dias no ambiente hospedado, sujeita à política do operador. Dados de teste são sintéticos. Rate limiting por IP no gateway antes de exposição externa: baseline 60 requests/min com burst 20 para rotas autenticadas, 10/min por IP para falhas de autenticação, health restrito à rede de operação. Gateway deve confiar somente no proxy conhecido para origem IP.

### 13.2 Redis

Criar um cliente por processo, fechar no lifespan; não criar cliente em cada request. Prefixo futuro: cce:<environment>:<tenant_uuid>:<purpose>:<key>. Cache exige TTL e nunca guarda token bruto ou autorização como fonte única de verdade.

No M0 o uso funcional de Redis é conectividade/readiness. Broker Celery será introduzido quando houver job real; uma interface de fila sem consumidor não é critério de conclusão.

Readiness fica 503 com Redis indisponível porque a composição M0 o declara dependência obrigatória; liveness continua 200. Isso não apaga dados nem converte falha de dependência em falha de autenticação.

## 14. Estratégia de testes e evidência de isolamento

### 14.1 Fixtures

tests/conftest.py define settings de test, app_engine, migrator_engine, session factories, redis_client e HTTP client via ASGITransport. Usar LifespanManager; HTTPX ASGITransport sozinho não executa startup/shutdown.

Guard obrigatório antes de qualquer reset/downgrade: CCE_ENV=test e nome do database começando por cce_test. Validar o nome parseando a URL, não por substring. Se não atender, abortar com mensagem sem DSN.

tests/factories.py cria Tenant A/B e chaves via helpers administrativos, usando uma transação por tenant com contexto aplicado. Fixtures de limpeza podem TRUNCATE tabelas **somente no banco descartável validado**, como migrator; nunca no runtime. Tests de migration rodam isolados antes do restante da suíte.

Não reutilizar transação de fixture superusuário nas consultas verificadas. Testes de RLS abrem conexões reais como cce_app. Factories não substituem ou desabilitam policies.

### 14.2 Matriz mínima

| Arquivo | Casos obrigatórios |
|---|---|
| test_config.py | Variáveis ausentes, DSN role errada, pepper inválido, TLS obrigatório, origin wildcard, segredo ausente do repr/erro |
| test_ids.py | encode/decode ten_/key_, prefixo errado, tamanho/hex inválido e roundtrip |
| test_key_security.py | Entropia/comprimento, underscore no secret, digest estável, HMAC diferente com outro pepper, marker errado e comparação |
| test_logging.py | Redação recursiva, sem DSN/header/body, request/trace presentes, contexto limpo entre requests |
| test_tenant_validation.py | Nome vazio/longo, timezone inválido, moeda/locale inválidos, campos proibidos |
| test_db_roles.py | Flags, ownership, NOINHERIT, sem membership de app, DDL/SET ROLE/TRUNCATE proibidos |
| test_rls.py | Sem contexto; A não lê B; UPDATE B afeta zero; WITH CHECK rejeita B; owner forçado; context reset |
| test_auth_lookup.py | Função sem tenant retorna um candidato válido; PUBLIC sem EXECUTE; app não lê digest diretamente; lookup sem listagem; search_path e owner corretos |
| test_authentication.py | Válida, ausente, malformada, segredo errado, expirada, revogada, tenant suspenso, outro ambiente, scope ausente e banco indisponível |
| test_transactions.py | Commit antes da resposta, erro no commit, rollback/cancelamento, pool reutilizado, concorrência A/B |
| test_tenant_api.py | GET/PATCH próprios, mass assignment recusado, header tenant falso não altera resultado, 401/403/422 uniformes |
| test_health.py | Live sem dependências, ready OK, banco/Redis fora, schema antigo, sem informação sensível |
| test_bootstrap.py | Criação atômica tenant+key, segredo só após commit, slug repetido, revoke repetido e chave de outro tenant |
| test_openapi.py | Paths exatos, Bearer security em dados, scopes documentados, schemas sem campos de segredo, códigos e prefixos |
| test_migration_chain.py | Empty→head, head→base→head em DB descartável; constraints/policies/grants restaurados |

### 14.3 Casos críticos com asserções

Os helpers runtime_session(tenant_id), runtime_no_context() e auth_headers(key) são fixtures/auxiliares definidos em conftest.py; factories devolvem UUIDs e chaves em memória.

~~~python
async def test_runtime_without_context_sees_no_tenants(runtime_no_context):
    async with runtime_no_context() as session:
        rows = (await session.execute(select(Tenant))).scalars().all()
        assert rows == []

async def test_a_cannot_read_b(runtime_session, tenant_a, tenant_b):
    async with runtime_session(tenant_a.id) as session:
        result = await session.get(Tenant, tenant_b.id)
        assert result is None

async def test_header_cannot_switch_tenant(client, key_a, tenant_a, tenant_b):
    response = await client.get(
        "/api/v1/tenant",
        headers={
            "Authorization": f"Bearer {key_a}",
            "X-Tenant-ID": str(tenant_b.id),
        },
    )
    assert response.status_code == 200
    assert response.json()["id"] == encode_tenant_id(tenant_a.id)
~~~

No probe, INSERT com tenant_id=B sob contexto A deve falhar por SQLSTATE 42501; após rollback, nova transação funciona. UPDATE de linha A para tenant_id B também falha por WITH CHECK. SELECT/UPDATE/DELETE sobre linha B retornam zero linhas; B permanece intacto.

**Pool de uma conexão:** executar A e finalizar; abrir sem contexto e obter zero; executar B e obter somente B; repetir após erro e cancelamento. Teste separado executa requests A/B concorrentes com sessões distintas e confirma respostas corretas.

**Commit:** forçar falha na saída da dependência transacional; response deve ser 500/409/503 conforme erro e nunca 200. Reabrir sessão independente e comprovar ausência da alteração.

**RLS forçado no owner:** conectar como cce_migrator sem SET ROLE cce_auth e sem contexto; SELECT retorna zero. Isso também verifica que NOINHERIT impede receber a policy global de autenticação.

### 14.4 Execução

~~~powershell
$env:CCE_ENV_FILE = ".env.test"
uv run --project apps/api --locked pytest tests/migrations -v
uv run --project apps/api --locked pytest tests/unit tests/integration tests/contracts --cov=cce --cov-config=apps/api/pyproject.toml --cov-report=term-missing --cov-report=xml
~~~

Resultado exigido: testes aprovados, cobertura de branches medida e cobertura global ≥85%. Casos críticos de RLS/auth/commit são obrigatórios independentemente do percentual. Não ajustar coverage com exclusões para esconder casos de segurança.

A CI também executa alembic check, scripts/check_schema.py e smoke por HTTP real no container. Testes futuros de contrato SDK e Golden Path pertencem aos marcos correspondentes.


## 15. CI/CD

### 15.1 CI obrigatória

.github/workflows/ci.yml:

~~~yaml
name: CI
on:
  pull_request:
  push:
    branches: [main]
  workflow_dispatch:
permissions:
  contents: read
jobs:
  foundation:
    runs-on: ubuntu-24.04
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683
        with:
          persist-credentials: false
      - name: Install pinned uv
        run: python3 -m pip install --user uv==0.12.10
      - name: Verify Foundation
        run: bash scripts/ci.sh
~~~

SHA acima identifica actions/checkout v4.2.2; revalidar suporte/vulnerabilidades ao implementar e atualizar por alteração revisada. Não trocar por tag flutuante na publicação. O [GitHub recomenda pin por SHA completo e permissões mínimas](https://docs.github.com/en/actions/reference/security/secure-use).

scripts/ci.sh executa na raiz, com set -euo pipefail:

1. Gera .env.ci com env=test e database=cce_test_ci.
2. Exporta CCE_ENV=test e CCE_ENV_FILE=.env.ci; não usa source em dotenv.
3. Instala Python 3.12 via uv; verifica lock; sincroniza grupo dev com --locked.
4. Executa Ruff e mypy.
5. Valida Compose com --env-file .env.ci -p cce-ci config --quiet, sem despejar valores.
6. Sobe postgres/redis com --wait.
7. Aplica Alembic head com DSN migrator local carregado de .env.ci.
8. Executa tests/migrations isoladamente.
9. Executa unit/integration/contracts com cobertura.
10. Executa alembic check e scripts/check_schema.py.
11. Executa pip-audit no ambiente sincronizado.
12. Constrói a imagem e sobe api pelo Compose; o serviço migrate roda separadamente.
13. Executa scripts/smoke.py por HTTP real, verificando readiness, A/B, scopes e revogação.
14. Registra relatórios sem segredos e faz teardown do projeto descartável cce-ci.

Instalar trap de cleanup logo após criar o projeto. O teardown com --volumes é restrito ao runner e ao projeto cce-ci criado neste job; não orientar esse comando como limpeza padrão do ambiente do usuário.

Comandos principais do script:

~~~bash
uv lock --project apps/api --check
uv sync --project apps/api --locked --group dev
uv run --project apps/api --locked ruff check --config apps/api/pyproject.toml apps/api/src tests scripts
uv run --project apps/api --locked ruff format --check --config apps/api/pyproject.toml apps/api/src tests scripts
uv run --project apps/api --locked mypy --config-file apps/api/pyproject.toml apps/api/src tests scripts
uv run --project apps/api --locked alembic -c alembic.ini upgrade head
uv run --project apps/api --locked pytest tests/migrations -v
uv run --project apps/api --locked pytest tests/unit tests/integration tests/contracts --cov=cce --cov-config=apps/api/pyproject.toml --cov-report=xml
uv run --project apps/api --locked alembic -c alembic.ini check
uv run --project apps/api --locked python scripts/check_schema.py
uv run --project apps/api --locked pip-audit --path apps/api/.venv/lib/python3.12/site-packages
~~~

O caminho do último comando é específico do runner Linux/Python 3.12. Local Windows usa a variação de site-packages da venv, documentada no runbook. Vulnerabilidade não é automaticamente ignorada: atualizar lock ou registrar exceção específica, justificada e com validade, antes de liberar o pipeline.

Branch protection exige job foundation aprovado. Pull requests não recebem credenciais de deploy nem permissão para publicar packages.

### 15.2 Release e publicação de imagem

.github/workflows/release.yml contém um workflow separado, disparado por workflow_run concluído do workflow CI. O job publish executa somente se:

- conclusion == success.
- evento original foi push.
- head_branch == main.
- repositório de origem é o próprio repositório.

Fazer checkout pelo head_sha do workflow validado, não pelo HEAD móvel de main. Permissões do job: contents:read e packages:write; persist-credentials=false.

Sequência exata:

1. Resolver repositório GHCR em minúsculas.
2. Confirmar lockfile e digests das imagens-base.
3. Construir com infrastructure/docker/api.Dockerfile e label org.opencontainers.image.revision=head_sha.
4. Tag: ghcr.io/<owner>/<repo>/cce-api:<head_sha>.
5. Autenticar no GHCR com GITHUB_TOKEN por stdin.
6. Publicar imagem; obter e registrar digest sha256 e commit.
7. Registrar referência imutável em resumo da execução, sem variáveis secretas.

O arquivo de release não aceita um checkout arbitrário de PR nem promove imagem só porque um nome de branch parece main. O artefato de release é construído do mesmo commit que passou na CI, e o digest publicado é a unidade de promoção.

### 15.3 CD e contrato de implantação

A escolha da plataforma não aparece no Blueprint. Portanto o M0 fixa o **contrato de deploy e o runbook**, sem inventar uma conta cloud, endpoint ou credencial. A implementação do adapter específico de hospedagem deve ocorrer antes do primeiro deploy em staging.

Entradas obrigatórias para esse adapter:

| Entrada | Uso |
|---|---|
| image_ref com @sha256 | Mesma imagem em staging e production |
| runtime secrets | DSN cce_app, Redis, peppers e CA |
| migration secrets | DSN cce_migrator somente no job de migration |
| release metadata | Commit, versão, revisions Alembic e digest |
| health URLs internas | Readiness pós-deploy |
| smoke credentials | Dois tenants de teste isolados, nunca chaves de clientes reais |

Ordem de promoção:

1. Validar backup e compatibilidade da migration.
2. Executar uma única instância do job Alembic com imagem nova e credencial migrator.
3. Exigir código 0; impedir dois jobs concorrentes de migration por ambiente.
4. Atualizar API para o digest novo.
5. Aguardar readiness e executar smoke com tenants sintéticos de staging.
6. Promover o mesmo digest para production conforme política de entrega.
7. Registrar release e confirmar métricas básicas.

O job runtime nunca recebe DSN migrator. A plataforma deve usar identidade temporária/OIDC quando disponível, reduzindo segredos persistentes; ver [OIDC do GitHub Actions](https://docs.github.com/en/actions/reference/security/oidc).

Rollback de aplicação retorna ao digest anterior somente se o schema continuar compatível. Mudanças destrutivas exigem expand/contract. Não fazer downgrade automático para tentar recuperar produção.

**Critério M0 de CD:** build/publicação definidos, credenciais separadas, contrato e runbook completos. Hospedagem e deploy real não são declarados executados por este plano.

## 16. ADRs e documentação

Cada ADR deve conter: título/ID, data, status, contexto, alternativas, decisão, consequências, impactos de segurança, referência ao Blueprint e critério de revisão.

Preservar os IDs 001–010 do Blueprint:

| Arquivo em docs/adr | Conteúdo obrigatório | Implementação |
|---|---|---|
| 001-modular-monolith.md | Fronteiras por contexto; sem extração prematura | M0 |
| 002-postgresql.md | Relacional, JSONB e transações | M0 |
| 003-shared-multi-tenant-rls.md | Tenant por empresa, RLS, roles, exceção cce_auth | M0 |
| 004-api-first.md | Prefixo /api/v1 e contrato programático | M0 |
| 005-immutable-accepted-proposal.md | Versão aceita e evidência imutáveis | Contexto registrado; código M1/M5 |
| 006-structured-ai.md | JSON validado; LLM sem autoridade comercial | Contexto registrado; código M6 |
| 007-provider-abstraction.md | Portas/adapters, sem vendor no domínio | Regra M0; adapters depois |
| 008-transactional-outbox.md | Evento e dados na mesma transação; at least once | Contexto registrado; antes dos webhooks M4 |
| 009-react-next-renderer.md | Admin/Proposal Next.js separados do core | Contexto registrado; código M2 |
| 010-fastapi-core.md | API assíncrona, Pydantic/OpenAPI | M0 |
| 011-python-uv.md | Python 3.12, uv fixo, lockfile por app | M0 |
| 012-api-key-bootstrap.md | HMAC/pepper, lookup, CLI e rotação | M0 |
| 013-transaction-tenant-context.md | GUC local, sessão por operação, commit antes do HTTP | M0 |
| 014-public-ids-api-errors.md | UUID4 interno, prefixos e códigos estáveis | M0 |
| 015-redis-role.md | Auxiliar; sem fonte de verdade ou broker fictício | M0 |
| 016-observability.md | Logs redigidos, request/trace e evolução | M0 |
| 017-delivery-environments.md | Ambientes, migrations separadas, imagem por digest | M0 |

ADRs 005–009 podem estar aceitos como direção arquitetural sem alegar código entregue. O status registra essa distinção.

Documentos adicionais:

- architecture/context.md: atores, host ERP, destinatário, API e providers futuros.
- architecture/foundation.md: componentes M0, transações, configuração e limites.
- architecture/security-model.md: roles, matriz de grants, RLS, função de auth e fronteiras.
- api/authentication.md: chave, scopes, expiração, revogação, HTTP e exemplos sem credencial real.
- api/errors.md: envelope e códigos da seção 12.
- Runbooks: geração local, bootstrap, migration, rotação, deploy e recuperação.
- README dos frontends/packages: marco futuro, responsabilidade e ausência de implementação atual.

## 17. Sequência de implementação arquivo a arquivo

Executar na ordem abaixo. Todos os caminhos são relativos à raiz commercial-closing-engine. Sempre escrever o teste do comportamento antes do código correspondente, verificar a falha, implementar e verificar aprovação. Configuração e documentação acompanham a tarefa que as torna verificáveis.

### Tarefa 1 — Projeto Python, configuração e IDs

**Arquivos, em ordem:**

1. .editorconfig: UTF-8, LF, newline final; indentação 4 em Python e 2 em YAML.
2. .gitignore e .dockerignore: segredos, ambientes, caches e artefatos.
3. .python-version: 3.12.
4. apps/api/pyproject.toml: conteúdo da seção 4.
5. apps/api/src/cce/__init__.py e todos os __init__.py indicados na árvore: sem side effects.
6. pytest.ini.
7. tests/unit/test_config.py e test_ids.py.
8. shared/config.py e shared/ids.py.
9. .env.example e scripts/init_env.py.
10. apps/api/uv.lock: gerar pela ferramenta, não redigir manualmente.

**Interfaces:** load_settings, load_migration_settings, load_admin_settings; encode_tenant_id(UUID)->str, decode_tenant_id(str)->UUID, encode_key_id(UUID)->str, decode_key_id(str)->UUID. Prefixo errado gera ValueError sem incluir valor secreto.

- [ ] Escrever testes: faltam variáveis → erro sanitizado; role de DSN incorreta → erro; stage sem CA → erro; origin "*" → erro; encode/decode preserva UUID.
- [ ] Executar pytest tests/unit/test_config.py tests/unit/test_ids.py; confirmar falha por interfaces ausentes.
- [ ] Implementar settings/IDs e gerador; recusar sobrescrita de dotenv.
- [ ] Executar mesmos testes, Ruff e import do pacote; confirmar aprovação.
- [ ] Gerar lock, executar uv lock --check e commit: chore: bootstrap Python configuration.

**Aceite independente:** pacote instalável e settings validados sem conectar a serviços.

### Tarefa 2 — Infraestrutura local e imagem

**Arquivos, em ordem:**

1. infrastructure/docker/postgres/01-bootstrap.sh.
2. infrastructure/docker/postgres/bootstrap.sql.
3. infrastructure/docker/api.Dockerfile.
4. docker-compose.yml.
5. docs/runbooks/local-development.md.
6. tests/integration/test_db_roles.py: inicialmente testes de provisionamento, ampliados na tarefa 3.

**Consome:** dotenv gerado e pacote da tarefa 1. **Produz:** roles/schemas, serviços PostgreSQL/Redis saudáveis e receita de imagem.

- [ ] Escrever asserções de roles/schemas e ausência de membership de cce_app.
- [ ] Confirmar falha quando roles ainda não existem.
- [ ] Implementar bootstrap, Compose e Dockerfile conforme seções 6–7.
- [ ] Gerar .env.test, executar Compose config --quiet e subir somente postgres/redis com --wait.
- [ ] Executar testes de roles. Validar composição do serviço API sem iniciá-lo até tarefa 7.
- [ ] Verificar configuração renderizada programaticamente sem imprimi-la: API não possui CCE_MIGRATOR_DATABASE_URL nem senha postgres.
- [ ] Commit: chore: add local PostgreSQL Redis and container build.

**Aceite independente:** infraestrutura pronta; ausência de aplicação neste ponto não é mascarada por um health falso.

### Tarefa 3 — Modelos e primeira migration

**Arquivos, em ordem:**

1. tests/migrations/test_migration_chain.py: teste inicial empty→0001.
2. tests/unit/test_tenant_validation.py.
3. shared/db/base.py e engine.py.
4. identity/tenants/model.py e schemas.py.
5. integrations/api_keys/model.py e scopes.py.
6. shared/db/models.py.
7. alembic.ini.
8. infrastructure/migrations/env.py e script.py.mako.
9. infrastructure/migrations/versions/0001_tenants_api_keys.py.
10. tests/factories.py e tests/conftest.py: engines, guards e fixtures básicas.

**Consome:** roles/schemas da tarefa 2. **Produz:** Tenant, APIKey, metadata, engines e migration 0001.

- [ ] Escrever testes de constraints, datas com timezone, scopes permitidos e campos PATCH proibidos.
- [ ] Confirmar falha por tabelas/classes ausentes.
- [ ] Implementar modelos/migration e fixtures com guard cce_test.
- [ ] Aplicar 0001 em banco descartável; testar empty→0001→base→0001.
- [ ] Verificar grants de DDL e ownership; executar alembic check após alinhar metadata.
- [ ] Commit: feat: add tenant and API key persistence.

**Aceite independente:** schema reproduzível com constraints, sem autenticação HTTP ainda.

### Tarefa 4 — Primitivas de chave

**Arquivos, em ordem:**

1. tests/unit/test_key_security.py.
2. shared/security.py.
3. integrations/api_keys/service.py: somente funções puras de material de chave nesta etapa.

**Interfaces produzidas:** ParsedKey, IssuedKey; generate_key, parse_key, digest_key e verify_digest da seção 11.

- [ ] Escrever testes de formato, 43 caracteres de segredo, marker, underscore, digest e pepper incorreto.
- [ ] Confirmar falha das funções ausentes.
- [ ] Implementar com secrets, hashlib e hmac da biblioteca padrão.
- [ ] Testar que repr de ParsedKey/IssuedKey não mostra segredo; compare_digest recebe bytes de 32 bytes.
- [ ] Executar suíte unitária e commit: feat: add API key cryptographic primitives.

**Aceite independente:** geração/verificação pura, sem banco e sem afirmar autenticação pronta.

### Tarefa 5 — RLS e sessão transacional

**Arquivos, em ordem:**

1. tests/integration/test_rls.py.
2. shared/db/rls.py.
3. shared/db/session.py.
4. infrastructure/migrations/versions/0002_tenant_rls.py.
5. scripts/check_schema.py.
6. tests/conftest.py: runtime_session, runtime_no_context e probe de teste.
7. docs/architecture/security-model.md: policies e limites de confiança.

**Interfaces:** set_tenant_context(session, UUID)->None; tenant_session(factory, UUID) async context manager; check_schema valida flags, policies, ownership e grants.

- [ ] Escrever testes de SELECT/UPDATE de A/B, WITH CHECK no probe, ausência de contexto, owner forçado e reset de pool.
- [ ] Executar antes de 0002 e confirmar que a leitura cruzada evidencia o defeito.
- [ ] Implementar 0002, contexto local e sessão com begin/flush/commit.
- [ ] Aplicar head; executar test_rls e test_db_roles como cce_app.
- [ ] Testar pool_size=1/max_overflow=0 após commit, rollback e cancelamento.
- [ ] Executar check_schema e commit: feat: enforce tenant isolation with PostgreSQL RLS.

**Aceite independente:** isolamento no banco provado sem HTTP nem bypass em runtime.

### Tarefa 6 — Lookup, Principal e bootstrap administrativo

**Arquivos, em ordem:**

1. tests/integration/test_auth_lookup.py, test_authentication.py e test_bootstrap.py.
2. infrastructure/migrations/versions/0003_auth_lookup.py.
3. integrations/api_keys/repository.py.
4. integrations/api_keys/service.py: completar lifecycle com sessão recebida.
5. integrations/api_keys/authentication.py.
6. admin/bootstrap.py.
7. admin/cli.py.
8. docs/api/authentication.md.
9. docs/runbooks/tenant-bootstrap.md e secrets-rotation.md.
10. scripts/check_schema.py: acrescentar função, owner, search_path e EXECUTE.

**Consome:** modelos, primitivas e tenant_session. **Produz:** KeyCandidate interno, Principal imutável, autenticação assíncrona e CLI.

- [ ] Escrever testes da função sem contexto, bloqueio de digest direto, chave válida/expirada/revogada e tenant suspenso.
- [ ] Confirmar falha por função inexistente e ausência de CLI.
- [ ] Implementar lookup de seção 11, comparação e provisionamento atômico.
- [ ] Testar que cce_migrator NOINHERIT não herda leitura global; cce_app não assume cce_auth.
- [ ] Testar chave de outro ambiente; DB offline dá erro de dependência, não credencial inválida.
- [ ] Testar criação com falha no commit: sem Tenant/APIKey persistidos nem chave exibida.
- [ ] Executar suite de autenticação/bootstrap e commit: feat: add tenant API key authentication and bootstrap.

**Aceite independente:** duas credenciais ligadas a tenants distintos autenticam sem tenant fornecido pelo cliente.

### Tarefa 7 — FastAPI, contexto e endpoints

**Arquivos, em ordem:**

1. tests/integration/test_tenant_api.py, test_transactions.py e test_health.py.
2. api/errors.py.
3. api/dependencies.py.
4. identity/tenants/repository.py e service.py.
5. identity/tenants/routes.py.
6. api/router.py e health.py.
7. shared/redis.py.
8. main.py.
9. scripts/smoke.py.
10. docs/api/errors.md.

**Interfaces:** create_app(settings=None)->FastAPI; get_principal; require_scope; get_tenant_session; TenantService.get_current/patch_current retornam TenantRead.

- [ ] Escrever GET/PATCH próprios, scope ausente, tenant falso, campo proibido, ready/live e falha de commit.
- [ ] Confirmar falha por aplicação/rotas ausentes.
- [ ] Implementar factory/lifespan, rotas e Depends(scope="function").
- [ ] Materializar resposta antes de fechar sessão; simular falha de saída e confirmar erro antes do envio HTTP.
- [ ] Verificar rollback com sessão independente, A/B concorrentes e closures após shutdown.
- [ ] Construir imagem e subir API pelo Compose; executar smoke em env test.
- [ ] Commit: feat: expose Foundation API and health endpoints.

**Aceite independente:** fatia operacional HTTP de dois tenants funciona e não vaza por contexto ou erro transacional.

### Tarefa 8 — Logging e contrato OpenAPI

**Arquivos, em ordem:**

1. tests/unit/test_logging.py.
2. tests/contracts/test_openapi.py.
3. shared/logging.py.
4. api/middleware.py.
5. main.py: instalar middleware e loggers.
6. docs/architecture/foundation.md.
7. docs/api/authentication.md e errors.md: completar exemplos.

**Interfaces:** configure_logging(settings)->None; RequestContextMiddleware ASGI; processors não retornam segredos.

- [ ] Escrever testes de request_id/trace_id inclusive em erro e contexto limpo após autenticação falha.
- [ ] Confirmar falha antes de instalar logging/middleware.
- [ ] Implementar allowlist/redação e desabilitar access log bruto.
- [ ] Testar headers/DSNs/objetos secretos em logs; não apenas uma string de exemplo.
- [ ] Verificar OpenAPI: paths, Bearer, schemas de resposta, códigos e ausência de digest.
- [ ] Commit: feat: add safe structured logging and API contracts.

**Aceite independente:** diagnóstico útil e contrato legível sem credenciais ou dados pessoais nos logs.

### Tarefa 9 — Cadeia de migrations e robustez final

**Arquivos, em ordem:**

1. tests/migrations/test_migration_chain.py: completar 0003→base→0003.
2. tests/integration/test_db_roles.py e test_transactions.py: completar falhas.
3. scripts/check_schema.py: verificar todo o catálogo esperado.
4. docs/runbooks/migrations.md.
5. docs/runbooks/backup-restore.md.

**Consome:** aplicação e schema completos. **Produz:** evidência de reconstrução e runbooks seguros.

- [ ] Escrever testes que removem temporariamente uma policy/grant em DB descartável e confirmam que check_schema detecta o desvio.
- [ ] Confirmar falha de verificação no schema adulterado.
- [ ] Implementar verificações restantes e restabelecer migration head.
- [ ] Executar cadeia inteira em base vazia, depois alembic check e todos os testes.
- [ ] Ensaiar backup/restore de dados sintéticos e confirmar IDs, constraints, policies e autenticação após restore.
- [ ] Commit: test: verify migration security and recovery.

**Aceite independente:** schema e controles são reconstruíveis; recuperação não depende de memória do implementador.

### Tarefa 10 — CI e contrato de entrega

**Arquivos, em ordem:**

1. scripts/ci.sh.
2. .github/workflows/ci.yml.
3. .github/workflows/release.yml.
4. docs/runbooks/deployment.md.
5. infrastructure/docker/api.Dockerfile: resolver digests antes da primeira publicação.

**Consome:** testes/runbooks locais. **Produz:** pipeline verificável e promoção por digest.

- [ ] Criar script com falha deliberada de check_schema e comprovar que retorna código não zero.
- [ ] Implementar pipeline da seção 15 e remover a adulteração de teste.
- [ ] Executar localmente no projeto descartável cce-ci e confirmar toda a sequência.
- [ ] Verificar segredos da composição da API sem exibir seus valores.
- [ ] Validar condições de release contra eventos push/main e PR/fork; apenas o primeiro pode publicar.
- [ ] Registrar limitações de hospedagem e entradas do adapter; nenhum deploy real é exigido para criar este plano.
- [ ] Commit: ci: add Foundation verification and release pipeline.

**Aceite independente:** pipeline reproduz o ambiente e falha quando um controle obrigatório falha.

### Tarefa 11 — ADRs, documentação e gate M0

**Arquivos, em ordem:**

1. docs/adr/README.md e ADRs 001–010 preservando IDs e decisões do Blueprint.
2. ADRs 011–017 conforme tabela da seção 16.
3. docs/architecture/context.md; revisar foundation.md e security-model.md.
4. README.md com quickstart, comandos, limites e roadmap original.
5. README.md em apps/admin-web, apps/proposal-web e quatro packages futuros.
6. Revisar os seis runbooks; remover exemplos que exponham segredos.

**Consome:** implementação validada. **Produz:** documentação correspondente ao estado real.

- [ ] Percorrer a matriz de cobertura da seção 19; cada requisito aponta para evidência.
- [ ] Executar quickstart numa cópia limpa, sem venv nem volume anterior.
- [ ] Verificar configuração de ambiente e dois tenants pelo smoke.
- [ ] Executar pipeline uma vez sem alterações pendentes que invalidem o resultado.
- [ ] Atualizar status do M0 somente se todos os critérios da seção 20 forem atendidos.
- [ ] Commit: docs: document Foundation architecture and operations.

**Aceite independente:** um engenheiro sem histórico da conversa consegue subir, verificar, operar e evoluir a base.

## 18. Runbook de execução local após implementar

Todos os comandos partem da raiz do futuro repositório. Docker Desktop deve estar com engine Linux e Docker Compose disponível. Python 3.12 e uv da versão registrada precisam estar instalados.

~~~powershell
python scripts/init_env.py --target .env --env local --database-name cce
uv sync --project apps/api --locked --group dev
docker compose --env-file .env config --quiet
docker compose --env-file .env up --build -d --wait
Invoke-RestMethod http://127.0.0.1:8000/health/ready
uv run --project apps/api --locked cce-admin tenant create --name "Empresa A" --slug empresa-a
uv run --project apps/api --locked cce-admin tenant create --name "Empresa B" --slug empresa-b
~~~

Guardar as chaves exibidas no provisionamento em local privado. Não colocá-las em exemplos versionados, screenshots, tickets ou logs.

Para testes, parar primeiro a composição local sem apagar volumes. Gerar .env.test uma vez e usar projeto separado:

~~~powershell
docker compose --env-file .env stop
python scripts/init_env.py --target .env.test --env test --database-name cce_test
$env:CCE_ENV = "test"
$env:CCE_ENV_FILE = ".env.test"
docker compose --env-file .env.test -p cce-test up -d --wait postgres redis
uv run --project apps/api --locked alembic -c alembic.ini upgrade head
uv run --project apps/api --locked pytest tests/migrations -v
uv run --project apps/api --locked pytest tests/unit tests/integration tests/contracts --cov=cce --cov-config=apps/api/pyproject.toml
docker compose --env-file .env.test -p cce-test up --build -d --wait api
uv run --project apps/api --locked python scripts/smoke.py --base-url http://127.0.0.1:8000 --provision
~~~

Smoke --provision só aceita CCE_ENV=test e database cce_test*. Usa admin helpers para gerar A/B sintéticos, mantém tokens em memória, chama HTTP real e limpa dados por contexto ao terminar. Em staging, smoke usa credenciais já provisionadas de tenants sintéticos via variáveis da plataforma, sem receber DSN administrativo.

Para voltar a local, remover CCE_ENV/CCE_ENV_FILE da sessão e parar cce-test antes de iniciar cce. Se o dotenv já existir, não executar init_env.py novamente.

### Backup/restore

O runbook deve distinguir dados, roles e configurações. pg_dump de um database não inclui automaticamente todos os roles do cluster. Em restore limpo: provisionar roles/schemas com o administrador → restaurar dump sob controle de ownership → aplicar migrations pendentes → verificar RLS/grants → executar smoke com dados sintéticos.

A API não recebe credencial de backup. Exercício inicial: restaurar ambiente descartável, recuperar dois tenants e confirmar isolamento/revogação. RPO/RTO e retenção de produção são decisões operacionais antes do piloto, não SLA assumido no Foundation.

## 19. Matriz de rastreabilidade

| Requisito solicitado | Seções | Tarefa / evidência |
|---|---|---|
| Estrutura exata | 3 | 1–11; árvore corresponde aos arquivos |
| Dependências/Python | 4 | 1; lock e import |
| Docker Compose | 6 | 2/7; health e smoke |
| PostgreSQL | 7–10 | 2/3/5/9; roles e schema |
| Redis | 6/12/13 | 2/7; readiness e fechamento |
| FastAPI | 12 | 7/8; HTTP e OpenAPI |
| SQLAlchemy | 8/10 | 3/5/7; sessões e rollback |
| Alembic | 9 | 3/9; cadeia e drift |
| Tenant | 8 | 3/7; schema e API |
| PostgreSQL RLS | 10 | 5/6; DML real e owner forçado |
| API keys | 11 | 4/6; digest, lookup, scopes e rotação |
| Ambientes | 5 | 1/10; validação e segregação |
| Logging | 13 | 8; redação e correlação |
| Testes | 14 | Todas; guard e matriz explícita |
| CI/CD | 15 | 10; pipeline e contrato de promoção |
| ADRs | 16 | 11; IDs originais preservados |
| Arquivo a arquivo | 17 | Sequência, interfaces e gates |
| Operação/recuperação | 18 | 9/11; ensaio descartável |

## 20. Definition of Done — Foundation

- [ ] Clone limpo gera configuração e sobe serviços sem providers externos.
- [ ] pyproject e uv.lock consistentes; Python/uv iguais em CI e imagem.
- [ ] API roda como usuário não root e não recebe credencial migrator/postgres.
- [ ] Migrations chegam a head por job separado.
- [ ] Tenant/APIKey possuem constraints e defaults documentados.
- [ ] RLS habilitada e forçada em todas as tabelas empresariais do M0.
- [ ] cce_app sem ownership, BYPASSRLS, memberships indevidos, DDL ou TRUNCATE.
- [ ] Lookup anterior ao tenant funciona somente pela função restrita.
- [ ] Segredo de chave nunca persistido bruto nem serializado em endpoint.
- [ ] Expiração, suspensão, scope e revogação verificados.
- [ ] Duas empresas isoladas em SQL e HTTP, com pool reutilizado e concorrência.
- [ ] Commit termina antes de resposta; rollback/cancelamento não deixam efeitos.
- [ ] Liveness/readiness distintos; drift de schema é detectado.
- [ ] Logs JSON redigidos com request/trace IDs, sem contexto residual.
- [ ] Contrato de erros e OpenAPI documentado e testado.
- [ ] Suíte real, cobertura ≥85%, lint, tipos, schema check, auditoria e build aprovados.
- [ ] CI sem segredos de produção; publicação limitada ao commit validado.
- [ ] ADRs, quickstart, bootstrap, rotação, migrations, deploy e recuperação documentados.
- [ ] Exercício de restore descartável demonstra dados e controles restaurados.
- [ ] Plataforma de hospedagem não é presumida; contrato do adapter está explícito.
- [ ] Não declarar Proposal Core, IA, pagamentos, frontend ou Golden Path entregues pelo M0.

## 21. Decisões pendentes antes das fases seguintes

| Decisão | Quando | Impacto no M0 |
|---|---|---|
| Plataforma de hospedagem e gerenciador de segredos | Antes de staging | Conectar adapter ao contrato de entrega |
| Hierarquia de parceiros e tenants | Antes do primeiro piloto | Confirmar tenant por empresa sem alterar isolamento |
| Retenção/RPO/RTO de produção | Antes do piloto | Completar política operacional sobre runbooks existentes |
| OIDC, usuário e RBAC humano | Antes de UI administrativa | Não bloqueia API keys servidor a servidor |
| Provider de e-mail/storage | M2/M4 conforme consumidor | Não instalar adapters sem caso de uso |
| SDK Python e nome do import | Depois de validar API no host referência | Evitar colisão com pacote interno cce |
| Rate limit da plataforma | Antes de exposição externa | Aplicar os limites da seção 13 no gateway escolhido |

Essas decisões não autorizam atalhos em RLS, segredos ou migrations. Elas delimitam o que o Foundation especifica e o que depende do ambiente de operação.

## 22. Fontes técnicas e uso deste plano

Documentação oficial consultada em 14/10/2026, referenciada junto às decisões relevantes: PostgreSQL 17 (RLS, policies, GUC e SECURITY DEFINER); SQLAlchemy 2 async; Alembic; FastAPI lifespan/dependencies; Docker Compose; Redis asyncio; uv e GitHub Actions.

O plano contém escolhas específicas de projeto além das capacidades descritas nessas fontes. Na implementação, validar a resolução de dependências e os digests reais, executar os testes e registrar a evidência. Nenhum bloco de configuração deste documento deve ser apresentado como testado apenas por constar no plano.

O próximo marco após o gate M0 é **Milestone 1 — Proposal Domain**, preservando Customer, Proposal, ProposalVersion, Items e Sections definidos no Blueprint.



