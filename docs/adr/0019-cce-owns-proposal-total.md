# O CCE calcula o Total da Proposal

O Total é sempre calculado pelo CCE a partir dos LineItemSnapshots, em unidades menores inteiras da moeda da Proposal. Se o Host também enviar um total e ele divergir, a requisição é rejeitada, o que funciona como conferência de reconciliação entre os dois sistemas. Tributos chegam como valores opacos informados pelo Host; o CCE nunca os calcula. Rejeitamos aceitar o total do Host sem conferência porque os valores são "trusted commercial facts" (Blueprint §21): o número que aparece no aceite e na cobrança precisa vir de uma única fonte auditável.

Complementa o ADR-006 (LLM não é autoridade sobre valores comerciais) e a não-meta de sistema fiscal do Blueprint §5.
