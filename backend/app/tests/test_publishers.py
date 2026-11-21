"""Phase 7 unit tests — the per-platform publisher adapters.

Every test here drives one publisher against an ``httpx.MockTransport``, so no
real platform is ever contacted; what is exercised for real is the *contract*
each adapter must honour:

* success is only reported once the platform's **own** processing status is
  terminal-success — an accepted upload is never a published video;
* a failure is classified (rate limit vs. auth vs. permanent vs. transient) so
  the orchestration service can decide between a retry and a dead letter;
* validation happens before a single byte is sent.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from app.core.config import settings
from app.db.enums import SocialPlatform
from app.services.publishers import (
    CaptionValidationError,
    MediaAsset,
    MediaValidationError,
    PublishAuthError,
    PublishPermanentError,
    PublishRateLimitError,
    PublishRequest,
    PublishTransientError,
    get_publisher,
    supported_platforms,
)
from app.services.publishers.instagram import InstagramPublisher
from app.services.publishers.tiktok import TikTokPublisher
from app.services.publishers.youtube import YouTubePublisher


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _asset(
    tmp_path,
    *,
    size_bytes: int = 1024,
    mime_type: str = "video/mp4",
    duration: float | None = 10.0,
    width: int | None = 1080,
    height: int | None = 1920,
    with_file: bool = True,
) -> MediaAsset:
    path = tmp_path / "clip.mp4"
    if with_file:
        path.write_bytes(b"\x00" * min(size_bytes, 4096) or b"x")
    return MediaAsset(
        media_id=uuid.uuid4(),
        path=path if with_file else None,
        size_bytes=size_bytes,
        mime_type=mime_type,
        duration_seconds=duration,
        width=width,
        height=height,
        public_url="https://cdn.example/clip.mp4?sig=abc",
    )


def _request(asset: MediaAsset, *, caption: str = "hello world", title: str | None = None):
    return PublishRequest(
        asset=asset,
        caption=caption,
        title=title,
        access_token="test-access-token",
        platform_account_id="acct-1",
    )


def _install(monkeypatch, platform: SocialPlatform, handler) -> None:
    monkeypatch.setattr(get_publisher(platform), "transport", httpx.MockTransport(handler))


@pytest.fixture
def fast_polling(monkeypatch):
    """Poll without sleeping and give up quickly when a test needs a timeout."""
    monkeypatch.setattr(settings, "publishing_poll_interval_seconds", 0)
    monkeypatch.setattr(settings, "publishing_poll_max_attempts", 3)


# ---------------------------------------------------------------------------
# Registry + capabilities
# ---------------------------------------------------------------------------
def test_registry_exposes_every_supported_platform() -> None:
    assert set(supported_platforms()) == {
        SocialPlatform.FACEBOOK,
        SocialPlatform.INSTAGRAM,
        SocialPlatform.YOUTUBE,
        SocialPlatform.TIKTOK,
    }


def test_every_capability_matches_its_publisher_and_is_usable() -> None:
    for platform in supported_platforms():
        publisher = get_publisher(platform)
        capability = publisher.capability()
        assert capability.platform is platform
        assert capability.display_name == publisher.display_name
        assert capability.max_size_bytes > 0
        assert capability.max_caption_length > 0
        assert capability.allowed_mime_types


# ---------------------------------------------------------------------------
# Facebook — accepted upload is not a published video
# ---------------------------------------------------------------------------
def test_facebook_reports_success_only_after_the_video_is_ready(
    monkeypatch, tmp_path
) -> None:
    polls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/videos"):
            return httpx.Response(200, json={"id": "vid-1"})
        polls["n"] += 1
        state = "ready" if polls["n"] >= 2 else "processing"
        return httpx.Response(
            200,
            json={"status": {"video_status": state}, "permalink_url": "https://fb/vid-1"},
        )

    _install(monkeypatch, SocialPlatform.FACEBOOK, handler)

    result = get_publisher(SocialPlatform.FACEBOOK).publish(_request(_asset(tmp_path)))

    assert result.remote_post_id == "vid-1"
    assert result.status == "ready"
    assert result.remote_url == "https://fb/vid-1"
    # The 200 that merely accepted the upload was *not* treated as success.
    assert polls["n"] >= 2


def test_facebook_transient_when_the_video_never_becomes_ready(
    monkeypatch, tmp_path, fast_polling
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/videos"):
            return httpx.Response(200, json={"id": "vid-1"})
        return httpx.Response(200, json={"status": {"video_status": "processing"}})

    _install(monkeypatch, SocialPlatform.FACEBOOK, handler)

    with pytest.raises(PublishTransientError):
        get_publisher(SocialPlatform.FACEBOOK).publish(_request(_asset(tmp_path)))


def test_facebook_failure_inside_a_200_envelope_is_permanent(
    monkeypatch, tmp_path
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"error": {"message": "Invalid video", "error_user_msg": "Use H.264"}},
        )

    _install(monkeypatch, SocialPlatform.FACEBOOK, handler)

    with pytest.raises(PublishPermanentError):
        get_publisher(SocialPlatform.FACEBOOK).publish(_request(_asset(tmp_path)))


def test_facebook_429_carries_the_platforms_retry_after(monkeypatch, tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "120"}, json={"error": {"message": "slow down"}})

    _install(monkeypatch, SocialPlatform.FACEBOOK, handler)

    with pytest.raises(PublishRateLimitError) as caught:
        get_publisher(SocialPlatform.FACEBOOK).publish(_request(_asset(tmp_path)))
    assert caught.value.retry_after == 120
    assert caught.value.retryable is True


# ---------------------------------------------------------------------------
# Instagram — the three-step container flow
# ---------------------------------------------------------------------------
def test_instagram_runs_container_then_publish_then_permalink(
    monkeypatch, tmp_path
) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        seen.append(f"{request.method} {path}")
        if path.endswith("/media_publish"):
            return httpx.Response(200, json={"id": "media-1"})
        if path.endswith("/media"):
            return httpx.Response(200, json={"id": "container-1"})
        if path.endswith("/container-1"):
            return httpx.Response(200, json={"status_code": "FINISHED"})
        if path.endswith("/media-1"):
            return httpx.Response(200, json={"permalink": "https://instagram.com/reel/x"})
        return httpx.Response(404, json={"error": {"message": "unexpected"}})

    _install(monkeypatch, SocialPlatform.INSTAGRAM, handler)

    result = get_publisher(SocialPlatform.INSTAGRAM).publish(
        _request(_asset(tmp_path), caption="a reel")
    )

    assert result.remote_post_id == "media-1"
    assert result.status == "FINISHED"
    assert result.remote_url == "https://instagram.com/reel/x"
    # Container created -> container polled -> published -> permalink read.
    assert any("POST" in entry and entry.endswith("/media") for entry in seen)
    assert any(entry.endswith("/media_publish") for entry in seen)


def test_instagram_permalink_failure_does_not_fail_a_published_reel(
    monkeypatch, tmp_path
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/media_publish"):
            return httpx.Response(200, json={"id": "media-1"})
        if path.endswith("/media"):
            return httpx.Response(200, json={"id": "container-1"})
        if path.endswith("/container-1"):
            return httpx.Response(200, json={"status_code": "FINISHED"})
        # Reading the permalink is best-effort; a 500 here must not fail publish.
        return httpx.Response(500, json={"error": {"message": "boom"}})

    _install(monkeypatch, SocialPlatform.INSTAGRAM, handler)

    result = get_publisher(SocialPlatform.INSTAGRAM).publish(_request(_asset(tmp_path)))
    assert result.remote_post_id == "media-1"
    assert result.remote_url == "https://www.instagram.com/reel/media-1"


# ---------------------------------------------------------------------------
# YouTube — resumable upload + required title
# ---------------------------------------------------------------------------
def test_youtube_requires_a_title() -> None:
    youtube = YouTubePublisher()
    # No title and no caption to fall back to -> the platform's rule is enforced.
    with pytest.raises(CaptionValidationError):
        youtube.validate_caption(caption="", title=None)


def test_youtube_uploads_then_polls_until_processed(monkeypatch, tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/videos") and request.method == "POST":
            return httpx.Response(200, headers={"Location": "https://upload.example/session-1"})
        if request.url.host == "upload.example":
            return httpx.Response(200, json={"id": "yt-1"})
        return httpx.Response(
            200,
            json={"items": [{"id": "yt-1", "status": {"uploadStatus": "processed"}}]},
        )

    _install(monkeypatch, SocialPlatform.YOUTUBE, handler)

    result = get_publisher(SocialPlatform.YOUTUBE).publish(
        _request(_asset(tmp_path), caption="description", title="My video")
    )
    assert result.remote_post_id == "yt-1"
    assert result.status == "processed"
    assert result.remote_url == "https://www.youtube.com/watch?v=yt-1"


def test_youtube_needs_the_bytes_on_local_storage(tmp_path) -> None:
    asset = _asset(tmp_path, with_file=False)
    with pytest.raises(MediaValidationError):
        get_publisher(SocialPlatform.YOUTUBE).publish(_request(asset, title="T"))


# ---------------------------------------------------------------------------
# TikTok — always-present error envelope
# ---------------------------------------------------------------------------
def test_tiktok_init_upload_then_status(monkeypatch, tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "open.tiktokapis.com" and request.url.path.endswith("/init/"):
            return httpx.Response(
                200,
                json={
                    "error": {"code": "ok"},
                    "data": {"publish_id": "pub-1", "upload_url": "https://upload.tiktok/1"},
                },
            )
        if request.url.host == "upload.tiktok":
            return httpx.Response(200, json={})
        return httpx.Response(
            200, json={"error": {"code": "ok"}, "data": {"status": "PUBLISH_COMPLETE"}}
        )

    _install(monkeypatch, SocialPlatform.TIKTOK, handler)

    result = get_publisher(SocialPlatform.TIKTOK).publish(_request(_asset(tmp_path)))
    assert result.remote_post_id == "pub-1"
    assert result.status == "PUBLISH_COMPLETE"
    # TikTok exposes no permalink before processing completes.
    assert result.remote_url is None


def test_tiktok_classifies_error_codes(monkeypatch, tmp_path) -> None:
    def _handler_with(code: str):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"error": {"code": code, "message": "nope"}})

        return handler

    _install(monkeypatch, SocialPlatform.TIKTOK, _handler_with("rate_limit_exceeded"))
    with pytest.raises(PublishTransientError):
        get_publisher(SocialPlatform.TIKTOK).publish(_request(_asset(tmp_path)))

    _install(monkeypatch, SocialPlatform.TIKTOK, _handler_with("access_token_invalid"))
    with pytest.raises(PublishAuthError):
        get_publisher(SocialPlatform.TIKTOK).publish(_request(_asset(tmp_path)))

    _install(monkeypatch, SocialPlatform.TIKTOK, _handler_with("invalid_params"))
    with pytest.raises(PublishPermanentError):
        get_publisher(SocialPlatform.TIKTOK).publish(_request(_asset(tmp_path)))


def test_tiktok_chunking_and_privacy_mapping() -> None:
    assert TikTokPublisher._chunk_plan(3 * 1024**2) == (3145728, 1)
    assert TikTokPublisher._chunk_plan(100 * 1024**2) == (67108864, 2)
    assert TikTokPublisher._privacy_level("public") == "PUBLIC_TO_EVERYONE"
    # An unknown visibility must fall back to the safe, private level.
    assert TikTokPublisher._privacy_level("nonsense") == "SELF_ONLY"


# ---------------------------------------------------------------------------
# Validation, enforced before any upload
# ---------------------------------------------------------------------------
def test_validation_rejects_an_oversized_video_before_uploading(tmp_path) -> None:
    instagram = InstagramPublisher()
    asset = _asset(tmp_path, size_bytes=2 * 1024**3)  # Instagram caps at 1 GB
    with pytest.raises(MediaValidationError):
        instagram.validate_media(asset)


def test_validation_rejects_unsupported_container_and_resolution(tmp_path) -> None:
    instagram = InstagramPublisher()
    with pytest.raises(MediaValidationError):
        instagram.validate_media(_asset(tmp_path, mime_type="video/avi"))
    with pytest.raises(MediaValidationError):
        instagram.validate_media(_asset(tmp_path, width=100, height=100))
