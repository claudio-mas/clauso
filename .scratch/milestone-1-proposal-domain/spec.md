Status: ready-for-agent
Type: spec

# Milestone 1 — Proposal Domain

## Problem Statement

Depois do Milestone 0, o CCE tem uma Foundation sem nenhum conceito comercial: existe Tenant, API key, RLS e observabilidade, mas um Host (ERP, CRM ou SaaS) ainda não consegue enviar um Quote e receber de volta uma Proposal estruturada. Sem o domínio de proposta não há o que publicar (M2), acompanhar (M4) ou fechar (M5), e a promessa central do produto, "um ERP cria uma proposta remotamente", não pode ser exercitada nem sequer em teste.

Do ponto de vista do Host, faltam quatro coisas: cadastrar seus Customers e Contacts no CCE com a identificação que ele já usa; enviar um orçamento e ter certeza de que o Total que o CCE guarda é o mesmo que o dele; reenviar o mesmo Quote sem criar duplicatas; e ter a garantia de que os dados de um Tenant jamais aparecem para outro.

## Solution

O M1 entrega, pela API REST `/api/v1`, o domínio de propostas em estado de rascunho: Customer, Contact, Proposal, ProposalVersion, itens de preço e Sections. Um Host autenticado por API key cria Customers e Contacts, envia um Quote como Proposal (com cliente, itens e Sections estruturadas) e recebe a Proposal e a ProposalVersion com o Total calculado pelo CCE. O reenvio do mesmo Quote (mesmo `external_source` + `external_id`) nunca duplica a Proposal: cria uma nova ProposalVersion quando o conteúdo mudou e devolve a existente quando não mudou.

Tudo é isolado por Tenant via RLS, auditado e coberto por testes de isolamento. O M1 não tem Publication, link público, template, PDF, Acceptance nem IA: toda ProposalVersion está em draft, e a trava que a torna imutável nasce no banco agora, para o M2 e o M5 apenas a acionarem.

## User Stories

### Host: Customers e Contacts

1. Como Host, quero cadastrar um Customer com o `external_id` do meu sistema, para reconciliar o cliente do CCE com o cliente do meu ERP.
2. Como Host, quero informar `external_source` junto com `external_id`, para que dois sistemas do mesmo Tenant não colidam nos identificadores.
3. Como Host, quero cadastrar Customer pessoa física ou jurídica com `document` (CPF ou CNPJ), para que a Proposal seja dirigida à parte correta.
4. Como Host, quero receber erro de validação quando o `document` tem dígitos verificadores inválidos, para não propagar cadastros errados para propostas.
5. Como Host, quero que o `document` seja aceito com ou sem pontuação e devolvido normalizado, para não ter de padronizar formato antes de enviar.
6. Como Host, quero consultar um Customer por ID público (`cus_...`), para recuperar o que o CCE guardou.
7. Como Host, quero buscar Customers por `external_id`, para achar o cliente sem guardar o ID do CCE.
8. Como Host, quero listar Customers com paginação por cursor, para sincronizar volumes grandes sem perder nem repetir itens.
9. Como Host, quero atualizar parcialmente um Customer (nome, e-mail, telefone, metadata), para refletir mudanças do meu ERP.
10. Como Host, quero que a tentativa de alterar `external_id`, `external_source` ou `tenant_id` seja recusada, para que a identidade do Customer não mude por engano.
11. Como Host, quero receber conflito (409) com o ID existente quando cadastro um Customer cujo `external_source` + `external_id` já existem, para decidir entre atualizar e ignorar.
12. Como Host, quero cadastrar Contacts de um Customer (nome, e-mail, telefone), para ter as pessoas que normalmente receberão uma Publication.
13. Como Host, quero listar, atualizar e consultar os Contacts de um Customer, para mantê-los em dia.
14. Como Host, quero anexar `metadata` (JSON pequeno) a Customers e Proposals, para guardar vendedor, filial e origem sem alterar o schema central.
15. Como Host, quero que `metadata` acima do limite de tamanho ou profundidade seja recusada com erro claro, para evitar abuso de armazenamento.

### Host: criar e reenviar Proposals

