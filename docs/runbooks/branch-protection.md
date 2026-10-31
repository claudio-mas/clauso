# Branch protection da `main`

Configuração que o humano aplica no GitHub depois do merge do ticket 10 do M0, para que a `main` só receba merge de Pull Request com a CI aprovada. A decisão está no [ADR-0017](../adr/0017-delivery-environments.md) e o fluxo em `docs/agents/workflow.md`. Os agentes não alteram essa configuração.

## O que a regra garante

1. **Merge só por Pull Request.** Ninguém faz push direto na `main`, nem quem administra o repositório.
2. **Job `foundation` obrigatório.** O merge só é liberado com o job `foundation` do workflow `CI` (`.github/workflows/ci.yml`) aprovado no último commit da PR.
3. **Branch atualizada antes do merge.** A branch da PR precisa conter o último commit da `main`; se a `main` andou, atualize a branch e espere o `foundation` rodar de novo.
4. **Histórico preservado.** Sem force push e sem apagar a `main`; o merge usa *merge commit* (não squash nem rebase), uma entrada por ticket.

## Antes de configurar

O GitHub só oferece o check `foundation` na lista de checks obrigatórios depois que ele rodou ao menos uma vez no repositório. Abra a PR do ticket 10 e espere o job terminar antes de criar a regra.

## Configurar (Settings → Rules → Rulesets)

Em **Settings → Rules → Rulesets → New ruleset → New branch ruleset**:

| Campo | Valor |
|---|---|
| Ruleset name | `main` |
| Enforcement status | `Active` |
| Bypass list | vazia (ninguém contorna a regra, nem administradores) |
| Target branches | `Include default branch` (a `main`) |
| Restrict deletions | marcado |
| Block force pushes | marcado |
| Require a pull request before merging | marcado; em *Allowed merge methods*, só `Merge` |
| Require status checks to pass | marcado, com `Require branches to be up to date before merging` marcado |
| Status checks that are required | `foundation`, com a origem *GitHub Actions* |

Em *Require a pull request before merging*, o número de aprovações fica a critério do humano: a revisão do Revisor é registrada pelo Regente no ticket e na PR (`docs/agents/workflow.md`), não como aprovação do GitHub. Salve com **Create**.

Em **Settings → General → Pull Requests**, deixe marcado só `Allow merge commits`, para que o botão de merge nunca ofereça squash ou rebase.

Se o repositório usar a proteção clássica (**Settings → Branches → Add classic branch protection rule**, padrão `main`), os equivalentes são: `Require a pull request before merging`, `Require status checks to pass before merging` com `Require branches to be up to date before merging` e o check `foundation`, `Do not allow bypassing the above settings`, e force push e deleção desmarcados.

## Conferir

```bash
gh api "repos/{owner}/{repo}/rules/branches/main"
```

A resposta lista as regras ativas na `main`: `pull_request`, `required_status_checks` com `foundation` e `strict_required_status_checks_policy: true`, `non_fast_forward` (force push bloqueado) e `deletion`. Com a proteção clássica, use `gh api "repos/{owner}/{repo}/branches/main/protection"`.

Um teste rápido: um `git push` direto para a `main` precisa ser recusado pelo GitHub, e uma PR com o `foundation` vermelho ou com a branch desatualizada precisa mostrar o merge bloqueado.

## O que o job `foundation` roda

`bash scripts/ci.sh`, que reproduz o ambiente do zero no projeto Compose descartável `cce-ci` e falha no primeiro controle que falhar. A lista de controles e como rodar localmente estão em `local-development.md` ("Verificação completa"). O workflow só lê o repositório, não usa segredos e não publica imagem; a publicação e o deploy são do ticket 12.

## Se o `foundation` falhar

- Leia o passo que falhou no log do job: cada etapa começa com `==> <etapa>`, e nenhuma imprime segredo.
- Reproduza localmente com `bash scripts/ci.sh` antes de mudar o workflow.
- Uma vulnerabilidade do `pip-audit` se resolve atualizando o lock (`uv lock --project apps/api --upgrade-package <pacote>`) ou registrando uma exceção específica, justificada e com validade numa alteração revisada. Nunca desligue a etapa nem marque o check como opcional.
- Nunca contorne a regra para "destravar" um merge: a regra existe para que a `main` só receba o que passou pelos controles.
