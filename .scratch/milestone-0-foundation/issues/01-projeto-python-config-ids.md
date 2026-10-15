# 01: Projeto Python, configuração e IDs

**What to build:** um desenvolvedor clona o repositório, gera os segredos locais automaticamente e instala o pacote da API com um lockfile reproduzível. Os settings por ambiente (local, test, staging, production) são validados e falham de forma sanitizada, sem conectar a nenhum serviço. A estrutura de diretórios da seção 3 do plano é criada na raiz deste repositório (ao lado de `CONTEXT.md` e `docs/`), não numa subpasta.

**Blocked by:** None

**Status:** ready-for-agent

**Plano:** Technical Implementation Plan do M0, Tarefa 1 e seções 3, 4 e 5. **Vocabulário:** Tenant (`CONTEXT.md`).

- [ ] Os testes de settings e IDs são escritos antes do código e falham por interfaces ausentes, depois passam
- [ ] Variável obrigatória ausente produz erro que cita o nome do campo e nunca o valor recebido
- [ ] DSN runtime com role errada, staging sem CA, dotenv usado em staging/production, `CCE_ALLOWED_ORIGINS` com `*` e pepper inválido são todos recusados
- [ ] `encode`/`decode` de `ten_` e `key_` preservam o UUID; prefixo errado, tamanho ou hex inválido geram `ValueError` sem valor secreto
- [ ] O gerador de dotenv só aceita `local` e `test`, troca somente os marcadores explícitos, não imprime segredos e recusa sobrescrever arquivo existente
- [ ] O pacote instala e `import cce` funciona; `uv lock --check` e `uv sync --locked` passam; Ruff e mypy estrito passam
- [ ] Nenhum módulo de negócio vazio é criado; diretórios futuros têm só README de escopo
- [ ] O ADR `docs/adr/0011-python-uv.md` registra Python 3.12, a versão fixada do uv e o lockfile por app (formato e conteúdo da seção 16 do plano)