16. Como Host, quero criar uma Proposal a partir de um Quote com `external_id`, cliente, itens e Sections, para ter a proposta no CCE com uma única chamada.
17. Como Host, quero referenciar o Customer por `customer_id` ou enviá-lo inline, para escolher entre cadastro prévio e criação sob demanda.
18. Como Host, quero que um Customer inline seja obtido ou criado por `external_source` + `external_id`, sem sobrescrever dados de um Customer já existente, para que um envio de proposta não altere o cadastro por acidente.
19. Como Host, quero que a Proposal nasça com a ProposalVersion de número 1, para ter sempre uma versão corrente consultável.
20. Como Host, quero que reenviar o mesmo Quote com conteúdo alterado crie uma nova ProposalVersion da mesma Proposal, para manter o histórico em vez de duplicar a proposta.
21. Como Host, quero que reenviar o mesmo Quote com conteúdo idêntico devolva a Proposal existente sem criar versão, para poder repetir a requisição com segurança depois de timeout.
22. Como Host, quero distinguir pelo status HTTP (201 criada, 200 existente ou versão nova) o que aconteceu no envio, para tratar cada caso.
23. Como Host, quero receber erro claro quando reenvio um Quote apontando para outro Customer, para não mover uma proposta entre clientes sem perceber.
24. Como Host, quero que a moeda da Proposal seja definida na criação e não mude depois, para que todas as versões sejam comparáveis.
25. Como Host, quero informar `valid_until` (validade da oferta) por versão, para que cada versão possa mudar o prazo.
26. Como Host, quero que um `valid_until` no passado seja recusado na escrita, para não gravar uma oferta já vencida.
27. Como Host, quero consultar uma Proposal e receber junto a ProposalVersion corrente, para evitar uma segunda chamada.
28. Como Host, quero listar Proposals filtrando por `external_id`, `customer_id`, status e período de criação, para montar as telas do meu ERP.
29. Como Host, quero listar as versões de uma Proposal em ordem, com número, checksum e data, para auditar o histórico.
30. Como Host, quero consultar uma ProposalVersion específica, para ver exatamente o conteúdo e o Total daquele momento.
31. Como Host, quero criar deliberadamente uma nova ProposalVersion em `POST /proposals/{id}/versions`, para registrar uma revisão comercial relevante.
32. Como Host, quero editar uma ProposalVersion em draft, para corrigir um rascunho sem acumular versões.
33. Como Host, quero que a edição de uma versão em draft recalcule Total e checksum, para que o que está guardado nunca fique inconsistente.
34. Como Host, quero que a edição concorrente seja detectada via `If-Match` com o checksum, para não sobrescrever a alteração de outro processo.
35. Como Host, quero alterar o título e a metadata da Proposal sem criar versão, para ajustes não comerciais.

### Host: preço e Total

36. Como Host, quero enviar itens com descrição, quantidade, preço unitário, desconto e tributo, para refletir fielmente o orçamento.
37. Como Host, quero informar preços em unidades menores inteiras da moeda (centavos), para eliminar erros de ponto flutuante.
38. Como Host, quero que o CCE calcule o total de cada linha e o Total da versão, para ter uma única fonte do número.
39. Como Host, quero enviar também o meu total e receber rejeição (422, `TOTAL_MISMATCH`, com esperado e recebido) se divergir, para detectar divergência de arredondamento ou de regra antes de a proposta circular.
40. Como Host, quero informar tributos como valores por linha que o CCE soma sem recalcular, para não depender de regras fiscais do CCE.
41. Como Host, quero quantidades decimais (por exemplo horas), com regra de arredondamento documentada, para precificar serviços.
42. Como Host, quero que desconto maior que o valor bruto da linha e valores negativos sejam recusados, para impedir Totais absurdos.
43. Como Host, quero identificar cada item por um `external_id` opcional, para reconciliar linhas com o meu orçamento.

### Host: Sections

44. Como Host, quero enviar o conteúdo como Sections tipadas (hero, problem, solution, scope, timeline, pricing, next_steps), para que o renderer futuro monte layouts diferentes sem reconstruir a proposta.
45. Como Host, quero que um tipo de Section desconhecido ou um campo fora do schema seja recusado, para nunca guardar conteúdo que o renderer não entende.
46. Como Host, quero que a Section `pricing` não carregue valores próprios e use o snapshot de preços, para que o número exibido não possa divergir do Total.
47. Como Host, quero ordenar as Sections e identificá-las por chave estável dentro da versão, para que o tracking futuro se refira a elas.
48. Como Host, quero que texto com HTML seja tratado como texto puro, para que o conteúdo editável não vire vetor de injeção na renderização futura.
49. Como Host, quero limites claros de quantidade e tamanho de Sections e itens, para saber o que posso enviar.

