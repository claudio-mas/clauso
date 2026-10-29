# 04: Proposal com ProposalVersion e Sections

**What to build:** um Host cria uma Proposal para um Customer existente, com as Sections tipadas do conteúdo, e a consulta de volta com a ProposalVersion corrente. Cada versão tem checksum canônico, `valid_until` opcional e fica em draft; o banco já rejeita alterar uma versão marcada como publicada ou aceita, exceto para registrar o aceite de uma versão publicada (ADR-0024). Itens de preço ainda não existem neste ticket: o Total é zero.

**Blocked by:** 01, 02

**Status:** ready-for-agent

**Spec:** milestone-1-proposal-domain/spec.md (user stories 16, 19, 24–27, 44–49, 59–60)

- [ ] `POST /proposals` com `customer_id`, `external_source`, `external_id`, título, moeda (BRL, USD ou EUR) e Sections cria a Proposal (`prop_...`, status `DRAFT`) e a versão 1 (`ver_...`) e devolve 201 com `result: created`
- [ ] `external_source` e `external_id` são obrigatórios na Proposal; o par é único por Tenant por índice único parcial, com CHECK no banco
- [ ] `GET /proposals/{id}` devolve a Proposal com a versão corrente embutida
- [ ] As Sections aceitam só os tipos hero, problem, solution, scope, timeline, pricing e next_steps, cada um com seu schema; tipo desconhecido ou campo extra retorna 422
- [ ] A Section `pricing` não aceita campos de valor; chave única por versão e ordem preservada
- [ ] Títulos, nomes e rótulos são texto puro; o corpo das Sections `problem`, `solution`, `scope` e `next_steps` aceita texto formatado restrito (ADR-0028): `p`, `br`, `strong`, `b`, `em`, `i`, `ul`, `ol`, `li`, sem atributos
- [ ] A sanitização na escrita remove atributos e tags fora da lista mantendo o texto, descarta o conteúdo de `script` e `style` e grava a forma sanitizada, que a API devolve; testes cobrem tag proibida, atributo (`onclick`, `style`, `href="javascript:..."`), entidades e HTML malformado
- [ ] `terms` aceita `payment_conditions` e `notes` opcionais em texto formatado restrito, até 16 KiB cada depois de sanitizados, e entram no checksum
- [ ] O checksum usa a forma sanitizada: o mesmo conteúdo enviado com e sem um atributo proibido gera o mesmo checksum
- [ ] Limites de 50 Sections e 256 KiB de corpo são aplicados
- [ ] `valid_until` é opcional e, quando informado no passado, retorna 422; a moeda e o `customer_id` da Proposal são imutáveis
- [ ] O checksum é estável: o mesmo conteúdo em ordem de chaves diferente gera o mesmo checksum, e conteúdo diferente gera outro
- [ ] Criar grava AuditEvent; o ator é o ID da API key; scopes `proposal:*` são exigidos
- [ ] Proposal, Customer ou versão de outro Tenant é 404; FK composta impede referência cruzada entre Tenants no banco
- [ ] O gatilho do banco (ADR-0024) deixa livre a versão sem `published_at` e `accepted_at`; com algum deles preenchido, rejeita DELETE e qualquer UPDATE, exceto preencher o outro carimbo ainda nulo sem mudar nenhuma outra coluna. Teste: como migrator, preencher `published_at`; depois, como `cce_app`, preencher `accepted_at` funciona, e alterar conteúdo, Total, `valid_until` ou um carimbo já preenchido falha
- [ ] RLS forçada nas tabelas novas, testes de banco como `cce_app`; migration sobe e desce
