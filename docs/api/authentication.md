# Autenticação por API key

Como um Host se identifica na API do CCE e o que acontece com a chave. A decisão e as alternativas estão no [ADR-0012](../adr/0012-api-key-bootstrap.md); o isolamento por Tenant, no [ADR-0013](../adr/0013-transaction-tenant-context.md).

## Estado atual

Estão implementados o material da chave, a função de lookup, a autenticação (`authenticate_api_key`, que devolve um `Principal`), a CLI que emite e revoga chaves e a API HTTP: `GET /api/v1/auth/context`, `GET /api/v1/tenant` e `PATCH /api/v1/tenant`. O formato dos erros está em [`errors.md`](errors.md). Em `local` e `test`, o contrato completo, com o esquema Bearer e os scopes de cada operação, está em `/openapi.json`.

## A chave

```text
cce_<marcador>_<id>_<segredo>
cce_dev_0123456789abcdef0123456789abcdef_<43 caracteres base64url>
```

| Parte | Significado |
|---|---|
| `cce` | Prefixo fixo |
| marcador | Ambiente: `dev` (local), `tst` (test), `stg` (staging), `prd` (production) |
| id | 32 caracteres hexadecimais: o identificador público da chave (`key_<id>`) |
| segredo | 43 caracteres base64url (256 bits). Pode conter `_` e `-` |

Uma chave de outro ambiente é recusada como qualquer chave inválida. O segredo é mostrado **uma única vez**, quando a chave é emitida; o CCE guarda só o digest (HMAC-SHA256 com pepper) e não consegue mostrá-la de novo. Se ela se perder, emita outra e revogue a antiga.

Os exemplos desta documentação nunca contêm uma credencial real.

## Como enviar

```http
GET /api/v1/auth/context HTTP/1.1
Authorization: Bearer cce_dev_0123456789abcdef0123456789abcdef_<segredo>
```

- Só o cabeçalho `Authorization: Bearer` autentica. Query string e cookie não.
- Exatamente um cabeçalho `Authorization`, no formato `Bearer <chave>` (o esquema não diferencia maiúsculas). Dois cabeçalhos, espaço sobrando ou outro esquema dão `401`.
- O cliente nunca informa o Tenant: ele vem da chave. Nenhum cabeçalho seleciona outro Tenant; `X-Tenant-ID` é ignorado.
- Fora de `local` e `test`, só por HTTPS.

## Contexto da chave

Basta uma chave válida, com qualquer scope. A resposta nunca traz o digest nem o segredo.

```http
GET /api/v1/auth/context HTTP/1.1
Host: api.cce.example
Authorization: Bearer cce_dev_0123456789abcdef0123456789abcdef_<segredo>
```

```http
HTTP/1.1 200 OK
Content-Type: application/json
X-Request-ID: req_0123456789abcdef0123456789abcdef
X-Trace-ID: 4bf92f3577b34da6a3ce929d0e0e4736

{"tenant_id": "ten_9f1c2d3e4b5a69788796a5b4c3d2e1f0", "key_id": "key_0123456789abcdef0123456789abcdef", "scopes": ["tenant:read", "tenant:write"]}
```

O `key_id` é o identificador da própria chave (o `<id>` dela, com o prefixo `key_`). Todo exemplo desta página usa valores ilustrativos.

## Exemplos completos

### Ler o próprio Tenant (`tenant:read`)

```http
GET /api/v1/tenant HTTP/1.1
Host: api.cce.example
Authorization: Bearer cce_dev_0123456789abcdef0123456789abcdef_<segredo>
```

```http
HTTP/1.1 200 OK
Content-Type: application/json
X-Request-ID: req_1123456789abcdef0123456789abcdef
X-Trace-ID: 5bf92f3577b34da6a3ce929d0e0e4736

{"id": "ten_9f1c2d3e4b5a69788796a5b4c3d2e1f0", "name": "Clínica Exemplo", "slug": "clinica-exemplo", "status": "active", "plan": "development", "default_currency": "BRL", "timezone": "America/Sao_Paulo", "locale": "pt-BR", "created_at": "2026-10-30T12:00:00+00:00", "updated_at": "2026-10-30T12:00:00+00:00"}
```

### Alterar o próprio Tenant (`tenant:write`)

Só `name`, `default_currency`, `timezone` e `locale`; qualquer outro campo dá `422`.

```http
PATCH /api/v1/tenant HTTP/1.1
Host: api.cce.example
Authorization: Bearer cce_dev_0123456789abcdef0123456789abcdef_<segredo>
Content-Type: application/json

{"name": "Clínica Exemplo Centro", "timezone": "America/Recife"}
```

