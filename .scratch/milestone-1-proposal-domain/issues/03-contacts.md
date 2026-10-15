# 03: Contacts de um Customer

**What to build:** um Host cadastra e mantém as pessoas de um Customer (Contacts), que depois poderão ser Recipients de uma Publication.

**Blocked by:** 02

**Status:** ready-for-agent

**Spec:** milestone-1-proposal-domain/spec.md (user stories 12–13, 51)

- [ ] Criar, consultar, listar e atualizar Contacts (`ctc_...`) como subrecurso de um Customer, com nome, e-mail e telefone
- [ ] Criar Contact para Customer de outro Tenant ou inexistente retorna 404
- [ ] A chave estrangeira composta por Tenant impede, no banco, ligar um Contact a Customer de outro Tenant (teste direto no banco)
- [ ] Listagem com paginação por cursor, no mesmo contrato do ticket 02
- [ ] Os scopes de customer se aplicam; criar e alterar gravam AuditEvent sem valores de campos pessoais; logs não contêm dados pessoais
- [ ] RLS forçada e testes de banco como `cce_app`; migration sobe e desce
