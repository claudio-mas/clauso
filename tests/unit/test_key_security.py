"""Primitivas de chave de API: formato, interpretação, digest e comparação.

Chaves e peppers daqui são sentinelas sintéticas geradas a cada execução. Os testes são puros: não
há banco, HTTP nem autenticação, só o material de chave.

Higiene de falhas: nenhuma verificação que envolva chave, segredo, pepper ou digest usa `assert`
com a expressão sensível, porque a introspecção do pytest imprimiria os operandos quando o teste
detecta um defeito. Elas passam por `require` (texto fixo) e `assert_no_leaks` (só rótulos). Os
parâmetros têm `id` explícito, e nenhum `assert_no_leaks` roda dentro de um `except`, para o erro
original (que pode trazer o segredo) não ficar encadeado à falha.
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
from integration.support import assert_no_leaks, require
from pydantic import SecretStr

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


def must_parse(raw_key: str, environment: Environment) -> ParsedKey:
    """`parse_key` para uma chave que este módulo acabou de gerar.

    Se ela for recusada, o teste falha com texto fixo; sem isto, a exceção inesperada subiria com a
    chave nos locais do traceback (`--showlocals`).
    """
    parsed = None
    try:
        parsed = parse_key(raw_key, environment)
    except InvalidKeyError:
        pass
    require(parsed is not None, "a key made by generate_key was rejected by parse_key")
    assert parsed is not None
    return parsed


def error_of(call: Callable[[], object], expected: type[Exception], what: str) -> Exception:
    """O erro levantado por `call`, ou falha com texto fixo se nada (ou outra coisa) for levantado.

    Evita `pytest.raises` em torno de chaves: "DID NOT RAISE" mostraria, com `--showlocals`, os
    locais do teste, que incluem a chave.
    """
    caught: Exception | None = None
    try:
        call()
    except expected as error:
        caught = error
    require(caught is not None, f"{what}: the expected error was not raised")
    assert caught is not None
    return caught


def key_forbidden(raw_key: str) -> dict[str, str]:
    """Pedaços de uma chave que nenhuma saída pode conter (rótulo -> valor)."""
    _, key_hex, secret = key_parts(raw_key)
    return {
        "key": raw_key,
        "key without prefix": raw_key[4:],
        "key id": key_hex,
        "secret": secret,
        "secret prefix": secret[:10],
        "secret suffix": secret[-10:],
    }


# --- formato --------------------------------------------------------------------------------


@pytest.mark.parametrize("environment", ENVIRONMENTS)
def test_generated_key_has_the_documented_format(environment: Environment) -> None:
    key_id = uuid4()

    raw = generate_key(key_id, environment)

    require(KEY_PATTERN.fullmatch(raw) is not None, "the key does not match the format")
    marker, key_hex, secret = key_parts(raw)
    require(marker == MARKERS[environment], "the environment marker is wrong")
    require(key_hex == key_id.hex, "the key id is not the uuid hex")
    require(len(secret) == 43, "the secret does not have 43 characters")
    require(len(raw) == len("cce_dev_") + 32 + 1 + 43, "the key has the wrong length")


def test_the_secret_carries_256_bits_from_the_secrets_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: list[int] = []
    real = secrets.token_urlsafe

    def spy(nbytes: int | None = None) -> str:
        requested.append(nbytes or 0)
        return real(nbytes)

    monkeypatch.setattr(secrets, "token_urlsafe", spy)

    raw = generate_key(uuid4(), "test")

    require(requested == [32], "the secret is not secrets.token_urlsafe(32)")
    decoded = base64.urlsafe_b64decode(key_parts(raw)[2] + "=")
    require(len(decoded) == 32, "the secret does not decode to 32 bytes")


def test_generated_secrets_never_repeat_and_use_only_base64url() -> None:
    secrets_seen = {key_parts(generate_key(uuid4(), "test"))[2] for _ in range(300)}

    require(len(secrets_seen) == 300, "a generated secret repeated")
    require(
        all(re.fullmatch(r"[A-Za-z0-9_-]{43}", s) for s in secrets_seen),
        "a secret has characters outside base64url",
    )


FORCED_SECRETS = [
    pytest.param("_" * 43, id="only-underscores"),
    pytest.param("-" * 43, id="only-hyphens"),
    pytest.param("_" + "a" * 41 + "_", id="underscore-at-both-ends"),
    pytest.param("ab_cd_ef" + "x" * 35, id="underscores-in-the-middle"),
    pytest.param("a_b-c_d-" * 5 + "abc", id="mixed-separators"),
]


@pytest.mark.parametrize("secret", FORCED_SECRETS)
def test_a_secret_that_contains_underscores_is_generated_and_parsed_intact(
    monkeypatch: pytest.MonkeyPatch, secret: str
) -> None:
    require(len(secret) == 43, "the forced secret does not have 43 characters")
    monkeypatch.setattr(secrets, "token_urlsafe", lambda nbytes=None: secret)
    key_id = uuid4()

    raw = generate_key(key_id, "production")
    parsed = must_parse(raw, "production")

    require(raw.endswith("_" + secret), "the generated key does not end with the secret")
    require(parsed.key_id == key_id, "the parsed key id is wrong")
    require(parsed.raw_key.get_secret_value() == raw, "the parsed key was altered")


def test_random_keys_with_underscores_in_the_secret_round_trip() -> None:
    with_underscore = 0
    for _ in range(400):
        key_id = uuid4()
        raw = generate_key(key_id, "staging")
        with_underscore += "_" in key_parts(raw)[2]
        require(must_parse(raw, "staging").key_id == key_id, "a random key did not round trip")
    # ~48% das chaves: o caso é exercitado de verdade
    require(with_underscore > 50, "too few keys had an underscore in the secret")


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

    error_of(lambda: parse_key(candidate, "test"), InvalidKeyError, "a malformed key")


@pytest.mark.parametrize("environment", ENVIRONMENTS)
def test_a_key_of_another_environment_is_rejected(environment: Environment) -> None:
    for other in ENVIRONMENTS:
        if other == environment:
            continue
        foreign = valid_key(other)
        error_of(lambda k=foreign: parse_key(k, environment), InvalidKeyError, "a foreign key")  # type: ignore[misc]


def test_every_rejection_has_the_same_error_type_and_the_same_message() -> None:
    candidates: list[Any] = [fn(valid_key("test")) for fn in malformed_keys().values()]
    candidates += [valid_key("production"), b"bytes", None, 12345, object()]
    types: set[type] = set()
    messages: set[str] = set()
    for candidate in candidates:
        try:
            parse_key(candidate, "test")
        except Exception as error:  # noqa: BLE001 - o tipo é justamente o que se verifica
            types.add(type(error))
            messages.add(str(error))
            continue
        pytest.fail("a malformed key was accepted", pytrace=False)

    require(types == {InvalidKeyError}, "the rejections do not share one error type")
    require(len(messages) == 1, "the rejections do not share one message")
    require(issubclass(InvalidKeyError, ValueError), "InvalidKeyError is not a ValueError")


def test_the_error_never_echoes_the_received_key_or_any_part_of_it() -> None:
    raw = valid_key("production")
    forbidden = key_forbidden(raw)
    for attempt in (raw, raw[:-1], raw + "x", raw.replace("_prd_", "_xyz_")):
        error = error_of(lambda a=attempt: parse_key(a, "test"), InvalidKeyError, "a key")  # type: ignore[misc]
        shown = str(error) + repr(error) + repr(error.args)
        chained = error.__cause__ is not None or (
            error.__context__ is not None and not error.__suppress_context__
        )
        assert_no_leaks(shown, forbidden, "the parse error")
        require(not chained, "the parse error is chained to another exception")


def test_parse_returns_the_key_id_and_keeps_the_raw_key_secret() -> None:
    key_id = uuid4()
    raw = generate_key(key_id, "staging")

    parsed = must_parse(raw, "staging")

    require(isinstance(parsed, ParsedKey), "parse_key did not return a ParsedKey")
    require(parsed.key_id == key_id, "the parsed key id is wrong")
    require(isinstance(parsed.raw_key, SecretStr), "the raw key is not a SecretStr")
    require(parsed.raw_key.get_secret_value() == raw, "the parsed raw key was altered")


# --- digest ---------------------------------------------------------------------------------


def test_digest_is_hmac_sha256_with_the_pepper_and_the_domain_prefix() -> None:
    raw, pepper = valid_key(), fresh_pepper()

    digest = digest_key(raw, pepper)

    expected = hmac.new(pepper, DOMAIN + raw.encode("ascii"), hashlib.sha256).digest()
    require(digest == expected, "the digest is not HMAC-SHA256 over domain + key")
    require(len(digest) == 32, "the digest does not have 32 bytes")


def test_the_domain_prefix_matters() -> None:
    raw, pepper = valid_key(), fresh_pepper()

    without_domain = hmac.new(pepper, raw.encode("ascii"), hashlib.sha256).digest()
    other_domain = hmac.new(pepper, b"cce-api-key:v2\0" + raw.encode(), hashlib.sha256).digest()

    require(
        digest_key(raw, pepper) not in {without_domain, other_domain},
        "the digest does not depend on the domain prefix",
    )


def test_digest_is_stable_for_the_same_input() -> None:
    raw, pepper = valid_key(), fresh_pepper()

    require(digest_key(raw, pepper) == digest_key(raw, pepper), "the digest is not stable")


def test_digest_changes_with_another_pepper_and_with_another_key() -> None:
    raw, pepper = valid_key(), fresh_pepper()
    flipped = bytes([pepper[0] ^ 1]) + pepper[1:]

    require(
        digest_key(raw, pepper) != digest_key(raw, fresh_pepper()), "another pepper, same digest"
    )
    require(digest_key(raw, pepper) != digest_key(valid_key(), pepper), "another key, same digest")
    require(
        digest_key(raw, pepper) != digest_key(raw, flipped), "a flipped pepper bit, same digest"
    )


@pytest.mark.parametrize(
    "pepper",
    [
        pytest.param(b"", id="empty"),
        pytest.param(b"\x00" * 31, id="31-bytes"),
        pytest.param(b"x" * 16, id="16-bytes"),
    ],
)
def test_a_pepper_shorter_than_32_bytes_is_refused(pepper: bytes) -> None:
    with pytest.raises(ValueError):
        digest_key(valid_key(), pepper)


def test_the_pepper_must_be_bytes() -> None:
    with pytest.raises(TypeError):
        digest_key(valid_key(), "x" * 32)  # type: ignore[arg-type]


def test_a_non_ascii_key_is_refused_without_echoing_it() -> None:
    raw = valid_key()[:-1] + "é"

    error = error_of(lambda: digest_key(raw, fresh_pepper()), InvalidKeyError, "a non-ascii key")
    shown = str(error) + repr(error)

    assert_no_leaks(shown, key_forbidden(raw), "the digest error")


def test_peppers_are_selected_by_version() -> None:
    mapping = peppers(1, 2)

    require(
        pepper_bytes(mapping, 1) == base64.b64decode(mapping[1].get_secret_value()),
        "pepper_bytes did not decode version 1",
    )
    require(pepper_bytes(mapping, 1) != pepper_bytes(mapping, 2), "two versions, same pepper")
    require(len(pepper_bytes(mapping, 2)) == 32, "the pepper does not have 32 bytes")


@pytest.mark.parametrize("version", [0, 3, -1])
def test_a_missing_pepper_version_is_an_error_that_does_not_show_any_pepper(
    version: int,
) -> None:
    mapping = peppers(1, 2)

    with pytest.raises(UnknownPepperVersion) as excinfo:
        pepper_bytes(mapping, version)
    shown = str(excinfo.value) + repr(excinfo.value)

    forbidden = {f"pepper {v}": s.get_secret_value() for v, s in mapping.items()}
    assert_no_leaks(shown, forbidden, "the pepper error")


# --- comparação -----------------------------------------------------------------------------


def test_verify_accepts_only_the_identical_digest() -> None:
    digest = digest_key(valid_key(), fresh_pepper())

    require(verify_digest(digest, digest) is True, "an identical digest was refused")
    require(
        verify_digest(bytes(digest), bytes(bytearray(digest))) is True,
        "an equal copy of the digest was refused",
    )
    for bit in (0, 100, 255):
        changed = bytearray(digest)
        changed[bit // 8] ^= 1 << (bit % 8)
        require(verify_digest(bytes(changed), digest) is False, "a changed digest was accepted")
    other = digest_key(valid_key(), fresh_pepper())
    require(verify_digest(other, digest) is False, "another digest was accepted")


@pytest.mark.parametrize("size", [0, 1, 31, 33, 64])
def test_verify_rejects_a_digest_of_the_wrong_size_on_either_side(size: int) -> None:
    digest = digest_key(valid_key(), fresh_pepper())
    wrong = b"\x01" * size

    require(verify_digest(wrong, digest) is False, "a short or long candidate was accepted")
    require(verify_digest(digest, wrong) is False, "a short or long stored digest was accepted")
    # nem dois valores iguais, mas inválidos, passam
    require(verify_digest(wrong, wrong) is False, "two equal invalid digests were accepted")


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("a" * 32, id="str"),
        pytest.param(None, id="none"),
        pytest.param(123, id="int"),
        pytest.param(bytearray(32), id="bytearray"),
        pytest.param([1] * 32, id="list"),
    ],
)
def test_verify_rejects_anything_that_is_not_bytes(value: Any) -> None:
    digest = digest_key(valid_key(), fresh_pepper())

    require(verify_digest(value, digest) is False, "a non-bytes candidate was accepted")
    require(verify_digest(digest, value) is False, "a non-bytes stored digest was accepted")


def test_verify_always_compares_32_byte_values_with_compare_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[type, int, type, int]] = []
    real = hmac.compare_digest

    def spy(left: bytes, right: bytes) -> bool:
        calls.append((type(left), len(left), type(right), len(right)))
        return real(left, right)

    monkeypatch.setattr(hmac, "compare_digest", spy)
    digest = digest_key(valid_key(), fresh_pepper())

    verify_digest(digest, digest)  # igual
    verify_digest(digest[:-1], digest)  # tamanho errado do candidato
    verify_digest(digest, digest + b"\x00")  # tamanho errado do armazenado
    verify_digest(b"", b"")  # ambos inválidos
    verify_digest(None, digest)  # type: ignore[arg-type]

    require(bool(calls), "verify_digest did not use hmac.compare_digest")
    # o caminho de rejeição também compara, em tempo constante
    require(len(calls) == 5, "verify_digest skipped the constant-time comparison on a rejection")
    require(
        all(call == (bytes, 32, bytes, 32) for call in calls),
        "compare_digest did not receive two 32-byte values",
    )


# --- repr e str -----------------------------------------------------------------------------


def exposures(obj: object) -> str:
    return repr(obj) + str(obj) + f"{obj}" + format(obj) + repr([obj]) + repr({"k": obj})


def test_parsed_key_never_shows_the_secret() -> None:
    raw = valid_key("production")
    parsed = must_parse(raw, "production")
    _, key_hex, _ = key_parts(raw)

    shown = exposures(parsed)

    assert_no_leaks(shown, key_forbidden(raw) | {"key id": ""}, "the ParsedKey representation")
    # o ID não é segredo e ajuda a depurar
    require(str(parsed.key_id) in shown or key_hex in shown, "the representation lost the key id")


def test_issued_key_never_shows_the_secret_or_the_digest() -> None:
    issued = issue_key_material("production", peppers(1), 1)
    raw = issued.raw_key.get_secret_value()

    shown = exposures(issued)

    assert_no_leaks(
        shown,
        key_forbidden(raw)
        | {
            "digest hex": issued.secret_digest.hex(),
            "digest repr": repr(issued.secret_digest),
        },
        "the IssuedKey representation",
    )


def test_a_parsed_key_stays_safe_inside_an_exception_message() -> None:
    parsed = must_parse(valid_key(), "test")
    forbidden = key_forbidden(parsed.raw_key.get_secret_value())
    message = ""
    try:
        raise RuntimeError(f"failed for {parsed!r} / {parsed}")
    except RuntimeError as error:
        message = str(error)

    # Fora do `except`: uma falha aqui não fica encadeada ao RuntimeError original.
    assert_no_leaks(message, forbidden, "an exception message")


def test_an_issued_key_stays_safe_inside_an_exception_message() -> None:
    issued = issue_key_material("staging", peppers(1), 1)
    forbidden = key_forbidden(issued.raw_key.get_secret_value()) | {
        "digest hex": issued.secret_digest.hex(),
        "digest repr": repr(issued.secret_digest),
    }
    message = ""
    try:
        raise RuntimeError(f"failed for {issued!r} / {issued}")
    except RuntimeError as error:
        message = str(error)

    assert_no_leaks(message, forbidden, "an exception message")


# --- IssuedKey / material -------------------------------------------------------------------


def test_issue_key_material_returns_a_parseable_key_and_its_digest() -> None:
    mapping = peppers(1, 2)
    key_id = uuid4()

    issued = issue_key_material("staging", mapping, 2, key_id=key_id)

    require(isinstance(issued, IssuedKey), "issue_key_material did not return an IssuedKey")
    require(issued.key_id == key_id, "the issued key id is wrong")
    require(issued.pepper_version == 2, "the issued pepper version is wrong")
    raw = issued.raw_key.get_secret_value()
    require(must_parse(raw, "staging").key_id == key_id, "the issued key does not parse")
    require(
        issued.secret_digest == digest_key(raw, pepper_bytes(mapping, 2)),
        "the issued digest does not use the requested pepper",
    )
    require(len(issued.secret_digest) == 32, "the issued digest does not have 32 bytes")
    require(
        issued.secret_digest != digest_key(raw, pepper_bytes(mapping, 1)),
        "the issued digest also matches another pepper",
    )
    require(
        verify_digest(digest_key(raw, pepper_bytes(mapping, 2)), issued.secret_digest),
        "the issued digest does not verify",
    )


def test_issue_key_material_generates_a_new_id_and_secret_each_time() -> None:
    mapping = peppers(1)

    first = issue_key_material("test", mapping, 1)
    second = issue_key_material("test", mapping, 1)

    require(first.key_id != second.key_id, "two issued keys share an id")
    require(
        first.raw_key.get_secret_value() != second.raw_key.get_secret_value(),
        "two issued keys share a token",
    )
    require(first.secret_digest != second.secret_digest, "two issued keys share a digest")


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
