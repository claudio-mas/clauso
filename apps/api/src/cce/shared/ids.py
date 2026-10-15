"""IDs públicos: UUID interno com prefixo de tipo e 32 caracteres hexadecimais minúsculos.

As mensagens de erro nunca repetem o valor recebido: quem decodifica pode ter recebido uma
API key bruta por engano, e o valor não pode parar em log nem em resposta.
"""

from __future__ import annotations

import re
from uuid import UUID

_TENANT_PREFIX = "ten_"
_KEY_PREFIX = "key_"

_HEX_32 = r"[0-9a-f]{32}"


def _encode(prefix: str, value: UUID) -> str:
    return prefix + value.hex


def _decode(prefix: str, label: str, value: str) -> UUID:
    if not re.fullmatch(re.escape(prefix) + _HEX_32, value):
        raise ValueError(
            f"invalid {label}: expected '{prefix}' followed by 32 lowercase hex digits"
        )
    return UUID(hex=value[len(prefix) :])


def encode_tenant_id(value: UUID) -> str:
    return _encode(_TENANT_PREFIX, value)


def decode_tenant_id(value: str) -> UUID:
    return _decode(_TENANT_PREFIX, "tenant id", value)


def encode_key_id(value: UUID) -> str:
    return _encode(_KEY_PREFIX, value)


def decode_key_id(value: str) -> UUID:
    return _decode(_KEY_PREFIX, "api key id", value)
