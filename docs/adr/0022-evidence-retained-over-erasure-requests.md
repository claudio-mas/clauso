# Evidência de aceite retida mesmo diante de pedido de exclusão

O Tenant é o controlador dos dados e o CCE é o operador. O Evidence Package e a ProposalVersion aceita não são apagados a pedido do titular: ficam retidos pelo prazo legal de guarda de contratos, e a exclusão só vale depois dele. Dados de Engagement (ViewSessions, IP de navegação, chat) são anonimizados ou excluídos conforme a política de retenção do Tenant, e o pedido do titular é recebido pelo Tenant, não pelo CCE. Rejeitamos apagar a evidência sob pedido porque isso destruiria a prova do aceite (ADR-005) justamente quando ela é contestada.

Consequência: o modelo de dados precisa separar o que é evidência retida do que é dado comportamental excluível desde o início, e a base legal da retenção deve constar na documentação de privacidade.
