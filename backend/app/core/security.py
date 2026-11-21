"""Password hashing, JWT issuing/verification and token helpers.

Three credential kinds exist in the platform:

* **passwords** — hashed with bcrypt (adaptive, salted, constant-time verify);
* **access tokens** — short-lived signed JWTs (HS256) carrying ``sub``/``sid``;
* **refresh & verification tokens** — opaque 256-bit random strings that are
  only ever stored as a SHA-256 digest.

Nothing in this module logs, returns or serialises a secret in clear text.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.core.config import settings

#: bcrypt silently truncates input beyond 72 bytes, which would make long
#: passphrases share a prefix. Longer inputs are pre-hashed instead so every
#: byte of entropy counts.
_BCRYPT_MAX_BYTES = 72

#: Shape of a well-formed bcrypt hash: prefix, two-digit cost (04-31), then the
#: 22-character salt and 31-character digest from bcrypt's base64 alphabet.
#: Matching this *before* calling bcrypt matters — its Rust binding panics
#: (raising ``PanicException``, a ``BaseException`` that ``except Exception``
#: cannot catch) when handed a structurally invalid hash, such as a truncated
#: salt. A malformed stored value must fail closed, not crash the request.
_BCRYPT_HASH_RE = re.compile(
    r"^\$2[aby]\$(0[4-9]|[12][0-9]|3[01])\$[./A-Za-z0-9]{53}$"
)

ACCESS_TOKEN_TYPE = "access"

#: Number of random bytes in an opaque token (256 bits of entropy).
_OPAQUE_TOKEN_BYTES = 32


class TokenError(Exception):
    """Raised when a JWT is malformed, expired, or of the wrong type."""


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    """Decoded, validated access-token payload."""

    user_id: uuid.UUID
    session_id: uuid.UUID
    token_id: uuid.UUID
    issued_at: datetime
    expires_at: datetime


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------
def _bcrypt_input(password: str) -> bytes:
    """Return the byte string bcrypt should hash for ``password``."""
    raw = password.encode("utf-8")
    if len(raw) > _BCRYPT_MAX_BYTES:
        # base64(SHA-256(...)) is 44 bytes — safely inside bcrypt's limit and
        # still infeasible to collide.
        return base64.b64encode(hashlib.sha256(raw).digest())
    return raw


def hash_password(password: str) -> str:
    """Hash a plain-text password with a fresh bcrypt salt."""
    salt = bcrypt.gensalt(rounds=settings.password_hash_rounds)
    return bcrypt.hashpw(_bcrypt_input(password), salt).decode("ascii")


def verify_password(password: str, hashed_password: str) -> bool:
    """Constant-time check of ``password`` against a stored bcrypt hash.

    Returns ``False`` (never raises) for a malformed or unknown hash so a
    corrupted row cannot be used to probe the API.
    """
    # Reject anything that is not a syntactically valid bcrypt hash first:
    # bcrypt's Rust binding panics on a malformed salt, and a panic is not an
    # ``Exception``, so the ``try`` below could not contain it.
    if not hashed_password or _BCRYPT_HASH_RE.match(hashed_password) is None:
        return False
    try:
        return bcrypt.checkpw(_bcrypt_input(password), hashed_password.encode("ascii"))
    except (ValueError, TypeError):
        return False


def password_needs_rehash(hashed_password: str) -> bool:
    """True when a stored hash uses an outdated algorithm or work factor."""
    parts = hashed_password.split("$")
    if len(parts) != 4 or parts[1] not in {"2a", "2b", "2y"}:
        return True
    try:
        return int(parts[2]) != settings.password_hash_rounds
    except ValueError:
        return True


# ---------------------------------------------------------------------------
# Access tokens (JWT)
# ---------------------------------------------------------------------------
def create_access_token(
    *,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    expires_delta: timedelta | None = None,
) -> tuple[str, datetime]:
    """Sign a short-lived access token.

    Returns the encoded token together with its expiry, so callers can report
    an accurate ``expires_in`` without decoding what they just signed.
    """
    # Truncate to whole seconds: JWT ``iat``/``exp`` are integer timestamps, so
    # a caller comparing the returned expiry with a decoded claim must see the
    # same instant (microseconds would silently disappear through the wire).
    now = datetime.now(timezone.utc).replace(microsecond=0)
    expires_at = now + (
        expires_delta
        if expires_delta is not None
        else timedelta(minutes=settings.access_token_expire_minutes)
    )
    payload = {
        "sub": str(user_id),
        "sid": str(session_id),
        "jti": str(uuid.uuid4()),
        "typ": ACCESS_TOKEN_TYPE,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm), expires_at


def decode_access_token(token: str) -> AccessTokenClaims:
    """Validate a JWT and return its claims.

    Raises:
        TokenError: the signature, issuer, audience, expiry, required claim set
            or token type is invalid.
    """
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.jwt_algorithm],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "sub", "jti", "typ"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc

    if payload.get("typ") != ACCESS_TOKEN_TYPE:
        raise TokenError("unexpected token type")

    try:
        return AccessTokenClaims(
            user_id=uuid.UUID(payload["sub"]),
            session_id=uuid.UUID(payload["sid"]),
            token_id=uuid.UUID(payload["jti"]),
            issued_at=datetime.fromtimestamp(payload["iat"], tz=timezone.utc),
            expires_at=datetime.fromtimestamp(payload["exp"], tz=timezone.utc),
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise TokenError("malformed token claims") from exc


# ---------------------------------------------------------------------------
# Opaque tokens (refresh + verification)
# ---------------------------------------------------------------------------
def generate_opaque_token() -> str:
    """Return a new URL-safe 256-bit random token."""
    return secrets.token_urlsafe(_OPAQUE_TOKEN_BYTES)


def hash_opaque_token(token: str) -> str:
    """SHA-256 digest of a token — what the database actually stores.

    A plain digest is enough here (unlike for passwords) because the input is
    already 256 bits of randomness, so brute-forcing the preimage is infeasible.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_equals(left: str, right: str) -> bool:
    """Timing-safe string comparison."""
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def utcnow() -> datetime:
    """Timezone-aware current time (single source for auth expiry maths)."""
    return datetime.now(timezone.utc)
