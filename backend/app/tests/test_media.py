"""End-to-end tests for the media API.

Everything here goes through HTTP: no service is called directly, so the tests
cover the route wiring, the permission guards, the resumable-upload protocol,
the error translation in :func:`app.api.v1.endpoints.media._translate`, and the
signed-URL file-serving route.

Uploads write to the ``media_storage`` temporary root supplied by
``media_client``, so the developer's real media tree is never touched.
"""

from __future__ import annotations

import uuid
from urllib.parse import urlsplit

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.db.enums import WorkspaceMemberStatus
from app.models import User, WorkspaceMember
from app.models.post import Post
from app.services import auth as auth_service

# --- Endpoint URLs ---------------------------------------------------------
MEDIA = "/api/v1/media"
REGISTER = "/api/v1/auth/register"
WORKSPACES = "/api/v1/workspaces"

STRONG_PASSWORD = "correct-horse-battery"
OWNER_EMAIL = "owner@example.com"
VIEWER_EMAIL = "viewer@example.com"
STRANGER_EMAIL = "stranger@example.com"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _account(client, email: str, full_name: str = "Test User") -> tuple[str, uuid.UUID]:
    """Register an account and return ``(access_token, personal_workspace_id)``."""
    response = client.post(
        REGISTER, json={"email": email, "password": STRONG_PASSWORD, "full_name": full_name}
    )
    assert response.status_code == 201, response.text
    token = response.json()["access_token"]

    listing = client.get(WORKSPACES, headers=_bearer(token))
    assert listing.status_code == 200, listing.text
    return token, uuid.UUID(listing.json()[0]["id"])


def _add_member(db, workspace_id: uuid.UUID, email: str, role_name: str) -> User:
    """Attach an existing account to a workspace with the given role."""
    user = db.scalar(select(User).where(User.email == email.lower()))
    assert user is not None, f"user {email!r} was not persisted"
    db.add(
        WorkspaceMember(
            workspace_id=workspace_id,
            user_id=user.id,
            role_id=auth_service.get_role(db, role_name).id,
            status=WorkspaceMemberStatus.ACTIVE,
        )
    )
    db.flush()
    return user


def _declare(
    client,
    workspace_id: uuid.UUID,
    token: str,
    data: bytes,
    *,
    filename: str = "clip.mp4",
    mime_type: str = "video/mp4",
    size_bytes: int | None = None,
    replace_media_id: uuid.UUID | None = None,
):
    payload: dict[str, object] = {
        "filename": filename,
        "mime_type": mime_type,
        "size_bytes": len(data) if size_bytes is None else size_bytes,
    }
    if replace_media_id is not None:
        payload["replace_media_id"] = str(replace_media_id)
    return client.post(f"{MEDIA}/{workspace_id}/uploads", json=payload, headers=_bearer(token))


def _send_chunks(
    client,
    workspace_id: uuid.UUID,
    token: str,
    upload_id: str,
    data: bytes,
    *,
    chunk_size: int | None = None,
    start_offset: int = 0,
):
    """Push ``data`` in chunks, always continuing from the reported offset."""
    size = chunk_size or max(len(data), 1)
    offset = start_offset
    last = None
    remaining = data[start_offset:]
    while remaining:
        chunk, remaining = remaining[:size], remaining[size:]
        last = client.put(
            f"{MEDIA}/{workspace_id}/uploads/{upload_id}?offset={offset}",
            content=chunk,
            headers={**_bearer(token), "Content-Type": "application/octet-stream"},
        )
        assert last.status_code == 200, last.text
        offset += len(chunk)
    return last


def _upload(
    client,
    workspace_id: uuid.UUID,
    token: str,
    data: bytes,
    *,
    filename: str = "clip.mp4",
    mime_type: str = "video/mp4",
    chunk_size: int | None = None,
    replace_media_id: uuid.UUID | None = None,
):
    """Declare, transfer, and complete one upload. Returns the completion response."""
    declared = _declare(
        client,
        workspace_id,
        token,
        data,
        filename=filename,
        mime_type=mime_type,
        replace_media_id=replace_media_id,
    )
    assert declared.status_code == 201, declared.text
    upload_id = declared.json()["id"]
    _send_chunks(client, workspace_id, token, upload_id, data, chunk_size=chunk_size)
    return client.post(
        f"{MEDIA}/{workspace_id}/uploads/{upload_id}/complete", headers=_bearer(token)
    )


