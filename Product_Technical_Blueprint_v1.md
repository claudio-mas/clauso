# Product & Technical Blueprint v1
## Commercial Closing Engine

**Versão:** 1.3  
**Data:** 28/10/2026  
**Status:** Baseline para planejamento técnico, alinhada ao glossário `CONTEXT.md`  
**Nome provisório do produto:** Commercial Closing Engine — CCE

> **Origem:** consolidação do Blueprint v1 a partir de uma conversa de análise do Fyrmo. Seções 1–41 recuperadas da conversa; seções 42–93 e conclusão fornecidas pelo autor nesta sessão. O conteúdo e as decisões foram preservados, com hierarquia Markdown, tabelas e blocos de código normalizados. Marcadores internos de citação foram removidos. As referências às capacidades do Fyrmo registram a análise original, sem nova verificação comercial. Documento de especificação; não declara funcionalidades já implementadas.

**Documento relacionado:** [Technical Implementation Plan — Milestone 0: Foundation](Technical_Implementation_Plan_Milestone_0_Foundation.md).

**Alinhamento ao glossário (v1.1):** os termos de domínio deste documento seguem o [CONTEXT.md](CONTEXT.md), e as decisões que o alinhamento exigiu estão registradas nos ADRs 0018–0022 em `docs/adr/`. Mudanças em relação à v1.0: (1) Tenant, Host, Customer, Contact e Recipient passam a ter definições fixas (§10–§11); (2) `Workspace` sai do modelo até existir um caso real; (3) a validade da oferta passa da Proposal para a ProposalVersion (§12–§13); (4) os estados de Proposal, Publication, Contract e Payment passam a ser ciclos independentes (§15); (5) um Quote corresponde a uma única Proposal (§16, §35); (6) cada Publication é fixada em uma única ProposalVersion (§17); (7) o Total é sempre calculado pelo CCE (§21); (8) a apresentação é congelada no aceite (§25); (9) uma Proposal aceita pode ter vários Payments (§29); (10) a evidência de aceite é retida mesmo diante de pedido de exclusão (§49). O Technical Implementation Plan do Milestone 0 não foi revisado nesta passagem.

**Revisão v1.2:** (1) publicar exige um User ou uma API key com o scope `proposal:publish`, e a revisão humana obrigatória vale para versões geradas pela IA do CCE (ADR-0023, §15.1, §16, §36); (2) `READY` deixa de ser estado da Proposal: *draft* e *ready* são atributos da ProposalVersion (§15.1); (3) a trava da ProposalVersion no banco admite só as transições de publicação e aceite (ADR-0024); (4) migrations de dados percorrem os Tenants um a um, sem bypass de RLS (ADR-0025, §38); (5) os ADRs 001–010 deste Blueprint passam a existir como arquivos `docs/adr/0001`–`0010` (§73).

**Revisão v1.3:** (1) o Host de referência passa a ser o DentBudget (Orçamento Fácil), e não um Mini ERP construído para isso (ADR-0026, §63, §64, §86, §92); (2) o Total admite um desconto global da versão, informado em valor, e o CCE nunca aplica percentuais (ADR-0027, §21); (3) campos de texto longo do conteúdo e dos termos aceitam um subconjunto fechado de HTML sanitizado (ADR-0028, §14).

**Navegação:** produto e arquitetura (1–10); domínio e propostas (11–19); IA e conversação (20–24); fechamento e integração (25–40); stack e infraestrutura (41–55); MVPs e integração (56–72); decisões, riscos e roadmap (73–93).

---

## 1. Executive Summary

O produto será uma **infraestrutura plugável de Commercial Engagement & Closing** para aplicações web.

Seu propósito é permitir que ERPs, CRMs, SaaS e sistemas verticais adicionem, por integração, um fluxo comercial completo:

**oportunidade/orçamento → proposta → apresentação → acompanhamento → negociação → aceite → contrato → cobrança → retorno do resultado ao sistema hospedeiro.**

O Fyrmo é utilizado como uma das referências de capacidades. Atualmente sua oferta pública combina geração de proposta por IA, personalização visual, link interativo, rastreamento de abertura e leitura por seção, chatbot, aceite digital, Pix, PDF, envio em lote, modo apresentação, contratos recorrentes, processamento de reuniões e recursos de equipe.

O projeto, entretanto, **não será um clone do Fyrmo**.

A diferenciação arquitetural será:

> O produto não é primariamente um SaaS para criar propostas; é uma API/plataforma headless que permite que outros softwares incorporem capacidades de fechamento comercial.

O princípio central é:

> **Build once, embed everywhere.**

---

## 2. Product Vision

### 2.1 Visão

Transformar o processo de fechamento comercial em uma capability reutilizável que qualquer aplicação possa consumir.

Um desenvolvedor de ERP não deveria precisar construir novamente:

propostas, templates, tracking, PDFs, aceite, contratos, cobrança, webhooks, notificações e IA.

O CCE fornece essa infraestrutura.

---

## 3. Product Positioning

### 3.1 Categoria

**Commercial Closing Infrastructure**

ou, comercialmente:

**Proposal, Contract & Payment Engine for SaaS and ERP.**

### 3.2 Proposta de valor

Para fabricantes de software:

> Adicione propostas inteligentes, acompanhamento, aceite, contratos e pagamentos ao seu sistema por API, SDK ou componentes incorporáveis.

Para aplicações próprias:

> Padronize o fluxo comercial em todos os produtos sem reimplementar infraestrutura de fechamento.

### 3.3 Posicionamento futuro

O produto poderá operar simultaneamente em dois modelos:

| Modelo | Utilização |
|---|---|
| Embedded/B2B2B | Empresas de software incorporam o CCE em seus produtos |
| Standalone SaaS | Empresas utilizam diretamente o painel do CCE |

A arquitetura será otimizada primeiro para **Embedded/B2B2B**.

---

## 4. Objetivos do Produto

O CCE deverá permitir que uma aplicação hospedeira:

| Objetivo | Resultado |
|---|---|
| Criar proposta | A partir de dados estruturados |
| Usar IA | Produzir narrativa comercial e escopo |
| Publicar | Gerar URL segura e responsiva |
| Personalizar | Aplicar marca do tenant |
| Acompanhar | Registrar comportamento do destinatário |
| Interagir | Chat contextual |
| Negociar | Revisar/versionar proposta |
| Aceitar | Registrar aceite e evidências |
| Contratar | Gerar ou integrar contrato |
| Cobrar | Criar pagamento |
| Sincronizar | Informar tudo ao sistema hospedeiro via webhook |

---

## 5. Non-Goals

Na versão inicial, o CCE **não será**:

| Fora do escopo | Motivo |
|---|---|
| CRM completo | Deve integrar-se a CRMs |
| ERP | Deve integrar-se a ERPs |
| Contabilidade | Apenas envia/recebe eventos financeiros |
| Sistema fiscal completo | NF será integração futura |
| Gateway financeiro | Usará PSPs existentes |
| Plataforma de assinatura própria regulamentada | Integrará provedores quando necessário |
| CMS genérico | Editor existe exclusivamente para propostas |
| Ferramenta de automação de marketing | Pode emitir eventos para ferramentas externas |

Esta fronteira deve ser preservada.

---

## 6. Product Principles

As decisões futuras deverão respeitar sete princípios:

**Headless first.** Tudo que a interface faz deve poder ser realizado pela API.

