# ADR-0016: Observabilidade inicial com logs redigidos por allowlist, `request_id` e `trace_id`

**Data:** 30/10/2026
**Status:** Proposto

## Contexto

O CCE atende vários Tenants pela mesma API, e a chave de cada um chega em todo pedido, no cabeçalho `Authorization`. Ao diagnosticar um problema, quem opera precisa ligar a resposta que o Host recebeu à linha de log correspondente, saber qual Tenant e qual rota estavam envolvidos e quanto tempo o pedido levou. Ao mesmo tempo, o log é o caminho mais comum de vazamento: um cabeçalho registrado por inteiro, um DSN numa mensagem de exceção, o `repr` de um objeto de configuração, uma query string com token ou o corpo de um pedido com nome e e-mail de cliente. O Blueprint pede `request_id`, `trace_id` e `tenant_id` em cada requisição e logs estruturados como base da observabilidade (§50), além de segurança e privacidade desde o desenho (§48, §49). O plano do M0 (seção 13.1) define os campos e o que nunca registrar.

## Alternativas

- **Denylist: registrar tudo e remover nomes conhecidos (`password`, `token`).** Falha aberta: um campo novo, com nome inesperado, sai inteiro. Depende de lembrar de cada nome em cada contexto.
- **Access log padrão do Uvicorn mais logs livres da aplicação.** O access log registra o path e a query string crus, onde um cliente pode pôr um token ou um e-mail, e não tem `request_id`, Tenant nem template da rota.
- **Tracing distribuído (OpenTelemetry) já no M0.** Dá spans e propagação entre serviços, mas o M0 tem um único processo, nenhum collector definido e nenhuma decisão de hospedagem (ticket 12). Seria infraestrutura sem consumidor.
- **Aceitar `X-Request-ID` ou `traceparent` do cliente.** Facilita a correlação com o Host, mas permite que o cliente escolha o ID que aparece nos logs, colidindo IDs ou injetando texto.

## Decisão

- **Formato.** `cce.shared.logging.configure_logging(settings)` instala um único handler no logger raiz: toda linha, da aplicação ou de bibliotecas, sai como um objeto JSON por linha no stdout. O formato legível (`CCE_LOG_FORMAT=console`) só é aceito em `local` e passa pelo mesmo filtro. O nível vem de `CCE_LOG_LEVEL`.
- **Allowlist.** Uma linha só leva os campos de `ALLOWED_FIELDS`: `timestamp` (UTC), `level`, `logger`, `event`, `service`, `version`, `environment`, `request_id`, `trace_id`, `tenant_id`, `key_id`, `method`, `route`, `status_code`, `duration_ms`, `error_type`, `frames` e `pepper_version`. Qualquer outro campo é descartado. Acrescentar um campo é uma mudança de código revisada.
- **Mensagem como template.** `event` é o template da mensagem (`record.msg`), nunca o texto formatado com os argumentos. Uma exceção vira só `error_type`; o traceback e a mensagem dela não são registrados.
- **Redação recursiva, além da allowlist.** Os campos permitidos passam por `redact`, que percorre mapeamentos (inclusive os cabeçalhos do Starlette), sequências, named tuples (como a URL do SQLAlchemy), modelos Pydantic (como `Settings`) e dataclasses (como `IssuedKey`). Redige pelo nome (`authorization`, `cookie`, `password`, `secret`, `token`, `api_key`, `pepper`, `digest`, `dsn`, `url`, `email`, `document`, `body`, `query` e semelhantes), pelo tipo (`SecretStr`, bytes, que cobrem digests e peppers) e pelo conteúdo do texto (chave `cce_...`, `Bearer <credencial>`, senha em URL, `senha=valor`). Qualquer outro objeto vira `<NomeDoTipo>`: `str` e `repr` nunca são chamados.
- **Uma linha por requisição.** O `RequestContextMiddleware` registra `http.request` com método, **template** da rota (`/api/v1/tenant`; `unmatched` quando nenhuma rota casou, como num 404), status e duração. Nunca o path, a query string, cabeçalhos ou corpo. O access log do Uvicorn é desligado pela aplicação (`configure_logging`) e pela imagem (`--no-access-log`).
- **`request_id` e `trace_id`.** Cada requisição recebe um `request_id` (`req_` + 32 hex) e um `trace_id` (32 hex, nunca zero) novos, gerados pelo servidor; os cabeçalhos do cliente são ignorados. Os dois voltam em `X-Request-ID` e `X-Trace-ID` em toda resposta, inclusive de erro, e o `request_id` é o mesmo do envelope de erro (ADR-0014). Os dois ficam num contexto da requisição (`contextvars`), que a autenticação completa com os IDs **públicos** do Tenant e da chave (`ten_...`, `key_...`). O contexto é restaurado no fim de toda requisição, inclusive de autenticação recusada, de banco indisponível e de erro inesperado: uma requisição nunca herda o Tenant de outra.
- **Evolução para tracing distribuído.** O `trace_id` já tem o formato do W3C Trace Context (16 bytes em hex), para que a troca por OpenTelemetry não mude o contrato dos cabeçalhos nem dos logs. A exportação de spans entra quando houver um segundo processo no caminho de uma requisição (worker do outbox, ADR-0008) ou um destino de telemetria escolhido junto com a hospedagem. Aceitar um `traceparent` de entrada só faz sentido atrás de um gateway confiável e será decidido nessa hora.

