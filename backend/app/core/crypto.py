"""Symmetric encryption for secrets that must be stored at rest.

OAuth access and refresh tokens grant full access to a user's social account,
so they are never written to the database in clear text. This module provides
the one primitive the rest of the platform uses for that: **AES-256-GCM**,
authenticated so a tampered ciphertext fails to decrypt rather than silently
returning garbage.

The key is derived from ``SECRET_KEY`` (or a dedicated ``OAUTH_ENCRYPTION_KEY``)
with **HKDF-SHA256** under a fixed application context. Deriving rather than
using the raw secret means:

* the JWT signing key and the token-encryption key are different bytes, so
  compromising one does not compromise the other; and
* rotating ``SECRET_KEY`` invalidates stored tokens instead of silently
  turning them into the new key's ciphertext.

Ciphertext layout (URL-safe, self-describing)::

    v1.<base64url(12-byte nonce || ciphertext || 16-byte GCM tag)>

The leading version marker lets a future key rotation read both formats.
"""

from __future__ import annotations

import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.config import settings

#: HKDF ``info`` string — a domain separator so this key can never collide with
#: any other key derived from the same secret.
_KEY_CONTEXT = b"ocp-oauth-token-encryption-v1"

#: Version prefix on every ciphertext produced by this module.
_VERSION = "v1"

#: AES-GCM nonce length in bytes (96 bits, the recommended size for GCM).
_NONCE_BYTES = 12


class EncryptionError(Exception):
    """A secret could not be encrypted or decrypted.

    Decryption failures (wrong key, truncated value, tampered ciphertext) all
    raise this, so callers treat "cannot read the token" as one condition.
    """


def _derive_key() -> bytes:
    """Derive the 32-byte AES key from the configured secret."""
    material = (settings.oauth_encryption_key or settings.secret_key).encode("utf-8")
    if not material:
        raise EncryptionError("No encryption key material is configured")
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=_KEY_CONTEXT,
    ).derive(material)


class TokenCipher:
    """Encrypts and decrypts short secrets (OAuth tokens, PKCE verifiers).

    An instance resolves its key once, on first use, so tests can swap
    ``settings.oauth_encryption_key`` and build a fresh cipher without clearing
    any global cache.
    """

    def __init__(self) -> None:
        self._aesgcm: AESGCM | None = None

    def _cipher(self) -> AESGCM:
        if self._aesgcm is None:
            self._aesgcm = AESGCM(_derive_key())
        return self._aesgcm

    def encrypt(self, plaintext: str) -> str:
        """Encrypt ``plaintext`` and return a versioned, URL-safe string."""
        if plaintext is None:
            raise EncryptionError("Cannot encrypt a null value")
        nonce = os.urandom(_NONCE_BYTES)
        blob = self._cipher().encrypt(nonce, plaintext.encode("utf-8"), None)
        return f"{_VERSION}.{base64.urlsafe_b64encode(nonce + blob).decode('ascii')}"

    def decrypt(self, token: str) -> str:
        """Reverse :meth:`encrypt`.

        Raises:
            EncryptionError: the value is malformed, was produced with a
                different key, or has been tampered with.
        """
        if not token:
            raise EncryptionError("Cannot decrypt an empty value")
        prefix, _, payload = token.partition(".")
        if prefix != _VERSION or not payload:
            raise EncryptionError("Unrecognised ciphertext format")
        try:
            raw = base64.urlsafe_b64decode(payload.encode("ascii"))
        except (ValueError, TypeError) as exc:
            raise EncryptionError("Ciphertext is not valid base64") from exc
        if len(raw) <= _NONCE_BYTES:
            raise EncryptionError("Ciphertext is too short to contain a nonce")
        nonce, blob = raw[:_NONCE_BYTES], raw[_NONCE_BYTES:]
        try:
            return self._cipher().decrypt(nonce, blob, None).decode("utf-8")
        except (InvalidTag, ValueError) as exc:
            raise EncryptionError(
                "This value could not be decrypted with the configured key"
            ) from exc


#: Shared cipher used by the account service. Building one instance reuses the
#: derived key instead of re-running HKDF per call.
default_cipher = TokenCipher()


def encrypt_secret(plaintext: str) -> str:
    """Encrypt using the process-wide cipher."""
    return default_cipher.encrypt(plaintext)


def decrypt_secret(token: str) -> str:
    """Decrypt using the process-wide cipher."""
    return default_cipher.decrypt(token)


def mask_secret(value: str | None, *, visible: int = 4) -> str:
    """Render a token for logs: last few characters only, never the whole value."""
    if not value:
        return "<none>"
    if len(value) <= visible:
        return "*" * len(value)
    return f"{'*' * 8}{value[-visible:]}"


__all__ = [
    "EncryptionError",
    "TokenCipher",
    "decrypt_secret",
    "default_cipher",
    "encrypt_secret",
    "mask_secret",
]