def _fetch_signed(client, url: str):
    """Request a signed URL by path, so the test never depends on the host name."""
    parts = urlsplit(url)
    return client.get(f"{parts.path}?{parts.query}")


# ---------------------------------------------------------------------------
# Capabilities and statistics
# ---------------------------------------------------------------------------
def test_capabilities_reports_the_server_limits(media_client) -> None:
    token, workspace_id = _account(media_client, OWNER_EMAIL)

    response = media_client.get(f"{MEDIA}/{workspace_id}/capabilities", headers=_bearer(token))
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["max_upload_bytes"] == settings.media_max_upload_bytes
    assert body["chunk_size_bytes"] == settings.media_chunk_size_bytes
    assert body["signed_url_ttl_seconds"] == settings.media_signed_url_ttl_seconds
    assert body["allowed_mime_types"] == settings.media_allowed_mime_types_list
    assert isinstance(body["ffmpeg_available"], bool)


def test_capabilities_require_authentication(media_client) -> None:
    token, workspace_id = _account(media_client, OWNER_EMAIL)
    response = media_client.get(f"{MEDIA}/{workspace_id}/capabilities")
    assert response.status_code == 401


def test_non_members_are_told_the_workspace_does_not_exist(media_client) -> None:
    _token, workspace_id = _account(media_client, OWNER_EMAIL)
    stranger_token, _stranger_workspace = _account(media_client, STRANGER_EMAIL, "Stranger")

    response = media_client.get(
        f"{MEDIA}/{workspace_id}/capabilities", headers=_bearer(stranger_token)
    )
    # 404, not 403: the API never confirms that another tenant's workspace exists.
    assert response.status_code == 404


def test_stats_reflect_a_completed_upload(media_client, sample_video) -> None:
    token, workspace_id = _account(media_client, OWNER_EMAIL)
    data = sample_video.read_bytes()

    assert _upload(media_client, workspace_id, token, data).status_code == 201

    stats = media_client.get(f"{MEDIA}/{workspace_id}/stats", headers=_bearer(token))
    assert stats.status_code == 200, stats.text
    assert stats.json() == {
        "file_count": 1,
        "total_bytes": len(data),
        "ready_count": 1,
        "failed_count": 0,
    }


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
def test_upload_is_stored_inspected_and_playable(media_client, media_storage, sample_video) -> None:
    token, workspace_id = _account(media_client, OWNER_EMAIL)
    data = sample_video.read_bytes()

    response = _upload(media_client, workspace_id, token, data)
    assert response.status_code == 201, response.text

    media = response.json()
    assert media["original_filename"] == "clip.mp4"
    assert media["mime_type"] == "video/mp4"
    assert media["size_bytes"] == len(data)
    assert media["status"] == "ready"
    assert media["storage_backend"] == "local"
    assert len(media["checksum_sha256"]) == 64
    # Duration and resolution are read from the stored bytes by FFprobe.
    assert media["duration_seconds"] == pytest.approx(2.0, abs=0.3)
    assert (media["width"], media["height"]) == (320, 240)
    assert "/files/original?" in media["video_url"]
    assert "signature=" in media["video_url"]
    assert media["thumbnail_url"] is not None
    assert "/files/thumbnail?" in media["thumbnail_url"]
    # The server never leaks the object key or a filesystem path.
    assert "storage_key" not in media
    assert str(media_storage.root) not in media["video_url"]

    # The signed URL serves the exact bytes that were uploaded.
    played = _fetch_signed(media_client, media["video_url"])
    assert played.status_code == 200, played.text
    assert played.content == data
    assert played.headers["content-type"] == "video/mp4"

    thumbnail = _fetch_signed(media_client, media["thumbnail_url"])
    assert thumbnail.status_code == 200, thumbnail.text
    assert thumbnail.content.startswith(b"\xff\xd8\xff")


def test_upload_in_many_chunks_reaches_the_same_bytes(media_client, sample_video) -> None:
    token, workspace_id = _account(media_client, OWNER_EMAIL)
    data = sample_video.read_bytes()

    response = _upload(media_client, workspace_id, token, data, chunk_size=512)
    assert response.status_code == 201, response.text
    assert response.json()["size_bytes"] == len(data)


