# Erros da API

Como a API do CCE responde quando algo dá errado. A decisão está no [ADR-0014](../adr/0014-public-ids-api-errors.md); a autenticação, em [`authentication.md`](authentication.md).

## Envelope

Toda resposta de erro tem a mesma forma:

```json
{
  "error": {
    "code": "AUTHENTICATION_FAILED",
    "message": "Authentication failed.",
    "details": {},
    "request_id": "req_0123456789abcdef0123456789abcdef"
  }
}
```

- `code` é o contrato: trate erros por ele. Ele só muda com uma versão nova da API.
- `message` é fixa por código e serve a pessoas. Não dependa do texto.
- `details` é sempre um objeto; o conteúdo depende do código (ver abaixo).
- `request_id` é o mesmo do cabeçalho `X-Request-ID`. Toda resposta, de erro ou não, traz também `X-Trace-ID`. Informe o `request_id` ao suporte.

## Códigos

| HTTP | `code` | Quando | `details` |
|---|---|---|---|
| 401 | `AUTHENTICATION_FAILED` | Sem `Authorization: Bearer`, mais de um cabeçalho `Authorization`, ou chave malformada, de outro ambiente, inexistente, com segredo errado, expirada, revogada ou de Tenant suspenso. A resposta traz `WWW-Authenticate: Bearer` e é idêntica para todos os motivos | `{}` |
| 403 | `FORBIDDEN` | A chave é válida, mas não tem o scope da rota | `{"required_scopes": ["tenant:write"]}` |
| 404 | `NOT_FOUND` | Rota inexistente (inclusive qualquer rota por ID de Tenant: elas não existem) | `{}` |
| 405 | `METHOD_NOT_ALLOWED` | Método não aceito pela rota | `{}` |
| 409 | `CONFLICT` | A alteração violou uma regra de integridade conhecida | `{"field": "slug"}` |
| 422 | `VALIDATION_ERROR` | Corpo malformado, sem campo, com campo proibido ou com valor inválido | `{"errors": [{"field": "name", "type": "string_too_short"}]}` |
| 500 | `INTERNAL_ERROR` | Erro inesperado | `{}` |
| 503 | `DEPENDENCY_UNAVAILABLE` | O banco não respondeu (inclusive durante a autenticação, que nunca vira 401) | `{}` |

## O que nunca aparece

- O valor que o cliente enviou. Em `422`, cada item tem só o campo e o tipo da violação; um campo desconhecido enviado pelo cliente aparece como `"field": null`, com `"type": "extra_forbidden"`.
- SQL, parâmetros, nomes de tabela ou de constraint, mensagens do driver, URLs, hosts ou portas.
- Qual foi o motivo de um `401`.

Uma resposta `2xx` só é enviada depois do commit da transação: se o commit levanta erro, a resposta nunca é de sucesso, e sim o erro (`409`, `500` ou `503`). Quando o banco recusou o commit e a transação foi desfeita, nada foi alterado. Mas se a conexão caiu durante o commit (`503`), o resultado é incerto: o banco pode ter gravado a alteração antes de a resposta se perder. Nesse caso, leia o recurso de novo (`GET`) antes de repetir a alteração.

## Exemplos completos

Os cabeçalhos `X-Request-ID` e `X-Trace-ID` vêm em toda resposta; os valores abaixo são ilustrativos. As chaves dos exemplos não são credenciais reais.

### 401: sem chave válida

```http
GET /api/v1/tenant HTTP/1.1
Authorization: Bearer cce_dev_0123456789abcdef0123456789abcdef_<segredo errado>
```

```http
HTTP/1.1 401 Unauthorized
Content-Type: application/json
WWW-Authenticate: Bearer
X-Request-ID: req_0123456789abcdef0123456789abcdef
X-Trace-ID: 4bf92f3577b34da6a3ce929d0e0e4736

{"error": {"code": "AUTHENTICATION_FAILED", "message": "Authentication failed.", "details": {}, "request_id": "req_0123456789abcdef0123456789abcdef"}}
```

### 403: chave sem o scope da rota

```http
PATCH /api/v1/tenant HTTP/1.1
Authorization: Bearer cce_dev_<id>_<segredo de uma chave só com tenant:read>
Content-Type: application/json

{"name": "Clínica Exemplo"}
```

```http
HTTP/1.1 403 Forbidden
Content-Type: application/json
X-Request-ID: req_1123456789abcdef0123456789abcdef
X-Trace-ID: 5bf92f3577b34da6a3ce929d0e0e4736

{"error": {"code": "FORBIDDEN", "message": "The API key does not have the required scope.", "details": {"required_scopes": ["tenant:write"]}, "request_id": "req_1123456789abcdef0123456789abcdef"}}
```

### 404: rota inexistente

```http
GET /api/v1/tenants/ten_0123456789abcdef0123456789abcdef HTTP/1.1
Authorization: Bearer cce_dev_<id>_<segredo>
```