**API first.** Nenhuma feature importante dependerá exclusivamente da UI.

**Event driven.** Mudanças significativas do domínio devem produzir eventos.

**Host agnostic.** Django, Flask, Node, PHP, Java ou outro stack podem integrar.

**Multi-tenant by design.** Nenhuma funcionalidade será criada presumindo tenant único.

**Immutable closing evidence.** Evidências de aceite e versões aceitas não podem ser alteradas.

**Provider abstraction.** IA, pagamento, armazenamento, e-mail e assinatura não serão acoplados a um fornecedor específico.

---

## 7. Integration Modes

O produto suportará quatro formas de integração.

| Modo | Aplicação |
|---|---|
| REST API | Integração universal |
| SDK | Integração simplificada para linguagens específicas |
| Embedded UI | Tela CCE incorporada ao ERP/SaaS |
| Web Components | Recursos pontuais incorporáveis |

### REST API

É o contrato fundamental.

Exemplo:

```http
POST /v1/proposals
```

### SDK Python

```python
proposal = cce.proposals.create(
    external_id="ORC-2026-187",
    customer=customer,
    items=items
)
```

### Embedded UI

Um ERP poderá abrir:

```text
ERP
 └── Comercial
      └── Propostas
           └── Embedded CCE
```

O acesso será realizado por token temporário assinado.

### Web Components

Futuramente:

```html
<cce-proposal-status proposal-id="..."></cce-proposal-status>
```

ou:

```html
<cce-create-proposal external-id="ORC-187"></cce-create-proposal>
```

---

## 8. System Context

```text
                    ┌─────────────────────┐
                    │    Host Systems     │
                    │ ERP / CRM / SaaS    │
                    └──────────┬──────────┘
                               │
                     API / SDK / SSO
                               │
                               ▼
                 ┌────────────────────────┐
                 │ Commercial Closing API │
                 └────────────┬───────────┘
                              │
          ┌───────────────────┼─────────────────────┐
          │                   │                     │
          ▼                   ▼                     ▼
 Proposal Engine       Engagement Engine       Closing Engine
          │                   │                     │
          ▼                   ▼                     ▼
       AI Engine        Tracking / Chat      Contract / Payment
          │                   │                     │
          └───────────────────┼─────────────────────┘
                              │
                              ▼
                         PostgreSQL
                              │
                   ┌──────────┴──────────┐
                   ▼                     ▼
                Redis                 Storage
                                      S3
```

---

## 9. Bounded Contexts

O domínio será dividido inicialmente em nove contextos.

| Contexto | Responsabilidade |
|---|---|
| Identity | Tenant, usuários, RBAC, SSO |
| Customer | Destinatários e organizações |
| Proposal | Criação, edição e versionamento |
| Presentation | Templates e renderização |
| Engagement | Tracking, sessões e analytics |
| Conversation | Chat humano/IA |
| Closing | Aceite e evidências |
| Contract | Documentos contratuais |
| Billing | Cobrança e status financeiro |

Um décimo módulo transversal será:

**Integration**, responsável por API keys, webhooks, external IDs e providers.

---

## 10. Core Domain Model

```text
Tenant
│
├── User
├── Role
├── Brand
├── Integration
├── APIKey
├── WebhookEndpoint
│
├── Customer
│    └── Contact
│
├── Proposal
│    ├── ProposalVersion
│    │    ├── Section
│    │    ├── LineItemSnapshot
│    │    └── MilestoneSnapshot
│    │
│    ├── Recipient
│    ├── Publication            (uma por ProposalVersion)
│    │    └── ViewSession
│    │         └── EngagementEvent
│    │
│    ├── Conversation           (uma por Recipient)
│    │    └── Message
│    │
│    ├── Acceptance             (gera o Evidence Package)
│    ├── Contract               (opcional, por política do Tenant)
│    └── Payment                (1..N)
│
└── AuditEvent
```

---

## 11. Entidades Centrais

## Tenant

A empresa vendedora que emite propostas pelo CCE e cujos dados são isolados das demais. Um **Host** (o ERP, CRM ou SaaS que integra o CCE) normalmente integra um Tenant por empresa independente; o Host ainda não é uma entidade do modelo, e a hierarquia de parceiros é decisão posterior.

Campos conceituais:

```text
id
name
slug
status
plan
default_currency
timezone
locale
created_at
```

## Customer

Comprador comercial do Tenant, pessoa física ou jurídica, a quem a Proposal é dirigida. Possui `Contact`s cadastrados previamente; o `Recipient` de uma Publication normalmente é um Contact, mas pode ser alguém avulso.

```text
id
tenant_id
external_id
name
document
email
phone
metadata
```

`external_id` permite mapear a entidade para o ERP.

Exemplo:

```text
CCE Customer = cus_123
ERP Customer = CLI00087
```

---

## 12. Proposal

`Proposal` representa o agregado comercial.

```text
id
tenant_id
external_source
external_id
customer_id

status

title
currency

current_version_id

created_by
created_at
updated_at
```

`Proposal` não armazena diretamente o conteúdo final apresentado ao cliente.

Este conteúdo pertence a `ProposalVersion`.

`(tenant_id, external_source, external_id)` é único: um Quote do Host corresponde a uma única Proposal (§35). `status` reflete o ciclo comercial da Proposal (§15). A validade da oferta não pertence à Proposal, mas a cada `ProposalVersion` (§13).

---

## 13. Proposal Versioning

Versionamento é uma decisão estrutural do produto.

```text
Proposal
│
├── V1
├── V2
├── V3
└── V4 ← accepted
```

Uma nova alteração comercial relevante gera nova versão.

`ProposalVersion`:

```text
id
proposal_id
version_number

content_json
pricing_snapshot
terms_snapshot
valid_until

checksum

created_at
created_by
```

Quando publicada:

```text
published_at
```

Quando aceita:

```text
accepted_at
```

Depois de aceita, a versão é **imutável**.

O Total da versão é sempre calculado pelo CCE a partir dos `LineItemSnapshot`s (§21). `valid_until` define até quando a versão pode ser aceita e é diferente da expiração do link, que pertence à Publication (§17). Uma versão aceita também congela as entradas de renderização (versão do template e Brand) e o PDF gerado no aceite (§25).

---

## 14. Proposal Content Schema

O conteúdo da proposta será estruturado, não HTML gerado por IA.

Exemplo:

```json
{
  "title": "Implantação ERP",
  "sections": [
    {
      "type": "hero",
      "headline": "Modernização da operação"
    },
    {
      "type": "problem",
      "content": "..."
    },
    {
      "type": "solution",
      "content": "..."
    },
    {
      "type": "scope",
      "items": []
    },
    {
      "type": "timeline",
      "milestones": []
    },
    {
      "type": "pricing"
    },
    {
      "type": "next_steps"
    }
  ]
}
```

O Renderer transforma esse schema em interface.

Isso separa:

```text
conteúdo
     ↓
presentation schema
     ↓
renderer
     ↓
template
     ↓
HTML
```

Essa separação permitirá diferentes layouts sem reconstruir propostas.

Campos de texto longo (o corpo das Sections de texto e as condições comerciais) aceitam um subconjunto fechado de HTML (parágrafos, quebra de linha, negrito, itálico e listas, sem atributos), sanitizado pelo CCE na escrita; os demais campos são texto puro (ADR-0028).

---

## 15. Lifecycles: Proposal, Publication, Contract e Payment

