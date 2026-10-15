# Transactional outbox para eventos externos

Eventos de negócio entregues ao Host (webhooks) são gravados numa tabela de outbox na mesma transação da mudança de dados, e um worker os entrega depois, ao menos uma vez, com ID de evento para deduplicação no Host.

Rejeitamos publicar o evento diretamente durante a requisição porque uma falha entre o commit e o envio perderia o evento, e um envio antes do commit poderia anunciar algo que não aconteceu.

Implementação: contexto registrado; código antes dos webhooks do M4 (Blueprint §45, §46 e §30).
