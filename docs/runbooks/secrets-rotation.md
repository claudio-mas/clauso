# Rotação de chaves e peppers

Como trocar uma API key sem interromper o Host e como trocar o pepper com versões. O contexto está no [ADR-0012](../adr/0012-api-key-bootstrap.md); os comandos, em `tenant-bootstrap.md`.

## Trocar a chave de um Tenant

Sem janela de indisponibilidade: as duas chaves valem ao mesmo tempo até a antiga ser revogada.

1. Emitir a chave nova: `cce-admin api-key issue --tenant ten_<id> --label "ERP 2027-01" --scope tenant:read --scope tenant:write`.
2. Guardar a chave no cofre e configurar o Host com ela.
3. Testar o Host com a chave nova.
4. Revogar a anterior: `cce-admin api-key revoke --tenant ten_<id> --key key_<id>`.

Uma chave comprometida se revoga imediatamente (passo 4), mesmo antes de haver substituta: vale na próxima autenticação.

## Trocar o pepper

O digest é `HMAC-SHA256(pepper, chave)`. Não dá para recalcular o digest de uma chave antiga sem o segredo bruto dela, então a troca **reemite** as chaves em vez de migrá-las. Cada chave guarda a `pepper_version` com que foi emitida.

1. **Gerar o pepper novo**: 32 bytes aleatórios em base64, por exemplo `python3 -c "import base64, os; print(base64.b64encode(os.urandom(32)).decode())"`. Guardá-lo no cofre; nunca no repositório, em ticket ou em log.
2. **Distribuir o mapa com as duas versões** (`CCE_API_KEY_PEPPERS='{"1": "...", "2": "..."}'`) para a API e para a CLI, mantendo `CCE_API_KEY_ACTIVE_PEPPER_VERSION=1`. Publicar a API: ela passa a conhecer a versão 2, e as chaves da versão 1 seguem autenticando.
3. **Tornar a nova a ativa** (`CCE_API_KEY_ACTIVE_PEPPER_VERSION=2`) na CLI. As chaves emitidas daí em diante usam a versão 2.
4. **Reemitir as chaves** de cada Tenant (seção anterior), atualizar os Hosts e testar.
5. **Revogar as chaves da versão 1.**
6. **Remover o pepper 1** do mapa só quando nenhuma chave ativa o usar. Uma chave da versão 1 que ainda exista deixa de autenticar sem o pepper dela, e a API registra um evento de erro `api key pepper version is not configured` (com o ID da chave e a versão, sem segredo) a cada tentativa.

Nunca remova um pepper antes de revogar ou reemitir as chaves que o usam.

## Se o pepper vazar

Trate como a perda de todas as chaves da versão: o pepper sozinho não autentica ninguém (falta o segredo de 256 bits de cada chave), mas somado a um dump do banco permite verificar candidatos offline. Siga o passo a passo acima com urgência: gerar o pepper novo, reemitir, revogar e remover o antigo.

## Se o pepper se perder

As chaves da versão perdida deixam de autenticar e não há como recuperá-las. Gere um pepper novo (versão nova), reemita as chaves e revogue as antigas.
