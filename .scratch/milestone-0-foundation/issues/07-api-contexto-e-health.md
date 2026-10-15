# 07: API, contexto de Tenant e health

**What to build:** a API HTTP existe e funciona com dois Tenants: cada um consulta e altera só a própria empresa, com scopes, erros padronizados e health que distingue "vivo" de "pronto". O commit termina antes de qualquer resposta de sucesso.

**Blocked by:** 06

**Status:** ready-for-agent

**Plano:** Tarefa 7 e seções 12 e 13.2. **Vocabulário:** Tenant, Host (`CONTEXT.md`).

- [ ] Testes de GET e PATCH próprios, scope ausente, `X-Tenant-ID` falso, campo proibido, health e falha de commit escritos antes e falhando
- [ ] `GET /api/v1/tenant` e `PATCH /api/v1/tenant` operam só sobre o Tenant do `Principal`; o header `X-Tenant-ID` é ignorado e nunca muda o resultado
- [ ] `GET /api/v1/auth/context` devolve Tenant público, ID da chave e scopes, sem digest
- [ ] PATCH sem campo, com campo proibido, nome vazio, timezone inválido, moeda ou locale fora da lista retorna 422; conflito de integridade retorna 409
- [ ] 401 (com `WWW-Authenticate: Bearer`), 403, 404, 409, 422, 500 e 503 usam o formato de erro padronizado com `request_id`
- [ ] A resposta de sucesso só sai depois do commit: uma falha injetada no flush ou commit nunca devolve 200 e a alteração não persiste (verificado com sessão independente)
- [ ] Requests A e B concorrentes e o pool reutilizado respondem corretamente
- [ ] `/health/live` não checa dependências; `/health/ready` verifica PostgreSQL, Redis e schema, com 503 quando algo falha e sem expor URLs, hosts ou mensagens brutas
- [ ] O Swagger fica desabilitado em staging e production, e o CORS só aceita origins explícitas
- [ ] A imagem constrói, a API sobe pelo Compose e o smoke com dois Tenants passa em ambiente de teste
- [ ] Os ADRs `docs/adr/0014-public-ids-api-errors.md` (UUID4 interno, prefixos públicos, códigos de erro estáveis) e `docs/adr/0015-redis-role.md` (Redis auxiliar, sem fonte de verdade nem broker fictício) estão escritos
