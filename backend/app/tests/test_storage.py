"""Unit tests for :mod:`app.services.storage`.

These need no database and no HTTP: they exercise the two guarantees the media
routes rely on — server-controlled object keys, and signed URLs that stop
working when they expire or are tampered with.
"""

from __future__ import annotations

import uuid

import pytest

from app.services import storage as storage_service
from app.services.storage import (
    LocalStorage,
    ObjectNotFoundError,
    UnsafeKeyError,
    original_key,
    sign_media_url,
    thumbnail_key,
    verify_media_signature,
)

WORKSPACE_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
MEDIA_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")


# ---------------------------------------------------------------------------
# Key construction and safety
# ---------------------------------------------------------------------------
def test_original_and_thumbnail_keys_are_server_controlled() -> None:
    assert original_key(WORKSPACE_ID, MEDIA_ID, ".mp4") == f"{WORKSPACE_ID}/{MEDIA_ID}/source.mp4"
    assert thumbnail_key(WORKSPACE_ID, MEDIA_ID) == f"{WORKSPACE_ID}/{MEDIA_ID}/thumbnail.jpg"


@pytest.mark.parametrize(
    "key",
    [
        "",
        "   ",
        "/etc/passwd",
        "..",
        "../outside.mp4",
        f"{WORKSPACE_ID}/../../escape.mp4",
        "back\\slash.mp4",
    ],
)
def test_unsafe_keys_are_refused(tmp_path, key: str) -> None:
    storage = LocalStorage(tmp_path)
    with pytest.raises(UnsafeKeyError):
        storage.path_for(key)


def test_a_safe_key_resolves_inside_the_root(tmp_path) -> None:
    storage = LocalStorage(tmp_path)
    resolved = storage.path_for(original_key(WORKSPACE_ID, MEDIA_ID, ".mp4"))
    assert resolved.is_relative_to(tmp_path.resolve())


# ---------------------------------------------------------------------------
# Object lifecycle
# ---------------------------------------------------------------------------
def test_write_read_append_and_delete_round_trip(tmp_path) -> None:
    storage = LocalStorage(tmp_path)
    key = "ws/file.bin"

    assert storage.write_bytes(key, b"abc") == 3
    assert storage.exists(key)
    assert storage.size(key) == 3
    assert storage.read_bytes(key) == b"abc"

    # Appending returns the authoritative size, which is what the resumable
    # upload protocol reports back as the next offset.
    assert storage.append_chunk(key, b"def") == 6
    assert storage.read_bytes(key) == b"abcdef"

    assert storage.delete(key) is True
    assert storage.delete(key) is False
    assert not storage.exists(key)


def test_reading_a_missing_object_raises(tmp_path) -> None:
    storage = LocalStorage(tmp_path)
    with pytest.raises(ObjectNotFoundError):
        storage.read_bytes("nope.bin")
    with pytest.raises(ObjectNotFoundError):
        storage.size("nope.bin")


def test_adopt_moves_a_temp_file_into_place(tmp_path) -> None:
    storage = LocalStorage(tmp_path)
    staging = tmp_path / ".uploads" / "part.bin"
    staging.parent.mkdir(parents=True, exist_ok=True)
    staging.write_bytes(b"payload")

    destination_key = "ws/media/source.mp4"
    assert storage.adopt(staging, destination_key) == 7
    assert not staging.exists(), "the temporary file must not survive the move"
    assert storage.read_bytes(destination_key) == b"payload"


def test_delete_media_directory_removes_every_object(tmp_path) -> None:
    storage = LocalStorage(tmp_path)
    storage.write_bytes(original_key(WORKSPACE_ID, MEDIA_ID, ".mp4"), b"v")
    storage.write_bytes(thumbnail_key(WORKSPACE_ID, MEDIA_ID), b"t")

    storage.delete_media_directory(WORKSPACE_ID, MEDIA_ID)

    assert list(storage.iter_keys(str(WORKSPACE_ID))) == []


def test_iter_keys_lists_files_under_a_prefix(tmp_path) -> None:
    storage = LocalStorage(tmp_path)
    storage.write_bytes(".uploads/a.json", b"{}")
    storage.write_bytes(".uploads/b.part", b"xy")
    storage.write_bytes(f"{WORKSPACE_ID}/{MEDIA_ID}/source.mp4", b"video")

    uploads = dict(storage.iter_keys(".uploads"))
    assert set(uploads) == {".uploads/a.json", ".uploads/b.part"}
    assert uploads[".uploads/b.part"] == 2


# ---------------------------------------------------------------------------
# Signed URLs
# ---------------------------------------------------------------------------
def test_signature_verifies_for_the_same_payload() -> None:
    expires_at, signature = sign_media_url(MEDIA_ID, "original")
    assert verify_media_signature(MEDIA_ID, "original", expires_at, signature)


@pytest.mark.parametrize(
    ("media_id", "variant", "signature"),
    [
        (uuid.uuid4(), "original", None),
        (MEDIA_ID, "thumbnail", None),
        (MEDIA_ID, "original", ""),
        (MEDIA_ID, "original", "0" * 64),
    ],
)
def test_signature_rejects_a_tampered_payload(media_id, variant, signature) -> None:
    expires_at, real_signature = sign_media_url(MEDIA_ID, "original")
    candidate = real_signature if signature is None else signature
    assert not verify_media_signature(media_id, variant, expires_at, candidate)


def test_signature_rejects_an_expired_url(monkeypatch) -> None:
    class _FrozenTime:
        now = 1_700_000_000

        @classmethod
        def time(cls) -> int:
            return cls.now

    monkeypatch.setattr(storage_service, "time", _FrozenTime)
    expires_at, signature = sign_media_url(MEDIA_ID, "original", ttl_seconds=60)

    assert verify_media_signature(MEDIA_ID, "original", expires_at, signature)

    _FrozenTime.now = expires_at + 1
    assert not verify_media_signature(MEDIA_ID, "original", expires_at, signature)


def test_the_default_ttl_comes_from_settings() -> None:
    from app.core.config import settings

    expires_at, _signature = sign_media_url(MEDIA_ID, "original")
    import time

    assert expires_at - int(time.time()) == pytest.approx(
        settings.media_signed_url_ttl_seconds, abs=2
    )
