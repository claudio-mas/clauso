from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import (
    ARRAY,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    LargeBinary,
    SmallInteger,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID

from cce.integrations.api_keys.model import APIKey
from cce.integrations.api_keys.scopes import ALLOWED_SCOPES, TENANT_READ, TENANT_WRITE
from cce.shared.db.base import NAMING_CONVENTION, Base
from cce.shared.db.models import APIKey as RegisteredAPIKey
from cce.shared.db.models import Tenant as RegisteredTenant

ROOT = Path(__file__).resolve().parents[2]


def table(name: str) -> Table:
    return Base.metadata.tables[f"cce.{name}"]


def constraint_names(name: str) -> set[str]:
    return {c.name for c in table(name).constraints if isinstance(c.name, str)}


def test_metadata_uses_the_cce_schema_and_registers_both_models() -> None:
    assert Base.metadata.schema == "cce"
    assert set(Base.metadata.tables) == {"cce.tenants", "cce.api_keys"}
    assert RegisteredTenant.__table__ is table("tenants")
    assert RegisteredAPIKey is APIKey


def test_naming_convention_is_the_documented_one() -> None:
    assert NAMING_CONVENTION == {
        "ix": "ix_%(table_name)s_%(column_0_name)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
    assert Base.metadata.naming_convention == NAMING_CONVENTION


def test_tenants_constraints_have_explicit_names() -> None:
    assert constraint_names("tenants") == {
        "pk_tenants",
        "uq_tenants_slug",
        "ck_tenants_name_not_blank",
        "ck_tenants_slug_format",
        "ck_tenants_status_allowed",
        "ck_tenants_currency_allowed",
        "ck_tenants_locale_allowed",
        "ck_tenants_timezone_known",
    }


def test_api_keys_constraints_have_explicit_names() -> None:
    assert constraint_names("api_keys") == {
        "pk_api_keys",
        "fk_api_keys_tenant_id_tenants",
        "ck_api_keys_label_not_blank",
        "ck_api_keys_secret_digest_length",
        "ck_api_keys_pepper_version_positive",
        "ck_api_keys_scopes_allowed",
        "ck_api_keys_expires_after_created",
    }
    assert {i.name for i in table("api_keys").indexes} == {"ix_api_keys_tenant_id"}


def test_tenant_columns() -> None:
    columns = table("tenants").columns

    assert isinstance(columns["id"].type, UUID) and columns["id"].primary_key
    assert isinstance(columns["name"].type, String) and columns["name"].type.length == 200
    assert isinstance(columns["slug"].type, String) and columns["slug"].type.length == 63
    assert columns["status"].type.length == 16  # type: ignore[attr-defined]
    assert columns["plan"].type.length == 50  # type: ignore[attr-defined]
    assert columns["default_currency"].type.length == 3  # type: ignore[attr-defined]
    assert columns["timezone"].type.length == 64  # type: ignore[attr-defined]
    assert columns["locale"].type.length == 16  # type: ignore[attr-defined]
    assert all(not column.nullable for column in columns)
    assert not any(c.name == "tenant_id" for c in columns)  # a raiz Tenant não tem tenant_id


def test_api_key_columns() -> None:
    columns = table("api_keys").columns

    assert isinstance(columns["id"].type, UUID) and columns["id"].primary_key
    assert isinstance(columns["tenant_id"].type, UUID) and not columns["tenant_id"].nullable
    assert isinstance(columns["secret_digest"].type, LargeBinary)
    assert isinstance(columns["pepper_version"].type, SmallInteger)
    assert isinstance(columns["scopes"].type, ARRAY) and isinstance(
        columns["scopes"].type.item_type, Text
    )
    assert columns["label"].type.length == 100  # type: ignore[attr-defined]
    assert columns["revoked_at"].nullable
    assert not any(not c.nullable for c in columns if c.name == "revoked_at")
    assert all(not c.nullable for c in columns if c.name != "revoked_at")


def test_every_datetime_column_has_a_timezone() -> None:
    datetimes = [
        column
        for name in ("tenants", "api_keys")
        for column in table(name).columns
        if isinstance(column.type, DateTime)
    ]

    assert {c.name for c in datetimes} == {"created_at", "updated_at", "expires_at", "revoked_at"}
    assert all(isinstance(c.type, DateTime) and c.type.timezone for c in datetimes)


def test_foreign_key_restricts_deletion() -> None:
    foreign_keys = [c for c in table("api_keys").constraints if isinstance(c, ForeignKeyConstraint)]

    assert len(foreign_keys) == 1
    assert foreign_keys[0].ondelete == "RESTRICT"
    assert [e.target_fullname for e in foreign_keys[0].elements] == ["cce.tenants.id"]


def test_slug_is_unique_and_checks_are_check_constraints() -> None:
    uniques = {c.name for c in table("tenants").constraints if isinstance(c, UniqueConstraint)}
    checks = [
        c
        for t in Base.metadata.tables.values()
        for c in t.constraints
        if isinstance(c, CheckConstraint)
    ]

    assert uniques == {"uq_tenants_slug"}
    assert len(checks) == 11


def test_allowed_scopes_are_exactly_the_two_tenant_scopes() -> None:
    assert (TENANT_READ, TENANT_WRITE) == ("tenant:read", "tenant:write")
    assert set(ALLOWED_SCOPES) == {"tenant:read", "tenant:write"}


def test_api_key_repr_never_includes_the_digest() -> None:
    digest = b"SENTINEL-" + b"d" * 23
    key = APIKey(label="ERP", secret_digest=digest, pepper_version=1, scopes=["tenant:read"])

    shown = repr(key) + str(key)

    assert digest.decode() not in shown
    assert "SENTINEL" not in shown
    assert "ERP" in shown


@pytest.mark.parametrize("folder", ["apps", "infrastructure", "scripts", "tests"])
def test_the_schema_is_never_created_with_create_all(folder: str) -> None:
    forbidden = "create" + "_all"
    offenders = [
        str(path.relative_to(ROOT))
        for path in (ROOT / folder).rglob("*.py")
        if ".venv" not in path.parts and forbidden in path.read_text()
    ]

    assert offenders == []
