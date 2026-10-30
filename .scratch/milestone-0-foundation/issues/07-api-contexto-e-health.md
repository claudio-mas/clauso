# 07: API, contexto de Tenant e health

**What to build:** a API HTTP existe e funciona com dois Tenants: cada um consulta e altera só a própria empresa, com scopes, erros padronizados e health que distingue "vivo" de "pronto". O commit termina antes de qualquer resposta de sucesso.

**Blocked by:** 06

**Status:** done

**Plano:** Tarefa 7 e seções 12 e 13.2. **Vocabulário:** Tenant, Host (`CONTEXT.md`).

- [x] Testes de GET e PATCH próprios, scope ausente, `X-Tenant-ID` falso, campo proibido, health e falha de commit escritos antes e falhando
- [x] `GET /api/v1/tenant` e `PATCH /api/v1/tenant` operam só sobre o Tenant do `Principal`; o header `X-Tenant-ID` é ignorado e nunca muda o resultado
- [x] `GET /api/v1/auth/context` devolve Tenant público, ID da chave e scopes, sem digest
- [x] PATCH sem campo, com campo proibido, nome vazio, timezone inválido, moeda ou locale fora da lista retorna 422; conflito de integridade retorna 409
- [x] 401 (com `WWW-Authenticate: Bearer`), 403, 404, 409, 422, 500 e 503 usam o formato de erro padronizado com `request_id`
- [x] A resposta de sucesso só sai depois do commit: uma falha injetada no flush ou commit nunca devolve 200 e a alteração não persiste (verificado com sessão independente)
- [x] Requests A e B concorrentes e o pool reutilizado respondem corretamente
- [x] `/health/live` não checa dependências; `/health/ready` verifica PostgreSQL, Redis e schema, com 503 quando algo falha e sem expor URLs, hosts ou mensagens brutas
- [x] O Swagger fica desabilitado em staging e production, e o CORS só aceita origins explícitas
- [x] A imagem constrói, a API sobe pelo Compose e o smoke com dois Tenants passa em ambiente de teste
- [x] Os ADRs `docs/adr/0014-public-ids-api-errors.md` (UUID4 interno, prefixos públicos, códigos de erro estáveis) e `docs/adr/0015-redis-role.md` (Redis auxiliar, sem fonte de verdade nem broker fictício) estão escritos

## Comments

### 2026-10-30 — Parecer do Revisor (Lupa), rodada 1: aprovado

PR #9, head `6e7581c`. Revisão com foco em segurança. Sem achados bloqueantes ou importantes. Cobriu: `GET/PATCH` pelo `Principal` com filtro por ID e RLS e `X-Tenant-ID` ignorado; autenticação (rejeita `Authorization` duplicado, lookup restrito sem contexto, sem cache positivo, 401 uniforme com challenge, 503 sanitizado); contexto de Tenant com `set_config` parametrizado e LOCAL à transação; `Depends(scope="function")` encerrando flush/commit antes do envio; health sanitizado; CORS explícito e docs bloqueadas em staging/production; 422 de JSON inválido antes da autenticação (aceitável, documentado); 405 padronizado; ADRs 0014 e 0015 como Proposto.

Evidências do Lupa (cópia extraída em /tmp): `test_tenant_api`, `test_transactions` e `test_health` 52 passed; autenticação, sessão, RLS, privilégios, lookup e saída de falha 126 passed; `ruff`, `mypy` (94 arquivos), `uv lock --check` e `git diff --check` limpos. `tests/unit` 747 passed + 2 falhas só por falta de `CCE_ENV_FILE`/falso positivo descrito abaixo; repetidos com o ambiente correto: 2 passed. Não reconfirmou a suíte completa (1221), a cobertura (97,13%) nem o build/smoke Compose (buildx sem escrita em `~/.docker` no sandbox); essa evidência é a informada pelo Forja.

Sugestões não impeditivas (follow-up): (1) `docs/api/errors.md` (~linha 44) afirma que falha de commit significa que nada foi alterado; qualificar como em `admin/bootstrap.py` (sucesso nunca é devolvido se o commit levanta; rollback comprovado garante não persistência; perda de conexão pode exigir reconciliação). (2) `tests/unit/test_authentication.py` (~linha 179, preexistente): `key_forbidden` extrai o segredo com `rsplit`, mas base64url admite `_`; um sufixo curto pode coincidir com texto de log e dar falso positivo intermitente; extrair o segredo pelo parser e revisar o mesmo padrão no meta-teste de saída.