### Segurança, isolamento e operação

50. Como Tenant, quero que nenhum dado meu apareça em requisições autenticadas por outro Tenant, para ter confiança no isolamento.
51. Como Tenant, quero que referenciar um Customer ou Proposal de outro Tenant falhe como inexistente (404), para não revelar sua existência.
52. Como Host, quero scopes separados `customer:read`, `customer:write`, `proposal:read` e `proposal:write`, para dar às chaves o mínimo necessário.
53. Como operador, quero emitir chaves com os novos scopes pela CLI administrativa, para dar acesso sem tocar no banco.
54. Como operador, quero que chaves já emitidas só ganhem os novos scopes por reemissão, para que nenhuma chave existente amplie acesso sozinha.
55. Como Host, quero erros no formato padrão com `code` estável (incluindo `TOTAL_MISMATCH` e `PRECONDITION_FAILED`), para tratá-los em código sem ler mensagens.
56. Como auditor do Tenant, quero um registro de auditoria de cada criação e alteração de Customer, Contact, Proposal e ProposalVersion, com ator, ação, valores antigo e novo, IP truncado e `request_id`, para reconstruir quem mudou o quê.
57. Como auditor, quero que o registro de auditoria seja somente de acréscimo, inclusive para o próprio runtime, para que o histórico não seja reescrito.
58. Como operador, quero que logs não contenham nome, e-mail, documento nem conteúdo de propostas, para não vazar dados pessoais por observabilidade.
59. Como desenvolvedor do CCE, quero que uma ProposalVersion marcada como publicada ou aceita seja rejeitada para UPDATE e DELETE pelo próprio banco, para que o M2 e o M5 herdem a imutabilidade (ADR-005) sem depender de disciplina de código.
60. Como desenvolvedor do CCE, quero que referências entre tabelas só aceitem registros do mesmo Tenant, para que um bug de aplicação não consiga ligar dados de Tenants diferentes.
61. Como desenvolvedor de Host, quero o contrato OpenAPI atualizado com paths, scopes exigidos e esquemas sem campos internos, para gerar clientes e SDK depois.

## Implementation Decisions

### Escopo e dependências

- O M1 depende do gate do M0 (RLS, sessão transacional por Tenant, autenticação por API key, erros padronizados, CLI administrativa). Nada do M1 enfraquece essas garantias.
- O M1 segue a numeração do Blueprint (Milestone 1 — Proposal Domain) e implementa apenas Customer, Contact, Proposal, ProposalVersion, itens de preço, Sections, auditoria básica e as migrations correspondentes.
- Vocabulário: Tenant, Host, Customer, Contact, Quote, Proposal, ProposalVersion, Offer validity, Total e Commercial status seguem o `CONTEXT.md`. Respeitam-se os ADRs 0018 a 0022: o M1 aplica o 0019 (Total calculado pelo CCE) e o 0020 (um Quote, uma Proposal) e prepara a imutabilidade e o congelamento que os 0018, 0021 e 0022 exigirão depois.

### Módulos

- **Customers**: Customer e Contact, com repository, service, schemas e rotas. Validação de CPF e CNPJ (dígitos verificadores) e normalização para só dígitos.
- **Proposals**: Proposal e ProposalVersion, com repository, service, schemas e rotas. Orquestra criação, reenvio idempotente e criação/edição de versões.
- **Pricing**: módulo de domínio puro, sem I/O, que recebe itens e devolve o snapshot de preços. Interface pública estreita: calcular o snapshot a partir de itens e moeda, e conferir contra um total informado.
- **Content**: esquema das Sections (união discriminada por tipo), validação de limites e canonicalização para o checksum.
- **Audit**: serviço compartilhado para gravar AuditEvent dentro da mesma transação do dado alterado.
- Os módulos seguem a regra do monólito modular: o domínio não conhece FastAPI; rotas só traduzem HTTP.

### Modelo de dados

