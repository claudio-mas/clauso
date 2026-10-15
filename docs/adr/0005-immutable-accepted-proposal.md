# Versões aceitas são imutáveis

Uma ProposalVersion aceita, e o Evidence Package do aceite, nunca mais mudam. Qualquer alteração comercial depois do aceite exige um novo Quote e uma nova Proposal (ADR-0020). A imutabilidade é garantida no banco, não só na aplicação (ADR-0024).

O motivo é a evidência: o aceite precisa citar exatamente o conteúdo, os valores e a apresentação que o Recipient viu, e isso só vale se nada daquilo puder ser alterado depois.

Implementação: contexto registrado; a trava no banco nasce no M1 e é acionada no M2 e no M5. Desdobrado nos ADRs 0018, 0021, 0022 e 0024.