def test_listing_returns_every_upload_and_is_scoped(media_client, sample_video) -> None:
    token, workspace_id = _account(media_client, OWNER_EMAIL)
    stranger_token, stranger_workspace = _account(media_client, STRANGER_EMAIL, "Stranger")
    data = sample_video.read_bytes()

    assert _upload(media_client, workspace_id, token, data, filename="first.mp4").status_code == 201
    assert _upload(media_client, workspace_id, token, data, filename="second.mp4").status_code == 201

    listing = media_client.get(f"{MEDIA}/{workspace_id}", headers=_bearer(token))
    assert listing.status_code == 200, listing.text
    # ``created_at`` is the transaction timestamp, so both rows of this test
    # share it exactly and their relative order is not part of the contract —
    # only that the listing is complete and stable.
    assert {item["original_filename"] for item in listing.json()} == {"first.mp4", "second.mp4"}
    assert len(listing.json()) == 2

    other = media_client.get(f"{MEDIA}/{stranger_workspace}", headers=_bearer(stranger_token))
    assert other.status_code == 200
    assert other.json() == []


def test_listing_can_be_searched_by_filename(media_client, sample_video) -> None:
    token, workspace_id = _account(media_client, OWNER_EMAIL)
    data = sample_video.read_bytes()
    assert _upload(media_client, workspace_id, token, data, filename="holiday.mp4").status_code == 201
    assert _upload(media_client, workspace_id, token, data, filename="standup.mp4").status_code == 201

    response = media_client.get(
        f"{MEDIA}/{workspace_id}?search=holi", headers=_bearer(token)
    )
    assert response.status_code == 200
    assert [item["original_filename"] for item in response.json()] == ["holiday.mp4"]


# ---------------------------------------------------------------------------
# Declaration-time validation
# ---------------------------------------------------------------------------
def test_unsupported_mime_type_is_refused_before_any_bytes_move(media_client) -> None:
    token, workspace_id = _account(media_client, OWNER_EMAIL)

    response = _declare(media_client, workspace_id, token, b"x", mime_type="application/zip")
    assert response.status_code == 415
    assert "application/zip" in response.json()["detail"]


def test_an_oversized_file_is_refused_at_declaration(media_client) -> None:
    token, workspace_id = _account(media_client, OWNER_EMAIL)

    response = _declare(
        media_client,
        workspace_id,
        token,
        b"x",
        size_bytes=settings.media_max_upload_bytes + 1,
    )
    assert response.status_code == 413


def test_a_filename_containing_a_path_separator_is_refused(media_client) -> None:
    token, workspace_id = _account(media_client, OWNER_EMAIL)

    response = _declare(media_client, workspace_id, token, b"x", filename="../../evil.mp4")
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Resumable protocol
# ---------------------------------------------------------------------------
def test_a_chunk_at_the_wrong_offset_is_refused_with_the_expected_offset(
    media_client, sample_video
) -> None:
    token, workspace_id = _account(media_client, OWNER_EMAIL)
    data = sample_video.read_bytes()

    declared = _declare(media_client, workspace_id, token, data)
    upload_id = declared.json()["id"]

    _send_chunks(media_client, workspace_id, token, upload_id, data[:512])

    # Skip ahead: the server must refuse rather than silently corrupt the file.
    response = media_client.put(
        f"{MEDIA}/{workspace_id}/uploads/{upload_id}?offset=4096",
        content=b"junk",
        headers=_bearer(token),
    )
    assert response.status_code == 409
    assert response.headers["x-upload-expected-offset"] == "512"


