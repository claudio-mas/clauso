# 06: Customer inline na criação da Proposal

**What to build:** um Host cria uma Proposal enviando o cliente no próprio corpo, sem cadastro prévio, e o CCE obtém ou cria o Customer pelo identificador do Host, sem sobrescrever um cliente que já exista.

**Blocked by:** 04

**Status:** ready-for-agent

**Spec:** milestone-1-proposal-domain/spec.md (user stories 17–18)

- [ ] `POST /proposals` aceita `customer_id` ou um objeto `customer`; enviar os dois, ou nenhum, retorna 422
- [ ] Com objeto inline e Customer inexistente para o par `external_source` + `external_id`, o Customer é criado (mesmas validações do ticket 02) na mesma transação da Proposal; se a Proposal falha, o Customer também não existe
- [ ] Com Customer já existente para o par, ele é usado e nenhum campo dele é alterado, mesmo que o corpo traga valores diferentes
- [ ] A criação do Customer inline grava AuditEvent
- [ ] Sempre que o corpo traz o objeto `customer`, o scope `customer:write` é exigido além de `proposal:write`, exista ou não o Customer; sem ele, 403 e nada é gravado. Com `customer_id`, basta `proposal:write`
