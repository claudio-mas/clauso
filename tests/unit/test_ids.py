from __future__ import annotations

import re
from collections.abc import Callable
from uuid import UUID, uuid4

import pytest

from cce.shared.ids import decode_key_id, decode_tenant_id, encode_key_id, encode_tenant_id

SAMPLES = [
    uuid4(),
    uuid4(),
    UUID(int=0),
    UUID(int=2**128 - 1),
    UUID("0123456789abcdef0123456789abcdef"),
]

Encoder = Callable[[UUID], str]
Decoder = Callable[[str], UUID]

KINDS = [
    pytest.param("ten_", encode_tenant_id, decode_tenant_id, id="tenant"),
    pytest.param("key_", encode_key_id, decode_key_id, id="api-key"),
]


@pytest.mark.parametrize(("prefix", "encode", "decode"), KINDS)
@pytest.mark.parametrize("value", SAMPLES)
def test_roundtrip_preserves_uuid(prefix: str, encode: Encoder, decode: Decoder, value: UUID) -> None:
    public_id = encode(value)

    assert public_id == prefix + value.hex
    assert re.fullmatch(prefix + "[0-9a-f]{32}", public_id)
    assert decode(public_id) == value


@pytest.mark.parametrize(("prefix", "encode", "decode"), KINDS)
def test_decode_rejects_wrong_prefix(prefix: str, encode: Encoder, decode: Decoder) -> None:
    hex_part = uuid4().hex
    other = "key_" if prefix == "ten_" else "ten_"

    for bad in (other + hex_part, "usr_" + hex_part, hex_part, "TEN_" + hex_part, "_" + hex_part):
        with pytest.raises(ValueError):
            decode(bad)


@pytest.mark.parametrize(("prefix", "encode", "decode"), KINDS)
@pytest.mark.parametrize(
    "suffix",
    [
        "",
        "0" * 31,
        "0" * 33,
        "0" * 64,
        "0123456789abcdef0123456789abcde-",
        "01234567-89ab-cdef-0123-456789abcdef",
    ],
)
def test_decode_rejects_wrong_length_or_shape(
    prefix: str, encode: Encoder, decode: Decoder, suffix: str
) -> None:
    with pytest.raises(ValueError):
        decode(prefix + suffix)


@pytest.mark.parametrize(("prefix", "encode", "decode"), KINDS)
@pytest.mark.parametrize(
    "suffix",
    [
        "g" * 32,
        "z123456789abcdef0123456789abcdef"[:32],
        "0123456789ABCDEF0123456789ABCDEF",
        " " + "0" * 31,
        "0" * 31 + "\n",
        "0" * 32 + "\n",
        "é" * 32,
    ],
)
def test_decode_rejects_invalid_hex(
    prefix: str, encode: Encoder, decode: Decoder, suffix: str
) -> None:
    with pytest.raises(ValueError):
        decode(prefix + suffix)


@pytest.mark.parametrize(("prefix", "encode", "decode"), KINDS)
def test_errors_never_echo_the_received_value(
    prefix: str, encode: Encoder, decode: Decoder
) -> None:
    """Quem passa uma API key bruta ao decoder não pode vê-la repetida na mensagem."""
    secret = "cce_dev_" + uuid4().hex + "_SECRET-material-0123456789abcdefghijklmnop"
    almost = prefix + "SECRET-" + "x" * 30

    for bad in (secret, almost, ""):
        with pytest.raises(ValueError) as excinfo:
            decode(bad)
        assert "SECRET" not in str(excinfo.value)
        assert "SECRET" not in repr(excinfo.value)
        assert not bad or bad not in str(excinfo.value)


def test_tenant_and_key_ids_are_not_interchangeable() -> None:
    value = uuid4()

    with pytest.raises(ValueError):
        decode_tenant_id(encode_key_id(value))
    with pytest.raises(ValueError):
        decode_key_id(encode_tenant_id(value))