Proposal, Publication, Contract e Payment têm ciclos de vida **independentes**. Nenhum estado de um ciclo vive em outro. O Host recebe um *Commercial status* derivado, calculado a partir dos quatro ciclos (ver `CONTEXT.md`).

Mudança em relação à v1.0: saíram da Proposal `VIEWED` (é dado de Engagement), `EXPIRED` e `REVOKED` (são da Publication), `CONTRACT_PENDING` e `CONTRACTED` (são do Contract) e `PAYMENT_PENDING` e `PAID` (são do Payment).

### 15.1 Proposal

```text
DRAFT ──► PUBLISHED ──► NEGOTIATING ──► ACCEPTED
              │              │
              └──► REJECTED ◄┘
```

| Estado | Significado |
|---|---|
| `DRAFT` | A Proposal ainda não teve nenhuma Publication. |
| `PUBLISHED` | Existe ao menos uma Publication ativa. Publicar exige ação explícita de um `User` ou de uma API key com o scope `proposal:publish` (ADR-0023). |
| `NEGOTIATING` | Existe intenção registrada de mudar: o Recipient pede ajuste, ou o vendedor cria uma nova versão depois da primeira Publication. Publicar a nova versão devolve a `PUBLISHED`. |
| `REJECTED` | Decisão explícita do Recipient. Não é final: publicar uma nova versão reabre a Proposal. |
| `ACCEPTED` | Acceptance registrado. A Proposal deixa de aceitar novas versões. |

`PUBLISHED → ACCEPTED` direto é válido.

A revisão não é estado da Proposal, e sim atributo de cada `ProposalVersion`: *draft* ou *ready*. Uma versão gerada pela IA do CCE nasce *draft* e só pode ser publicada depois de marcada como *ready* por um ator autorizado; conteúdo enviado pelo Host pode ser publicado diretamente (ADR-0023). Na v1.1 existia o estado `READY` da Proposal; ele foi removido.

### 15.2 Publication

`ACTIVE`, `EXPIRED` (expiração do link), `REVOKED` e `SUPERSEDED` (uma Publication mais nova da mesma Proposal a substituiu). A expiração do link é independente da validade da oferta (§13): com a oferta vencida o link continua abrindo, mas sem a ação de aceitar.

### 15.3 Contract

`PENDING → SIGNED`. O Contract é opcional (§27). Estados adicionais, como cancelado ou recusado, serão definidos no Milestone 5.

### 15.4 Payment

Ver §29.

### 15.5 Commercial status

O Commercial status é uma visão calculada para o Host, nunca um estado guardado da Proposal. Exemplos: visualizada, aceita, paga, aceita com pagamento estornado. É o que o ERP exibe no §64.

---

## 16. Proposal Creation Flow

Fluxo originado de ERP:

```text
ERP Orçamento
      │
      ▼
POST /proposals
      │
      ▼
Proposal DRAFT
      │
      ▼
AI Generation
      │
      ▼
ProposalVersion V1
      │
      ▼
Revisão humana (obrigatória se o conteúdo veio da IA)
      │
      ▼
Publish (User ou API key com proposal:publish)
      │
      ▼
URL pública
```

Se o Host reenviar o mesmo Quote (`external_source` + `external_id`), o CCE cria uma nova `ProposalVersion` da mesma Proposal. Depois do aceite o reenvio é rejeitado com um código de erro explícito; renegociar exige um novo Quote (ADR-0020).

---

## 17. Public Proposal

Cada publicação terá um token não enumerável.

Exemplo conceitual:

```text
https://p.example.com/p/7Lw8QpR19x
```

Nunca:

```text
/proposal/123
```

A publicação pode possuir:

```text
expiration
password
allowed_email
max_views
revoked_at
```

Cada Publication expõe exatamente **uma** `ProposalVersion` (ADR-0018). Revisar uma Proposal já publicada cria uma nova versão e uma nova Publication, com novo token; a Publication anterior passa a `SUPERSEDED` e o link antigo avisa que existe uma versão mais nova. `expiration` pertence à Publication e é independente da validade da oferta (`valid_until` da versão): com a oferta vencida o link continua abrindo, mas sem a ação de aceitar.

---

## 18. Engagement Tracking

O tracking será event-based.

Exemplos:

```text
proposal.opened
proposal.returned
section.entered
section.left
pricing.viewed
cta.clicked
chat.opened
acceptance.started
payment.started
```

Evento:

```json
{
  "event": "section.viewed",
  "proposal_id": "prop_x",
  "publication_id": "pub_x",
  "session_id": "ses_x",
  "section": "pricing",
  "duration_ms": 18240,
  "occurred_at": "..."
}
```

Uma `ViewSession` pertence a uma **Publication**, não a uma pessoa. Ela é anônima até alguém se identificar (por `allowed_email`, ao aceitar ou ao abrir o chat); só então é associada a um `Recipient`. O CCE não afirma que "o cliente abriu" quando não sabe quem abriu.

---

## 19. Analytics

O Analytics Engine poderá produzir:

| Métrica | Exemplo |
|---|---|
| First open | 14:31 |
| Last open | 16:52 |
| Views | 4 |
| Unique sessions | 2 |
| Total engagement | 08m32s |
| Scope engagement | 02m15s |
| Pricing engagement | 03m41s |
| Return visits | 2 |
| CTA clicks | 3 |

Posteriormente poderá gerar:

**Engagement Score**.

Exemplo:

```text
0–30  baixo interesse
31–60 moderado
61–80 alto
81–100 muito alto
```

Esse score não entra no MVP inicial.

---

## 20. AI Architecture

O módulo de IA nunca deverá controlar diretamente regras comerciais.

A arquitetura será:

```text
Application
     │
     ▼
AI Orchestrator
     │
     ├── Proposal Generator
     ├── Rewrite Assistant
     ├── Scope Generator
     ├── Timeline Generator
     ├── Meeting Processor
     └── Proposal Chat
```

---

## 21. Structured AI Output

Toda geração crítica deverá utilizar schema estruturado.

Nunca:

```text
LLM → HTML
```

Preferência:

```text
LLM
 ↓
JSON
 ↓
Schema validation
 ↓
Business validation
 ↓
ProposalVersion
```

O modelo não poderá alterar silenciosamente:

```text
preço
quantidade
desconto
tributação
condição financeira
identificadores
```

Esses dados serão tratados como **trusted commercial facts**.

A IA trabalha sobre a narrativa; o domínio controla os números.

O **Total** da proposta é sempre calculado pelo CCE a partir dos `LineItemSnapshot`s, em unidades menores inteiras da moeda da Proposal. Se o Host também enviar um total e ele divergir, a requisição é rejeitada (conferência de reconciliação). Tributos chegam como valores opacos informados pelo Host; o CCE nunca os calcula (ADR-0019). A versão pode ter um desconto global, informado em valor; o Total é a soma dos itens menos esse desconto, e o CCE nunca aplica percentuais (ADR-0027).

---

## 22. AI Provider Abstraction

Interface conceitual:

```python
class AIProvider:
    generate_structured(...)
    generate_text(...)
    embed(...)
    transcribe(...)
```

Providers poderão incluir:

```text
OpenAI
Anthropic
Google
Local model
```

O domínio nunca chamará diretamente APIs específicas.

---

## 23. Proposal Chat

O chatbot utilizará exclusivamente contexto autorizado da proposta.

Contexto possível:

