# Bootstrap de Tenants e chaves

Como criar um Tenant, emitir e revogar chaves de API e suspender ou ativar um Tenant com a CLI `cce-admin`. Plataforma de referência: Linux com bash. O contexto está no [ADR-0012](../adr/0012-api-key-bootstrap.md).

## Antes de começar

- A CLI roda como `cce_migrator` (`AdminSettings`), **fora da API e do container da API**. Ela precisa de `CCE_MIGRATOR_DATABASE_URL` e do material de chave: `CCE_API_KEY_PEPPERS`, `CCE_API_KEY_ACTIVE_PEPPER_VERSION` e `CCE_API_KEY_TTL_DAYS`. Em local e test eles vêm do dotenv (`CCE_ENV_FILE`); em staging e production, do ambiente do processo (nunca de um dotenv).
- O banco já deve estar migrado (`alembic upgrade head`, ver `local-development.md`).
- A CLI imprime **a chave bruta uma vez**. Guarde a saída num cofre de segredos; não a cole em tickets, chats nem logs.

```bash
export CCE_ENV_FILE=.env
alias cce-admin='uv run --project apps/api --locked cce-admin'
```

## Criar um Tenant e a chave inicial

```bash
cce-admin tenant create --name "Empresa A" --slug empresa-a
```

Cria o Tenant (`active`) e uma chave inicial com os scopes `tenant:read` e `tenant:write`, **na mesma transação**; a chave só é impressa depois do commit. Se o commit falhar, a transação é desfeita e a chave não é impressa. A ausência de saída, porém, não prova que nada foi gravado: se a conexão cair depois de o servidor confirmar o commit, ou a saída falhar depois dele, o Tenant e a chave podem existir sem que o JSON tenha chegado (ver "Se algo der errado"). Opções: `--plan`, `--currency` (`BRL`, `USD`, `EUR`), `--timezone` e `--locale` (`pt-BR`, `en-US`).

Saída (stdout, um objeto JSON; os valores aqui são ilustrativos):

```json
{"tenant": {"id": "ten_<32 hex>", "slug": "empresa-a", "status": "active", "...": "..."},
 "api_key": {"id": "key_<32 hex>", "key": "cce_dev_<32 hex>_<segredo>", "label": "initial",
             "scopes": ["tenant:read", "tenant:write"], "expires_at": "2027-01-27T12:00:00+00:00"}}
```

## Emitir outra chave

```bash
cce-admin api-key issue --tenant ten_<id> --label "ERP" --scope tenant:read --scope tenant:write --ttl-days 90
```

`--scope` é repetido e aceita só scopes conhecidos (ao menos um). O Tenant precisa existir.

## Revogar uma chave

```bash
cce-admin api-key revoke --tenant ten_<id> --key key_<id>
```

Vale na próxima autenticação. Repetir é um sucesso e não muda o instante da primeira revogação. A chave de outro Tenant é recusada (código de saída 1) e não é alterada. Revogar não apaga o registro.

## Suspender e ativar um Tenant

```bash
cce-admin tenant suspend --tenant ten_<id>
cce-admin tenant activate --tenant ten_<id>
```

Um Tenant suspenso não autentica nenhuma chave, sem revogá-las; ativar devolve o acesso. Nada é apagado.

## Códigos de saída

| Código | Significa |
|---|---|
| 0 | Sucesso (JSON em stdout) |
| 1 | Operação recusada: Tenant ou chave não encontrados, slug em uso (JSON de erro em stderr) |
| 2 | Uso ou configuração inválidos (opção ausente ou malformada, variável de ambiente inválida) |
| 3 | Banco indisponível ou falha inesperada; o erro cita só o tipo, nunca o DSN nem a mensagem do driver |

Os erros saem em stderr como `{"error": "...", "kind": "..."}`. Um valor mal digitado nunca é repetido na mensagem.

## Se algo der errado

- **A chave foi impressa e se perdeu:** não há como recuperá-la. Emita outra e revogue a antiga.
- **Dúvida se o comando criou algo (sem JSON, ou com código 3):** não presuma que nada foi gravado. A conexão pode cair depois de o servidor confirmar o commit, e a saída pode falhar depois dele; nesses casos o Tenant e a chave existem, mas a chave nunca foi vista. Confira pelo slug: repetir `tenant create` com o mesmo slug responde com código 1 se o Tenant já existe. Se existe e a chave inicial não chegou, emita uma nova com `api-key issue` e revogue a emissão cuja entrega ficou incerta (`api-key revoke`, com o `key_<id>` dela, se você o tiver; senão revogue as chaves do Tenant que o Host não usa).
- **Código 3 em `tenant create`:** verifique a conectividade e o `CCE_MIGRATOR_DATABASE_URL` e confira o slug como acima antes de repetir. O código 3 não garante que nada foi gravado.
