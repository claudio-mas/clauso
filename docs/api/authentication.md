# Autenticação por API key

Como um Host se identifica na API do CCE e o que acontece com a chave. A decisão e as alternativas estão no [ADR-0012](../adr/0012-api-key-bootstrap.md); o isolamento por Tenant, no [ADR-0013](../adr/0013-transaction-tenant-context.md).

## Estado atual

Estão implementados o material da chave, a função de lookup, a autenticação (`authenticate_api_key`, que devolve um `Principal`) e a CLI que emite e revoga chaves. As rotas HTTP, o cabeçalho `Authorization` e os códigos de resposta chegam com o ticket 07 do M0; esta página descreve o contrato que o ticket 07 implementa.

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
- O cliente nunca informa o Tenant: ele vem da chave. Nenhum cabeçalho seleciona outro Tenant.
- Fora de `local` e `test`, só por HTTPS.

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
