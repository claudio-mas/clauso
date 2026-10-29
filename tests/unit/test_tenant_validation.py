from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from cce.identity.tenants.model import Tenant
from cce.identity.tenants.schemas import TenantCreate, TenantPatch, TenantRead
from cce.shared.ids import encode_tenant_id

# --- TenantPatch ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    ["id", "tenant_id", "status", "plan", "slug", "created_at", "updated_at", "unknown"],
)
def test_patch_refuses_fields_a_client_must_not_change(field: str) -> None:
    with pytest.raises(ValidationError) as excinfo:
        TenantPatch.model_validate({"name": "Nova Empresa", field: "valor"})

    assert [error["type"] for error in excinfo.value.errors()] == ["extra_forbidden"]
    assert excinfo.value.errors()[0]["loc"] == (field,)


def test_patch_refuses_a_tenant_id_even_when_it_is_the_callers_own() -> None:
    own = encode_tenant_id(uuid4())

    with pytest.raises(ValidationError):
        TenantPatch.model_validate({"name": "Empresa", "id": own, "tenant_id": own})


def test_patch_requires_at_least_one_field() -> None:
    with pytest.raises(ValidationError):
        TenantPatch.model_validate({})


@pytest.mark.parametrize("field", ["name", "default_currency", "timezone", "locale"])
def test_patch_does_not_accept_an_explicit_null(field: str) -> None:
    with pytest.raises(ValidationError):
        TenantPatch.model_validate({field: None})


def test_patch_accepts_each_editable_field_alone_and_all_together() -> None:
    assert TenantPatch.model_validate({"name": "Empresa"}).name == "Empresa"
    assert TenantPatch.model_validate({"default_currency": "USD"}).default_currency == "USD"
    assert TenantPatch.model_validate({"timezone": "Europe/Lisbon"}).timezone == "Europe/Lisbon"
    assert TenantPatch.model_validate({"locale": "en-US"}).locale == "en-US"
    patch = TenantPatch.model_validate(
        {"name": "Empresa", "default_currency": "EUR", "timezone": "UTC", "locale": "pt-BR"}
    )
    assert patch.model_dump(exclude_unset=True) == {
        "name": "Empresa",
        "default_currency": "EUR",
        "timezone": "UTC",
        "locale": "pt-BR",
    }


def test_patch_exposes_only_the_fields_that_were_sent() -> None:
    patch = TenantPatch.model_validate({"locale": "en-US"})

    assert patch.model_dump(exclude_unset=True) == {"locale": "en-US"}


@pytest.mark.parametrize("name", ["", " ", "   ", "\t\n", "n" * 201, "  " + "n" * 201 + "  "])
def test_patch_rejects_blank_or_overlong_names(name: str) -> None:
    with pytest.raises(ValidationError):
        TenantPatch.model_validate({"name": name})


def test_patch_strips_the_name_before_measuring_it() -> None:
    assert TenantPatch.model_validate({"name": "  Empresa  "}).name == "Empresa"
    assert TenantPatch.model_validate({"name": " " + "n" * 200 + " "}).name == "n" * 200


@pytest.mark.parametrize("currency", ["GBP", "brl", "", "BR", "BRLL", 1])
def test_patch_rejects_currencies_outside_the_initial_list(currency: Any) -> None:
    with pytest.raises(ValidationError):
        TenantPatch.model_validate({"default_currency": currency})


@pytest.mark.parametrize("locale", ["fr-FR", "pt-br", "en", "", "pt_BR"])
def test_patch_rejects_locales_outside_the_initial_list(locale: str) -> None:
    with pytest.raises(ValidationError):
        TenantPatch.model_validate({"locale": locale})


@pytest.mark.parametrize(
    "zone", ["Mars/Olympus", "", "America/Sao Paulo", "../etc/passwd", "/etc/passwd", "x", "+03"]
)
def test_patch_rejects_time_zones_that_zoneinfo_does_not_know(zone: str) -> None:
    with pytest.raises(ValidationError):
        TenantPatch.model_validate({"timezone": zone})


@pytest.mark.parametrize("zone", ["America/Sao_Paulo", "UTC", "Europe/Lisbon", "Asia/Tokyo"])
def test_patch_accepts_zoneinfo_time_zones(zone: str) -> None:
    assert TenantPatch.model_validate({"timezone": zone}).timezone == zone


# --- TenantCreate ---------------------------------------------------------------------------


def test_create_needs_only_name_and_slug_and_fills_the_documented_defaults() -> None:
    tenant = TenantCreate.model_validate({"name": "Empresa A", "slug": "empresa-a"})

    assert tenant.model_dump() == {
        "name": "Empresa A",
        "slug": "empresa-a",
        "plan": "development",
        "default_currency": "BRL",
        "timezone": "America/Sao_Paulo",
        "locale": "pt-BR",
    }