```text
ProposalVersion
Contract
FAQ
Company profile
Catalog
Approved knowledge
```

Fluxo:

```text
Pergunta
   ↓
Retrieve context
   ↓
Generate grounded answer
   ↓
Confidence validation
   ↓
Resposta
```

Quando não houver informação suficiente:

> Esta informação não está definida na proposta. Posso registrar a pergunta para o responsável comercial.

Não deverá inventar compromissos comerciais. A IA nunca assume compromisso sobre preço, desconto, prazo ou escopo novo: toda pergunta cuja resposta não esteja na `ProposalVersion` vira uma **pergunta pendente** para um `User` do Tenant, visível na Conversation.

---

## 24. Human Chat

Conversation terá:

```text
customer
seller
AI
system
```

como possíveis autores.

Assim, a IA poderá iniciar a resposta e transferir para humano.

Existe uma `Conversation` por Proposal e por Recipient, e ela atravessa as versões. Cada mensagem registra a Publication e a `ProposalVersion` que o Recipient estava vendo, e a IA responde sempre a partir dessa versão. Quando um humano responde, a IA fica em silêncio na Conversation até um `User` devolvê-la explicitamente.

---

## 25. Acceptance

Aceite deve gerar um **Evidence Package**.

Conteúdo mínimo:

```text
proposal_id
proposal_version_id
proposal_version_hash

recipient
timestamp

publication_id
session_id

IP
user-agent

acceptance_method
terms_version
```

A versão aceita fica permanentemente associada ao aceite.

O Acceptance é um ato de **um** `Recipient` identificado: ele informa nome e e-mail (ou documento) ao aceitar, e basta um aceitante. Quem aceita sem estar na lista é registrado como Recipient no ato, com a identificação informada; o Tenant restringe a pessoas pré-cadastradas por `allowed_email`. Assinatura de várias partes pertence ao provedor de assinatura (§26). O aceite só ocorre com a Publication ativa e a validade da oferta em vigor.

No aceite o CCE congela também a apresentação (versão do template e Brand) e guarda o PDF gerado naquele momento (ADR-0021). Templates e Brands, portanto, são versionados, não editados no lugar. O Evidence Package não é apagado a pedido do titular (§49, ADR-0022).

---

## 26. Electronic Signature Strategy

CCE distinguirá:

```text
Simple Acceptance
```

de:

```text
Electronic Signature Provider
```

Para fluxos que exigirem assinatura especializada, será utilizado adapter.

Exemplo:

```text
Contract
   ↓
SignatureAdapter
   ├── Clicksign
   ├── ZapSign
   ├── DocuSign
   └── outros
```

O CCE não tentará reinventar infraestrutura criptográfica/regulatória especializada.

---

## 27. Contracts

`Contract` poderá nascer de:

```text
template
+
proposal snapshot
+
customer
+
commercial terms
```

Exemplo:

```text
Contrato
├── partes
├── objeto
├── escopo
├── valores
├── pagamento
├── vigência
├── obrigações
└── assinatura
```

Templates possuirão variáveis.

```text
{{ customer.name }}
{{ proposal.total }}
{{ proposal.valid_until }}
```

O Contract é **opcional**, definido por política do Tenant. Sem contrato, o Simple Acceptance basta para iniciar o Payment; se a política exigir contrato, ele é gerado automaticamente no Acceptance a partir da versão aceita, e o Payment espera a assinatura. O Tenant é dono dos templates (o CCE fornece as variáveis). Assinam as duas partes, um Recipient e um signatário do Tenant, por meio do provedor de assinatura (§26). O Contract fica ligado a uma única `ProposalVersion` aceita e é imutável depois de assinado.

---

## 28. Payments

Pagamento será abstraído por provider.

```text
BillingService
      │
      ├── AsaasAdapter
      ├── StripeAdapter
      ├── MercadoPagoAdapter
      └── FutureAdapter
```

Interface:

```text
create_charge()
get_charge()
cancel_charge()
refund()
process_webhook()
```

---

## 29. Payment Lifecycle

```text
PENDING
  │
  ├── PAID
  ├── EXPIRED
  ├── CANCELLED
  ├── REFUNDED
  └── FAILED
```

Eventos financeiros recebidos do PSP serão normalizados antes de chegar ao domínio.

Exemplo:

```text
Asaas PAYMENT_RECEIVED
         ↓
Provider Adapter
         ↓
payment.paid
         ↓
CCE
         ↓
ERP webhook
```

Uma Proposal aceita pode ter **vários** Payments (1:N). Cada Payment é uma tentativa de cobrança ligada à versão aceita e a uma condição de pagamento do `pricing_snapshot`; um Pix vencido não é alterado, e o vendedor cria um novo Payment para a mesma condição. A soma dos Payments `PAID` de uma condição não pode passar do valor dela. O MVP3 só cria uma cobrança Pix integral, mas a cardinalidade já é 1:N. Falha ou estorno de um Payment nunca desfaz o Acceptance: o Commercial status passa a refletir, por exemplo, "aceita, com pagamento estornado", e o Host decide o que fazer.

---

## 30. Webhook Architecture

O CCE deverá comunicar mudanças ao sistema hospedeiro.

Exemplos:

```text
proposal.created
proposal.published
proposal.opened
proposal.viewed
proposal.accepted
proposal.rejected
proposal.expired

contract.created
contract.signed

payment.created
payment.paid
payment.failed
payment.refunded

conversation.message.created
```

Os eventos de negócio dos quatro ciclos (Proposal, Publication, Contract, Payment) são sempre entregues ao Host, pelo outbox (§46), ao menos uma vez; o Host deduplica pelo `X-CCE-Event-ID`. Os eventos finos de Engagement (`section.entered`, `cta.clicked`) ficam na API de analytics, e o Tenant pode ativar webhooks para marcos específicos, como a primeira abertura (`proposal.opened`, que o Golden Path ativa).

---

## 31. Webhook Delivery

Cada entrega possuirá:

```text
id
event_id
endpoint_id

attempt
status

request_body
response_code

sent_at
next_retry_at
```

Política inicial de retry:

```text
1 min
5 min
30 min
2 h
12 h
24 h
```

Após o limite:

```text
DEAD LETTER
```

O tenant poderá reprocessar manualmente.

---

## 32. Webhook Security

Webhook conterá assinatura HMAC.

Exemplo:

```http
X-CCE-Signature: sha256=...
X-CCE-Timestamp: ...
X-CCE-Event-ID: evt_...
```

O SDK terá função:

```python
cce.webhooks.verify(request)
```

---

## 33. Idempotency

Todas as operações que possam gerar efeitos externos deverão aceitar:

```http
Idempotency-Key
```

Exemplo:

```http
POST /v1/payments
Idempotency-Key: ERP-187-payment-1
```

Isso impede criação duplicada durante retries.

---

## 34. REST API v1

Base:

```text
/api/v1
```

Recursos principais:

```text
/tenants
/customers
/proposals
/templates
/publications
/conversations
/contracts
/payments
/webhooks
/integrations
```

Exemplos:

```http
POST /v1/customers
GET  /v1/customers/{id}

POST /v1/proposals
GET  /v1/proposals/{id}
PATCH /v1/proposals/{id}

POST /v1/proposals/{id}/versions
POST /v1/proposals/{id}/generate
POST /v1/proposals/{id}/publish

GET /v1/proposals/{id}/analytics

POST /v1/proposals/{id}/accept

POST /v1/contracts
POST /v1/payments
```

