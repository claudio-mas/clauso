"""Material de chave de API: gerar, interpretar, calcular o digest e comparar.

Funções puras, sem banco nem HTTP. Este módulo não autentica nada: o lookup, os scopes e a
decisão de acesso vêm em tickets seguintes.

Uma chave é um segredo aleatório de 256 bits e não uma senha humana. Por isso o digest é um HMAC
rápido com um pepper do servidor; um hash lento, desenhado para senhas escolhidas por pessoas,
não acrescentaria segurança aqui. Não use estas funções para senhas de usuários.

Nenhuma mensagem de erro, `repr` ou `str` deste módulo contém a chave, o segredo, o digest ou um
pepper.
"""

import base64
import hashlib
import hmac
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from pydantic import SecretStr

from cce.shared.config import Environment

KEY_PREFIX = "cce"
ENVIRONMENT_MARKERS: Mapping[Environment, str] = {
    "local": "dev",
    "test": "tst",
    "staging": "stg",
    "production": "prd",
}
SECRET_BYTES = 32  # 256 bits; secrets.token_urlsafe(32) rende 43 caracteres base64url
DIGEST_SIZE = hashlib.sha256().digest_size
MIN_PEPPER_BYTES = 32
DIGEST_DOMAIN = b"cce-api-key:v1\0"

# Regex ancorada: o segredo é base64url e pode conter "_", então a chave nunca é dividida em
# partes por separador. Os grupos têm tamanho fixo.
_KEY_PATTERN = re.compile(
    r"cce_(?P<marker>[a-z]{3})_(?P<key_id>[0-9a-f]{32})_(?P<secret>[A-Za-z0-9_-]{43})",
    re.ASCII,
)


class InvalidKeyError(ValueError):
    """Chave recusada. Mesmo tipo e mesma mensagem para qualquer motivo; nunca ecoa a entrada."""


class UnknownPepperVersion(ValueError):
    """Não há pepper para a versão pedida. A chave correspondente deve ser tratada como inválida."""


_INVALID = "invalid API key"


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class ParsedKey:
    """Chave interpretada. O token inteiro fica em `raw_key`, que nunca aparece em `repr`/`str`."""

    key_id: UUID
    raw_key: SecretStr

    def __repr__(self) -> str:
        return f"ParsedKey(key_id={self.key_id!s}, raw_key=<redacted>)"

    __str__ = __repr__


def generate_key(key_id: UUID, environment: Environment) -> str:
    """Nova chave `cce_<marcador>_<uuid hex>_<segredo base64url de 43 caracteres>`."""
    marker = ENVIRONMENT_MARKERS[environment]
    return f"{KEY_PREFIX}_{marker}_{key_id.hex}_{secrets.token_urlsafe(SECRET_BYTES)}"


def parse_key(raw_key: str, environment: Environment) -> ParsedKey:
    """Interpreta a chave apresentada para este ambiente.

    Formato malformado, tamanho errado e marcador de outro ambiente levantam o mesmo erro, com a
    mesma mensagem, sem repetir a entrada.
    """
    match = _KEY_PATTERN.fullmatch(raw_key) if isinstance(raw_key, str) else None
    if match is None or match["marker"] != ENVIRONMENT_MARKERS[environment]:
        raise InvalidKeyError(_INVALID) from None
    return ParsedKey(key_id=UUID(hex=match["key_id"]), raw_key=SecretStr(raw_key))


def pepper_bytes(peppers: Mapping[int, SecretStr], version: int) -> bytes:
    """Bytes do pepper da versão pedida (o mapa guarda cada pepper em base64)."""
    pepper = peppers.get(version)
    if pepper is None:
        raise UnknownPepperVersion(f"no pepper for version {version}")
    return base64.b64decode(pepper.get_secret_value())


def digest_key(raw_key: str, pepper: bytes) -> bytes:
    """HMAC-SHA256(pepper, domínio + chave): 32 bytes, estável para a mesma entrada."""
    if not isinstance(pepper, bytes):
        raise TypeError("the pepper must be bytes")
    if len(pepper) < MIN_PEPPER_BYTES:
        raise ValueError(f"the pepper must have at least {MIN_PEPPER_BYTES} bytes")
    if not isinstance(raw_key, str) or not raw_key.isascii():
        raise InvalidKeyError(_INVALID) from None
    return hmac.new(pepper, DIGEST_DOMAIN + raw_key.encode("ascii"), hashlib.sha256).digest()


def verify_digest(candidate: bytes, stored: bytes) -> bool:
    """Compara dois digests em tempo constante, sempre sobre bytes de 32 bytes.

    Um valor que não é `bytes` de 32 bytes é substituído por zeros para a comparação rodar do
    mesmo jeito, e o resultado é falso mesmo quando os dois valores inválidos são iguais.
    """
    valid = _is_digest(candidate) and _is_digest(stored)
    left = candidate if _is_digest(candidate) else bytes(DIGEST_SIZE)
    right = stored if _is_digest(stored) else bytes(DIGEST_SIZE)
    return hmac.compare_digest(left, right) and valid


def _is_digest(value: object) -> bool:
    return isinstance(value, bytes) and len(value) == DIGEST_SIZE
