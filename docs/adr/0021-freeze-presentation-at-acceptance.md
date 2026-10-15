# Congelar a apresentação no aceite

No Acceptance o CCE congela, junto com a ProposalVersion, as entradas de renderização (versão do template e Brand) e guarda o PDF gerado naquele momento. Trocar logo ou template depois não altera a versão aceita; abrir o link ou baixar o PDF anos depois mostra exatamente o que o Recipient aceitou. Rejeitamos re-renderizar com o template e a Brand atuais porque a imutabilidade do conteúdo (ADR-005) não bastaria como evidência se a aparência pudesse mudar.

Consequência: templates e Brands precisam ser versionados, não editados no lugar.
