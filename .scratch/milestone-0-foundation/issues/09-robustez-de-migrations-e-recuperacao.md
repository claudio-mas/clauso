# 09: Robustez de migrations

**What to build:** o schema e seus controles de segurança são reconstruíveis do zero e não dependem da memória de quem implementou: adulterar o schema é detectado. O ensaio de backup e restore foi movido para o ticket 12.

**Blocked by:** 07

**Status:** in-review

**Plano:** Tarefa 9 e seção 9.3. O ensaio de backup/restore da seção 18 está no ticket 12.

- [ ] Em banco descartável, remover temporariamente uma policy ou um grant faz `check_schema` falhar; restabelecido o head, ele passa
- [ ] A cadeia `0001 → 0003 → base → 0003` passa em banco vazio e restaura constraints, policies e grants
- [ ] `alembic check` e a suíte completa passam depois da cadeia
- [ ] Os testes de roles e de transações cobrem as falhas restantes (cancelamento, pool reutilizado, concorrência A/B)
- [ ] O runbook de migrations está escrito, não expõe segredos e descreve o padrão de migration de dados por Tenant (ADR-0025)
- [ ] Downgrade só é permitido em banco descartável `cce_test*`; o runbook de produção descreve expand/contract
