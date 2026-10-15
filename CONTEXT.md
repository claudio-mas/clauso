# Commercial Closing Engine (CCE)

Infraestrutura plugável e multi-tenant de fechamento comercial: um sistema externo (Host) cria propostas pelo CCE, acompanha o que o comprador faz e recebe de volta o resultado do aceite, do contrato e do pagamento.

## Language

### Atores

**Tenant**:
A empresa vendedora que emite propostas pelo CCE e cujos dados são isolados das demais.
_Avoid_: Company, account, client

**Host**:
O ERP, CRM ou SaaS que integra o CCE em nome de um ou mais Tenants. Hoje é só um conceito; ainda não existe como entidade.
_Avoid_: Partner, integrator, client

**User**:
Pessoa do Tenant que opera o CCE (por exemplo, o vendedor). A autenticação é do Host; o CCE cria ou atualiza o User quando o Host abre uma sessão embedded.
_Avoid_: Seller, agent, operator

**Customer**:
O comprador comercial de um Tenant, a quem a Proposal é dirigida. Pode ser pessoa física ou jurídica.
_Avoid_: Client, buyer, account

**Contact**:
Pessoa de um Customer, cadastrada previamente.
_Avoid_: Recipient, representative

**Recipient**:
A pessoa que recebe uma Publication em nome do Customer. Normalmente é um Contact, mas pode ser alguém avulso, sem Contact correspondente.
_Avoid_: Viewer, addressee

### Proposta

**Quote** ("orçamento"):
O orçamento que nasce no Host e dá origem a uma Proposal. Vive no Host, não no CCE. Um Quote corresponde a uma única Proposal; se ele mudar no Host, o reenvio gera uma nova ProposalVersion, e após o Acceptance é rejeitado. Renegociar depois do aceite exige um novo Quote.
_Avoid_: Budget

**Proposal** ("proposta"):
O agregado comercial de um Tenant para um Customer, que reúne todas as suas versões e o que acontece depois delas.
_Avoid_: Quote

**ProposalVersion** ("versão da proposta"):
O conteúdo comercial completo da Proposal em um dado momento, incluindo sua Offer validity. Uma vez aceita, a versão nunca mais muda. Antes de publicada é um *draft*; uma versão gerada por IA sempre nasce como draft e só é publicada por ação explícita de um User. *Ready* significa revisada por um humano, não gerada.
_Avoid_: Revision

**Offer validity** ("validade da oferta"):
Até quando uma ProposalVersion pode ser aceita. É da versão, porque cada versão pode mudar o prazo. Diferente da expiração do link, que é da Publication.
_Avoid_: Expiration, valid until

**Negotiation** ("negociação"):
Fase em que existe uma intenção registrada de mudar a Proposal: o Recipient pede ajuste, ou o vendedor cria uma nova versão depois da primeira Publication. Publicar a nova versão encerra a fase.
_Avoid_: Counter-offer

**Rejection** ("rejeição"):
Decisão explícita do Recipient de recusar a ProposalVersion. Não é final: publicar uma nova versão reabre a Proposal.
_Avoid_: Decline, loss

**Total**:
O valor da Proposal, sempre calculado pelo CCE a partir dos itens. Se o Host enviar um total diferente, a requisição é rejeitada. Tributos chegam do Host como valores informados; o CCE nunca os calcula.
_Avoid_: Amount, price

### Fechamento

**Publication** ("publicação"):
A exposição de uma única ProposalVersion a um Recipient por um link próprio. Uma nova versão exige uma nova Publication; o link antigo avisa que foi substituído. Tem expiração própria, independente da Offer validity: com a oferta vencida o link continua abrindo, mas sem aceite.
_Avoid_: Link, share

**Acceptance** ("aceite"):
O ato de um Recipient identificado aceitar uma ProposalVersion publicada. Basta um aceitante; quem aceita sem estar na lista é registrado como Recipient no ato, e o Tenant pode restringir a pessoas pré-cadastradas por `allowed_email`. Assinatura de várias partes pertence ao provedor de assinatura, não ao Acceptance. Só ocorre com a Publication e a Offer validity em vigor. No aceite, o CCE congela também a apresentação (template e Brand) e guarda o PDF gerado naquele momento.
_Avoid_: Approval, signature

**Contract** ("contrato"):
O documento formal que pode seguir o Acceptance. É opcional: o Tenant define por política se é exigido; quando exigido, é gerado automaticamente no Acceptance e o Payment espera a assinatura. O Tenant é dono dos templates; assinam as duas partes (um Recipient e um signatário do Tenant), por meio do provedor de assinatura. Fica ligado a uma única ProposalVersion aceita e é imutável depois de assinado.
_Avoid_: Agreement

**Evidence Package** ("pacote de evidências"):
O registro permanente de um Acceptance: quem aceitou, quando, de onde e qual ProposalVersion. Não é apagado a pedido do titular; fica retido pelo prazo legal de guarda de contratos.
_Avoid_: Audit trail, receipt

**Payment** ("pagamento"):
Uma tentativa de cobrança ligada à versão aceita e a uma condição de pagamento dela. Uma Proposal aceita pode ter várias, e cada tentativa é um Payment separado que fica no histórico (um Pix vencido não é alterado; o vendedor cria outro). A soma dos Payments pagos de uma condição não passa do valor dela. Falha ou estorno de um Payment nunca desfaz o Acceptance.
_Avoid_: Charge, invoice

### Engagement

**ViewSession** ("sessão de visualização"):
Um período contínuo de acesso a uma Publication. Pertence à Publication, não a uma pessoa, e fica anônima até alguém se identificar (por `allowed_email`, ao aceitar ou ao abrir o chat); só então é associada a um Recipient.
_Avoid_: Visit, view

**Conversation** ("conversa"):
O chat entre um Recipient e o Tenant. Existe uma por Proposal e por Recipient e atravessa as versões; cada mensagem registra a Publication que o Recipient estava vendo, e a IA responde sempre a partir dessa versão.
_Avoid_: Thread, chat

**Pending question** ("pergunta pendente"):
Pergunta do Recipient cuja resposta não está na ProposalVersion. A IA nunca assume compromisso comercial (preço, desconto, prazo, escopo novo): ela registra a pergunta para um User do Tenant. Quando um humano responde, a IA fica em silêncio na Conversation até um User devolvê-la.
_Avoid_: Escalation, ticket

### Estado

**Commercial status** ("status comercial"):
Resumo que o CCE calcula para o Host (por exemplo, visualizada, aceita, paga, aceita com pagamento estornado) a partir dos ciclos de vida independentes da Proposal, da Publication, do Contract e do Payment. Nunca é um estado guardado da Proposal.
_Avoid_: Proposal status, stage

**Host event** ("evento do Host"):
Evento de negócio de um dos quatro ciclos (Proposal, Publication, Contract, Payment) que o CCE sempre entrega ao Host, ao menos uma vez; o Host deduplica pelo ID do evento. Os eventos finos de Engagement (como a entrada em uma seção) ficam na API de analytics, e o Tenant pode ativar marcos específicos, como a primeira abertura.
_Avoid_: Notification, webhook