---

## 35. External IDs

Toda entidade integrável deverá suportar:

```text
external_id
```

e opcionalmente:

```text
external_source
```

Exemplo:

```json
{
  "external_source": "dentbudget",
  "external_id": "ORC-000187"
}
```

Isso simplifica reconciliação entre sistemas.

`(tenant_id, external_source, external_id)` é único por tipo de entidade. Para a Proposal, isso significa um Quote, uma Proposal (ADR-0020).

---

## 36. API Authentication

Três mecanismos:

| Contexto | Autenticação |
|---|---|
| Server-to-server | API Key |
| Usuário interno | JWT/OIDC |
| Embedded UI | Short-lived signed token |
| Cliente da proposta | Publication token |

API keys deverão possuir scopes.

Exemplo:

```text
proposal:read
proposal:write
proposal:publish
payment:read
payment:create
webhook:manage
```

---

## 37. SSO Embedded

Fluxo:

```text
Usuário autenticado no ERP
          │
          ▼
ERP Backend
          │
          ▼
POST /embedded/sessions
          │
          ▼
CCE gera token 60 s
          │
          ▼
Embedded UI
```

O usuário não realiza segundo login.

O CCE cria ou atualiza o `User` sob demanda: o ERP envia `external_id`, nome e papel em `POST /embedded/sessions`, e o CCE cria ou atualiza o User daquele Tenant. Assim `created_by` e o Audit Log sempre apontam para uma pessoa real, e o Host continua dono da autenticação.

---

## 38. Multi-Tenancy

Modelo inicial:

```text
Shared PostgreSQL
+
tenant_id
+
PostgreSQL Row Level Security
```

Não utilizaremos inicialmente:

```text
database por tenant
schema por tenant
```

Motivos:

```text
menor complexidade operacional
migrations centralizadas
pool de conexões eficiente
analytics simplificado
menor custo
```

Isolamento lógico continuará obrigatório.

A RLS é forçada também para o role proprietário das tabelas. Migrations de dados percorrem os Tenants um a um, cada um em sua transação com o contexto aplicado, sem `BYPASSRLS` (ADR-0025).

---

## 39. White Label

`Brand` será entidade de primeira classe.

```text
logo
logo_dark
favicon

primary_color
secondary_color

font_heading
font_body

company_name
footer_text

email_sender_name
email_sender_address

custom_domain
```

Template + Brand produzirão o visual.

---

## 40. Custom Domains

Fase posterior:

```text
propostas.cliente.com.br
```

Fluxo:

```text
DNS verification
      ↓
domain ownership
      ↓
certificate provisioning
      ↓
tenant mapping
```

---

## 41. Suggested Technology Stack

## Backend

```text
Python 3.12+
FastAPI
Pydantic
SQLAlchemy 2
Alembic
```

## Database

```text
PostgreSQL
```

## Queue/cache

```text
Redis
Celery
```

Celery poderá ser substituído posteriormente, mas esta será a baseline.

## Frontend

```text
Next.js
React
TypeScript
```

## Styling

```text
Tailwind CSS
```

## Storage

S3-compatible:

```text
AWS S3
Cloudflare R2
MinIO
```

## PDF

Preferência:

```text
HTML/CSS
↓
Playwright/Chromium
↓
PDF
```

Isso maximiza fidelidade entre proposta web e PDF.

---


## 42. High-Level Deployment

```text
                    Internet
                       │
                  CDN / WAF
                       │
        ┌──────────────┴──────────────┐
        ▼                             ▼
 Public Proposal                  Admin App
     Next.js                       Next.js
        │                             │
        └──────────────┬──────────────┘
                       ▼
                    API
                  FastAPI
                       │
        ┌──────────────┼───────────────┐
        ▼              ▼               ▼
 PostgreSQL          Redis            S3
                       │
                       ▼
                    Workers
                       │
            ┌──────────┼─────────┐
            ▼          ▼         ▼
           AI        Email    Providers
```

## 43. Modular Monolith First

O backend começará como modular monolith, não microservices.

Estrutura lógica:

```text
identity
customers
proposals
engagement
conversations
closing
contracts
billing
integrations
ai
```

A separação entre módulos será forte o suficiente para permitir extração futura.

Não teremos complexidade distribuída prematuramente.

## 44. Repository Strategy

Baseline recomendada:

```text
commercial-closing-engine/
│
├── apps/
│   ├── api/
│   ├── admin-web/
│   └── proposal-web/
│
├── packages/
│   ├── sdk-python/
│   ├── sdk-js/
│   ├── ui/
│   └── schemas/
│
├── infrastructure/
│   ├── docker/
│   └── migrations/
│
├── docs/
│   ├── architecture/
│   ├── api/
│   └── adr/
│
├── tests/
│
├── docker-compose.yml
└── README.md
```

Backend Python poderá possuir internamente:

```text
apps/api/src/cce/
├── identity/
├── customers/
├── proposals/
├── engagement/
├── closing/
├── contracts/
├── billing/
├── ai/
├── integrations/
└── shared/
```

## 45. Event Architecture

Internamente:

```text
Command
   ↓
Domain
   ↓
Domain Event
   ↓
Event Handler
```

Exemplo:

```text
AcceptProposal
      ↓
ProposalAccepted
      ├── create contract
      ├── create payment
      ├── send email
      ├── deliver webhook
      └── audit event
```

A infraestrutura externa inicialmente pode usar PostgreSQL + queue.

Não precisamos de Kafka no início.

## 46. Transactional Outbox

Para evitar perda de eventos:

```text
Business transaction
      │
      ├── proposal.accepted
      │
      └── outbox row
           COMMIT
              │
              ▼
          worker
              │
              ▼
          webhook
```

A Transactional Outbox Pattern será obrigatória para eventos externos importantes.

## 47. Audit Log

Audit deverá registrar:

```text
actor
action
entity
entity_id

old_values
new_values

timestamp
IP
request_id
```

Eventos relevantes:

```text
proposal.created
proposal.edited
proposal.published
proposal.accepted
contract.generated
payment.created
template.changed
user.permission.changed
```

Audit log não é o mesmo que analytics.

## 48. Security Baseline

O baseline deverá conter:

| Controle | Aplicação |
|---|---|
| TLS | Obrigatório |
| RBAC | Administração |
| RLS | Isolamento tenant |
| Rate limiting | APIs públicas |
| CSRF | Interfaces autenticadas |
| CSP | Proposal/public UI |
| Sanitization | Conteúdo editável |
| Signed URLs | Downloads |
| Secret rotation | API keys |
| Encryption at rest | Infraestrutura |
| Audit log | Operações sensíveis |
| Password hashing | Argon2id |
| MFA | Fase posterior |

## 49. LGPD / Privacy by Design

Tracking comercial envolve dados comportamentais.

Portanto devemos prever desde o início:

```text
retention policy
data export
data deletion
anonymization
consent/preferences
auditability
processor records
tenant isolation
```

Eventos de analytics deverão ser configuráveis.

Por exemplo:

```yaml
tracking:
  basic: true
  section_tracking: true
  ip_storage: truncated
  retention_days: 180
```

