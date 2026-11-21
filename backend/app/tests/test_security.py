"""Phase 3 unit tests: password hashing, JWTs and opaque tokens.

No database and no HTTP — these run against :mod:`app.core.security` directly.
The bcrypt work factor is lowered to 4 by ``conftest.py``; the format
assertions below are therefore about *shape and behaviour*, not cost.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import jwt
import pytest

from app.core.config import settings
from app.core.security import (
    ACCESS_TOKEN_TYPE,
    TokenError,
    constant_time_equals,
    create_access_token,
    decode_access_token,
    generate_opaque_token,
    hash_opaque_token,
    hash_password,
    password_needs_rehash,
    verify_password,
)

# --- Passwords -------------------------------------------------------------
def test_hash_password_produces_a_salted_bcrypt_hash() -> None:
    first = hash_password("correct horse battery staple")
    second = hash_password("correct horse battery staple")

    assert first.startswith(("$2a$", "$2b$", "$2y$"))
    assert first != second, "each hash must use a fresh salt"
    assert "correct horse battery staple" not in first


def test_verify_password_accepts_the_right_password_only() -> None:
    hashed = hash_password("s3cret-password-value")

    assert verify_password("s3cret-password-value", hashed) is True
    assert verify_password("s3cret-password-valuE", hashed) is False
    assert verify_password("", hashed) is False


@pytest.mark.parametrize("broken", ["", "not-a-hash", "$2b$12$too-short"])
def test_verify_password_returns_false_for_a_corrupt_hash(broken: str) -> None:
    """A damaged row must never raise — it must simply fail to authenticate."""
    assert verify_password("anything-at-all", broken) is False


def test_passwords_longer_than_the_bcrypt_limit_still_differ() -> None:
    """bcrypt truncates at 72 bytes; longer input must be pre-hashed instead."""
    base = "a" * 80
    other = "a" * 79 + "b"

    hashed = hash_password(base)

    assert verify_password(base, hashed) is True
    assert verify_password(other, hashed) is False


def test_password_needs_rehash_detects_cost_and_format_changes() -> None:
    assert password_needs_rehash(hash_password("whatever-value-here")) is False
    assert password_needs_rehash("$2b$99$" + "x" * 53) is True
    assert password_needs_rehash("$1$legacy-md5-style") is True
    assert password_needs_rehash("") is True


# --- Access tokens ---------------------------------------------------------
def test_access_token_round_trip_carries_its_claims() -> None:
    user_id, session_id = uuid.uuid4(), uuid.uuid4()

    token, expires_at = create_access_token(user_id=user_id, session_id=session_id)
    claims = decode_access_token(token)

    assert claims.user_id == user_id
    assert claims.session_id == session_id
    assert claims.expires_at == expires_at
    assert claims.issued_at <= claims.expires_at
    assert isinstance(claims.token_id, uuid.UUID)


def test_each_access_token_has_a_unique_identifier() -> None:
    user_id, session_id = uuid.uuid4(), uuid.uuid4()

    first, _ = create_access_token(user_id=user_id, session_id=session_id)
    second, _ = create_access_token(user_id=user_id, session_id=session_id)

    assert decode_access_token(first).token_id != decode_access_token(second).token_id


def test_access_token_rejects_a_tampered_signature() -> None:
    token, _ = create_access_token(user_id=uuid.uuid4(), session_id=uuid.uuid4())
    header, payload, signature = token.split(".")

    with pytest.raises(TokenError):
        decode_access_token(f"{header}.{payload}.{signature[:-2]}xx")


def test_access_token_rejects_an_expired_token() -> None:
    token, _ = create_access_token(
        user_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        expires_delta=timedelta(minutes=-5),
    )

    with pytest.raises(TokenError):
        decode_access_token(token)


def test_access_token_rejects_foreign_issuer_audience_or_type() -> None:
    now = 1_800_000_000
    base_claims = {
        "sub": str(uuid.uuid4()),
        "sid": str(uuid.uuid4()),
        "jti": str(uuid.uuid4()),
        "typ": ACCESS_TOKEN_TYPE,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "nbf": now,
        "exp": now + 600,
    }

    def sign(**overrides: object) -> str:
        return jwt.encode(
            {**base_claims, **overrides},
            settings.secret_key,
            algorithm=settings.jwt_algorithm,
        )

    with pytest.raises(TokenError):
        decode_access_token(sign(iss="somebody-else"))
    with pytest.raises(TokenError):
        decode_access_token(sign(aud="some-other-api"))
    with pytest.raises(TokenError):
        decode_access_token(sign(typ="refresh"))
    with pytest.raises(TokenError):
        decode_access_token(sign(sub="not-a-uuid"))


def test_access_token_rejects_a_token_signed_with_another_key() -> None:
    claims = {
        "sub": str(uuid.uuid4()),
        "sid": str(uuid.uuid4()),
        "jti": str(uuid.uuid4()),
        "typ": ACCESS_TOKEN_TYPE,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": 1_800_000_000,
        "exp": 1_800_000_600,
    }
    forged = jwt.encode(claims, "a-completely-different-secret", algorithm="HS256")

    with pytest.raises(TokenError):
        decode_access_token(forged)


# --- Opaque tokens ---------------------------------------------------------
def test_opaque_tokens_are_unique_and_store_only_a_digest() -> None:
    first, second = generate_opaque_token(), generate_opaque_token()

    assert first != second
    assert len(first) >= 40

    digest = hash_opaque_token(first)
    assert len(digest) == 64
    assert digest == hash_opaque_token(first)
    assert digest != first
    assert hash_opaque_token(second) != digest


def test_constant_time_equals() -> None:
    assert constant_time_equals("abc", "abc") is True
    assert constant_time_equals("abc", "abd") is False