def test_an_upload_can_be_inspected_and_resumed(media_client, sample_video) -> None:
    token, workspace_id = _account(media_client, OWNER_EMAIL)
    data = sample_video.read_bytes()

    declared = _declare(media_client, workspace_id, token, data)
    assert declared.status_code == 201, declared.text
    state = declared.json()
    assert state["received_bytes"] == 0
    assert state["offset"] == 0
    assert state["chunk_size_bytes"] == settings.media_chunk_size_bytes

    upload_id = state["id"]
    _send_chunks(media_client, workspace_id, token, upload_id, data[:512])

    resumed = media_client.get(
        f"{MEDIA}/{workspace_id}/uploads/{upload_id}", headers=_bearer(token)
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["received_bytes"] == 512
    assert resumed.json()["offset"] == 512

    _send_chunks(media_client, workspace_id, token, upload_id, data, start_offset=512)
    complete = media_client.post(
        f"{MEDIA}/{workspace_id}/uploads/{upload_id}/complete", headers=_bearer(token)
    )
    assert complete.status_code == 201, complete.text
    assert complete.json()["size_bytes"] == len(data)


def test_completing_before_every_byte_arrived_is_refused(media_client, sample_video) -> None:
    token, workspace_id = _account(media_client, OWNER_EMAIL)
    data = sample_video.read_bytes()

    declared = _declare(media_client, workspace_id, token, data)
    upload_id = declared.json()["id"]
    _send_chunks(media_client, workspace_id, token, upload_id, data[:512])

    response = media_client.post(
        f"{MEDIA}/{workspace_id}/uploads/{upload_id}/complete", headers=_bearer(token)
    )
    assert response.status_code == 409
    assert str(len(data)) in response.json()["detail"]


def test_aborting_an_upload_discards_it(media_client, sample_video) -> None:
    token, workspace_id = _account(media_client, OWNER_EMAIL)
    data = sample_video.read_bytes()

    declared = _declare(media_client, workspace_id, token, data)
    upload_id = declared.json()["id"]

    aborted = media_client.delete(
        f"{MEDIA}/{workspace_id}/uploads/{upload_id}", headers=_bearer(token)
    )
    assert aborted.status_code == 200, aborted.text

    gone = media_client.get(
        f"{MEDIA}/{workspace_id}/uploads/{upload_id}", headers=_bearer(token)
    )
    assert gone.status_code == 404


def test_a_body_larger_than_the_chunk_ceiling_is_refused(media_client) -> None:
    token, workspace_id = _account(media_client, OWNER_EMAIL)

    declared = _declare(
        media_client,
        workspace_id,
        token,
        b"x",
        size_bytes=settings.media_chunk_size_bytes * 8,
    )
    upload_id = declared.json()["id"]

    response = media_client.put(
        f"{MEDIA}/{workspace_id}/uploads/{upload_id}?offset=0",
        content=b"0" * (settings.media_chunk_size_bytes * 4 + 1),
        headers=_bearer(token),
    )
    assert response.status_code == 413


def test_another_workspace_cannot_touch_my_upload(media_client, sample_video) -> None:
    token, workspace_id = _account(media_client, OWNER_EMAIL)
    stranger_token, stranger_workspace = _account(media_client, STRANGER_EMAIL, "Stranger")
    data = sample_video.read_bytes()

    declared = _declare(media_client, workspace_id, token, data)
    upload_id = declared.json()["id"]

    assert (
        media_client.get(
            f"{MEDIA}/{stranger_workspace}/uploads/{upload_id}", headers=_bearer(stranger_token)
        ).status_code
        == 404
    )
    assert (
        media_client.put(
            f"{MEDIA}/{stranger_workspace}/uploads/{upload_id}?offset=0",
            content=b"x",
            headers=_bearer(stranger_token),
        ).status_code
        == 404
    )


# ---------------------------------------------------------------------------
# Content validation happens on the real bytes
# ---------------------------------------------------------------------------
def test_a_renamed_non_video_is_rejected_at_completion(media_client) -> None:
    token, workspace_id = _account(media_client, OWNER_EMAIL)
    impostor = b"PK\x03\x04 this is a zip archive pretending to be a video"

    response = _upload(media_client, workspace_id, token, impostor, filename="movie.mp4")
    # 422: the declared type was accepted, but FFprobe read the real bytes.
    assert response.status_code == 422, response.text


# ---------------------------------------------------------------------------
# Signed URLs
# ---------------------------------------------------------------------------
def test_a_tampered_signature_is_refused(media_client, sample_video) -> None:
    token, workspace_id = _account(media_client, OWNER_EMAIL)
    media = _upload(media_client, workspace_id, token, sample_video.read_bytes()).json()

    parts = urlsplit(media["video_url"])
    response = media_client.get(f"{parts.path}?expires=9999999999&signature={'0' * 64}")
    assert response.status_code == 403


def test_a_signed_url_without_a_signature_is_refused(media_client, sample_video) -> None:
    token, workspace_id = _account(media_client, OWNER_EMAIL)
    media = _upload(media_client, workspace_id, token, sample_video.read_bytes()).json()

    path = urlsplit(media["video_url"]).path
    assert media_client.get(path).status_code == 422


# ---------------------------------------------------------------------------
# Deletion
# ---------------------------------------------------------------------------
def test_deleting_media_removes_it_and_frees_its_bytes(media_client, media_storage, sample_video) -> None:
    token, workspace_id = _account(media_client, OWNER_EMAIL)
    media = _upload(media_client, workspace_id, token, sample_video.read_bytes()).json()
    signed_url = media["video_url"]

    response = media_client.delete(f"{MEDIA}/{workspace_id}/{media['id']}", headers=_bearer(token))
    assert response.status_code == 200, response.text

    assert media_client.get(f"{MEDIA}/{workspace_id}/{media['id']}", headers=_bearer(token)).status_code == 404
    assert _fetch_signed(media_client, signed_url).status_code == 404
    # The physical objects are gone, not merely hidden.
    assert list(media_storage.iter_keys(str(workspace_id))) == []


def test_media_referenced_by_a_post_cannot_be_deleted(media_client, db, sample_video) -> None:
    token, workspace_id = _account(media_client, OWNER_EMAIL)
    media = _upload(media_client, workspace_id, token, sample_video.read_bytes()).json()

    db.add(Post(workspace_id=workspace_id, media_file_id=uuid.UUID(media["id"])))
    db.flush()

    response = media_client.delete(f"{MEDIA}/{workspace_id}/{media['id']}", headers=_bearer(token))
    assert response.status_code == 409
    assert "post" in response.json()["detail"]


def test_deleting_a_missing_file_is_a_404(media_client) -> None:
    token, workspace_id = _account(media_client, OWNER_EMAIL)
    response = media_client.delete(f"{MEDIA}/{workspace_id}/{uuid.uuid4()}", headers=_bearer(token))
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Replacement
# ---------------------------------------------------------------------------
def test_replacing_keeps_the_media_id_and_swaps_the_bytes(media_client, sample_video) -> None:
    token, workspace_id = _account(media_client, OWNER_EMAIL)
    original = _upload(media_client, workspace_id, token, sample_video.read_bytes()).json()

    response = _upload(
        media_client,
        workspace_id,
        token,
        sample_video.read_bytes(),
        filename="clip-v2.mp4",
        replace_media_id=uuid.UUID(original["id"]),
    )
    assert response.status_code == 201, response.text

    updated = response.json()
    assert updated["id"] == original["id"], "replacing must not create a second library item"
    assert updated["original_filename"] == "clip-v2.mp4"

    listing = media_client.get(f"{MEDIA}/{workspace_id}", headers=_bearer(token)).json()
    assert len(listing) == 1


def test_replacing_a_file_from_another_workspace_is_a_404(media_client, sample_video) -> None:
    token, workspace_id = _account(media_client, OWNER_EMAIL)
    stranger_token, stranger_workspace = _account(media_client, STRANGER_EMAIL, "Stranger")
    data = sample_video.read_bytes()

    stranger_media = _upload(media_client, stranger_workspace, stranger_token, data).json()

    response = _declare(
        media_client,
        workspace_id,
        token,
        data,
        replace_media_id=uuid.UUID(stranger_media["id"]),
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Role-based access
# ---------------------------------------------------------------------------
def test_a_viewer_can_read_but_not_upload(media_client, db, sample_video) -> None:
    token, workspace_id = _account(media_client, OWNER_EMAIL)
    data = sample_video.read_bytes()
    assert _upload(media_client, workspace_id, token, data).status_code == 201

    viewer_token, _ = _account(media_client, VIEWER_EMAIL, "Viewer")
    _add_member(db, workspace_id, VIEWER_EMAIL, "viewer")

    assert (
        media_client.get(f"{MEDIA}/{workspace_id}", headers=_bearer(viewer_token)).status_code
        == 200
    )

    refused = _declare(media_client, workspace_id, viewer_token, data)
    assert refused.status_code == 403
    assert "media:upload" in refused.json()["detail"]


def test_a_viewer_cannot_delete_media(media_client, db, sample_video) -> None:
    token, workspace_id = _account(media_client, OWNER_EMAIL)
    media = _upload(media_client, workspace_id, token, sample_video.read_bytes()).json()

    viewer_token, _ = _account(media_client, VIEWER_EMAIL, "Viewer")
    _add_member(db, workspace_id, VIEWER_EMAIL, "viewer")

    response = media_client.delete(
        f"{MEDIA}/{workspace_id}/{media['id']}", headers=_bearer(viewer_token)
    )
    assert response.status_code == 403
    assert "media:delete" in response.json()["detail"]
