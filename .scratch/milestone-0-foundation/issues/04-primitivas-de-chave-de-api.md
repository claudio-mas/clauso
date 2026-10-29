# 04: Primitivas de chave de API

**What to build:** o CCE sabe gerar, interpretar e verificar uma chave de API (formato com marcador de ambiente, UUID e segredo de 256 bits) de forma pura, sem banco e sem declarar a autenticação pronta.

**Blocked by:** 01

**Status:** in-review

**Plano:** Tarefa 4 e seção 11.1.

- [ ] Testes de formato, tamanho do segredo (43 caracteres), marcador por ambiente e segredo com underscore escritos antes e falhando
- [ ] `parse_key` rejeita chave de outro ambiente, formato malformado e tamanho errado, com o mesmo tipo de erro
- [ ] O digest é HMAC-SHA256 com o pepper versionado e prefixo de domínio; é estável para a mesma entrada e diferente com outro pepper
- [ ] A comparação usa tempo constante sobre bytes de tamanho fixo
- [ ] `repr` e `str` de `ParsedKey` e `IssuedKey` nunca mostram o segredo
- [ ] Nada usa bcrypt ou Argon2 para chaves, e nenhum documento apresenta o HMAC como hash de senha humana

## Comments

### 2026-10-29 — Parecer do Revisor (Lupa), rodada 1: mudanças necessárias

PR #6, head `351a5b9`. Revisão com foco em segurança. Os critérios de aceite estão atendidos e não há defeito criptográfico bloqueante nem importante na implementação: formato `cce_<marcador>_<UUID hex>_<segredo de 43 caracteres>` (marcadores `dev`/`tst`/`stg`/`prd`, `secrets.token_urlsafe(32)`, `_` preservado, UUID extraído); `InvalidKeyError` com mensagem única e sem eco para ambiente divergente, formato e tamanho inválidos; HMAC-SHA256 estável, dependente da chave e do pepper e separado por domínio; seleção de pepper por versão; `compare_digest` sempre sobre dois valores de 32 bytes, inclusive nas rejeições; `repr`/`str` de `ParsedKey` e `IssuedKey` mascaram token e digest (também em f-string, `format`, lista e dicionário); sem bcrypt/Argon2 e sem tratar o HMAC como hash de senha humana; o módulo declara que não autentica nada.

Problema:
1. **[importante]** `tests/unit/test_key_security.py:66`, `:231` e `:415-421`: as regressões podem imprimir as próprias chaves e segredos quando detectam um defeito. Há asserts introspectivos sobre `raw`/`get_secret_value` e, no teste de exceção, `assert_no_leaks` é chamado dentro do `except` que trata um `RuntimeError` com a chave na mensagem; se a representação deixar de mascarar o token, o `pytest.fail` do helper fica encadeado ao `RuntimeError` original e a saída mostra a chave antes da mensagem fixa, mesmo com `pytrace=False`. Contraprovas em memória do Revisor, sem expor as sentinelas: (a) trocar `generate_key` só no módulo de testes por uma chave sintética com um caractere extra fez `test_generated_key_has_the_documented_format[test]` falhar imprimindo a chave inteira e o segredo; (b) trocar `ParsedKey.__repr__`/`__str__` por um retorno do token fez os dois testes de representação falharem, e o de exceção imprimiu o token no encadeamento do `RuntimeError`. São chaves sintéticas da suíte, não credenciais reais; o defeito é que a própria evidência de não vazamento não mantém a higiene de falhas exigida.
   Correção sugerida: usar verificações booleanas com `require` e mensagens fixas para toda expressão que envolva chave, segredo, pepper ou digest, em vez de `assert` introspectivo (revisar também os asserts de igualdade de peppers, digests e material emitido). No teste de exceção, capturar a mensagem no `except` e executar `assert_no_leaks` depois de sair dele, para não encadear o erro original sensível. Acrescentar regressão que execute pytest real com os defeitos sintéticos e verifique stdout, stderr e traceback: deve falhar detectando o defeito sem imprimir sentinela, chave, segredo, digest nem pepper. Cobrir também `IssuedKey` em mensagem de exceção.

Opiniões sobre os sete pontos, todas favoráveis: (1) `fullmatch` ancora a regex e rejeita newline final; grupos de tamanho fixo e classes ASCII evitam divisão por `_` e homóglifos; retirar só `re.ASCII` é mutação equivalente, não lacuna de teste. (2) Substituir digest inválido por zeros e aplicar a validade ao resultado é correto; o Revisor não afirma tempo rigorosamente constante da função Python inteira nem usou benchmark como prova. (3) Os erros de base64 não ecoam o pepper; usar `validate=True` também em `pepper_bytes` seria defesa adicional, não impedimento. (4) O `key_id` e o marcador de ambiente já estão na mensagem HMAC (a chave inteira); a versão do pepper seleciona a chave HMAC por metadado confiável e não precisa ser concatenada; manter e revogar versões é dos tickets seguintes. (5) `UnknownPepperVersion` com a versão (inteiro, não segredo) é aceitável; o autenticador futuro deve convertê-la em credencial inválida e evento operacional. (6) `SecretStr` protege a representação, não memória nem serialização arbitrária; `ParsedKey` não tem `__dict__`; `asdict`, `copy`, `deepcopy` e o dump JSON do Pydantic ficam mascarados; `pickle` contém o token em texto, o que é acesso explícito. `IssuedKey.secret_digest` fica acessível como bytes por contrato e não deve virar payload ou log genérico. (7) As contraprovas do Revisor (remover `DIGEST_DOMAIN`, trocar `verify_digest` por `==`, representação insegura) derrubaram os testes certos; falta apenas a regressão da saída de falha do item importante.

Checks do Revisor: `uv sync --locked`, `uv lock --check` (76 pacotes), `pytest tests/unit` (535 passed), Ruff, `ruff format --check` (48 arquivos), mypy estrito e `git diff --check` passaram. Docker não foi usado nem era necessário. O ticket volta a `in-progress`.
