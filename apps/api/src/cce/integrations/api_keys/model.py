"""APIKey: credencial servidor a servidor de um Tenant. Tabela `cce.api_keys`.

Guarda só o digest (HMAC-SHA256 com pepper, 32 bytes) e a versão do pepper; o segredo bruto nunca é
persistido. Roles de runtime não leem esta tabela diretamente; o lookup é por função restrita (11).
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    ARRAY,
    CheckConstraint,
    DateTime,
    ForeignKey,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from cce.integrations.api_keys.scopes import ALLOWED_SCOPES
from cce.shared.db.base import Base

_SCOPE_LIST = ", ".join(repr(scope) for scope in ALLOWED_SCOPES)


class APIKey(Base):
    __tablename__ = "api_keys"
    __table_args__ = (
        CheckConstraint(r"label ~ '\S'", name="label_not_blank"),
        CheckConstraint("octet_length(secret_digest) = 32", name="secret_digest_length"),
        CheckConstraint("pepper_version > 0", name="pepper_version_positive"),
        CheckConstraint(f"scopes <@ ARRAY[{_SCOPE_LIST}]::text[]", name="scopes_allowed"),
        CheckConstraint("expires_at > created_at", name="expires_after_created"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("cce.tenants.id", ondelete="RESTRICT"), index=True
    )
    label: Mapped[str] = mapped_column(String(100))
    secret_digest: Mapped[bytes] = mapped_column(LargeBinary)
    pepper_version: Mapped[int] = mapped_column(SmallInteger)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        # Sem digest: nem o hash de uma chave deve aparecer em log ou traceback.
        return f"APIKey(id={self.id!r}, tenant_id={self.tenant_id!r}, label={self.label!r})"
