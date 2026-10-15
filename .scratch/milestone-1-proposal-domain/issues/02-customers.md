# 02: Customers

**What to build:** um Host autenticado cria, consulta, lista e atualiza Customers pela API, identificando-os pelo `external_source` + `external_id` do seu ERP, e nunca vê nem altera Customers de outro Tenant.

**Blocked by:** 01

**Status:** ready-for-agent

**Spec:** milestone-1-proposal-domain/spec.md (user stories 1–11, 14–15, 50–52)

- [ ] `POST` cria o Customer (`cus_...`) e devolve 201; `GET` por ID e por `external_id` funcionam
- [ ] `external_source` + `external_id` são únicos por Tenant; repetir retorna 409 `CONFLICT` com o ID existente em `details`
- [ ] `external_id` sem `external_source` retorna 422; no banco, um CHECK impede a combinação e o índice único parcial cobre o par (teste direto no banco: inserir o mesmo par duas vezes falha)
- [ ] `document` é validado como CPF ou CNPJ (dígitos verificadores), aceito com ou sem pontuação e devolvido normalizado; inválido retorna 422
- [ ] `PATCH` altera nome, e-mail, telefone e `metadata`; tentar alterar `external_id`, `external_source` ou `tenant_id` é recusado (422)
- [ ] `metadata` acima de 8 KiB ou profundidade 3 é recusada com erro claro
- [ ] A listagem pagina por cursor opaco (`limit` padrão 50, máximo 200), sem perder nem repetir itens, ordenada por `created_at` decrescente com desempate por ID
- [ ] Criar e alterar gravam AuditEvent com os campos alterados, sem valores de nome, e-mail, telefone, documento ou `metadata`; logs também não contêm esses dados
- [ ] Os scopes `customer:read` e `customer:write` são exigidos e a ausência retorna 403; chave inválida retorna 401 uniforme
- [ ] Customer de outro Tenant é 404 em todas as operações; `X-Tenant-ID` falso não muda o resultado
- [ ] Tabela com RLS forçada e testes de banco como `cce_app` (sem contexto, A não lê B, `WITH CHECK`); migration sobe e desce