- Novas tabelas: customers, contacts, proposals, proposal_versions e audit_events, todas no schema `cce`, com `tenant_id` obrigatório, ENABLE e FORCE ROW LEVEL SECURITY e policies de isolamento no mesmo padrão do M0.
- Cada tabela de domínio tem unicidade composta `(tenant_id, id)`, e as chaves estrangeiras entre tabelas de domínio são compostas `(tenant_id, referência)`, de modo que ligar dados de Tenants diferentes é impossível no nível do banco.
- Unicidade por Tenant de `(external_source, external_id)` em customers e em proposals, quando `external_id` existe.
- IDs públicos novos: `cus_`, `ctc_`, `prop_`, `ver_` e `aud_`; UUID4 internamente, como no M0.
- **Proposal**: `external_source`, `external_id`, `customer_id`, `status`, `title`, `currency`, `current_version_id`, `metadata`, `created_by`, `created_at`, `updated_at`. No M1 `status` é sempre `DRAFT`; READY e PUBLISHED chegam com a publicação (M2). `customer_id` e `currency` são imutáveis. A validade da oferta não pertence à Proposal.
- **ProposalVersion**: `version_number` (sequencial por Proposal a partir de 1, sem lacunas), `content` (Sections), `pricing_snapshot`, `terms_snapshot`, `valid_until` (anulável, no futuro quando informada), `checksum`, `published_at` e `accepted_at` (anuláveis, só usados a partir do M2 e do M5), `created_at`, `created_by`. `current_version_id` da Proposal aponta sempre para a de maior número.
- O `pricing_snapshot` é JSONB que embute os itens e seus totais calculados, porque a versão é um retrato imutável; não há tabela relacional de itens no M1.
- `checksum` é SHA-256 sobre a forma canônica (chaves ordenadas, sem espaços, números em forma fixa) de conteúdo, preços, termos e `valid_until`.
- **AuditEvent**: ator, ação, entidade, ID da entidade, valores antigo e novo, instante, IP truncado (IPv4 /24, IPv6 /48) e `request_id`. O runtime só tem INSERT e SELECT; nunca UPDATE, DELETE ou TRUNCATE.
- Trava no banco: um gatilho rejeita UPDATE e DELETE de qualquer linha de proposal_versions cujo `published_at` ou `accepted_at` não seja nulo. No M1 nenhuma versão chega a esse estado pela API; o gatilho existe para que o M2 e o M5 já nasçam protegidos.
- `created_by` registra o ator como o ID da API key, porque usuários humanos só existem depois da sessão embedded; passa a referenciar `User` quando ele existir.

### Preço e Total (ADR-0019)

- Valores monetários são inteiros em unidades menores da moeda da Proposal; moedas aceitas BRL, USD e EUR, as mesmas do Tenant.
- Cada item tem descrição, quantidade decimal positiva com até 3 casas, preço unitário, `discount` e `tax` (inteiros não negativos) e `external_id` opcional.
- Total bruto da linha = quantidade × preço unitário, arredondado para o inteiro mais próximo com meio para cima. Total da linha = bruto − desconto + tributo. Total da versão = soma dos totais das linhas.
- Desconto não pode exceder o bruto da linha. O Total nunca é negativo. Versão sem itens é válida em draft e tem Total zero; a validação de "publicável" é do M2.
- Tributo é valor opaco informado pelo Host; o CCE só soma.
- Se o Host enviar `total`, ele é conferido; divergência retorna 422 `TOTAL_MISMATCH` com os valores esperado e recebido, e nada é gravado.

### Sections

- Tipos: hero, problem, solution, scope, timeline, pricing e next_steps, cada um com schema próprio; a Section `pricing` não tem campos de valor e é renderizada a partir do snapshot.
- Cada Section tem uma chave única dentro da versão e a ordem é a do array. Campos de texto são texto puro: não há interpretação de marcação e a renderização futura precisa escapar.
- Limites iniciais (ajustáveis por configuração): 50 Sections, 200 itens, 256 KiB de corpo de requisição, `metadata` até 8 KiB com profundidade máxima 3.

### Criação, reenvio e versões (ADR-0020)

