"""Schemas de Tenant. Datas são sempre com timezone e saem em JSON como ISO 8601 em UTC."""

from datetime import UTC, datetime
from typing import Annotated, Any, Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    StringConstraints,
    field_serializer,
    field_validator,
    model_validator,
)

from cce.identity.tenants.model import SLUG_PATTERN
from cce.shared.ids import decode_tenant_id, encode_tenant_id

Currency = Literal["BRL", "USD", "EUR"]
Locale = Literal["pt-BR", "en-US"]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Slug = Annotated[str, StringConstraints(pattern=SLUG_PATTERN, min_length=1, max_length=63)]
Plan = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]


def _known_time_zone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise ValueError("unknown time zone") from None
    return value


TimeZone = Annotated[str, StringConstraints(max_length=64), AfterValidator(_known_time_zone)]


class TenantRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    slug: str
    status: Literal["active", "suspended", "archived"]
    plan: str
    default_currency: Currency
    timezone: str
    locale: Locale
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @field_validator("id", mode="before")
    @classmethod
    def public_id(cls, value: Any) -> str:
        if isinstance(value, UUID):
            return encode_tenant_id(value)
        decode_tenant_id(value)  # recusa prefixo, tamanho ou hex inválidos
        return str(value)

    @field_serializer("created_at", "updated_at")
    def utc_iso_8601(self, value: datetime) -> str:
        return value.astimezone(UTC).isoformat()


class TenantPatch(BaseModel):
    """Só o que o próprio Tenant pode mudar. `id`, `tenant_id`, `status`, `plan` e `slug` não."""

    model_config = ConfigDict(extra="forbid")

    name: Name | None = None
    default_currency: Currency | None = None
    timezone: TimeZone | None = None
    locale: Locale | None = None

    @model_validator(mode="after")
    def at_least_one_value(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("at least one field is required")
        for field in self.model_fields_set:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class TenantCreate(BaseModel):
    """Usado só pela CLI administrativa. O status inicial é sempre `active`."""

    model_config = ConfigDict(extra="forbid")

    name: Name
    slug: Slug
    plan: Plan = "development"
    default_currency: Currency = "BRL"
    timezone: TimeZone = "America/Sao_Paulo"
    locale: Locale = "pt-BR"
