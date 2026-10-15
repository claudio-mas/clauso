# 09: Robustez de migrations e recuperação

**What to build:** o schema e seus controles de segurança são reconstruíveis do zero e a recuperação não depende da memória de quem implementou: adulterar o schema é detectado, e um backup restaurado preserva dados, policies e autenticação.

**Blocked by:** 07

**Status:** ready-for-agent

**Plano:** Tarefa 9 e seções 9.3 e 18.

- [ ] Em banco descartável, remover temporariamente uma policy ou um grant faz `check_schema` falhar; restabelecido o head, ele passa
- [ ] A cadeia `0001 → 0003 → base → 0003` passa em banco vazio e restaura constraints, policies e grants
- [ ] `alembic check` e a suíte completa passam depois da cadeia
- [ ] Os testes de roles e de transações cobrem as falhas restantes (cancelamento, pool reutilizado, concorrência A/B)
- [ ] Backup e restore de dados sintéticos preservam IDs, constraints, policies e a autenticação das chaves
- [ ] Os runbooks de migrations e de backup/restore estão escritos e não expõem segredos
- [ ] Downgrade só é permitido em banco descartável `cce_test*`; o runbook de produção descreve expand/contract
