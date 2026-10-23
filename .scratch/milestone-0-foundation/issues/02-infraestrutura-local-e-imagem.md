# 02: Infraestrutura local e imagem

**What to build:** um desenvolvedor sobe PostgreSQL 17 e Redis saudáveis com Docker Compose, com as roles do produto provisionadas pela matriz de privilégios, e consegue construir a imagem da API. A composição da API não recebe credencial administrativa. Ainda não há aplicação rodando, e nenhum health falso mascara isso.

**Blocked by:** 01

**Status:** in-progress

**Plano:** Tarefa 2 e seções 6 e 7.

- [ ] Os testes de roles e schemas são escritos antes e falham enquanto as roles não existem
- [ ] `cce_migrator`, `cce_app` e `cce_auth` existem com os atributos da matriz (sem superusuário, sem `BYPASSRLS`, `NOINHERIT`; `cce_auth` sem login) e `cce_app` não é membro de nenhuma outra
- [ ] Os schemas `cce` e `cce_private` existem, com `CREATE` em `public` revogado
- [ ] Como `cce_app`, `CREATE TABLE` e `SET ROLE cce_migrator` falham com privilégio insuficiente
- [ ] O bootstrap não rotaciona senhas silenciosamente e é reaplicável
- [ ] A configuração renderizada do serviço da API não contém a URL do migrator nem a senha do `postgres` (verificado por código, sem imprimir segredos)
- [ ] A imagem constrói, roda como usuário não root e não contém dotenv, `uv` nem dependências de desenvolvimento
- [ ] O serviço de migration é de execução única e a API só inicia depois que ele conclui
- [ ] `.env.test` usa projeto Compose e banco separados (`cce_test`)
