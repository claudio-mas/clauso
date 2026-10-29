# ADR-0013: Contexto de Tenant local à transação e sessão por operação

**Data:** 29/10/2026
**Status:** Proposto (aguarda aprovação da pessoa na Pull Request do ticket 05 do M0)

## Contexto

O ADR-0003 escolheu um banco compartilhado com RLS forçada: cada linha de tabela empresarial carrega o `tenant_id`, e as policies a comparam ao Tenant da transação. Falta decidir como esse Tenant chega ao PostgreSQL e por quanto tempo ele vale. O risco central é uma conexão devolvida ao pool carregar o Tenant anterior: a requisição seguinte, de outro Tenant ou sem Tenant, leria ou alteraria dados alheios. O mesmo vale para uma operação cancelada no meio de uma transação e para uma resposta HTTP de sucesso enviada antes de a transação terminar.

## Alternativas

- **`SET app.tenant_id` de sessão, ou configurado no checkout do pool.** O valor sobrevive à transação e depende de alguém limpá-lo. Um erro, um cancelamento ou um caminho de código que esqueça o reset deixa o Tenant anterior na conexão reutilizada.
- **Um role de banco por Tenant.** Multiplica roles, pools e migrations, e o ADR-0003 já rejeitou o isolamento por estrutura.
- **Filtro `WHERE tenant_id = ...` só na aplicação.** Uma consulta sem o filtro vaza dados. A RLS existe justamente como defesa em profundidade contra essa omissão.
- **Uma sessão por requisição HTTP, reaproveitada por vários casos de uso.** Prende a transação à vida da requisição, permite trocar de Tenant no meio dela e esconde o momento do commit.
- **Commit depois de montar a resposta (ou em um middleware).** Uma falha no commit chegaria ao cliente depois de um `200`.

## Decisão

- **Contexto local à transação.** O Tenant é aplicado com `SELECT set_config('app.tenant_id', :tenant_id, true)`, com parâmetro ligado. O terceiro argumento `true` limita o valor à transação corrente: no COMMIT ou no ROLLBACK ele desaparece, inclusive quando a transação termina por erro ou cancelamento. Não se usa `SET` de sessão, SQL concatenado nem configuração no checkout do pool, e o reset do pool continua habilitado.
- **Falha fechada.** As policies comparam `tenant_id` a `NULLIF(current_setting('app.tenant_id', true), '')::uuid`. Sem contexto (GUC ausente ou vazio depois de uma transação anterior) a comparação é nula e o runtime vê zero linhas, e uma escrita é recusada. Um valor que não é UUID gera erro (`22P02`), nunca um fallback para "todas as linhas".
- **API.** `set_tenant_context(session, tenant_id: UUID)` exige um UUID e uma transação aberta. `tenant_session(factory, tenant_id)` é o gerenciador de contexto que abre a sessão, abre a transação, aplica o contexto antes de qualquer acesso, entrega a sessão, faz `flush` e `commit` ao sair normalmente, e desfaz a transação em exceção ou cancelamento. Em todos os casos fecha a sessão e devolve a conexão.
- **Sessão por operação.** Cada operação de negócio e cada autenticação abrem a própria sessão. Uma `AsyncSession` nunca é compartilhada entre tarefas concorrentes, e casos de uso não abrem transações aninhadas para trocar de Tenant. Trabalho em segundo plano abre sessão e contexto próprios e nunca captura a sessão da requisição.
- **Repositories não confirmam.** Eles podem dar `flush`, nunca `commit` nem `rollback`; só `tenant_session` termina transações. Um teste percorre o código-fonte e recusa qualquer chamada a `commit` ou `rollback` no pacote.
- **Commit antes da resposta HTTP.** A dependência transacional da API (ticket 07) só devolve o controle depois de `tenant_session` ter confirmado a transação. Uma falha de flush ou de commit vira erro HTTP (500, 409 ou 503, conforme o erro) e nunca `200`.
- **Prova.** O isolamento é testado em PostgreSQL real, como `cce_app`, com `pool_size=1` e `max_overflow=0`: Tenant A, depois sem contexto (zero linhas), depois Tenant B (só B), repetido após erro, após exceção do chamador e após cancelamento. O DML é provado numa tabela de teste (`rls_probe`) criada só no banco de teste, porque o runtime não tem INSERT nas tabelas reais.

## Consequências

- Todo acesso a tabela empresarial passa por `tenant_session`; fora dela o runtime não enxerga nada. Trabalho que precise de mais de um Tenant percorre os Tenants um a um (ADR-0025).
- A transação dura a operação inteira e o contexto é imutável dentro dela. Tarefas longas precisam de mais de uma operação.
- O commit acontece dentro do gerenciador, então a camada HTTP não tem como responder antes dele, e o repository não tem como confirmar por conta própria.
- Uma conexão cancelada ou com erro volta ao pool sem contexto; o pior caso é descartar a conexão e abrir outra.

## Impactos de segurança

- O GUC não é prova criptográfica de identidade. Código capaz de executar SQL arbitrário como runtime pode chamar `set_config` com outro UUID (plano do M0, seção 10.3). A RLS protege contra omissão de filtro e acesso acidental; SQL parametrizado, ausência de execução de SQL livre e autorização na API continuam obrigatórios, e o backend é a fronteira de confiança.
- O valor de `tenant_id` sempre vai como parâmetro ligado e sai de um `UUID` tipado, nunca de texto do cliente.
- O contexto não carrega segredo. Logs e erros não incluem parâmetros de SQL (`hide_parameters=True` no engine).
- As policies valem também para o dono das tabelas (`FORCE`), então uma migration ou a CLI administrativa sem contexto também veem zero linhas.

## Referência ao Blueprint

Blueprint §38 (isolamento entre Tenants) e plano do M0, seções 10.1 a 10.4 e 14. Complementa o ADR-0003.

## Critério de revisão

Revisar este ADR quando: uma operação precisar de mais de um Tenant na mesma transação; o pool, o driver ou o SQLAlchemy mudarem a semântica de cancelamento de transações; a API passar a executar trabalho que sobreviva à requisição; ou surgir um mecanismo de identidade verificável no banco que dispense o GUC.
