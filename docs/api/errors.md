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

Uma resposta `2xx` só é enviada depois do commit da transação. Se o commit falhar, a resposta é o erro (`409`, `500` ou `503`) e nada foi alterado.

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
