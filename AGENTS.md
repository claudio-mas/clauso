# AGENTS.md

Commercial Closing Engine (CCE): infraestrutura multi-tenant, API-first, para que um Host (ERP, CRM ou SaaS) crie propostas, acompanhe o comprador e receba o resultado do aceite, do contrato e do pagamento. Este arquivo vale para qualquer agente de código; o `CLAUDE.md` aponta para os mesmos documentos.

## Leia antes de agir

1. `CONTEXT.md`: o glossário. Use esses termos em código, testes, commits e tickets. Se um conceito não estiver lá, pare e pergunte em vez de inventar uma palavra.
2. `docs/adr/`: decisões já tomadas. Se seu trabalho contradiz um ADR, diga isso explicitamente em vez de sobrescrever.
3. O ticket que você recebeu, e a spec ou o plano que ele cita.
4. `docs/agents/workflow.md`: papéis, ciclo de status, branches e revisão.

## Onde está o trabalho

- Tracker: arquivos locais em `.scratch/<feature>/`. A spec é `spec.md` e cada ticket é `issues/NN-slug.md`. Detalhes em `docs/agents/issue-tracker.md`.
- Milestone 0 (Foundation): `.scratch/milestone-0-foundation/issues/`. Plano de referência: `Technical_Implementation_Plan_Milestone_0_Foundation.md`.
- Milestone 1 (Proposal Domain): `.scratch/milestone-1-proposal-domain/`. Só começa depois do ticket 11 do M0.
- Quando um ticket diverge do plano ou da spec que ele cita, o ticket prevalece: ele é mais recente e já incorpora as revisões.
- Visão de produto e arquitetura: `Product_Technical_Blueprint_v1.md`.

## Regras que valem para todos

- Um ticket por vez, em uma branch própria. Não faça trabalho fora do escopo do ticket.
- Teste antes do código: escreva, veja falhar, implemente, veja passar. Testes de banco rodam em PostgreSQL real, nunca em SQLite.
- Nunca diga que algo está pronto sem evidência: cite o comando executado e o resultado.
- Segredos, DSNs, chaves e dados pessoais não aparecem em código, logs, commits nem tickets.
- Não edite `CONTEXT.md`, ADRs nem arquivos de ticket, a menos que seu papel (ver `docs/agents/workflow.md`) permita.
- Nada vai para a `main` sem Pull Request, revisão aprovada e, a partir do ticket 10 do M0, CI verde. Ninguém faz push direto na `main` (detalhes em `docs/agents/workflow.md`).
- Plataforma de referência: Linux com bash, igual à CI. Comandos em PowerShell que aparecem no plano do M0 são equivalentes para quem desenvolve no Windows; scripts e runbooks novos são escritos para bash.
- Idioma: documentação e tickets em português-BR; identificadores de código e termos do glossário em inglês.
