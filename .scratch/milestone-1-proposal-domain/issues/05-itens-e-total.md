# 05: Itens de preço e Total calculado pelo CCE

**What to build:** um Host envia os itens do orçamento e recebe de volta a ProposalVersion com o Total calculado pelo CCE. Se o Host também enviar o próprio total e ele divergir, a requisição é rejeitada antes de gravar qualquer coisa (ADR-0019).

**Blocked by:** 04

**Status:** ready-for-agent

**Spec:** milestone-1-proposal-domain/spec.md (user stories 36–43, 49)

- [ ] Cada item tem descrição, quantidade decimal positiva (até 3 casas), preço unitário, `discount` e `tax` em unidades menores inteiras, e `external_id` opcional
- [ ] O bruto da linha é quantidade × preço unitário, arredondado meio para cima; total da linha = bruto − desconto + tributo; Total da versão = soma das linhas
- [ ] Valores negativos, desconto maior que o bruto e quantidade zero ou negativa retornam 422
- [ ] Lista de itens vazia é aceita em draft e resulta em Total zero; mais de 200 itens retorna 422
- [ ] Tributo é somado como informado, sem qualquer cálculo fiscal
- [ ] Total informado pelo Host que diverge retorna 422 `TOTAL_MISMATCH` com os valores esperado e recebido, e nada é gravado; total igual é aceito
- [ ] Casos de arredondamento e limites cobertos por tabela de casos (quantidade fracionária, meio exato, desconto igual ao bruto, tributo)
- [ ] O `pricing_snapshot` entra no checksum: itens diferentes produzem checksums diferentes
- [ ] Nenhum valor monetário aparece em logs
