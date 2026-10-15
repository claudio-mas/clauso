# API first

Toda capacidade relevante do CCE tem uma interface programática, publicada sob o prefixo `/api/v1`, documentada em OpenAPI e com códigos de erro estáveis. Interfaces de usuário (Admin, Embedded, Public Proposal) consomem a mesma API ou capacidades equivalentes; nenhuma feature importante depende exclusivamente da UI.

É a premissa do produto: o CCE é infraestrutura que um Host (ERP, CRM ou SaaS) integra, não um SaaS de propostas com API acessória.

Implementação: a partir do M0 (Blueprint §6, §34 e §69).