O Tenant é o controlador dos dados e o CCE é o operador; o pedido do titular é recebido pelo Tenant. O Evidence Package e a `ProposalVersion` aceita **não** são apagados a pedido do titular: ficam retidos pelo prazo legal de guarda de contratos (ADR-0022). Dados de Engagement (ViewSessions, IP de navegação, chat) são anonimizados ou excluídos conforme a política de retenção do Tenant. O modelo de dados deve separar desde o início a evidência retida do dado comportamental excluível.

## 50. Observability

Cada request possuirá:

```text
request_id
trace_id
tenant_id
```

Stack conceitual:

```text
structured logs
metrics
traces
error tracking
```

Métricas:

```text
API latency
error rate
queue depth
AI latency
AI token consumption
webhook failure rate
payment webhook lag
PDF generation time
```

## 51. AI Cost Observability

Cada chamada de IA deverá registrar:

```text
tenant
feature
provider
model

tokens_in
tokens_out
cost

latency
status
```

Permitirá calcular:

```text
AI cost / proposal
AI cost / tenant
AI cost / closed deal
```

## 52. Testing Strategy

A pirâmide será:

```text
Unit tests
     ↓
Domain tests
     ↓
Integration tests
     ↓
API contract tests
     ↓
E2E tests
```

Fluxos E2E críticos:

```text
create proposal
publish proposal
open proposal
accept proposal
generate contract
create payment
receive payment webhook
notify ERP
```

## 53. Contract Testing

SDKs e API deverão possuir contract tests.

Quando API v1 mudar:

```text
SDK Python
SDK JavaScript
Host integrations
```

não poderão quebrar silenciosamente.

API v1 terá política explícita de compatibilidade.

## 54. CI/CD

Pipeline:

```text
lint
↓
type checking
↓
unit tests
↓
integration tests
↓
security scan
↓
build images
↓
deploy staging
↓
E2E
↓
production
```

Migrations deverão rodar separadamente da inicialização da aplicação.

## 55. Environments

```text
local
test
staging
production
```

Local:

```text
Docker Compose
```

incluindo:

```text
PostgreSQL
Redis
MinIO
Mailpit
API
Workers
Frontend
```

Permitirá desenvolvimento integral sem serviços externos.

## 56. MVP Definition

### MVP 1 — Proposal Core

Objetivo:

Criar uma proposta via API, editá-la, publicá-la e permitir que um cliente a visualize.

Inclui:

```text
Tenant
API keys
Customers
Proposal
ProposalVersion
LineItems
Sections
Brand
Template
Publication
Public Proposal
PDF
Basic audit
```

Não contém IA inicialmente como dependência obrigatória.

## 57. MVP 2 — Engagement

Objetivo:

Saber o que aconteceu depois que a proposta foi enviada.

Inclui:

```text
ViewSession
EngagementEvent
proposal.opened
section tracking
analytics
notifications
webhooks
```

## 58. MVP 3 — Closing

Objetivo:

Transformar proposta visualizada em transação comercial.

Inclui:

```text
acceptance
evidence package
version locking
contract generation
payment provider
Pix
payment webhooks
ERP webhook
```

Este é o primeiro estágio em que teremos o ciclo completo:

```text
ERP
 ↓
Proposal
 ↓
Customer
 ↓
Acceptance
 ↓
Payment
 ↓
ERP
```

## 59. MVP 4 — AI

Inclui:

```text
proposal generation
scope generation
copy rewrite
timeline generation
brand voice
proposal chatbot
```

IA será adicionada sobre um domínio já funcional.

Não construiremos o sistema em torno do LLM.

## 60. MVP 5 — Scale

Inclui:

```text
team users
advanced RBAC
batch proposals
presentation mode
recurring contracts
meeting transcription
meeting → proposal
custom domains
advanced analytics
```

Essas capacidades também aparecem no benchmark do Fyrmo em seus planos superiores, particularmente lote, modo apresentação, contratos recorrentes, métricas, reuniões e usuários adicionais.

## 61. Feature Matrix

| Capacidade | MVP1 | MVP2 | MVP3 | MVP4 | MVP5 |
|---|:---:|:---:|:---:|:---:|:---:|
| Propostas | ✓ | | | | |
| Templates | ✓ | | | | |
| Branding | ✓ | | | | |
| Link público | ✓ | | | | |
| PDF | ✓ | | | | |
| Tracking | | ✓ | | | |
| Analytics | | ✓ | | | |
| Webhooks | | ✓ | | | |
| Aceite | | | ✓ | | |
| Contratos | | | ✓ | | |
| Pix | | | ✓ | | |
| IA proposta | | | | ✓ | |
| Chatbot | | | | ✓ | |
| Brand voice | | | | ✓ | |
| Equipe | | | | | ✓ |
| Recorrência | | | | | ✓ |
| Reunião → proposta | | | | | ✓ |
| Domínio customizado | | | | | ✓ |

## 62. MVP Acceptance Criteria

O primeiro produto vertical completo será considerado funcional quando conseguirmos executar automaticamente:

1. ERP cria customer via API.
2. ERP envia orçamento.
3. CCE cria proposta.
4. Usuário publica proposta.
5. Cliente recebe URL.
6. Cliente abre proposta.
7. ERP recebe proposal.opened.
8. Cliente aceita, identificando-se (nome e e-mail ou documento).
9. CCE registra evidence package.
10. CCE gera cobrança Pix.
11. Cliente paga.
12. PSP envia confirmação.
13. CCE altera payment para PAID.
14. ERP recebe payment.paid.

Esse será nosso Golden Path.

No Golden Path o Contract é dispensado pela política do Tenant (o Simple Acceptance basta), e o Tenant ativa o webhook `proposal.opened` (§30).

## 63. First Integration Target

A primeira integração será feita com uma aplicação nossa: o **DentBudget** (Orçamento Fácil), sistema Django 5 + DRF + PostgreSQL de gestão de orçamentos para revenda de produtos odontológicos (ADR-0026).

Motivos:

- domínio conhecido;
- orçamento, itens e cliente já existem e já estão em produção;
- várias empresas numa mesma instalação, cada uma um Tenant;
- regras reais que o domínio precisa suportar: desconto global, quantidade fracionária, preço negociado por item e condições de pagamento formatadas;
- ambiente sob nosso controle.

O DentBudget será utilizado como reference host, num ambiente de teste com dados sintéticos.

Isso ajudará a validar a qualidade da API antes de publicarmos SDK.

## 64. Reference Integration

Fluxo:

```text
DentBudget
│
├── Cliente
│
├── Orçamento
│
└── Botão "Criar proposta"
        │
        ▼
       CCE
        │
        ▼
Public Proposal
        │
        ▼
      Cliente
        │
        ▼
   Acceptance
        │
        ▼
     Payment
        │
        ▼
DentBudget recebe webhook
```

No ERP:

```text
ORCAMENTO
    ↓
PROPOSTA ENVIADA
    ↓
VISUALIZADA
    ↓
ACEITA
    ↓
PAGA
```

## 65. Initial SDKs

Ordem recomendada:

1. REST API
2. Python SDK
3. JavaScript/TypeScript SDK
4. Django adapter
5. Flask adapter
6. React components

O SDK nunca conterá regra de negócio central.

Ele apenas simplifica comunicação.

## 66. Python SDK Example

Objetivo de ergonomia:

```python
from cce import CCE

client = CCE(api_key="...")

proposal = client.proposals.create(
    external_id="ORC-187",
    customer={
        "external_id": "CLI-38",
        "name": "Empresa ABC"
    },
    items=[
        {
            "description": "Implantação",
            "quantity": 1,
            "unit_price": 8500
        }
    ]
)

proposal.publish()
```

