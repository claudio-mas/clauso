# 06: Autenticação por API key e bootstrap administrativo

**What to build:** um operador cria Tenants e emite chaves pela CLI administrativa, e a autenticação resolve a chave para um `Principal` imutável (Tenant, chave e scopes) sem que o cliente informe o Tenant. Duas credenciais de Tenants distintos autenticam corretamente, e a revogação vale na próxima autenticação.

**Blocked by:** 04, 05

**Status:** ready-for-agent

**Plano:** Tarefa 6 e seção 11. **Vocabulário:** Tenant, User (`CONTEXT.md`); o ator é a chave, não um User.

- [ ] Testes do lookup, da autenticação e do bootstrap escritos antes e falhando por função inexistente e ausência de CLI
- [ ] A migration `0003` cria a função de lookup restrita (`SECURITY DEFINER`, `search_path` fixo, `EXECUTE` só para o runtime, sem `PUBLIC`) e as policies de leitura de `cce_auth`
- [ ] O runtime não lê o digest diretamente, não assume `cce_auth` e a função não aceita tenant, padrão nem paginação
- [ ] `cce_migrator`, por ser `NOINHERIT`, não herda a leitura global
- [ ] Chave válida autentica; ausente, malformada, segredo errado, expirada, revogada, tenant suspenso e chave de outro ambiente retornam o mesmo erro uniforme
- [ ] Candidato ausente executa comparação com digest sentinela; pepper ausente invalida a chave e gera evento operacional sem segredo
- [ ] Banco fora do ar produz erro de dependência (503), nunca credencial inválida (401)
- [ ] A CLI cria Tenant e chave inicial na mesma transação e mostra a chave só depois do commit; falha no commit não deixa Tenant, chave nem exibição
- [ ] A CLI revoga chaves, rejeita revogação de chave de outro Tenant e suspende ou ativa Tenants
- [ ] Nenhum cache positivo de chaves existe; a revogação vale na autenticação seguinte
- [ ] `check_schema` passa a verificar função, owner, `search_path` e `EXECUTE`
