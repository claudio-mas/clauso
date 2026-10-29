# DentBudget é o Host de referência

O Host de referência do Milestone 3 é o DentBudget (Orçamento Fácil, repositório `claudio-mas/dentbudget`), e não um Mini ERP Django construído para esse fim. O DentBudget já é um sistema Django 5 + DRF + PostgreSQL em produção, com orçamento (`Budget`), itens com preço negociado, cliente com CPF/CNPJ, validade e várias empresas (`Company`) numa mesma instalação; cada `Company` corresponde a um Tenant, com sua própria API key. Na integração, `external_source` é `dentbudget` e o `external_id` do Quote é o ID do orçamento.

Rejeitamos construir um Mini ERP só para a integração porque ele exercitaria apenas os casos que nós mesmos inventássemos, enquanto o DentBudget traz regras reais que o Clauso precisa suportar desde o M1: desconto global no orçamento (ADR-0027), condições de pagamento em texto formatado (ADR-0028), quantidade fracionária, preço negociado por item, numeração própria (`AER-ORC-0512`) e um cliente compartilhado entre empresas.

Consequências:

- A integração roda contra um ambiente de teste do DentBudget, com dados sintéticos. Dados reais de clientes do DentBudget não entram no Clauso antes de existirem staging, política de retenção e contrato de operador (ADR-0022, Blueprint §49).
- O código de integração vive no DentBudget (ou num adapter Django, Blueprint §67), nunca no core do Clauso.
- A decisão de produto de como o aceite do cliente se relaciona com a aprovação interna do DentBudget (que gera pedido e baixa de estoque) é do DentBudget e será tomada na spec do M3.
