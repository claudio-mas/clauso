# 03: Tenant e APIKey persistidos (migration 0001)

**What to build:** o schema com `Tenant` e `APIKey` é reproduzível por migration, em banco descartável, com constraints e defaults documentados. Ainda não há autenticação HTTP.

**Blocked by:** 02

**Status:** in-review

**Plano:** Tarefa 3 e seções 8 e 9. **Vocabulário:** Tenant (`CONTEXT.md`).

- [ ] Testes de constraints (slug, status, moeda, locale, timezone, expiração posterior à criação, digest de 32 bytes, scopes permitidos) escritos antes e falhando por tabelas ausentes
- [ ] A migration `0001` sobe do zero e `empty → 0001 → base → 0001` passa em banco descartável
- [ ] O owner das tabelas é `cce_migrator`; `cce_app` não tem DDL
- [ ] Datas são sempre com timezone e em UTC; JSON usa ISO 8601 com offset
- [ ] Os schemas de PATCH de Tenant recusam `id`, `tenant_id`, `status`, `plan` e `slug` (`extra="forbid"`)
- [ ] As fixtures só rodam com `CCE_ENV=test` e banco com prefixo `cce_test`, validado parseando a URL; qualquer outro caso aborta sem mostrar o DSN
- [ ] `alembic check` passa depois de alinhar a metadata; `create_all` nunca é usado
- [ ] Migrations rodam com `NullPool` e sem DSN em logs
