# 08: Logging seguro e contrato OpenAPI

**What to build:** cada request deixa um diagnóstico útil e correlacionável por `request_id` e `trace_id`, sem credenciais nem dados pessoais, e o contrato público da API está documentado e testado.

**Blocked by:** 07

**Status:** in-review

**Plano:** Tarefa 8 e seções 12.5 e 13.1.

- [ ] Testes de logging e do contrato OpenAPI escritos antes e falhando
- [ ] Toda resposta, inclusive de erro, traz `X-Request-ID` e `X-Trace-ID`, e os contextos são limpos depois de cada request, inclusive após autenticação falha
- [ ] Logs JSON só aceitam campos de uma allowlist e redigem recursivamente `Authorization`, chaves, peppers, DSNs e digests, testados com objetos e headers reais, não só uma string de exemplo
- [ ] O access log bruto do Uvicorn está desabilitado e logs usam o template da rota (ou `unmatched` em 404)
- [ ] Logs não contêm corpo, query string, nome, e-mail nem documento
- [ ] O OpenAPI lista os paths exatos, o esquema Bearer, os scopes exigidos por operação, os códigos de erro e nenhum campo de segredo
- [ ] Os documentos de autenticação e de erros têm exemplos completos
- [ ] O ADR `docs/adr/0016-observability.md` registra logs redigidos por allowlist, `request_id`/`trace_id` e a evolução para tracing distribuído