Esse nível de simplicidade será um requisito de produto.

## 67. Django Adapter

Posteriormente:

```python
INSTALLED_APPS = [
    ...,
    "cce_django"
]
```

Configuração:

```python
CCE_API_KEY = env("CCE_API_KEY")
CCE_BASE_URL = env("CCE_BASE_URL")
```

Poderemos fornecer helpers:

```python
create_proposal_from_order(order)
```

mas eles viverão no adapter, não no Core.

## 68. Developer Experience

O produto depende fortemente de DX.

Documentação deverá conter:

```text
Quickstart
API reference
Webhook guide
SDK examples
Authentication
Error handling
Testing
Sandbox
Migration guides
```

Deveremos ter ambiente:

```text
sandbox.cce...
```

antes de buscar clientes externos.

## 69. Error Model

Resposta padronizada:

```json
{
  "error": {
    "code": "PROPOSAL_NOT_PUBLISHABLE",
    "message": "Proposal has no recipient.",
    "details": {},
    "request_id": "req_..."
  }
}
```

Nunca dependeremos apenas de texto da mensagem.

**code** será parte do contrato da API.

## 70. IDs

IDs públicos utilizarão prefixos.

Exemplos:

```text
ten_
usr_
cus_
prop_
ver_
pub_
evt_
acc_
ctr_
pay_
wh_
```

Exemplo:

```text
prop_01J...
```

Internamente podemos utilizar UUIDv7.

Isso melhora:

- debugging;
- logs;
- suporte;
- DX.

## 71. Search and Metadata

Entidades poderão conter:

```json
{
  "metadata": {
    "salesperson_id": "VEN-12",
    "branch": "RJ",
    "source": "ERP"
  }
}
```

Metadata deve aceitar dados de integração sem alterar constantemente o schema central.

## 72. Extensibility

Adapters deverão existir para:

```text
AI
payments
signature
email
storage
notifications
```

Estrutura:

```text
ports/
adapters/
```

ou equivalente.

O domínio conhece interfaces, não fornecedores.

## 73. Architecture Decision Records

ADRs deverão nascer junto com o projeto.

| ADR | Decisão | Motivo / observação |
|---|---|---|
| ADR-001 — Modular Monolith | Iniciar como modular monolith | Reduzir complexidade distribuída mantendo boundaries claros |
| ADR-002 — PostgreSQL | PostgreSQL como database principal | Domínio fortemente relacional, transações, JSONB, RLS e consistência |
| ADR-003 — Shared Multi-Tenant Database | Shared DB + tenant_id + RLS | Menor custo operacional e melhor escalabilidade inicial |
| ADR-004 — API First | Toda capacidade relevante terá interface programática | Contrato fundamental do produto |
| ADR-005 — Immutable Accepted Proposal | Versões aceitas são imutáveis | Evidências e rastreabilidade |
| ADR-006 — Structured AI | LLM não gera HTML e não é autoridade sobre valores comerciais | Fatos comerciais controlados pelo domínio |
| ADR-007 — Provider Abstraction | Integrações críticas sem acoplamento direto a um único fornecedor | Substituição por adapters |
| ADR-008 — Transactional Outbox | Eventos externos relevantes utilizam outbox | Evitar perda de eventos |
| ADR-009 — React/Next.js Renderer | Proposal UI e Admin UI utilizam React/Next.js | Renderer definido na baseline |
| ADR-010 — FastAPI Core | API principal utiliza Python/FastAPI | Razões abaixo |
| ADR-018 — Publication fixada em uma versão | Cada Publication expõe uma única ProposalVersion | Evidência de aceite com hash exato; ciclos independentes |
| ADR-019 — Total calculado pelo CCE | O CCE calcula o Total; divergência do Host é rejeitada | Fatos comerciais com fonte única |
| ADR-020 — Um Quote, uma Proposal | Reenvio vira nova versão; após o aceite é rejeitado | Idempotência e histórico de versões |
| ADR-021 — Apresentação congelada no aceite | Template, Brand e PDF congelados | Evidência reproduzível |
| ADR-022 — Evidência retida | O Evidence Package não é apagado a pedido do titular | Prova do aceite contra pedido de exclusão (LGPD) |
| ADR-023 — Publicação por API key | Publicar exige User ou API key com `proposal:publish`; revisão humana obrigatória para conteúdo da IA | Golden Path pela API sem abrir mão da revisão do que a IA gera |
| ADR-024 — Trava de versão no banco | Gatilho admite só as transições de publicação e aceite | Imutabilidade garantida pelo banco sem impedir o aceite |
| ADR-025 — Migrations de dados por Tenant | Backfills percorrem os Tenants um a um, sem bypass de RLS | Preservar o isolamento no processo mais privilegiado |
| ADR-026 — DentBudget como Host de referência | O M3 integra o DentBudget, não um Mini ERP construído para isso | Regras reais do Host no domínio desde o M1 |
| ADR-027 — Desconto global em valor | Total = soma das linhas − desconto global; o CCE nunca aplica percentual | Hosts dão desconto no orçamento inteiro sem divergência de arredondamento |
| ADR-028 — Texto formatado restrito | Campos longos aceitam HTML com allowlist fechada, sanitizado na escrita | Manter a formatação do Host sem vetor de injeção |

Os ADRs 011–017 estão reservados pelo plano do Milestone 0; por isso os ADRs deste alinhamento começam em 018. Os arquivos ficam em `docs/adr/` com quatro dígitos (`0001-...md`).

Razões para ADR-010:

- Python já domina nosso ecossistema;
- excelente suporte a schemas;
- OpenAPI nativo;
- integração simples com IA;
- bom desempenho para APIs;
- forte tipagem via Pydantic.

## 74. Product Risks

| Risco | Mitigação |
|---|---|
| Escopo crescer demais | Fases rígidas |
| Virar CRM | Respeitar non-goals |
| IA controlar negócio | Structured outputs |
| Eventos perdidos | Outbox |
| Duplicidade financeira | Idempotency |
| Vazamento multi-tenant | RLS + testes |
| Tracking invasivo | Privacy by design |
| Lock-in de fornecedor | Adapters |
| UI acoplada | API-first |
| Quebra de integrações | Versionamento API |

## 75. Technical Debt Rules

Não aceitaremos como dívida deliberada:

- ausência de tenant_id;
- segredos hardcoded;
- eventos financeiros sem idempotência;
- propostas aceitas mutáveis;
- webhooks sem assinatura;
- provider chamado diretamente pelo domínio;
- HTML gerado diretamente pelo LLM.

Esses itens são restrições arquiteturais.

## 76. Definition of Done

Uma feature somente estará concluída quando possuir, conforme aplicável:

```text
domain implementation
API
authorization
tenant isolation
validation
tests
audit
events
OpenAPI documentation
error codes
migration
observability
```

Uma tela funcionando não significa feature concluída.

## 77. Product Metrics

Quando o produto estiver em produção, métricas principais deverão incluir:

| Categoria | Métrica |
|---|---|
| Activation | Primeira proposta publicada |
| Engagement | Propostas abertas |
| Conversion | Propostas aceitas |
| Closing | Aceites → pagamentos |
| Speed | Tempo criação → publicação |
| Speed | Publicação → abertura |
| Speed | Abertura → aceite |
| Reliability | Webhook success |
| AI | Custo por proposta |
| Integration | API errors por tenant |

