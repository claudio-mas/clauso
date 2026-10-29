"""Helpers de testes que manipulam chave, segredo, pepper ou digest.

Nada aqui deixa uma exceção inesperada subir: o traceback carregaria a chave nos locais. Os testes
que usam estes helpers também levam o marcador `sensitive`, que é a segunda barreira.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import cast

from integration.support import require

from cce.shared.config import Environment
from cce.shared.security import ParsedKey, parse_key


def call_or_fail[T](call: Callable[[], T], what: str) -> T:
    """Executa `call`; qualquer exceção vira uma falha de texto fixo (só o nome do tipo).

    Uma exceção inesperada (de qualquer tipo, com ou sem a chave na mensagem) nunca sobe do teste:
    ela levaria a chave nos locais do traceback.
    """
    result: T | None = None
    failure = ""
    done = False
    try:
        result = call()
        done = True
    except Exception as error:
        failure = type(error).__name__
    require(done, f"{what} raised an unexpected {failure}")
    return cast(T, result)


async def acall_or_fail[T](call: Callable[[], Awaitable[T]], what: str) -> T:
    """Versão assíncrona de `call_or_fail`: `call` devolve uma corrotina."""
    result: T | None = None
    failure = ""
    done = False
    try:
        result = await call()
        done = True
    except Exception as error:
        failure = type(error).__name__
    require(done, f"{what} raised an unexpected {failure}")
    return cast(T, result)


def must_parse(raw_key: str, environment: Environment) -> ParsedKey:
    """`parse_key` para uma chave que o teste acabou de gerar."""
    parsed = call_or_fail(lambda: parse_key(raw_key, environment), "parse_key")
    require(isinstance(parsed, ParsedKey), "parse_key did not return a ParsedKey")
    return parsed


def error_of(call: Callable[[], object], expected: type[Exception], what: str) -> Exception:
    """O erro do tipo esperado levantado por `call`.

    Falha com texto fixo se nada for levantado ou se for levantado outro tipo (a detecção do tipo
    incorreto continua valendo), sem propagar a exceção inesperada nem a mensagem dela.
    """
    caught: Exception | None = None
    unexpected = ""
    try:
        call()
    except expected as error:
        caught = error
    except Exception as error:
        unexpected = type(error).__name__
    got = f"a {unexpected}" if unexpected else "no error"
    require(caught is not None, f"{what}: expected {expected.__name__}, got {got}")
    assert caught is not None
    return caught


async def aerror_of(
    call: Callable[[], Awaitable[object]], expected: type[Exception], what: str
) -> Exception:
    """Como `error_of`, para uma corrotina: o erro esperado, ou uma falha de texto fixo."""
    caught: Exception | None = None
    unexpected = ""
    try:
        await call()
    except expected as error:
        caught = error
    except Exception as error:
        unexpected = type(error).__name__
    got = f"a {unexpected}" if unexpected else "no error"
    require(caught is not None, f"{what}: expected {expected.__name__}, got {got}")
    assert caught is not None
    return caught
