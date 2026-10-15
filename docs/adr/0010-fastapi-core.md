# Core da API em Python com FastAPI

A API principal do CCE é escrita em Python com FastAPI, Pydantic, SQLAlchemy 2 (assíncrono) e Alembic.

Motivos: Python já domina o ecossistema do projeto; Pydantic dá schemas fortes e tipagem; o OpenAPI é nativo; a integração com IA é simples; o desempenho atende a uma API de negócio.

Implementação: a partir do M0 (Blueprint §41 e §73). A versão de Python, o gerenciador de dependências e as versões fixadas são o assunto do ADR-011, escrito no ticket 01 do M0.
