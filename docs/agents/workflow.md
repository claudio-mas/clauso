# Fluxo de trabalho dos agentes

Como Regente, Programador e Revisor trabalham juntos neste repositório. Vale para o Maestri ou para qualquer outro orquestrador.

## Papéis

| Papel | Pode | Não pode |
|---|---|---|
| **Regente** | Ler tudo, escolher o próximo ticket, delegar, mudar o `Status:` dos tickets, registrar comentários, fazer o merge na `main` depois da aprovação, propor mudanças em `CONTEXT.md` e ADRs ao humano | Escrever código de produção; alterar `CONTEXT.md` ou ADRs sem aprovação do humano; fazer push |
| **Programador** | Implementar um ticket em uma branch própria, escrever testes, rodar lint, tipos e testes, fazer commits na própria branch | Mudar `Status:`, editar tickets, `CONTEXT.md` ou ADRs, fazer merge, fazer push |
| **Revisor** | Ler o diff da branch, rodar testes, devolver parecer | Editar código, tickets, `CONTEXT.md` ou ADRs |
| **Revisor de segurança** | O mesmo Revisor, com foco em isolamento e segredos. Obrigatório nos tickets 05, 06 e 07 do M0 e em qualquer ticket que mude RLS, grants, autenticação ou transações | Idem ao Revisor |
| **Humano** | Tudo: decisões, push, aprovação de ADRs e do glossário | |

O Regente atribui o papel e é o único canal entre Programador e Revisor. Os dois não se falam diretamente.

## Ciclo de status

O estado de cada ticket fica na linha `Status:` do próprio arquivo. Só o Regente a altera.

```text
ready-for-agent ──► in-progress ──► in-review ──► done
                        ▲               │
                        └── changes ────┘
```

| Status | Significa |
|---|---|
| `ready-for-agent` | Pronto para pegar. Só vale se todos os tickets de "Blocked by" estiverem `done`. |
| `in-progress` | Um Programador está implementando. Só um ticket por Programador. |
| `in-review` | A branch está pronta e aguarda o parecer do Revisor. |
| `done` | Aprovado e mergeado na `main`. |
| `blocked` | Há uma decisão ou pendência do humano. Registrar o motivo em `## Comments`. |

Quando o Revisor pede mudanças, o ticket volta de `in-review` para `in-progress`.

## Escolha do ticket (fronteira)

O Regente só delega um ticket cujos bloqueios estejam todos `done`. Se vários estão livres, escolhe o de menor número. Tickets paralelos só são delegados a Programadores diferentes quando cada um tem branch e diretório de trabalho próprios (`git worktree`). Se não houver um segundo Programador, siga em fila.

## Branches e commits

- Uma branch por ticket, nomeada pelo marco e pelo arquivo do ticket: `m0/01-projeto-python-config-ids`, `m1/02-customers`.
- Commits pequenos, com mensagens no estilo do plano (`chore:`, `feat:`, `test:`, `docs:`, `ci:`). O M0 define a mensagem final de cada tarefa na seção 17 do plano.
- O merge na `main` é feito pelo Regente com `git merge --no-ff`, depois da aprovação, para manter uma entrada por ticket no histórico.
- Ninguém faz push. O humano envia a `main` para o remoto.
- Nunca reescrever a história da `main`.

## Definição de pronto de um ticket

O Programador só pede revisão quando, para cada critério de aceite do ticket:

1. Existe um teste que falhou antes e passa agora (ou, em infraestrutura, uma verificação executada).
2. Lint, tipos e a suíte do escopo do ticket passam.
3. Os comandos rodados e o resultado estão listados no pedido de revisão.
4. Nada fora do escopo foi alterado e não há segredos no diff.

## Revisão

O Revisor recebe o nome da branch e o caminho do ticket. Verifica:

- Cada critério de aceite do ticket está atendido e provado por teste ou comando.
- O diff usa os termos de `CONTEXT.md` e não contradiz um ADR (se contradisser, aponta o ADR).
- Isolamento entre Tenants, segredos e logs seguem o plano e o Blueprint.
- Os testes provam o comportamento: um teste que passa sem a implementação é um problema.
- Há código ou documentação fora do escopo do ticket.

O parecer é "aprovado" ou uma lista numerada de problemas, cada um com severidade (bloqueante, importante, sugestão). Só os bloqueantes e importantes impedem a aprovação.

O Regente registra o parecer no fim do arquivo do ticket, sob `## Comments`, com a data. Depois de três rodadas de correção sem aprovação, o ticket vai para `blocked` e o humano decide.

## Decisões do humano

O Regente para e pergunta ao humano quando:

- Um conceito não está no `CONTEXT.md` ou duas definições se contradizem.
- A implementação exige contradizer um ADR ou o Blueprint.
- Um ticket parece errado, grande demais ou com bloqueios incorretos.
- A decisão envolve hospedagem, custo, plataforma, dados reais ou qualquer coisa fora do repositório.

O humano decide, e o Regente registra a decisão no ticket. Só então, se for o caso, propõe uma entrada em `CONTEXT.md` ou um ADR.

## Atualização de documentos

- `CONTEXT.md` e ADRs só mudam por pedido do humano, na hora em que um termo ou uma decisão se resolve.
- Os tickets não são reescritos depois de `done`; novos achados viram um ticket novo ou um comentário.
- O ticket 11 do M0 e o ticket 10 do M1 existem para reconciliar a documentação com o que foi realmente implementado.
