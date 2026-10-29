# Texto formatado restrito em campos longos

Campos de texto longo do conteúdo e dos termos de uma ProposalVersion (o corpo das Sections de texto e as condições comerciais em `terms_snapshot`) aceitam um subconjunto fechado de HTML: `p`, `br`, `strong`, `b`, `em`, `i`, `ul`, `ol` e `li`, sem nenhum atributo. Os demais campos (títulos, nomes, rótulos, descrições de item) continuam texto puro.

Na escrita, o CCE sanitiza esses campos com uma allowlist: remove atributos e tags fora da lista (mantendo o texto delas), descarta por completo o conteúdo de `script`, `style` e similares, e grava a forma sanitizada. O checksum, a comparação de reenvio e a resposta da API usam essa forma, de modo que reenviar o mesmo HTML é idempotente. A renderização futura (M2) trata o campo como HTML já sanitizado e aplica de novo a mesma allowlist ao exibir, como defesa em profundidade.

Rejeitamos manter só texto puro porque o Host de referência (ADR-0026) já guarda condições de pagamento e observações formatadas com esse mesmo conjunto (editor Tiptap com negrito, itálico e listas), e a formatação se perderia na proposta. Rejeitamos aceitar HTML livre ou Markdown: HTML livre é vetor de injeção na página pública, e Markdown obrigaria todo Host a converter e teria dialetos divergentes.

Consequência: a dependência de sanitização no backend (por exemplo, `nh3`) entra no M1, com testes de injeção (tags e atributos proibidos, `javascript:`, entidades, HTML malformado).
