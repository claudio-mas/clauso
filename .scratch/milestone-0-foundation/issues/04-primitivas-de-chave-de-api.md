# 04: Primitivas de chave de API

**What to build:** o CCE sabe gerar, interpretar e verificar uma chave de API (formato com marcador de ambiente, UUID e segredo de 256 bits) de forma pura, sem banco e sem declarar a autenticação pronta.

**Blocked by:** 01

**Status:** ready-for-agent

**Plano:** Tarefa 4 e seção 11.1.

- [ ] Testes de formato, tamanho do segredo (43 caracteres), marcador por ambiente e segredo com underscore escritos antes e falhando
- [ ] `parse_key` rejeita chave de outro ambiente, formato malformado e tamanho errado, com o mesmo tipo de erro
- [ ] O digest é HMAC-SHA256 com o pepper versionado e prefixo de domínio; é estável para a mesma entrada e diferente com outro pepper
- [ ] A comparação usa tempo constante sobre bytes de tamanho fixo
- [ ] `repr` e `str` de `ParsedKey` e `IssuedKey` nunca mostram o segredo
- [ ] Nada usa bcrypt ou Argon2 para chaves, e nenhum documento apresenta o HMAC como hash de senha humana
