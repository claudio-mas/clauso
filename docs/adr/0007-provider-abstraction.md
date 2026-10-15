# Integrações críticas atrás de abstração de provider

IA, pagamento, assinatura eletrônica, armazenamento e e-mail são acessados por portas definidas pelo CCE e implementados por adapters. O domínio nunca chama a API de um fornecedor diretamente, e eventos dos providers são normalizados antes de chegar ao domínio.

Rejeitamos o acoplamento direto a um fornecedor porque o CCE é embutido em Hosts com necessidades diferentes e precisa trocar ou combinar fornecedores sem reescrever o domínio.

Implementação: regra válida desde o M0; nenhum adapter é criado sem um caso de uso (Blueprint §22, §26 e §28).
