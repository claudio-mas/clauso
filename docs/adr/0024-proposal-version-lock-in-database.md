# Trava de ProposalVersion no banco, com transições permitidas

Uma ProposalVersion sem `published_at` e sem `accepted_at` é livremente editável. Depois que um dos dois é preenchido, um gatilho no PostgreSQL rejeita DELETE e qualquer UPDATE, exceto o que preenche `published_at` ou `accepted_at` que ainda estejam nulos, sem alterar nenhuma outra coluna. Os carimbos nunca voltam a nulo nem mudam de valor. O gatilho nasce no M1, antes de qualquer versão chegar a esse estado, para que o M2 (publicação) e o M5 (aceite) herdem a proteção sem depender de disciplina de código.

Rejeitamos a forma mais simples, "rejeitar todo UPDATE e DELETE de versão publicada ou aceita", porque ela impediria o próprio aceite: registrar `accepted_at` numa versão já publicada é um UPDATE. Rejeitamos também deixar a trava só na aplicação, porque um bug ou um script de manutenção poderia alterar a versão que o Evidence Package cita pelo hash (ADR-0018).

Consequências:

- `check_schema` verifica a existência do gatilho, e os testes cobrem as transições permitidas e as proibidas.
- Correções de dados em versões travadas não são possíveis nem para o migrator; um erro numa versão publicada se corrige com uma nova versão.

Complementa o ADR-005 (versões aceitas são imutáveis) e o ADR-0021 (apresentação congelada no aceite).
