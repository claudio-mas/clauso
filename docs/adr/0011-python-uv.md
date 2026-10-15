# ADR-0011: Python 3.12 e uv com lockfile por app

**Data:** 15/10/2026
**Status:** Aceito

## Contexto

O ADR-0010 escolheu FastAPI e deixou para este ADR a versão de Python, o gerenciador de dependências e as versões fixadas. O backend precisa instalar igual em três lugares (máquina de quem desenvolve, CI e imagem), e uma dependência que muda sem revisão é uma fonte de falha e de ataque à cadeia de suprimentos.

O Blueprint pede Python 3.12 ou superior (§41). O plano do M0 trata as faixas de dependência como piso e deixa para a Tarefa 1 decidir se elas são ampliadas.

## Alternativas

- **pip com `requirements.txt` (ou pip-tools).** Não gerencia o interpretador, e o lock sem o interpretador deixa a versão de Python variar entre ambientes.
- **Poetry ou PDM.** Resolvem e travam dependências, mas acrescentam uma ferramenta a instalar e a fixar sem ganho para um único app Python.
- **Python 3.13 ou superior.** Mais novo que o exigido pelo Blueprint. Sem consumidor no M0 que precise dele, e nem toda dependência acompanha a versão nova com a mesma rapidez.
- **Um lockfile na raiz (workspace único).** O repositório terá apps que não são Python (`admin-web` e `proposal-web` usam Node), e o lock de um não deve mudar quando outro muda.
- **uv sem versão fixada.** O resultado da resolução e o formato do lock passariam a depender de quando cada máquina instalou a ferramenta.

## Decisão

- **Python 3.12.** CPython 3.12 em desenvolvimento local, CI e imagem. `.python-version` contém `3.12` e o projeto declara `requires-python = ">=3.12,<3.13"`. O uv instala o interpretador (`uv python install 3.12`), então não depende do Python do sistema.
- **uv 0.12.10, fixado.** A mesma versão em desenvolvimento local, Docker e CI. Instala-se com a versão explícita (`pip install --user uv==0.12.10` na CI; o instalador oficial com a versão na URL localmente). A versão só muda por alteração revisada, que atualiza este ADR.
- **Um lockfile por app.** `apps/api/pyproject.toml` e `apps/api/uv.lock` ficam no app e o lock é versionado. O lock é gerado pela ferramenta (`uv lock --project apps/api`), nunca editado à mão. A CI usa `uv lock --check` e `uv sync --locked`, que falham em vez de reescrever o lock. Não há workspace nem lock na raiz.
- **Faixas de dependência ficam como estão no plano; o lock fixa as versões exatas.** As faixas são piso e limite superior deliberado, não alegação de "última versão". Em 15/10/2026 há versões fora delas: redis-py 8.x (faixa `>=6,<8`), SQLAlchemy 2.1 (faixa `<2.1`) e mypy 2.x (faixa `<2`). Nenhuma foi ampliada, porque o M0 não precisa delas e cada uma cruza um limite de major ou de série que merece uma alteração revisada, com os testes de RLS e de transação rodando contra a versão nova. O lock resolveu, entre outras, redis-py 7.4.1 e SQLAlchemy 2.0.54.
- **`import cce` pertence ao backend no M0.** O SDK futuro decide o nome de distribuição e de import antes de coexistir com o pacote da API no mesmo ambiente.

## Consequências

- Instalação reproduzível: duas máquinas com o mesmo commit resolvem as mesmas versões, e a CI detecta um `pyproject.toml` que não bate com o lock.
- Atualizar uma dependência, o uv ou o Python é uma alteração explícita (`uv lock --upgrade-package`, mudança de faixa ou de versão fixada) e passa por revisão.
- Cada app futuro traz o seu gerenciador e o seu lock, sem acoplamento entre eles.
- Quem desenvolve precisa instalar o uv na versão fixada. O runbook de desenvolvimento local (ticket 11) descreve o passo.
- Ficamos temporariamente atrás de redis-py 8.x, SQLAlchemy 2.1 e mypy 2.x.

## Impactos de segurança

- O lock com versões exatas e `--locked` impede que uma instalação aceite uma versão não revisada. Isso reduz a exposição a um pacote comprometido publicado depois do lock.
- A auditoria de vulnerabilidades (`pip-audit`, ticket 10) roda sobre o ambiente sincronizado a partir do lock, então audita o que realmente é instalado.
- O uv é instalado por uma URL ou versão explícita, nunca por um script de instalação flutuante.
- O lock não contém segredos. Dependências de desenvolvimento ficam em um grupo à parte e não entram na imagem de produção (ticket 02).

## Referência ao Blueprint

Blueprint §41 (stack tecnológica) e §44 (estratégia de repositório); plano do M0, seções 2, 4 e 16. Complementa o ADR-0010.

## Critério de revisão

Revisar este ADR quando: o Python 3.12 se aproximar do fim do suporte de segurança; surgir um segundo app Python que precise compartilhar dependências; uma vulnerabilidade sem correção nas versões travadas exigir mudar de faixa; ou o ganho de adotar redis-py 8.x, SQLAlchemy 2.1, mypy 2.x ou uma versão nova do uv justificar a alteração revisada.
