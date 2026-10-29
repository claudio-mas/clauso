# ADR-0012: Autenticação por API key, lookup restrito e bootstrap administrativo

**Data:** 29/10/2026
**Status:** Aceito (29/10/2026)

## Contexto

A API do M0 é servidor a servidor: o Host se identifica com uma API key, e o CCE descobre o Tenant a partir dela (Blueprint §36). Há três problemas ligados.

1. **A autenticação acontece antes de haver Tenant.** A RLS (ADR-0003, ADR-0013) só mostra linhas com o contexto de um Tenant, mas é a chave que diz qual é o Tenant. Alguém precisa ler `api_keys` e `tenants` sem contexto, e isso não pode virar uma leitura global para o runtime.
2. **A chave é um segredo.** Guardá-la em claro, ou deixá-la vazar por log, mensagem de erro, `repr` ou tempo de resposta, entrega o acesso a um Tenant inteiro.
3. **Alguém precisa criar o primeiro Tenant e a primeira chave.** Não existe cadastro público nem chave global aceita pela API; o provisionamento é uma operação de quem opera o CCE.

## Alternativas

- **Guardar a chave em claro, ou cifrada de forma reversível.** Um dump do banco ou uma leitura indevida entregaria todas as chaves. O digest não é reversível.
- **bcrypt, scrypt ou Argon2 para o digest.** Foram desenhados para senhas escolhidas por pessoas. A chave tem 256 bits aleatórios, então um HMAC rápido com pepper não perde segurança e não custa dezenas de milissegundos por requisição.
- **SHA-256 simples, sem pepper.** Um dump do banco bastaria para verificar candidatos offline; o pepper fica fora do banco.
- **O runtime lê `api_keys` direto, com uma policy global para `cce_app`.** Faria da leitura global uma capacidade permanente do runtime, inclusive do digest, e esvaziaria a RLS justamente na tabela de credenciais.
- **Um role com `BYPASSRLS`, ou o runtime assumindo `cce_auth` com `SET ROLE`.** O mesmo problema, com uma superfície maior: qualquer SQL arbitrário do runtime ganharia a leitura global.
- **Pedir o Tenant em um cabeçalho e filtrar por ele.** O cliente passaria a escolher o Tenant. Uma chave só vale para o Tenant que a emitiu, e nenhum cabeçalho seleciona outro.
- **Cache positivo de chaves (Redis ou memória).** Faria a revogação e a suspensão esperarem um TTL. O Redis é auxiliar (plano do M0, seção 13.2) e nunca é fonte de autorização.
- **Função `plpgsql`, ou `SECURITY DEFINER` pertencente ao dono das tabelas.** O dono das tabelas (`cce_migrator`) não pode ser o definer: um defeito na função daria ao dono o que a RLS lhe nega. Funções SQL de uma única consulta têm menos superfície que `plpgsql`.
- **JWT assinado pelo CCE como chave.** Serve a outro caso (Blueprint §36: usuário interno e embedded UI); revogar antes da expiração exigiria uma lista no banco, que é o que a chave opaca já resolve.

## Decisão

### Chave e digest

- **Formato.** `cce_<marcador>_<uuid hex>_<segredo base64url>`: marcador por ambiente (`dev`, `tst`, `stg`, `prd`), 32 caracteres hexadecimais do ID e 43 caracteres de `secrets.token_urlsafe(32)`. O parser é uma regex ancorada; a chave nunca é dividida por separador, porque o segredo pode conter `_`.
- **Digest.** `HMAC-SHA256(pepper, "cce-api-key:v1\0" + chave)`, 32 bytes, comparado com `hmac.compare_digest`. O banco guarda só o digest e `pepper_version`; o segredo bruto nunca é persistido.
- **Pepper versionado.** `CCE_API_KEY_PEPPERS` é um mapa `versão -> base64` (cada um com 32 bytes ou mais), e `CCE_API_KEY_ACTIVE_PEPPER_VERSION` escolhe a versão das chaves novas. O lookup devolve a `pepper_version` da chave, que seleciona o pepper na verificação. Pepper ausente invalida a chave e gera um evento operacional (nível ERROR, logger `cce.api_keys.authentication`) com o ID da chave e a versão, nunca o segredo.

### Lookup restrito

A migration 0003 cria `cce_private.lookup_api_key(p_key_id uuid)`:

