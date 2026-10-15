# IA com saída estruturada e sem autoridade sobre valores comerciais

O LLM não gera HTML. Toda geração crítica produz JSON, que passa por validação de schema e validação de negócio antes de virar uma ProposalVersion. O modelo trabalha sobre a narrativa; preço, quantidade, desconto, tributação, condições financeiras e identificadores são fatos comerciais confiáveis controlados pelo domínio e nunca alterados silenciosamente pela IA. Conteúdo gerado pela IA nasce *draft* e exige revisão antes da publicação (ADR-0023).

Rejeitamos HTML gerado pelo modelo porque mistura conteúdo e apresentação, impede a validação e abre vetor de injeção.

Implementação: contexto registrado; código no M6 (Blueprint §20 e §21).
