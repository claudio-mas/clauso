# ADR-0017: Ambientes de entrega, migrations em job separado e CI obrigatória antes do merge

**Data:** 31/10/2026
**Status:** Proposto

## Contexto

O Blueprint define quatro ambientes (§55: `local`, `test`, `staging`, `production`), um pipeline que vai do lint à produção (§54) e exige que as migrations rodem separadas da inicialização da aplicação (§54). O plano do M0 previa para este ADR os ambientes, as migrations separadas e a promoção da imagem por digest (seção 16), com release e CD nas seções 15.2 e 15.3.

Na revisão de 15/10/2026, o humano tirou do M0 a publicação de imagem, o deploy e a promoção, porque dependem da escolha de hospedagem e do gerenciador de segredos, ainda abertas (ticket 12). O que já dá para decidir sem plataforma: quais ambientes existem e o que muda entre eles, como a migration roda em relação à API e o que precisa estar verde antes de um merge na `main`. Até o ticket 09, a evidência de cada ticket era uma execução local registrada na Pull Request, e nada impedia o merge de uma mudança que quebrasse um controle.

## Alternativas

- **Migrations no startup da API** (`alembic upgrade head` no entrypoint ou no lifespan). Réplicas concorrentes disputariam a mesma migration, o processo da API precisaria da credencial do `cce_migrator` e uma migration que falha viraria um loop de reinício da API.
- **Continuar só com a execução local registrada na PR.** Depende da disciplina de quem roda e de quem revisa, e não reproduz o ambiente do zero: uma máquina com volumes, caches ou dotenv antigos esconde problemas.
- **Pipeline escrito só no YAML do GitHub Actions.** Não roda igual na máquina de quem desenvolve, e uma falha da CI vira tentativa e erro no próprio GitHub.
- **Publicar a imagem já no M0** (o `release.yml` da seção 15.2, para o GHCR). Sem plataforma nem consumidor, publicaria um artefato que ninguém promove e exigiria `packages: write` antes de existir uma política de entrega.
- **Mais ambientes agora** (sandbox público, preview por PR). O Blueprint pede um sandbox antes de clientes externos (§55), mas nada no M0 o consome.

## Decisão

### Quatro ambientes

`CCE_ENV` aceita só `local`, `test`, `staging` e `production` (`cce.shared.config`).

| Ambiente | Onde roda | Configuração | Regras |
|---|---|---|---|
| `local` | Compose do repositório, projeto `cce` | dotenv gerado por `scripts/init_env.py` | Swagger ligado por padrão; não é manifesto de produção |
| `test` | Compose, projeto `cce-test` (local) ou `cce-ci` (CI) | dotenv gerado, banco `cce_test*` | Única fonte de dados descartáveis: `downgrade`, reset de schema e `down --volumes` só aqui |
| `staging` | Plataforma do ticket 12 | só o ambiente do processo; dotenv é recusado | TLS obrigatório para PostgreSQL e Redis, Swagger desligado |
| `production` | Plataforma do ticket 12 | idem `staging` | idem `staging`; nunca recebe chave de teste |

O código é o mesmo nos quatro. O que muda é a configuração, validada no startup, que recusa combinações inseguras (por exemplo, dotenv ou Swagger em `staging` e `production`).

### Migrations em job separado

- A migration é um processo próprio, que roda `alembic upgrade head` uma vez com a credencial do `cce_migrator` e termina. No Compose é o serviço `migrate`, da mesma imagem da API. A API só inicia depois que ele termina com sucesso.
- O processo da API recebe só a credencial de runtime (`cce_app`), nunca a URL do migrator nem a senha do `postgres`. `scripts/check_compose_secrets.py` confere isso na composição renderizada.
- Na CI, o job de migration roda à parte (`compose run --rm --no-deps migrate`) antes de a API subir (`compose up --no-deps api`), como na ordem de promoção que o ticket 12 vai detalhar: uma única migration por ambiente, sem duas concorrentes, e a API só depois do código 0.

### CI obrigatória antes do merge

