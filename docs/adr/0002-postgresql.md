# PostgreSQL como banco principal

O PostgreSQL é o banco de todos os dados de negócio do CCE. O domínio é fortemente relacional (Tenant, Customer, Proposal, versões, aceite, pagamento), precisa de transações e integridade referencial, e se beneficia de JSONB para conteúdo estruturado (Sections, snapshots, metadata) e de Row Level Security para o isolamento entre Tenants (ADR-003).

Rejeitamos bancos de documentos como fonte principal porque as garantias de integridade e de evidência (ADR-005) ficariam na aplicação. Redis e storage S3 são auxiliares e nunca guardam estado comercial como fonte de verdade.

Implementação: a partir do M0 (Blueprint §41 e §92). A versão de referência é o PostgreSQL 17.
