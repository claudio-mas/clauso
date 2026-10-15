# 04: Proposal com ProposalVersion e Sections

**What to build:** um Host cria uma Proposal para um Customer existente, com as Sections tipadas do conteúdo, e a consulta de volta com a ProposalVersion corrente. Cada versão tem checksum canônico, `valid_until` opcional e fica em draft; o banco já rejeita alterar uma versão marcada como publicada ou aceita. Itens de preço ainda não existem neste ticket: o Total é zero.

**Blocked by:** 01, 02

**Status:** ready-for-agent

**Spec:** milestone-1-proposal-domain/spec.md (user stories 16, 19, 24–27, 44–49, 59–60)

- [ ] `POST /proposals` com `customer_id`, `external_source`, `external_id`, título, moeda (BRL, USD ou EUR) e Sections cria a Proposal (`prop_...`, status `DRAFT`) e a versão 1 (`ver_...`) e devolve 201
- [ ] `GET /proposals/{id}` devolve a Proposal com a versão corrente embutida
- [ ] As Sections aceitam só os tipos hero, problem, solution, scope, timeline, pricing e next_steps, cada um com seu schema; tipo desconhecido ou campo extra retorna 422
- [ ] A Section `pricing` não aceita campos de valor; chave única por versão e ordem preservada
- [ ] Texto é tratado como texto puro; limites de 50 Sections e 256 KiB de corpo são aplicados
- [ ] `valid_until` é opcional e, quando informado no passado, retorna 422; a moeda e o `customer_id` da Proposal são imutáveis
- [ ] O checksum é estável: o mesmo conteúdo em ordem de chaves diferente gera o mesmo checksum, e conteúdo diferente gera outro
- [ ] Criar grava AuditEvent; o ator é o ID da API key; scopes `proposal:*` são exigidos
- [ ] Proposal, Customer ou versão de outro Tenant é 404; FK composta impede referência cruzada entre Tenants no banco
- [ ] O gatilho do banco rejeita UPDATE e DELETE de versão com `published_at` ou `accepted_at` preenchido (teste como migrator preenchendo o campo, depois tentando como `cce_app`)
- [ ] RLS forçada nas tabelas novas, testes de banco como `cce_app`; migration sobe e desce
