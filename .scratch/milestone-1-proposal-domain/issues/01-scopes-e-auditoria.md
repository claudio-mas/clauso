# 01: Scopes novos e auditoria append-only

**What to build:** um operador consegue emitir, pela CLI administrativa, chaves com os scopes `customer:read`, `customer:write`, `proposal:read` e `proposal:write`, e o CCE passa a ter um serviço de auditoria que grava um AuditEvent (ator, ação, entidade, campos alterados, valores antigo e novo, instante, IP truncado e `request_id`) na mesma transação do dado alterado. O AuditEvent nunca guarda valores de campos pessoais (ADR-0022). Chaves já emitidas não ganham os novos scopes sozinhas. Este ticket é o prefactor: os demais tickets de escrita dependem dele.

**Blocked by:** m0/11

**Status:** ready-for-agent

**Spec:** milestone-1-proposal-domain/spec.md (user stories 52–54, 56–57)

- [ ] A CLI emite uma chave com qualquer combinação dos quatro scopes novos; scope fora da lista continua recusado
- [ ] Chaves emitidas antes da mudança mantêm exatamente os scopes antigos
- [ ] Um AuditEvent é gravado na transação do dado alterado: se a transação falha, o evento também não existe
- [ ] O IP é guardado truncado (IPv4 /24, IPv6 /48) e o `request_id` é o da requisição
- [ ] O serviço de auditoria recebe a lista de campos pessoais de cada entidade e, para eles, grava só o nome em `changed_fields`, sem valor antigo nem novo; um teste confirma que nenhum nome, e-mail, telefone ou documento chega a audit_events
- [ ] O role runtime consegue INSERT e SELECT em audit_events, mas não UPDATE, DELETE nem TRUNCATE (teste real como `cce_app`)
- [ ] RLS isola audit_events por Tenant: sem contexto retorna zero linhas, A não lê B, `WITH CHECK` rejeita escrita para B
- [ ] Migration sobe e desce em banco descartável; `check_schema` cobre a tabela nova
