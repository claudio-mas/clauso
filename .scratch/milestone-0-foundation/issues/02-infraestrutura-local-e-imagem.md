# 02: Infraestrutura local e imagem

**What to build:** um desenvolvedor sobe PostgreSQL 17 e Redis saudáveis com Docker Compose, com as roles do produto provisionadas pela matriz de privilégios, e consegue construir a imagem da API. A composição da API não recebe credencial administrativa. Ainda não há aplicação rodando, e nenhum health falso mascara isso.

**Blocked by:** 01

**Status:** in-review

**Plano:** Tarefa 2 e seções 6 e 7.

- [ ] Os testes de roles e schemas são escritos antes e falham enquanto as roles não existem
- [ ] `cce_migrator`, `cce_app` e `cce_auth` existem com os atributos da matriz (sem superusuário, sem `BYPASSRLS`, `NOINHERIT`; `cce_auth` sem login) e `cce_app` não é membro de nenhuma outra
- [ ] Os schemas `cce` e `cce_private` existem, com `CREATE` em `public` revogado
- [ ] Como `cce_app`, `CREATE TABLE` e `SET ROLE cce_migrator` falham com privilégio insuficiente
- [ ] O bootstrap não rotaciona senhas silenciosamente e é reaplicável
- [ ] A configuração renderizada do serviço da API não contém a URL do migrator nem a senha do `postgres` (verificado por código, sem imprimir segredos)
- [ ] A imagem constrói, roda como usuário não root e não contém dotenv, `uv` nem dependências de desenvolvimento
- [ ] O serviço de migration é de execução única e a API só inicia depois que ele conclui
- [ ] `.env.test` usa projeto Compose e banco separados (`cce_test`)

## Comments

### 2026-10-23 — Parecer do Revisor (Lupa), rodada 1: mudanças necessárias

PR #3, head `b8568ea`. Revisão com foco em segurança, feita pelo git local (o sandbox do Revisor não acessa o Docker; a integração foi avaliada por leitura, e os 32 testes de integração verdes são evidência do Regente, não reproduzida pelo Revisor).

Checks do Revisor: `uv sync --locked`, `uv lock --check` (76 pacotes), `pytest tests/unit` (223 passed), Ruff, `ruff format --check`, mypy estrito, `docker compose config --quiet` e `pytest tests/integration/test_compose_config.py` (10 passed, sem daemon) passaram.

Problemas:
1. **[importante]** `tests/integration/test_db_roles.py:290` e `:292`: o teste de reaplicabilidade coloca a senha alternativa literalmente no argv de `docker compose exec -e CCE_APP_DB_PASSWORD=... -e CCE_MIGRATOR_DB_PASSWORD=...`. Corrigir passando os valores pelo ambiente do subprocess e usando `exec -e NOME` sem `=valor`; adaptar `run_compose` (`support.py:34`), que hoje remove todas as `CCE_*`, para receber explicitamente as variáveis permitidas. Verificar argv e saída com sentinela, sem imprimir o conteúdo.
2. **[importante]** `tests/integration/test_compose_config.py:43`: `assert_absent` imprime `what!r` na mensagem de falha, e em `:107` recebe as senhas reais de app e postgres; ao detectar o vazamento, registraria a senha na saída do pytest (confirmado com marcador sintético). Usar rótulos fixos associados aos valores, nunca o valor proibido. Revisar também os asserts sobre dicionários/DSNs (`:69-72`, `:85`, `:105`) e `test_db_roles.py:297`, cuja introspecção pode mostrar environment/DSN/senha em falhas; usar `pytest.fail` com texto fixo. Acrescentar prova de que o caminho de falha não imprime sentinelas.

Opiniões sobre os desvios, todas favoráveis:
- Dockerfile sem `COPY` de `alembic.ini`/migrations: aceitável; cabe ao ticket 03 incluí-los.
- `pg_isready -h 127.0.0.1`: melhoria correta.
- `ALTER DEFAULT PRIVILEGES` sem `IN SCHEMA`: correto, cobre `cce_migrator` e `cce_auth`; não revoga funções preexistentes nem substitui a segurança da função. No ticket 06, cada função `SECURITY DEFINER` precisa de `search_path` próprio seguro (incluindo `pg_temp`), referências qualificadas, owner `NOLOGIN`, grants mínimos e `GRANT EXECUTE` explícito.
- Senhas via `\getenv` no psql: adequado para o bootstrap.
- Imagens-base por tag: aceitável na baseline local; fixar digests antes da primeira publicação (plano, seção 6).

Sem conflito com ADR-0003 nesta etapa. O ticket volta a `in-progress` para os dois itens acima.