## Consequências

- Diagnóstico por `request_id`: o Host informa o ID do envelope de erro, e uma busca no log mostra a linha da requisição, a rota, o Tenant, o status e a duração, além da linha de erro inesperado, se houver, com tipo e frames.
- Um campo útil que não está na allowlist some em silêncio até alguém acrescentá-lo. É o custo de falhar fechado.
- A redação por nome é ampla e pode esconder um valor inofensivo (um campo `name` de um objeto de configuração, por exemplo). Como os campos de topo já passam pela allowlist, isso só afeta valores aninhados.
- Os logs de segurança operacionais (`tenant.created`, `api_key.issued`, `api_key.revoked`) e a retenção de 30 dias do plano (seção 13.1) não fazem parte desta decisão de formato. Os eventos da CLI administrativa e a retenção no ambiente hospedado ficam para os tickets que tratarem deles. Logs não substituem o AuditEvent de domínio do Proposal Core.

## Impactos de segurança

- Chave de API, segredo, digest, pepper, DSN, URL do Redis, cookies e `Authorization` não aparecem em log, mesmo que um desenvolvedor passe o objeto inteiro (`Settings`, cabeçalhos, `IssuedKey`) num campo ou numa mensagem. Os testes provam isso com objetos reais e com um Uvicorn de verdade, não só com uma string de exemplo.
- Nome, e-mail, documento, corpo e query string não entram em log: a rota sai como template, e esses nomes são redigidos mesmo em valores aninhados.
- O `tenant_id` só entra no contexto depois de a chave ser verificada, e sai no fim da requisição. Uma autenticação recusada é registrada sem Tenant.
- Os IDs de correlação são gerados pelo servidor, então o cliente não controla o que aparece nos logs nem consegue colidir IDs de propósito.

## Referência ao Blueprint

Blueprint §48 (segurança), §49 (LGPD), §50 (observabilidade), §53 (testes de contrato) e §69 (erros). Plano do M0, seções 12.5 e 13.1 e Tarefa 8.

## Critério de revisão

Revisar quando entrar o primeiro processo além da API (worker do outbox ou Celery), quando for escolhido um destino de logs ou de telemetria (com a hospedagem, ticket 12), quando um gateway confiável passar a propagar `traceparent`, ou quando um incidente mostrar um valor sensível que a redação não pegou.
