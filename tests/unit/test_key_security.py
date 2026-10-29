"""Primitivas de chave de API: formato, interpretação, digest e comparação.

Chaves e peppers daqui são sentinelas sintéticas geradas a cada execução. Os testes são puros: não
há banco, HTTP nem autenticação, só o material de chave.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from integration.support import assert_no_leaks
from pydantic import SecretStr

import cce.shared.security as security
from cce.integrations.api_keys.service import IssuedKey, issue_key_material
from cce.shared.config import Environment
from cce.shared.security import (
    InvalidKeyError,
    ParsedKey,
    UnknownPepperVersion,
    digest_key,
    generate_key,
    parse_key,
    pepper_bytes,
    verify_digest,
)

ROOT = Path(__file__).resolve().parents[2]
ENVIRONMENTS: list[Environment] = ["local", "test", "staging", "production"]
MARKERS = {"local": "dev", "test": "tst", "staging": "stg", "production": "prd"}
DOMAIN = b"cce-api-key:v1\0"
KEY_PATTERN = re.compile(r"cce_(dev|tst|stg|prd)_[0-9a-f]{32}_[A-Za-z0-9_-]{43}")


def fresh_pepper() -> bytes:
    return secrets.token_bytes(32)


def peppers(*versions: int) -> dict[int, SecretStr]:
    return {v: SecretStr(base64.b64encode(fresh_pepper()).decode()) for v in versions}


def key_parts(raw_key: str) -> tuple[str, str, str]:
    _, marker, key_hex, secret = raw_key.split("_", 3)
    return marker, key_hex, secret


# --- formato --------------------------------------------------------------------------------


@pytest.mark.parametrize("environment", ENVIRONMENTS)
def test_generated_key_has_the_documented_format(environment: Environment) -> None:
    key_id = uuid4()

    raw = generate_key(key_id, environment)

    marker, key_hex, secret = key_parts(raw)
    assert KEY_PATTERN.fullmatch(raw)
    assert marker == MARKERS[environment]
    assert key_hex == key_id.hex
    assert len(secret) == 43
    assert len(raw) == len("cce_dev_") + 32 + 1 + 43


def test_the_secret_carries_256_bits_from_the_secrets_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: list[int] = []
    real = secrets.token_urlsafe

    def spy(nbytes: int | None = None) -> str:
        requested.append(nbytes or 0)
        return real(nbytes)

    monkeypatch.setattr(security.secrets, "token_urlsafe", spy)

    raw = generate_key(uuid4(), "test")

    assert requested == [32]
    decoded = base64.urlsafe_b64decode(key_parts(raw)[2] + "=")
    assert len(decoded) == 32


def test_generated_secrets_never_repeat_and_use_only_base64url() -> None:
    secrets_seen = {key_parts(generate_key(uuid4(), "test"))[2] for _ in range(300)}

    assert len(secrets_seen) == 300
    assert all(re.fullmatch(r"[A-Za-z0-9_-]{43}", s) for s in secrets_seen)


@pytest.mark.parametrize(
    "secret",
    [
        "_" * 43,
        "-" * 43,
        "_" + "a" * 41 + "_",
        "ab_cd_ef" + "x" * 35,
        "a_b-c_d-" * 5 + "abc",
    ],
)
def test_a_secret_that_contains_underscores_is_generated_and_parsed_intact(
    monkeypatch: pytest.MonkeyPatch, secret: str
) -> None:
    assert len(secret) == 43
    monkeypatch.setattr(security.secrets, "token_urlsafe", lambda nbytes=None: secret)
    key_id = uuid4()

    raw = generate_key(key_id, "production")
    parsed = parse_key(raw, "production")

    assert raw.endswith("_" + secret)
    assert parsed.key_id == key_id
    assert parsed.raw_key.get_secret_value() == raw


def test_random_keys_with_underscores_in_the_secret_round_trip() -> None:
    with_underscore = 0
    for _ in range(400):
        key_id = uuid4()
        raw = generate_key(key_id, "staging")
        with_underscore += "_" in key_parts(raw)[2]
        assert parse_key(raw, "staging").key_id == key_id
    assert with_underscore > 50  # ~48% das chaves: o caso é exercitado de verdade


# --- parse_key ------------------------------------------------------------------------------


def valid_key(environment: Environment = "test") -> str:
    return generate_key(uuid4(), environment)


def malformed_keys() -> dict[str, Callable[[str], str]]:
    return {
        "other-environment-marker": lambda k: k.replace("_tst_", "_prd_", 1),
        "unknown-marker": lambda k: k.replace("_tst_", "_xyz_", 1),
        "empty-marker": lambda k: k.replace("_tst_", "__", 1),
        "wrong-prefix": lambda k: "key" + k[3:],
        "uppercase-prefix": lambda k: k.upper(),
        "uppercase-hex": lambda k: k[:8] + k[8:40].upper() + k[40:],
        "hex-31": lambda k: k[:8] + k[9:],
        "hex-33": lambda k: k[:8] + "0" + k[8:],
        "non-hex": lambda k: k[:8] + "g" + k[9:],
        "secret-42": lambda k: k[:-1],
        "secret-44": lambda k: k + "A",
        "secret-plus": lambda k: k[:-1] + "+",
        "secret-slash": lambda k: k[:-1] + "/",
        "secret-padding": lambda k: k[:-1] + "=",
        "secret-space": lambda k: k[:-1] + " ",
        "secret-non-ascii": lambda k: k[:-1] + "é",
        "leading-space": lambda k: " " + k,
        "trailing-space": lambda k: k + " ",
        "trailing-newline": lambda k: k + "\n",
        "bearer-prefix": lambda k: "Bearer " + k,
        "extra-part": lambda k: k + "_extra",
        "missing-secret": lambda k: k[: k.rindex("_")],
        "missing-hex": lambda k: k[:8] + k[41:],
        "empty": lambda k: "",
        "only-prefix": lambda k: "cce_",
        "uuid-with-hyphens": lambda k: k[:8] + str(UUID(k[8:40])) + k[40:],
    }


@pytest.mark.parametrize("name", list(malformed_keys()))
def test_parse_rejects_everything_that_is_not_a_key_of_this_environment(name: str) -> None:
    candidate = malformed_keys()[name](valid_key("test"))

    with pytest.raises(InvalidKeyError):
        parse_key(candidate, "test")


@pytest.mark.parametrize("environment", ENVIRONMENTS)
def test_a_key_of_another_environment_is_rejected(environment: Environment) -> None:
    for other in ENVIRONMENTS:
        if other == environment:
            continue
        with pytest.raises(InvalidKeyError):
            parse_key(valid_key(other), environment)


def test_every_rejection_has_the_same_error_type_and_the_same_message() -> None:
    errors: list[BaseException] = []
    candidates = [fn(valid_key("test")) for fn in malformed_keys().values()]
    candidates += [valid_key("production"), b"bytes", None, 12345, object()]  # type: ignore[list-item]
    for candidate in candidates:
        with pytest.raises(Exception) as excinfo:
            parse_key(candidate, "test")  # type: ignore[arg-type]
        errors.append(excinfo.value)

    assert {type(error) for error in errors} == {InvalidKeyError}
    assert len({str(error) for error in errors}) == 1
    assert issubclass(InvalidKeyError, ValueError)


def test_the_error_never_echoes_the_received_key_or_any_part_of_it() -> None:
    raw = valid_key("production")
    marker, key_hex, secret = key_parts(raw)
    forbidden = {
        "key": raw,
        "secret": secret,
        "secret prefix": secret[:12],
        "key id": key_hex,
        "key without prefix": raw[4:],
    }
    for attempt in (raw, raw[:-1], raw + "x", raw.replace("_prd_", "_xyz_")):
        with pytest.raises(InvalidKeyError) as excinfo:
            parse_key(attempt, "test")
        shown = str(excinfo.value) + repr(excinfo.value) + repr(excinfo.value.args)
        assert_no_leaks(shown, forbidden, "the parse error")
        assert excinfo.value.__cause__ is None
        assert excinfo.value.__context__ is None or excinfo.value.__suppress_context__


def test_parse_returns_the_key_id_and_keeps_the_raw_key_secret() -> None:
    key_id = uuid4()
    raw = generate_key(key_id, "staging")

    parsed = parse_key(raw, "staging")

    assert isinstance(parsed, ParsedKey)
    assert parsed.key_id == key_id
    assert isinstance(parsed.raw_key, SecretStr)
    assert parsed.raw_key.get_secret_value() == raw


# --- digest ---------------------------------------------------------------------------------


def test_digest_is_hmac_sha256_with_the_pepper_and_the_domain_prefix() -> None:
    raw, pepper = valid_key(), fresh_pepper()

    digest = digest_key(raw, pepper)

    assert digest == hmac.new(pepper, DOMAIN + raw.encode("ascii"), hashlib.sha256).digest()
    assert len(digest) == 32


def test_the_domain_prefix_matters() -> None:
    raw, pepper = valid_key(), fresh_pepper()

    without_domain = hmac.new(pepper, raw.encode("ascii"), hashlib.sha256).digest()
    other_domain = hmac.new(pepper, b"cce-api-key:v2\0" + raw.encode(), hashlib.sha256).digest()

    assert digest_key(raw, pepper) not in {without_domain, other_domain}


def test_digest_is_stable_for_the_same_input() -> None:
    raw, pepper = valid_key(), fresh_pepper()

    assert digest_key(raw, pepper) == digest_key(raw, pepper)


def test_digest_changes_with_another_pepper_and_with_another_key() -> None:
    raw, pepper = valid_key(), fresh_pepper()

    assert digest_key(raw, pepper) != digest_key(raw, fresh_pepper())
    assert digest_key(raw, pepper) != digest_key(valid_key(), pepper)
    flipped = bytes([pepper[0] ^ 1]) + pepper[1:]
    assert digest_key(raw, pepper) != digest_key(raw, flipped)


@pytest.mark.parametrize("pepper", [b"", b"\x00" * 31, b"x" * 16])
def test_a_pepper_shorter_than_32_bytes_is_refused(pepper: bytes) -> None:
    with pytest.raises(ValueError):
        digest_key(valid_key(), pepper)


def test_the_pepper_must_be_bytes() -> None:
    with pytest.raises(TypeError):
        digest_key(valid_key(), "x" * 32)  # type: ignore[arg-type]


def test_a_non_ascii_key_is_refused_without_echoing_it() -> None:
    raw = valid_key()[:-1] + "é"

    with pytest.raises(InvalidKeyError) as excinfo:
        digest_key(raw, fresh_pepper())

    assert_no_leaks(str(excinfo.value) + repr(excinfo.value), {"key": raw}, "the digest error")


def test_peppers_are_selected_by_version() -> None:
    mapping = peppers(1, 2)

    assert pepper_bytes(mapping, 1) == base64.b64decode(mapping[1].get_secret_value())
    assert pepper_bytes(mapping, 1) != pepper_bytes(mapping, 2)
    assert len(pepper_bytes(mapping, 2)) == 32


@pytest.mark.parametrize("version", [0, 3, -1])
def test_a_missing_pepper_version_is_an_error_that_does_not_show_any_pepper(
    version: int,
) -> None:
    mapping = peppers(1, 2)

    with pytest.raises(UnknownPepperVersion) as excinfo:
        pepper_bytes(mapping, version)

    forbidden = {f"pepper {v}": s.get_secret_value() for v, s in mapping.items()}
    assert_no_leaks(str(excinfo.value) + repr(excinfo.value), forbidden, "the pepper error")


# --- comparação -----------------------------------------------------------------------------


def test_verify_accepts_only_the_identical_digest() -> None:
    digest = digest_key(valid_key(), fresh_pepper())

    assert verify_digest(digest, digest) is True
    assert verify_digest(bytes(digest), bytes(bytearray(digest))) is True
    for bit in (0, 100, 255):
        changed = bytearray(digest)
        changed[bit // 8] ^= 1 << (bit % 8)
        assert verify_digest(bytes(changed), digest) is False
    assert verify_digest(digest_key(valid_key(), fresh_pepper()), digest) is False


@pytest.mark.parametrize("size", [0, 1, 31, 33, 64])
def test_verify_rejects_a_digest_of_the_wrong_size_on_either_side(size: int) -> None:
    digest = digest_key(valid_key(), fresh_pepper())
    wrong = b"\x01" * size

    assert verify_digest(wrong, digest) is False
    assert verify_digest(digest, wrong) is False
    assert verify_digest(wrong, wrong) is False  # nem dois valores iguais, mas inválidos, passam


@pytest.mark.parametrize("value", ["a" * 32, None, 123, bytearray(32), [1] * 32])
def test_verify_rejects_anything_that_is_not_bytes(value: Any) -> None:
    digest = digest_key(valid_key(), fresh_pepper())

    assert verify_digest(value, digest) is False
    assert verify_digest(digest, value) is False


def test_verify_always_compares_32_byte_values_with_compare_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[type, int, type, int]] = []
    real = hmac.compare_digest

    def spy(left: bytes, right: bytes) -> bool:
        calls.append((type(left), len(left), type(right), len(right)))
        return real(left, right)

    monkeypatch.setattr(security.hmac, "compare_digest", spy)
    digest = digest_key(valid_key(), fresh_pepper())

    verify_digest(digest, digest)  # igual
    verify_digest(digest[:-1], digest)  # tamanho errado do candidato
    verify_digest(digest, digest + b"\x00")  # tamanho errado do armazenado
    verify_digest(b"", b"")  # ambos inválidos
    verify_digest(None, digest)  # type: ignore[arg-type]

    assert calls, "verify_digest não usou hmac.compare_digest"
    assert len(calls) == 5  # o caminho de rejeição também compara, em tempo constante
    assert all(call == (bytes, 32, bytes, 32) for call in calls)


# --- repr e str -----------------------------------------------------------------------------


def exposures(obj: object) -> str:
    return repr(obj) + str(obj) + f"{obj}" + format(obj) + repr([obj]) + repr({"k": obj})


def test_parsed_key_never_shows_the_secret() -> None:
    raw = valid_key("production")
    parsed = parse_key(raw, "production")
    marker, key_hex, secret = key_parts(raw)

    shown = exposures(parsed)

    assert_no_leaks(
        shown,
        {
            "raw key": raw,
            "secret": secret,
            "secret prefix": secret[:10],
            "secret suffix": secret[-10:],
        },
        "the ParsedKey representation",
    )
    assert str(parsed.key_id) in shown or key_hex in shown  # o ID não é segredo


def test_issued_key_never_shows_the_secret_or_the_digest() -> None:
    issued = issue_key_material("production", peppers(1), 1)
    raw = issued.raw_key.get_secret_value()
    secret = key_parts(raw)[2]

    shown = exposures(issued)

    assert_no_leaks(
        shown,
        {
            "raw key": raw,
            "secret": secret,
            "secret prefix": secret[:10],
            "digest hex": issued.secret_digest.hex(),
            "digest repr": repr(issued.secret_digest),
        },
        "the IssuedKey representation",
    )


def test_representations_stay_safe_inside_exceptions_and_logs() -> None:
    parsed = parse_key(valid_key(), "test")
    secret = parsed.raw_key.get_secret_value()
    try:
        raise RuntimeError(f"failed for {parsed!r}")
    except RuntimeError as error:
        assert_no_leaks(str(error), {"raw key": secret}, "an exception message")


# --- IssuedKey / material -------------------------------------------------------------------


def test_issue_key_material_returns_a_parseable_key_and_its_digest() -> None:
    mapping = peppers(1, 2)
    key_id = uuid4()

    issued = issue_key_material("staging", mapping, 2, key_id=key_id)

    assert isinstance(issued, IssuedKey)
    assert issued.key_id == key_id
    assert issued.pepper_version == 2
    raw = issued.raw_key.get_secret_value()
    assert parse_key(raw, "staging").key_id == key_id
    assert issued.secret_digest == digest_key(raw, pepper_bytes(mapping, 2))
    assert len(issued.secret_digest) == 32
    assert issued.secret_digest != digest_key(raw, pepper_bytes(mapping, 1))
    assert verify_digest(digest_key(raw, pepper_bytes(mapping, 2)), issued.secret_digest)


def test_issue_key_material_generates_a_new_id_and_secret_each_time() -> None:
    mapping = peppers(1)

    first = issue_key_material("test", mapping, 1)
    second = issue_key_material("test", mapping, 1)

    assert first.key_id != second.key_id
    assert first.raw_key.get_secret_value() != second.raw_key.get_secret_value()
    assert first.secret_digest != second.secret_digest


def test_issue_key_material_with_an_unknown_pepper_version_fails() -> None:
    with pytest.raises(UnknownPepperVersion):
        issue_key_material("test", peppers(1), 2)


# --- sem hash de senha humana ---------------------------------------------------------------


def source_files() -> list[Path]:
    return [
        *(ROOT / "apps" / "api" / "src").rglob("*.py"),
        ROOT / "apps" / "api" / "pyproject.toml",
        ROOT / "apps" / "api" / "uv.lock",
    ]


def test_no_password_hashing_library_is_used_for_keys() -> None:
    names = re.compile(r"bcrypt|argon2|passlib|scrypt|pbkdf2", re.IGNORECASE)

    offenders = [str(p.relative_to(ROOT)) for p in source_files() if names.search(p.read_text())]

    assert offenders == []


def test_no_document_presents_hmac_as_a_human_password_hash() -> None:
    claim = re.compile(
        r"hash de senha|hash de senhas|password hash|hashing de senha", re.IGNORECASE
    )
    negation = re.compile(r"\bnão\b|\bnao\b|\bnunca\b|\bnot\b|\bnever\b", re.IGNORECASE)
    documents = [*(ROOT / "docs").rglob("*.md"), *(ROOT / "apps" / "api" / "src").rglob("*.py")]

    offenders = []
    for path in documents:
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            if "hmac" in line.lower() and claim.search(line) and not negation.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{number}")

    assert offenders == []