- **Quem é o definer.** `cce_auth`, role sem login, sem `BYPASSRLS`, que não é dono das tabelas. A função é criada com `SET LOCAL ROLE cce_auth` dentro da migration (o bootstrap deu `SET` ao `cce_migrator`, sem herança), então nasce com o dono certo e sem `ALTER OWNER`.
- **O que o definer lê.** Duas policies `auth_lookup` (`FOR SELECT TO cce_auth USING (true)`) e grants de **coluna**: `tenants(id, status)` e `api_keys(id, tenant_id, secret_digest, pepper_version, scopes, expires_at, revoked_at)`. Nenhuma tabela inteira, nenhuma escrita.
- **O que a função faz.** Recebe só o ID da chave: não há Tenant, padrão nem paginação como argumento, e a chave primária garante no máximo uma linha. Devolve o candidato somente se a chave não foi revogada, não expirou e o Tenant está `active`.
- **Endurecimento.** `SECURITY DEFINER`, `STABLE`, `SET search_path = pg_catalog, pg_temp`, `SET row_security = on`, objetos qualificados e `pg_catalog.now()`. `REVOKE ALL ... FROM PUBLIC` e `GRANT EXECUTE` explícito só para `cce_app` (o `ALTER DEFAULT PRIVILEGES` do bootstrap não é `IN SCHEMA`, então cada função exige o seu grant).
- **O que não se herda.** `cce_migrator` é `NOINHERIT`: não herda a leitura de `cce_auth` e não executa a função. `cce_app` não é membro de `cce_auth` e não pode `SET ROLE`. Só a migration assume `cce_auth`, e a API nunca muda de role.
- **O runtime não lê o digest da tabela.** Não tem grant em `secret_digest` nem em `pepper_version`. O acesso a `api_keys` pelo runtime lista as colunas não secretas (`repository.METADATA_COLUMNS`); o modelo ORM `APIKey` inteiro não é carregado pelo runtime.
- **A função é fixada.** `scripts/check_schema.py` verifica dono, `SECURITY DEFINER`, volatilidade, `search_path`, `row_security`, tipo de retorno, o md5 do corpo e a lista de quem executa; uma função `SECURITY DEFINER` fora da lista é um problema. Mudar o corpo exige nova migration e novo hash.

### Autenticação

`authenticate_api_key(raw_key, settings, session_factory) -> Principal`, onde `Principal` é imutável (`tenant_id`, `key_id`, `scopes`).

1. A chave é interpretada para o ambiente do processo. Ausente, malformada e de outro ambiente falham aqui, sem tocar no banco.
2. Uma sessão curta, **sem contexto de Tenant**, chama o lookup e é fechada antes de qualquer comparação.
3. O HMAC é calculado com o pepper da versão da chave e comparado em tempo constante. Sem candidato (inexistente, expirada, revogada ou Tenant suspenso) ou sem o pepper da versão, a comparação roda contra um digest sentinela e o resultado é descartado.
4. Só uma comparação válida devolve o `Principal`. Todo motivo de rejeição levanta o mesmo `AuthenticationFailed`, com a mesma mensagem e sem causa encadeada (401 `AUTHENTICATION_FAILED` na API, ticket 07).
5. Qualquer erro do banco durante o lookup (conexão recusada, senha, banco inexistente, timeout, permissão) vira `DependencyUnavailable` (503 `DEPENDENCY_UNAVAILABLE`), nunca credencial inválida. A operação de negócio abre depois outra sessão, já sob o contexto do Tenant do `Principal` (ADR-0013).
6. **Sem cache positivo.** Cada autenticação consulta o banco: revogar uma chave ou suspender um Tenant vale na autenticação seguinte. Uma requisição já autenticada pode terminar; não se promete cancelamento instantâneo.

### CLI administrativa

`cce-admin` (Typer; também `python -m cce.admin`) roda como `cce_migrator` e `AdminSettings`, fora da API:

