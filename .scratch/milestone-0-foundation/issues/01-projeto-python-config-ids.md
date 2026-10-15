# 01: Projeto Python, configuração e IDs

**What to build:** um desenvolvedor clona o repositório, gera os segredos locais automaticamente e instala o pacote da API com um lockfile reproduzível. Os settings por ambiente (local, test, staging, production) são validados e falham de forma sanitizada, sem conectar a nenhum serviço. A estrutura de diretórios da seção 3 do plano é criada na raiz deste repositório (ao lado de `CONTEXT.md` e `docs/`), não numa subpasta.

**Blocked by:** None

**Status:** done

**Plano:** Technical Implementation Plan do M0, Tarefa 1 e seções 3, 4 e 5. **Vocabulário:** Tenant (`CONTEXT.md`).

- [ ] Os testes de settings e IDs são escritos antes do código e falham por interfaces ausentes, depois passam
- [ ] Variável obrigatória ausente produz erro que cita o nome do campo e nunca o valor recebido
- [ ] DSN runtime com role errada, staging sem CA, dotenv usado em staging/production, `CCE_ALLOWED_ORIGINS` com `*` e pepper inválido são todos recusados
- [ ] `encode`/`decode` de `ten_` e `key_` preservam o UUID; prefixo errado, tamanho ou hex inválido geram `ValueError` sem valor secreto
- [ ] O gerador de dotenv só aceita `local` e `test`, troca somente os marcadores explícitos, não imprime segredos e recusa sobrescrever arquivo existente
- [ ] O pacote instala e `import cce` funciona; `uv lock --check` e `uv sync --locked` passam; Ruff e mypy estrito passam
- [ ] Nenhum módulo de negócio vazio é criado; diretórios futuros têm só README de escopo
- [ ] O ADR `docs/adr/0011-python-uv.md` registra Python 3.12, a versão fixada do uv e o lockfile por app (formato e conteúdo da seção 16 do plano)

## Comments

### 2026-10-15 — Parecer do Revisor (Lupa), rodada 1: aprovado

PR #2, branch `m0/01-projeto-python-config-ids`. Sem problemas bloqueantes ou importantes.

Checks do Revisor, numa cópia extraída com `git archive` (sem fetch, worktree ou escrita em `.git`):
- `uv sync --project apps/api --locked --group dev`: sucesso, 75 pacotes
- `uv lock --project apps/api --check`: Resolved 76 packages
- `import cce` e `import fastapi`: ok
- `pytest tests/unit`: 223 passed; cobertura de `cce` 98,31% (mínimo 85%)
- `ruff check` e `ruff format --check`: passam
- `mypy` estrito: Success, 9 arquivos

Todos os critérios de aceite foram conferidos contra testes ou comandos. Pontos levantados e aprovados:
1. Só `cce/` e `cce/shared/` têm `__init__.py` (o ticket proíbe módulo de negócio vazio). O entry point `cce-admin` aponta para `cce.admin.cli` até o ticket 06.
2. Faixas de dependência não ampliadas, registrado no ADR-0011.
3. `known-first-party = ["cce"]` no isort do Ruff.
4. Validações extras nos settings (`ssl*` na query remota, `CCE_DOCS_ENABLED` e log `console` restritos). Observação do Revisor: as docs permanecem habilitadas por padrão em `test`, conforme a tabela 5.2 do plano.
5. ADR-0011 criado pelo Programador, por exigência expressa do critério de aceite.

Observações sem ação: a falha inicial dos testes está registrada nos commits `a9eee8e` e `61f265e` e na descrição da PR; o Revisor não reexecutou esses commits antigos. Não há CI antes do ticket 10, então a evidência é local.