- `POST /api/v1/proposals` com um `external_source` + `external_id` ainda inexistentes cria a Proposal e a versão 1 (201). Com o par já existente, compara o checksum do conteúdo enviado com o da versão corrente: igual devolve a Proposal sem alteração (200); diferente cria uma nova ProposalVersion (200). Cliente diferente do original retorna 422 `CUSTOMER_MISMATCH`.
- A regra "após o Acceptance o reenvio é rejeitado" pertence ao M5; o M1 não tem Acceptance e deixa o ponto de extensão documentado, sem código de erro implementado.
- O cliente pode vir como `customer_id` ou como objeto inline; o inline faz obter-ou-criar por `external_source` + `external_id` e nunca altera um Customer existente (atualizações usam `PATCH /customers/{id}`).
- Em draft a ProposalVersion é editável por `PUT` até a primeira Publication (M2). `POST /proposals/{id}/versions` cria deliberadamente outra versão; a edição não cria versão. A numeração é alocada sob bloqueio da linha da Proposal, para que a concorrência nunca produza números repetidos ou lacunas.
- `PUT` aceita `If-Match` com o checksum; divergência retorna 412 `PRECONDITION_FAILED`. A ausência do cabeçalho não bloqueia. O ETag devolvido é o checksum.
- `PATCH /proposals/{id}` altera só `title` e `metadata`, sem nova versão, e é auditado.

### API

- Recursos: customers, contacts de um customer, proposals e versions de uma proposal, todos sob `/api/v1`, nos moldes do Blueprint (§34) e do plano do M0.
- Listagens usam cursor opaco, `limit` padrão 50 e máximo 200, ordenadas por `created_at` decrescente com desempate por ID.
- Novos scopes: `customer:read`, `customer:write`, `proposal:read`, `proposal:write`. A restrição de scopes no banco e a lista da aplicação são estendidas por migration; a CLI aceita emitir chaves com eles. Chaves existentes não mudam.
- Novo código de erro HTTP 412 `PRECONDITION_FAILED` e erros de domínio 422 `TOTAL_MISMATCH` e `CUSTOMER_MISMATCH`, somados à tabela do M0. Violações de unicidade retornam 409 `CONFLICT` com o ID existente em `details`.
- Contrato OpenAPI documenta os scopes exigidos por operação (extensão já usada no M0) e não expõe campos internos.
- Referência a recurso de outro Tenant resulta em 404, nunca 403, e as respostas de erro não revelam existência.

### Observabilidade e dados pessoais

- Logs seguem a regra do M0: sem nome, e-mail, documento, conteúdo de propostas nem corpo de requisição; apenas IDs, rota (template) e códigos.
- A auditoria guarda valores antigos e novos, o que inclui dados pessoais do Customer; sua retenção e anonimização seguem a política do Tenant e não são implementadas no M1.

## Testing Decisions

- Um bom teste exercita comportamento externo, nunca estrutura interna: faz uma requisição (ou abre uma conexão como o role runtime) e afirma sobre a resposta, o estado visível por outra requisição ou o erro do banco. Não afirma nomes de funções, ordem de chamadas nem formato de tabela além do contrato.
- **Seam principal (API HTTP)**: cliente httpx com `LifespanManager` contra PostgreSQL 17 real, com Tenants A e B e chaves de cada um, nos moldes de `test_tenant_api.py` do plano do M0. Cobre todas as user stories de comportamento: ciclo de Customers e Contacts, criação e reenvio de Proposal (criada, idêntica, alterada, cliente divergente), versões e edição em draft, `If-Match`, cálculo do Total e `TOTAL_MISMATCH` com casos de arredondamento e limites, validação de Sections e limites, paginação e filtros, scopes (401/403/404 uniformes), formato de erro e logs sem dados pessoais.
- **Isolamento via API**: para cada recurso novo, A não lê, não altera e não referencia dado de B; `X-Tenant-ID` falso não muda o resultado (mesma ideia do `test_header_cannot_switch_tenant`).
- **Seam secundário (banco, RLS, grants e migrations)**, no padrão de `test_rls.py`, `test_db_roles.py`, `check_schema.py` e `test_migration_chain.py`: para cada tabela nova, ausência de contexto retorna zero linhas, A não vê B, `WITH CHECK` rejeita escrita para B, FORCE RLS vale para o owner; chave estrangeira composta recusa referência cruzada entre Tenants; o runtime não consegue UPDATE, DELETE ou TRUNCATE em audit_events; o gatilho rejeita UPDATE e DELETE de ProposalVersion com `published_at` ou `accepted_at` preenchido (o teste preenche o campo como migrator, em banco descartável); a cadeia de migrations sobe e desce em banco descartável; `check_schema.py` falha se faltar FORCE RLS, policy ou grant esperado nas tabelas novas.
- O módulo Pricing é verificado pela API, com uma tabela de casos (quantidade fracionária, arredondamento no meio, desconto igual ao bruto, tributo, lista vazia). Se as combinações crescerem a ponto de a API ficar lenta ou ilegível, a decisão de adicionar um seam de domínio puro volta para revisão.
- Os casos de segurança (isolamento, trava de versão, auditoria somente de acréscimo, scopes) são obrigatórios independentemente do percentual de cobertura; a meta global de 85% do M0 continua valendo.
- Dados de teste são sintéticos; documentos usados nos testes têm dígitos verificadores válidos gerados por helper.
- O contrato OpenAPI é testado em `tests/contracts` no mesmo estilo de `test_openapi.py`: paths exatos, scopes documentados, schemas sem campos internos e códigos de erro esperados.

