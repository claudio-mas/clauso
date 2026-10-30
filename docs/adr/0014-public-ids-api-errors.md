# ADR-0014: IDs públicos com prefixo sobre UUID4 e códigos de erro estáveis

**Data:** 30/10/2026
**Status:** Aceito (30/10/2026)

## Contexto

A API do M0 expõe o Tenant e a API key, e cada recurso dos marcos seguintes terá um ID público (Blueprint §70). Ao mesmo tempo, o Host precisa reagir a erros por programa: o código do erro faz parte do contrato, a mensagem não (Blueprint §69). Faltava decidir três coisas: o formato do ID interno, a forma do ID que sai pela API e o vocabulário e o envelope dos erros. A forma dos erros também é uma fronteira de segurança, porque uma resposta de erro é um caminho natural para vazar dado recebido, SQL ou detalhe de infraestrutura.

## Alternativas

- **Expor o UUID cru.** Não diz o tipo do recurso: um ID de chave colado no lugar de um de Tenant passa despercebido, e logs e suporte ficam mais difíceis.
- **UUIDv7 já no M0.** Ordenável por tempo e melhor para índices em tabelas grandes, mas o M0 tem só duas tabelas pequenas e a biblioteca padrão do Python 3.12 não gera UUIDv7. Fica como evolução.
- **IDs sequenciais.** Revelam volume e permitem enumeração entre Tenants.
- **ID público diferente do interno (tabela de mapeamento).** Mais uma consulta por requisição sem ganho no M0.
- **Erros só com status HTTP e texto livre.** O Host dependeria da mensagem, que muda; e texto livre tende a carregar o valor recebido.
- **Problem Details (RFC 9457).** Bom formato, mas o Blueprint já fixou o envelope `error.code/message/details/request_id`, e o SDK futuro parte dele.

## Decisão

### IDs

- O ID interno é um **UUID4**, gerado pela aplicação antes do INSERT (`uuid.uuid4()`), guardado como `uuid` no PostgreSQL.
- O ID público é **prefixo + 32 caracteres hexadecimais minúsculos** do mesmo UUID: `ten_<hex>` para Tenant e `key_<hex>` para API key. Os próximos prefixos seguem o Blueprint §70 (`cus_`, `prop_`, `ver_`, `pub_` etc.) e chegam com os marcos que os usam.
- A decodificação (`cce.shared.ids`) aceita só o formato exato e recusa outro prefixo, tamanho ou letra maiúscula. A mensagem de recusa nunca repete o valor recebido, porque quem decodifica pode ter recebido uma API key bruta por engano.
- Na API, o Tenant nunca vem de um ID enviado pelo cliente: vem da chave. Não existe rota por `tenant_id` nem listagem de Tenants, e o cabeçalho `X-Tenant-ID` é ignorado.

### Erros

- Toda resposta de erro usa o envelope do Blueprint §69: `{"error": {"code", "message", "details", "request_id"}}`. O `request_id` é o mesmo do cabeçalho `X-Request-ID`.
- **Os códigos são contrato** e só mudam com versão nova da API. No M0:

  | HTTP | `code` |
  |---|---|
  | 401 | `AUTHENTICATION_FAILED` (com `WWW-Authenticate: Bearer`) |
  | 403 | `FORBIDDEN` |
  | 404 | `NOT_FOUND` |
  | 405 | `METHOD_NOT_ALLOWED` |
  | 409 | `CONFLICT` |
  | 422 | `VALIDATION_ERROR` |
  | 500 | `INTERNAL_ERROR` |
  | 503 | `DEPENDENCY_UNAVAILABLE` |

  O `405` não estava na tabela do plano do M0 (seção 12.5); entrou para que nenhuma resposta de erro do roteador saia fora do envelope. Códigos de domínio mais específicos (por exemplo `PROPOSAL_NOT_PUBLISHABLE`) podem ser acrescentados pelos marcos seguintes, sem renomear estes.
- **A mensagem é fixa por código** e serve a pessoas, não a programas.
- **`details` só leva o que é seguro e útil:** na validação, uma lista de `{field, type}`, sem o valor recebido, sem `ctx` e sem a mensagem do Pydantic, e sem o nome de um campo desconhecido enviado pelo cliente (`extra_forbidden` sai com `field: null`); no `403`, os scopes exigidos; no `409`, o campo público ligado à constraint.
- **Erros do banco:** uma constraint conhecida vira `409` com o campo público; qualquer outra violação, `500`. Banco inalcançável (conexão recusada, timeout, conexão invalidada) vira `503`. Nunca saem SQL, parâmetros, nome de tabela ou constraint, nem mensagem do driver.
- **Autenticação:** todo motivo de recusa da chave dá o mesmo `401`; falha do banco durante a autenticação dá `503`, nunca `401`.
- **Erro inesperado:** `500 INTERNAL_ERROR`, gerado pelo middleware mais externo para que também leve `X-Request-ID` e `X-Trace-ID`. O log registra o tipo e os frames, sem mensagem nem locais.
- As respostas de `/health/ready` não usam o envelope: são `{"status", "checks"}`, com `503` quando algo não está pronto (ADR-0015).

## Consequências

- O Host trata erros pelo `code` e correlaciona com o suporte pelo `request_id`.
- Um ID público é reconhecível em logs e no suporte, e um ID do tipo errado é recusado na fronteira.
- UUID4 não é ordenável por tempo; migrar para UUIDv7 não muda o formato público (continua prefixo + 32 hex).
- Mensagens de validação ficam menos detalhadas que as do Pydantic: o cliente recebe o campo e o tipo do erro, e a documentação do contrato explica os tipos.

## Impactos de segurança

- Nenhuma resposta de erro ecoa o que o cliente enviou: um segredo colado no campo errado não volta no corpo.
- IDs aleatórios não permitem enumeração; e, como o Tenant vem da chave, conhecer o ID de outro Tenant não dá acesso a nada.
- O `401` não distingue chave inexistente, expirada, revogada ou de Tenant suspenso.

## Referência ao Blueprint

Blueprint §69 (modelo de erro) e §70 (IDs). Plano do M0, seções 12.3 e 12.5. Complementa o ADR-0004 (API-first) e o ADR-0012 (autenticação).

## Critério de revisão

Revisar quando: uma tabela crescer a ponto de a ordem do UUID pesar nos índices (UUIDv7); um marco precisar de um código de erro que não caiba nesta tabela; o SDK ou o OpenAPI público exigirem outro formato de erro; ou surgir um ID público que não possa derivar do interno.
