# Desconto global da versão, informado em valor

O `pricing_snapshot` de uma ProposalVersion aceita, além dos itens, um desconto global opcional em unidades menores inteiras da moeda da Proposal. O Total passa a ser: soma dos totais das linhas menos o desconto global. O desconto global não pode ser negativo nem maior que a soma das linhas, de modo que o Total nunca é negativo. O Host pode mandar uma descrição curta do desconto (por exemplo, "Desconto de 10%"), que é só apresentação e não entra em cálculo.

O CCE nunca aplica percentuais. Quando o desconto do Host é percentual, o Host converte em valor com a sua própria regra de arredondamento e envia o valor; a conferência do Total (`TOTAL_MISMATCH`) continua valendo. O mesmo vale para descontos percentuais de item.

Rejeitamos manter o desconto só por linha porque Hosts reais, a começar pelo de referência (ADR-0026), dão desconto no orçamento inteiro, e obrigá-los a ratear o desconto entre as linhas mudaria os valores de cada item que o comprador vê. Rejeitamos aceitar percentual porque o CCE teria de escolher uma regra de arredondamento que diverge da do Host, gerando diferenças de centavos.

Complementa o ADR-0019 (o CCE calcula o Total): o Total continua tendo uma única fonte auditável, agora com dois componentes, as linhas e o desconto global.