## Out of Scope

- Publication, link público, templates, Brand, renderer e PDF (M2). Os estados `READY` e `PUBLISHED` da Proposal e o estado `ACTIVE` da Publication também ficam para o M2.
- Integração de referência com o Mini ERP (M3), Engagement, ViewSession e webhooks (M4), Acceptance, Evidence Package, Contract e Payment (M5), IA (M6), SDKs, Embedded UI e Web Components.
- Entidade `User`, RBAC humano e provisionamento sob demanda; o ator é a API key.
- Outbox e eventos de domínio (`proposal.created` etc.): entram junto com os webhooks no M4; a rastreabilidade do M1 vem do AuditEvent.
- Cabeçalho `Idempotency-Key` genérico: no M1 não há efeito externo, e o reenvio é naturalmente idempotente pelos identificadores do Host.
- Modelagem de condições de pagamento e `MilestoneSnapshot` dentro do pricing snapshot: o formato é extensível, mas o conteúdo é do M5 (ADR de Payment 1:N).
- Exclusão, arquivamento e anonimização de Customers, retenção de auditoria e fluxos de LGPD.
- Remoção ou cancelamento de Proposals em draft.
- Descontos e tributos calculados no nível da proposta, moedas além de BRL, USD e EUR, e múltiplas moedas por Proposal.
- Busca textual e filtros avançados além dos listados.

## Further Notes

Premissas assumidas nesta spec que podem ser revistas antes da implementação:

- **Drafts acumulados**: como a edição é in place, só o `POST /versions` explícito e o reenvio de Quote com conteúdo diferente criam versões; um Host que reenvia a cada pequena alteração acumulará versões. Aceito por manter o histórico comercial explícito (ADR-0020).
- **Quantidade decimal com 3 casas e arredondamento meio para cima** foram escolhidos para cobrir horas e unidades fracionárias; se o Mini ERP usar outra regra, o erro aparecerá como `TOTAL_MISMATCH` e a regra deve ser ajustada aqui, não no Host.
- **Validação de CPF e CNPJ** assume Customers brasileiros; documentos estrangeiros precisarão de um tipo de documento explícito quando surgirem.
- **IP truncado na auditoria** segue a configuração de privacidade do Blueprint (§49); a política final de retenção é decisão do Tenant.
- **Limites de tamanho** são valores iniciais de configuração, sem justificativa de carga real.
- **Eventos adiados**: o Definition of Done do Blueprint (§76) inclui "events"; o M1 o cumpre por auditoria e deixa os eventos para o M4, quando o outbox existir. Se for preferível ter os eventos de domínio desde já, o outbox precisa antecipar.
- **Trava no banco** (gatilho em ProposalVersion) é uma decisão de arquitetura não registrada em ADR; vale um ADR quando o M2 a acionar.

Referências: `CONTEXT.md`, ADRs 0018 a 0022, Product & Technical Blueprint v1.1 (§12 a §14, §21, §33 a §36, §47, §49, §69 a §76 e §84) e o plano do Milestone 0 (seções 10, 11, 12 e 14).