@pytest.mark.parametrize("missing", ["name", "slug"])
def test_create_requires_name_and_slug(missing: str) -> None:
    values = {"name": "Empresa A", "slug": "empresa-a"}
    del values[missing]

    with pytest.raises(ValidationError):
        TenantCreate.model_validate(values)


@pytest.mark.parametrize("slug", ["a", "empresa-a", "a1-b2", "0", "x" * 63])
def test_create_accepts_valid_slugs(slug: str) -> None:
    assert TenantCreate.model_validate({"name": "E", "slug": slug}).slug == slug


@pytest.mark.parametrize(
    "slug",
    ["", "Empresa", "-a", "a-", "a--b", "a_b", "a b", "á", "a/b", "x" * 64, "a\n", " a"],
)
def test_create_rejects_invalid_slugs(slug: str) -> None:
    with pytest.raises(ValidationError):
        TenantCreate.model_validate({"name": "E", "slug": slug})


@pytest.mark.parametrize("field", ["id", "tenant_id", "status", "created_at", "unknown"])
def test_create_refuses_extra_fields_and_always_starts_active(field: str) -> None:
    with pytest.raises(ValidationError):
        TenantCreate.model_validate({"name": "E", "slug": "e", field: "valor"})


def test_create_validates_the_same_presentation_fields_as_patch() -> None:
    for bad in ({"default_currency": "GBP"}, {"locale": "fr-FR"}, {"timezone": "Mars/Olympus"}):
        with pytest.raises(ValidationError):
            TenantCreate.model_validate({"name": "E", "slug": "e", **bad})


# --- TenantRead -----------------------------------------------------------------------------


def make_tenant(**overrides: Any) -> Tenant:
    values: dict[str, Any] = {
        "id": uuid4(),
        "name": "Empresa A",
        "slug": "empresa-a",
        "status": "active",
        "plan": "development",
        "default_currency": "BRL",
        "timezone": "America/Sao_Paulo",
        "locale": "pt-BR",
        "created_at": datetime(2026, 10, 28, 12, 0, tzinfo=UTC),
        "updated_at": datetime(2026, 10, 28, 12, 30, tzinfo=UTC),
    }
    values.update(overrides)
    return Tenant(**values)


def test_read_exposes_the_public_id_with_the_ten_prefix() -> None:
    tenant = make_tenant()

    read = TenantRead.model_validate(tenant)

    assert read.id == encode_tenant_id(tenant.id)
    assert read.id.startswith("ten_") and len(read.id) == 36


def test_read_has_exactly_the_documented_fields() -> None:
    read = TenantRead.model_validate(make_tenant())

    assert list(read.model_dump(mode="json")) == [
        "id",
        "name",
        "slug",
        "status",
        "plan",
        "default_currency",
        "timezone",
        "locale",
        "created_at",
        "updated_at",
    ]


def test_read_serializes_instants_as_iso_8601_with_a_utc_offset() -> None:
    read = TenantRead.model_validate(make_tenant())

    dumped = read.model_dump(mode="json")

    assert dumped["created_at"] == "2026-10-28T12:00:00+00:00"
    assert dumped["updated_at"] == "2026-10-28T12:30:00+00:00"
    assert '"created_at":"2026-10-28T12:00:00+00:00"' in read.model_dump_json()


def test_read_converts_other_offsets_to_utc() -> None:
    brasilia = timezone(timedelta(hours=-3))
    tenant = make_tenant(created_at=datetime(2026, 10, 28, 9, 0, tzinfo=brasilia))

    dumped = TenantRead.model_validate(tenant).model_dump(mode="json")

    assert dumped["created_at"] == "2026-10-28T12:00:00+00:00"


def test_read_never_accepts_a_datetime_without_timezone() -> None:
    tenant = make_tenant(created_at=datetime(2026, 10, 28, 12, 0))

    with pytest.raises(ValidationError):
        TenantRead.model_validate(tenant)


def test_read_rejects_an_id_without_the_public_prefix() -> None:
    with pytest.raises(ValidationError):
        TenantRead.model_validate({**_read_payload(), "id": uuid4().hex})


def _read_payload() -> dict[str, Any]:
    tenant = make_tenant()
    return {
        "id": encode_tenant_id(UUID(int=1)),
        "name": tenant.name,
        "slug": tenant.slug,
        "status": tenant.status,
        "plan": tenant.plan,
        "default_currency": tenant.default_currency,
        "timezone": tenant.timezone,
        "locale": tenant.locale,
        "created_at": tenant.created_at,
        "updated_at": tenant.updated_at,
    }


def test_read_accepts_a_payload_that_already_has_the_public_id() -> None:
    read = TenantRead.model_validate(_read_payload())

    assert read.id == encode_tenant_id(UUID(int=1))
