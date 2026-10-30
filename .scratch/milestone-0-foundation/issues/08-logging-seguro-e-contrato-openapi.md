# 08: Logging seguro e contrato OpenAPI

**What to build:** cada request deixa um diagnóstico útil e correlacionável por `request_id` e `trace_id`, sem credenciais nem dados pessoais, e o contrato público da API está documentado e testado.

**Blocked by:** 07

**Status:** done

**Plano:** Tarefa 8 e seções 12.5 e 13.1.

- [ ] Testes de logging e do contrato OpenAPI escritos antes e falhando
- [ ] Toda resposta, inclusive de erro, traz `X-Request-ID` e `X-Trace-ID`, e os contextos são limpos depois de cada request, inclusive após autenticação falha
- [ ] Logs JSON só aceitam campos de uma allowlist e redigem recursivamente `Authorization`, chaves, peppers, DSNs e digests, testados com objetos e headers reais, não só uma string de exemplo
- [ ] O access log bruto do Uvicorn está desabilitado e logs usam o template da rota (ou `unmatched` em 404)
- [ ] Logs não contêm corpo, query string, nome, e-mail nem documento
- [ ] O OpenAPI lista os paths exatos, o esquema Bearer, os scopes exigidos por operação, os códigos de erro e nenhum campo de segredo
- [ ] Os documentos de autenticação e de erros têm exemplos completos
- [ ] O ADR `docs/adr/0016-observability.md` registra logs redigidos por allowlist, `request_id`/`trace_id` e a evolução para tracing distribuído

## Comments

### 2026-10-30 — Parecer do Revisor (Lupa), rodada 3: aprovado

PR #10, head `e43b51a`. Revisão com foco em segurança. Histórico: a rodada 1 achou 2 importantes (texto livre em `event` com redação incompleta; método HTTP do cliente gravado cru no log); a rodada 2 confirmou o método corrigido e achou 1 importante (a segunda barreira textual não rodava em `event` nem nos campos validados por formato); a rodada 3 não achou bloqueantes nem importantes.

Cobriu: catálogo fechado de eventos (4 nomes; o resto vira `log.unrecognized_message`; `configure_logging(events=...)` recusa nome que a redação alteraria); segunda barreira em `logger`/`service`/`version`/`error_type`, nomes de evento e templates de rota, com `request_id`/`trace_id`/`tenant_id`/`key_id` isentos de forma explícita e após validação do formato exato; `route` só sai se for template declarado, senão `[REDACTED]`; registro global de rotas declaradas (nenhum chamador usa `scope.path`, query, headers, corpo ou parâmetro de rota); cabeçalhos `X-Request-ID`/`X-Trace-ID`/`X-Tenant-ID` forjados não substituem IDs; método com CPF vira `OTHER`; contextos limpos em 401/503/500/concorrência.

Evidências do Lupa (cópia extraída em /tmp): `tests/unit`, `tests/contracts`, `test_request_logging`, `test_tenant_api` e `test_health` com `COVERAGE_FILE` isolado, 885 passed; `logging.py` 99%, OpenAPI 100%; `ruff`, `mypy` e `uv lock --check` limpos. Não repetiu `test_image`, `test_migrate_service` e `test_smoke` (buildx sem escrita em `~/.docker` no sandbox; código desses builds não mudou na rodada 3). Evidência do Forja: suíte completa 1327 passed, cobertura 97%.

Limitações documentadas (ADR-0016, Proposto): nome de pessoa em nome de logger, caminho da instalação ou catálogo configurado não é reconhecível; nome de pessoa no caminho de arquivo de um frame de traceback continua visível. Observação para o ticket 10 (CI): a cobertura de `logging.py` aparece em 67% quando a suíte completa grava no `.coverage` padrão (provável sobrescrita por meta-testes que rodam pytest em subprocesso); com `COVERAGE_FILE` isolado dá 99%.