- `tenant create`, `tenant suspend`, `tenant activate`, `api-key issue` e `api-key revoke`.
- Toda operação abre `tenant_session` com o Tenant alvo: o dono também está sujeito à RLS (`FORCE`). Para criar um Tenant, o UUID nasce antes do INSERT e vira o contexto.
- **O Tenant e a chave inicial nascem na mesma transação**, e a chave bruta só é impressa depois do commit. Se o commit falha, a transação é desfeita e a chave não é impressa. Isso não garante o inverso: se a conexão cair depois de o servidor confirmar o commit, ou a saída falhar depois dele, o Tenant e a chave podem existir sem que a chave tenha sido mostrada; o runbook manda conferir o slug e, se preciso, emitir outra chave e revogar a de entrega incerta. `tenant create` emite a chave inicial com todos os scopes (`tenant:read`, `tenant:write`); `api-key issue` aceita só scopes conhecidos, ao menos um.
- Revogar é idempotente (repetir é sucesso e não move o instante), e a chave de outro Tenant é recusada e não é alterada. Suspender e revogar não apagam registros.
- Saída: um objeto JSON em stdout; erros como um objeto JSON com texto fixo em stderr (nunca DSN, mensagem de driver, traceback ou o valor digitado). Códigos: 0 sucesso, 1 operação recusada, 2 uso ou configuração inválidos, 3 banco indisponível ou falha inesperada. A CLI não é uma forma de autenticação HTTP.

### Rotação

- **Chave:** emitir a nova, atualizar o Host e testar, revogar a anterior.
- **Pepper:** distribuir o mapa com a versão antiga e a nova, tornar a nova a ativa, reemitir as chaves, revogar as antigas e só então remover o pepper antigo. Não é possível recalcular o digest de uma chave antiga sem o segredo bruto dela, por isso as chaves são reemitidas, não migradas. Os passos operacionais estão em `docs/runbooks/secrets-rotation.md`.

## Consequências

- Duas credenciais de Tenants distintos autenticam sem que o cliente informe o Tenant, e o runtime continua sem nenhuma leitura global nem acesso ao digest por tabela.
- Cada requisição autenticada custa uma ida ao banco para o lookup, além da transação de dados. É o preço de uma revogação imediata; um cache só entraria com um ADR próprio que definisse o TTL aceito.
- O corpo da função e as permissões ficam fixados por `check_schema`; uma alteração legítima exige migration, hash novo e este ADR atualizado.
- O pepper é um segredo de operação: perdê-lo invalida todas as chaves daquela versão, e vazá-lo, somado a um dump do banco, permite verificar candidatos offline (a chave de 256 bits ainda não é adivinhável).

## Impactos de segurança

- **Fronteira de confiança.** Como no ADR-0013, código capaz de executar SQL arbitrário como runtime pode chamar o lookup com qualquer ID e receber o digest daquela chave. O ID tem 128 bits aleatórios e o digest sozinho não autentica ninguém (é preciso o segredo de 256 bits). O backend continua sendo a fronteira: SQL parametrizado, nenhuma rota que exponha o resultado do lookup e autorização na API.
- **Enumeração e tempo.** Todo motivo de rejeição tem o mesmo erro e a mesma mensagem, e o caminho sem candidato gasta o mesmo HMAC e uma comparação em tempo constante. A diferença de tempo entre uma chave malformada (sem ida ao banco) e uma bem formada é aceita: a malformada não revela nada sobre chaves existentes.
- **Segredos fora de saída.** Nenhum `repr`, `str`, mensagem de erro, evento operacional ou saída da CLI contém a chave, o segredo, o digest ou o pepper. Um erro de uso da CLI não repete o valor digitado (quem cola uma chave no lugar de um ID não a vê na tela). Os testes que manipulam chaves levam o marcador `sensitive`.
- **Quem lê o quê.** `cce_app` executa a função e não lê o digest por tabela; `cce_migrator` não executa a função e, sem `SET ROLE`, não lê nada sem contexto; `cce_auth` não tem login e só lê as colunas listadas.

## Referência ao Blueprint

Blueprint §36 (autenticação de API: API key com scopes), §38 (isolamento entre Tenants) e §48 (rotação de segredos). Plano do M0, seções 11.1 a 11.4. Complementa o ADR-0003 (exceção de `cce_auth`) e o ADR-0013.

## Critério de revisão

Revisar este ADR quando: a API precisar autenticar sem a ida ao banco (cache, com o TTL aceito); surgirem chaves por usuário ou por Host (e não só por Tenant); a lista de scopes deixar de ser fixa; o pepper passar a vir de um cofre com rotação automática; ou o PostgreSQL oferecer uma forma melhor de leitura global restrita que dispense o `SECURITY DEFINER`.