- `.github/workflows/ci.yml` tem um único job, `foundation`, disparado em `pull_request` e em `push` na `main` (e manualmente). Ele só instala o uv fixado (ADR-0011) e roda `bash scripts/ci.sh`.
- `scripts/ci.sh` é o pipeline, e roda igual localmente. Ele reproduz o ambiente do zero num projeto Compose descartável (`cce-ci`, banco `cce_test_ci`, segredos novos) e para no primeiro controle que falhar: lock, lint e formato, tipos, configuração e segredos do Compose, migrations até o head, testes de migration, suíte de unidade, integração e contrato com cobertura de ao menos 85%, `alembic check`, `scripts/check_schema.py`, auditoria de dependências (`pip-audit`), build da imagem, job de migration, API pelo Compose e smoke com dois Tenants por HTTP.
- O workflow só lê o repositório (`permissions: contents: read`), não usa segredos do GitHub, não persiste a credencial do checkout e fixa cada action por SHA completo, com a versão no comentário.
- Uma vulnerabilidade apontada pelo `pip-audit` nunca é ignorada automaticamente: atualiza-se o lock ou registra-se uma exceção específica, justificada e com validade, numa alteração revisada.
- A `main` só recebe merge por Pull Request, com o job `foundation` aprovado e a branch atualizada com a `main` (`docs/runbooks/branch-protection.md`). Quem aplica essa configuração no GitHub é o humano.

### Promoção por digest: adiada para o ticket 12

A unidade de promoção entre `staging` e `production`, a publicação da imagem e o rollback ficam para o ticket 12, junto com a hospedagem. A direção do plano (seções 15.2 e 15.3: imagem construída do mesmo commit que passou na CI, publicada com label de revisão, promovida pelo digest `sha256` e nunca por tag) fica registrada como ponto de partida, não como decisão. No M0 não existe `release.yml` nem etapa de publicação de imagem, e este ADR será revisto pelo ticket 12.

## Consequências

- Nenhuma mudança chega à `main` sem o pipeline verde, depois que o humano ativar a branch protection. Até lá, a regra vale pelo `docs/agents/workflow.md`.
- Quem desenvolve reproduz a CI com `bash scripts/ci.sh`. O script usa as mesmas portas de loopback dos projetos `cce` e `cce-test`, que não podem estar de pé ao mesmo tempo, e apaga no fim só o projeto `cce-ci` que criou.
- O pipeline é longo (constrói a imagem duas vezes e roda toda a suíte de integração contra PostgreSQL e Redis reais), e cada PR espera por ele.
- Os testes de Compose aceitam só os projetos descartáveis `cce-test` e `cce-ci` (`CCE_TEST_COMPOSE_PROJECT`), e os meta-testes que rodam pytest em subprocesso não gravam dados de cobertura (`tests/child_pytest.py`).
- Atualizar uma action, o uv ou uma dependência é uma alteração revisada; uma vulnerabilidade nova publicada contra o lock faz a CI falhar até o lock ser atualizado.
- `staging` e `production` existem na configuração, mas ainda não têm plataforma, deploy nem smoke próprios.

## Impactos de segurança

- Uma Pull Request, inclusive de fork, roda com token só de leitura, sem segredos do repositório e sem permissão de publicar pacotes. Não há credencial de deploy para vazar.
- Os segredos da composição são gerados a cada execução, ficam num arquivo `0600` num diretório temporário, são verificados sem serem exibidos e somem no teardown. As chaves do smoke passam pelo ambiente do processo, nunca pelo argv nem pela saída.
- O runtime nunca recebe a credencial do migrator, e o job de migration é o único processo com ela.
- Pin por SHA completo protege contra a troca de uma tag de action. `persist-credentials: false` tira o token do checkout do `.git` que o pipeline usa.
- O teardown destrutivo (`down --volumes`) é restrito ao projeto `cce-ci` criado pelo script, e o script recusa rodar se esse projeto sobrou de uma execução interrompida.

## Referência ao Blueprint

Blueprint §54 (CI/CD: pipeline e migrations separadas da inicialização) e §55 (ambientes). Plano do M0, seção 15.1, Tarefa 10 e nota de revisão (seções 15.2 e 15.3 movidas para o ticket 12). Complementa os ADRs 0011 (uv fixado e `pip-audit`), 0012 e 0013 (credenciais separadas por role) e 0015 (TLS do Redis fora de `local` e `test`).

## Critério de revisão

Revisar quando: o ticket 12 escolher a hospedagem e decidir a publicação, a promoção por digest e o rollback; um app que não é Python (Node) entrar no pipeline; o sandbox do Blueprint (§55) for criado; o pipeline passar de 30 minutos; ou o GitHub Actions deixar de ser a plataforma de CI.