```http
HTTP/1.1 200 OK
Content-Type: application/json
X-Request-ID: req_2123456789abcdef0123456789abcdef
X-Trace-ID: 6bf92f3577b34da6a3ce929d0e0e4736

{"id": "ten_9f1c2d3e4b5a69788796a5b4c3d2e1f0", "name": "Clínica Exemplo Centro", "slug": "clinica-exemplo", "status": "active", "plan": "development", "default_currency": "BRL", "timezone": "America/Recife", "locale": "pt-BR", "created_at": "2026-10-30T12:00:00+00:00", "updated_at": "2026-10-30T12:05:00+00:00"}
```

### Chave recusada

Qualquer motivo (ausente, malformada, de outro ambiente, inexistente, segredo errado, expirada, revogada, Tenant suspenso) dá a mesma resposta:

```http
HTTP/1.1 401 Unauthorized
Content-Type: application/json
WWW-Authenticate: Bearer
X-Request-ID: req_3123456789abcdef0123456789abcdef
X-Trace-ID: 7bf92f3577b34da6a3ce929d0e0e4736

{"error": {"code": "AUTHENTICATION_FAILED", "message": "Authentication failed.", "details": {}, "request_id": "req_3123456789abcdef0123456789abcdef"}}
```

### Scope ausente

Uma chave só com `tenant:read` tentando o `PATCH` acima:

```http
HTTP/1.1 403 Forbidden
Content-Type: application/json
X-Request-ID: req_4123456789abcdef0123456789abcdef
X-Trace-ID: 8bf92f3577b34da6a3ce929d0e0e4736

{"error": {"code": "FORBIDDEN", "message": "The API key does not have the required scope.", "details": {"required_scopes": ["tenant:write"]}, "request_id": "req_4123456789abcdef0123456789abcdef"}}
```

### Banco indisponível na autenticação

```http
HTTP/1.1 503 Service Unavailable
Content-Type: application/json
X-Request-ID: req_5123456789abcdef0123456789abcdef
X-Trace-ID: 9bf92f3577b34da6a3ce929d0e0e4736

{"error": {"code": "DEPENDENCY_UNAVAILABLE", "message": "A required dependency is unavailable.", "details": {}, "request_id": "req_5123456789abcdef0123456789abcdef"}}
```

Nesse caso a chave não foi julgada: tente de novo mais tarde, sem trocar de chave.

### Com `curl`

```bash
# A chave vem de uma variável de ambiente e o cabeçalho é lido da entrada padrão (`-H @-`):
# ela não fica no histórico do shell nem nos argumentos do processo.
curl -sS https://api.cce.example/api/v1/auth/context \
  -H @- <<< "Authorization: Bearer ${CCE_API_KEY:?defina CCE_API_KEY}"
```

A chave nunca aparece nos logs do CCE: a requisição é registrada com o template da rota, o status, a duração e os IDs públicos do Tenant e da chave ([ADR-0016](../adr/0016-observability.md)).

## Scopes

| Scope | Permite |
|---|---|
| `tenant:read` | Ler o próprio Tenant |
| `tenant:write` | Alterar os campos permitidos do próprio Tenant |

Uma chave carrega ao menos um scope e só scopes conhecidos. A chave inicial de um Tenant tem os dois. Os scopes de domínio (por exemplo `proposal:publish`, ADR-0023) chegam com os marcos que os usam.

## Validade e revogação

- A chave expira (`--ttl-days`, de 1 a 365 dias; o padrão vem de `CCE_API_KEY_TTL_DAYS`, 90).
- Revogar ou suspender o Tenant vale **na autenticação seguinte**: não há cache de chaves. Uma requisição que já passou da autenticação pode terminar.

## Respostas

| Situação | Resposta |
|---|---|
| Chave ausente, malformada, de outro ambiente, inexistente, com segredo errado, expirada, revogada ou de Tenant suspenso | `401 AUTHENTICATION_FAILED`, sempre a mesma mensagem, sem dizer qual foi o motivo |
| O banco não respondeu à autenticação | `503 DEPENDENCY_UNAVAILABLE` (nunca `401`) |
| Chave válida sem o scope exigido pela rota | `403 FORBIDDEN` |

A resposta não permite descobrir se uma chave existe nem por que foi recusada.

## Emitir, revogar e rotacionar

Pela CLI administrativa, por quem opera o CCE: [`tenant-bootstrap.md`](../runbooks/tenant-bootstrap.md) para criar Tenants e chaves e [`secrets-rotation.md`](../runbooks/secrets-rotation.md) para rotacionar chaves e peppers. Não existe cadastro público nem chave global aceita pela API.
