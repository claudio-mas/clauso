# Publication fixada em uma única ProposalVersion

Cada Publication expõe exatamente uma ProposalVersion, com link próprio. Revisar uma Proposal já publicada cria uma nova versão e uma nova Publication; o link antigo passa a `SUPERSEDED` e avisa que existe uma versão mais nova. Rejeitamos um link estável por Proposal que sempre mostra a última versão, porque o Evidence Package precisa citar o hash exato da versão que o Recipient viu, e um link que muda permitiria aceitar algo diferente do que o tracking registrou.

Consequência: expirar e revogar são fatos da Publication, não da Proposal. Os ciclos de Proposal, Publication, Contract e Payment são independentes, e o Host recebe um Commercial status derivado.

Complementa o ADR-005 (versões aceitas são imutáveis).