```http
HTTP/1.1 404 Not Found
Content-Type: application/json
X-Request-ID: req_2123456789abcdef0123456789abcdef
X-Trace-ID: 6bf92f3577b34da6a3ce929d0e0e4736

{"error": {"code": "NOT_FOUND", "message": "Resource not found.", "details": {}, "request_id": "req_2123456789abcdef0123456789abcdef"}}
```

### 405: método não aceito

```http
DELETE /api/v1/tenant HTTP/1.1
Authorization: Bearer cce_dev_<id>_<segredo>
```

```http
HTTP/1.1 405 Method Not Allowed
Allow: GET
Content-Type: application/json
X-Request-ID: req_3123456789abcdef0123456789abcdef
X-Trace-ID: 7bf92f3577b34da6a3ce929d0e0e4736

{"error": {"code": "METHOD_NOT_ALLOWED", "message": "Method not allowed.", "details": {}, "request_id": "req_3123456789abcdef0123456789abcdef"}}
```

### 409: regra de integridade

```http
HTTP/1.1 409 Conflict
Content-Type: application/json
X-Request-ID: req_4123456789abcdef0123456789abcdef
X-Trace-ID: 8bf92f3577b34da6a3ce929d0e0e4736

{"error": {"code": "CONFLICT", "message": "The request conflicts with the current state of the resource.", "details": {"field": "name"}, "request_id": "req_4123456789abcdef0123456789abcdef"}}
```

### 422: corpo inválido

```http
PATCH /api/v1/tenant HTTP/1.1
Authorization: Bearer cce_dev_<id>_<segredo>
Content-Type: application/json

{"name": "", "tenant_id": "ten_ffffffffffffffffffffffffffffffff"}
```

```http
HTTP/1.1 422 Unprocessable Entity
Content-Type: application/json
X-Request-ID: req_5123456789abcdef0123456789abcdef
X-Trace-ID: 9bf92f3577b34da6a3ce929d0e0e4736

{"error": {"code": "VALIDATION_ERROR", "message": "The request is invalid.", "details": {"errors": [{"field": "name", "type": "string_too_short"}, {"field": null, "type": "extra_forbidden"}]}, "request_id": "req_5123456789abcdef0123456789abcdef"}}
```

O nome do campo desconhecido (`tenant_id`) e os valores enviados não voltam.

### 500 e 503

```http
HTTP/1.1 500 Internal Server Error
Content-Type: application/json
X-Request-ID: req_6123456789abcdef0123456789abcdef
X-Trace-ID: abf92f3577b34da6a3ce929d0e0e4736

{"error": {"code": "INTERNAL_ERROR", "message": "Internal error.", "details": {}, "request_id": "req_6123456789abcdef0123456789abcdef"}}
```

```http
HTTP/1.1 503 Service Unavailable
Content-Type: application/json
X-Request-ID: req_7123456789abcdef0123456789abcdef
X-Trace-ID: bbf92f3577b34da6a3ce929d0e0e4736

{"error": {"code": "DEPENDENCY_UNAVAILABLE", "message": "A required dependency is unavailable.", "details": {}, "request_id": "req_7123456789abcdef0123456789abcdef"}}
```

## Suporte e logs

Informe o `request_id` (ou o `X-Request-ID`) ao suporte. Cada requisição deixa uma linha de log com esse ID, o `trace_id`, o template da rota (`unmatched` quando a rota não existe), o status e a duração e, se a chave foi aceita, os IDs públicos do Tenant e da chave. Cada campo do log tem formato fixo (IDs, template da rota, método HTTP conhecido ou `OTHER`, status, duração), e as mensagens são nomes de evento, nunca texto livre: o log nunca registra a chave, o corpo, a query string, o path recebido nem dados pessoais ([ADR-0016](../adr/0016-observability.md)).

## Contrato OpenAPI

Em `local` e `test`, `/openapi.json` (e o Swagger em `/docs`) descrevem cada operação com o esquema `ApiKey` (HTTP Bearer), os scopes exigidos em `x-cce-required-scopes`, os códigos de erro possíveis com o envelope `ErrorEnvelope` e um exemplo, `WWW-Authenticate` no `401` e os cabeçalhos `X-Request-ID` e `X-Trace-ID` em toda resposta. Em staging e production o documento não é servido.

## Ordem das verificações

Autenticação, depois scope, depois validação do corpo: um pedido sem chave válida recebe `401` mesmo com corpo inválido. Exceção: um corpo que nem é JSON válido é recusado com `422` antes da autenticação, porque é lido antes dela; a resposta não diz nada sobre a chave nem sobre o Tenant.

## Health

`/health/live` e `/health/ready` não usam o envelope:

```json
{"status": "ok"}
{"status": "ready", "checks": {"postgres": "ok", "redis": "ok", "schema": "ok"}}
{"status": "not_ready", "checks": {"postgres": "ok", "redis": "unavailable", "schema": "ok"}}
```

A readiness responde `503` com `not_ready` quando qualquer check está `unavailable`. A liveness não checa dependências.
