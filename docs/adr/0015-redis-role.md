# ADR-0015: Redis como dependência auxiliar, sem fonte de verdade nem broker fictício

**Data:** 30/10/2026
**Status:** Proposto

## Contexto

O Blueprint lista Redis e Celery na stack (§41), e o Compose do M0 sobe um Redis 7.4. Mas o M0 não tem job, cache nem fila de verdade: o estado comercial e a autorização são do PostgreSQL (ADR-0002, ADR-0012, ADR-0013). Sem uma decisão explícita, o Redis tende a virar duas coisas ruins: um cache de autorização que atrasa a revogação, ou uma interface de fila sem consumidor que só existe para "marcar" a stack como pronta.

## Alternativas

- **Não subir Redis no M0.** A dependência e a operação dele (TLS, timeouts, readiness) chegariam junto com o primeiro job real, num marco já mais carregado.
- **Cache de API keys ou de `Principal` no Redis.** Revogação e suspensão passariam a esperar um TTL, e uma falha do Redis viraria uma decisão de autorização. O ADR-0012 já recusou cache positivo de chaves.
- **Celery com uma tarefa de exemplo.** Um broker sem consumidor real não prova nada sobre filas, retentativas ou idempotência, e cria código para manter.
- **Redis como armazenamento de sessão ou de estado.** Ele não é a fonte de verdade de nada no CCE; o PostgreSQL é.

## Decisão

- **Papel no M0:** só conectividade. A API cria **um cliente por processo** no lifespan (`cce.shared.redis.build_redis`), com `socket_connect_timeout=1`, `socket_timeout=1` e `decode_responses=True`, e o fecha no shutdown (`aclose`), inclusive depois de um startup que falhou no meio. Nenhum cliente por requisição e nenhuma conexão na importação.
- **Readiness:** `/health/ready` faz `PING` com limite de 750 ms. O Compose do M0 declara o Redis obrigatório, então Redis fora do ar dá `503` na readiness; a liveness continua `200`. Uma falha do Redis nunca vira falha de autenticação nem apaga dado.
- **TLS:** em staging e production a URL é `rediss://` (a configuração recusa outra), com certificado e hostname verificados e a CA de `CCE_REDIS_CA_FILE` quando houver.
- **Nunca no Redis:** chave bruta, digest, pepper, autorização como fonte única, estado comercial, Evidence Package ou qualquer dado que precise sobreviver a um `FLUSHALL`.
- **Uso futuro, quando houver:** chaves com prefixo `cce:<environment>:<tenant_uuid>:<purpose>:<key>`, cache sempre com TTL e sempre derivável do PostgreSQL. O broker do Celery entra com o primeiro job real (por exemplo, a entrega de Host events a partir do outbox, ADR-0008), com consumidor, retentativa e idempotência testados.

## Consequências

- O Redis já faz parte da topologia, do health e do lifespan, então o primeiro uso real não muda a operação, só acrescenta funções.
- Enquanto não houver uso funcional, o Redis só pesa na readiness: um Redis fora do ar tira a API do balanceador mesmo sem uso real. É o custo de manter a composição do M0 honesta com a dos marcos seguintes.
- Não há código de fila nem de cache para manter no M0.

## Impactos de segurança

- Como nada de autorização vem do Redis, comprometê-lo ou derrubá-lo não dá acesso a Tenant algum e não atrasa revogação.
- A resposta da readiness não expõe URL, host, porta, senha nem mensagem do cliente Redis.
- A URL do Redis é segredo (`SecretStr`) e nunca é logada.

## Referência ao Blueprint

Blueprint §41 (stack sugerida) e §45–46 (eventos e outbox). Plano do M0, seções 12.1, 12.3 e 13.2.

## Critério de revisão

Revisar quando entrar o primeiro job real (broker Celery), o primeiro cache, rate limiting na aplicação ou qualquer uso do Redis que guarde dado por Tenant; ou se a readiness precisar distinguir dependências obrigatórias de opcionais.
