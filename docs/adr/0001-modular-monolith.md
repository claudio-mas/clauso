# Começar como monólito modular

O backend do CCE é um único serviço implantável, dividido internamente em módulos com fronteiras fortes (identity, customers, proposals, engagement, conversations, closing, contracts, billing, integrations, ai), de modo que um módulo possa ser extraído no futuro sem reescrever os outros. Um módulo só usa outro pela interface pública dele; o domínio não conhece FastAPI nem providers.

Rejeitamos microservices desde o início porque trariam rede, consistência eventual, deploy coordenado e observabilidade distribuída antes de existir qualquer carga ou equipe que justifique esse custo.

Implementação: a partir do M0 (Blueprint §43).
