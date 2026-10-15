# Um Quote, uma Proposal; reenvio vira nova versão

O Host identifica a Proposal por `(external_source, external_id)`, único por Tenant, de modo que um Quote corresponde a uma única Proposal. Reenviar um Quote alterado cria uma nova ProposalVersion da mesma Proposal; depois do Acceptance o reenvio é rejeitado com um código de erro explícito, e renegociar exige um novo Quote. Rejeitamos criar uma Proposal por envio porque o Host perderia o histórico de versões e a idempotência de reenvios, e rejeitamos aceitar versões novas após o aceite para não violar o ADR-005.
