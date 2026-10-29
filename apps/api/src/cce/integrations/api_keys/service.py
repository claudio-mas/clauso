"""Material de chave de API: somente funções puras (sem banco, sessão ou autenticação).

Emitir, persistir e revogar chaves, e o lookup na autenticação, vêm em tickets seguintes.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID, uuid4

from pydantic import SecretStr

from cce.shared.config import Environment
from cce.shared.security import digest_key, generate_key, pepper_bytes


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class IssuedKey:
    """Chave recém-emitida: o token só existe aqui, para ser mostrado uma vez; o banco guarda o
    digest e a versão do pepper. Nem o token nem o digest aparecem em `repr`/`str`.
    """

    key_id: UUID
    raw_key: SecretStr
    secret_digest: bytes
    pepper_version: int

    def __repr__(self) -> str:
        return (
            f"IssuedKey(key_id={self.key_id!s}, pepper_version={self.pepper_version}, "
            "raw_key=<redacted>, secret_digest=<redacted>)"
        )

    __str__ = __repr__


def issue_key_material(
    environment: Environment,
    peppers: Mapping[int, SecretStr],
    pepper_version: int,
    key_id: UUID | None = None,
) -> IssuedKey:
    """Gera uma chave nova e o digest que seria guardado, com o pepper da versão informada."""
    pepper = pepper_bytes(peppers, pepper_version)
    identifier = key_id if key_id is not None else uuid4()
    raw_key = generate_key(identifier, environment)
    return IssuedKey(
        key_id=identifier,
        raw_key=SecretStr(raw_key),
        secret_digest=digest_key(raw_key, pepper),
        pepper_version=pepper_version,
    )