## 78. Future Intelligence Layer

Depois de acumular dados suficientes, poderemos desenvolver:

```text
Proposal Health Score
Closing Probability
Best Follow-up Time
Price Sensitivity
Section Drop-off
Suggested Follow-up
Proposal Benchmark
```

Exemplo:

> O cliente abriu a proposta quatro vezes, retornou três vezes à seção de investimento e permaneceu 3m20s nessa seção. Probabilidade de decisão elevada; sugerir contato comercial.

Isso transforma o CCE futuramente em uma camada de Revenue Intelligence.

Não pertence ao MVP.

## 79. Long-Term Architecture

Se o volume justificar, alguns módulos poderão ser extraídos:

```text
Modular Monolith
       │
       ├── Engagement Service
       ├── Notification Service
       ├── PDF Service
       ├── AI Service
       └── Billing Service
```

A extração ocorrerá somente por necessidade comprovada.

## 80. Commercial Packaging — Future

Possível monetização futura:

```text
Platform fee
+
usage
```

Dimensões possíveis:

```text
propostas publicadas
AI generations
AI tokens
active tenants
payments
storage
meetings processed
team users
```

Para fabricante de software:

```text
Starter
Growth
Platform
Enterprise
```

Mas pricing não deverá influenciar prematuramente o modelo de domínio.

## 81. Clean-Room Product Rule

O Fyrmo e outras ferramentas serão tratados exclusivamente como referências de mercado.

Podemos estudar:

- problemas resolvidos;
- fluxos;
- categorias funcionais;
- expectativas dos usuários.

Não devemos reproduzir:

- código;
- templates proprietários;
- copy;
- identidade visual;
- assets;
- layout distintivo;
- implementação interna.

O CCE possuirá arquitetura e experiência próprias.

## 82. Roadmap Técnico Recomendado

```text
FOUNDATION
    ↓
DOMAIN
    ↓
API
    ↓
PUBLIC PROPOSAL
    ↓
REFERENCE ERP INTEGRATION
    ↓
ENGAGEMENT
    ↓
CLOSING
    ↓
PAYMENT
    ↓
AI
    ↓
SDKs
    ↓
SCALE
```

Essa ordem é importante.

Não começaremos pela IA.

O primeiro objetivo será provar:

> Um ERP consegue criar uma proposta remotamente, publicá-la e receber o resultado do fechamento.

## 83. Milestone 0 — Foundation

Primeira milestone do desenvolvimento: **Foundation**.

Entregáveis conceituais:

```text
repository
developer environment
FastAPI application
PostgreSQL
Redis
migrations
configuration
logging
tenant model
API authentication
CI
testing foundation
architecture documentation
ADRs
```

Nenhuma proposta comercial precisa existir ainda.

## 84. Milestone 1 — Proposal Domain

Objetivo:

```text
Customer
+
Proposal
+
ProposalVersion
+
Items
+
Sections
```

API funcional.

Resultado:

```http
POST /customers
POST /proposals
POST /proposals/{id}/versions
```

## 85. Milestone 2 — Publication

Adicionar:

```text
templates
branding
public links
renderer
publication lifecycle
PDF
```

Resultado:

Um ERP consegue criar e publicar uma proposta real.

## 86. Milestone 3 — Reference Integration

Integrar ao DentBudget (ADR-0026).

A spec do M3 decide como o aceite do comprador se relaciona com a aprovação interna do DentBudget, que hoje gera o pedido e a baixa de estoque.

Criar no orçamento:

```text
[ Gerar proposta ]
```

e exibir posteriormente:

```text
Status: Visualizada
Status: Aceita
Status: Paga
```

Essa milestone valida o conceito de produto plugável.

## 87. Milestone 4 — Engagement

Adicionar tracking e webhooks.

Essa fase responde:

> O CCE consegue informar ao ERP o que o cliente fez?

## 88. Milestone 5 — Closing

Adicionar:

```text
acceptance
evidence
contract
payment
```

Essa fase completa o Golden Path.

## 89. Milestone 6 — AI

Somente então adicionar geração inteligente.

Isso evita que IA esconda deficiências no domínio ou arquitetura.

## 90. Architectural North Star

Todas as decisões futuras deverão ser confrontadas com esta pergunta:

> Esta capacidade pode ser utilizada por uma aplicação externa sem conhecer detalhes internos do CCE?

Se a resposta for não, provavelmente estamos criando acoplamento indevido.

A arquitetura deve permitir que um sistema externo veja o CCE simplesmente como:

```text
INPUT
customer
proposal data
commercial terms

        ↓

Commercial Closing Engine

        ↓

OUTPUT
publication
engagement
acceptance
contract
payment
events
```

## 91. Product North Star

A pergunta fundamental do produto será:

> Quanto esforço um desenvolvedor precisa fazer para adicionar um fluxo profissional de fechamento comercial ao seu software?

Nosso objetivo final deve ser:

```text
API key
+
algumas chamadas
+
webhook
=
commercial closing completo
```

## 92. Decisão v1

Com este Blueprint, ficam estabelecidas como baseline:

| Área | Decisão |
|---|---|
| Produto | Commercial Closing Engine |
| Estratégia | Headless / Embedded |
| Arquitetura | Modular Monolith |
| Backend | FastAPI |
| Banco | PostgreSQL |
| Multi-tenancy | Shared DB + RLS |
| Frontend | Next.js / React |
| Jobs | Redis + Celery |
| Storage | S3-compatible |
| Integração | REST + Webhooks |
| IA | Provider abstraction |
| Pagamentos | Provider abstraction |
| Assinatura | Provider abstraction |
| Eventos | Domain events + Outbox |
| Primeiro host | DentBudget (Orçamento Fácil), Django |
| Prioridade inicial | Proposal Core |
| IA | Após Golden Path básico |
| Microservices | Não inicialmente |

## 93. Critério para iniciar codificação

O projeto está suficientemente definido conceitualmente para entrar na próxima fase.

Entretanto, a próxima atividade não deve ser começar a escrever endpoints aleatórios.

Antes da implementação deve ser produzido o:

**Technical Implementation Plan — Foundation**

Esse documento deverá decompor o Milestone 0 — Foundation em:

- estrutura exata do repositório;
- dependências;
- configuração Python;
- Docker Compose;
- PostgreSQL;
- Redis;
- FastAPI;
- SQLAlchemy;
- Alembic;
- modelo Tenant;
- RLS;
- API keys;
- configuração de ambientes;
- logging;
- testes;
- CI/CD;
- ADRs;
- ordem de implementação arquivo a arquivo.

Depois dele, a codificação poderá começar de maneira controlada.

## Conclusão

O produto deixa de ser conceitualmente “um sistema semelhante ao Fyrmo” e passa a ter uma definição própria:

> Commercial Closing Engine é uma infraestrutura multi-tenant, API-first e embeddable que permite a qualquer ERP, CRM ou SaaS incorporar criação e publicação de propostas, engagement tracking, IA, negociação, aceite, contratos e pagamentos sem desenvolver essa infraestrutura internamente.

O fluxo fundamental será:

```text
Host Application
       │
       ▼
Commercial Data
       │
       ▼
Proposal
       │
       ▼
Engagement
       │
       ▼
Acceptance
       │
       ▼
Contract
       │
       ▼
Payment
       │
       ▼
Events / Webhooks
       │
       ▼
Host Application
```

