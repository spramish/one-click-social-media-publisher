"""Unit tests for :mod:`app.services.video` (the FFmpeg layer).

FFmpeg is an external dependency, so the suite must behave in two ways: it
exercises real inspection and thumbnail generation where the binaries exist, and
it proves the *unavailable* path is a clean, recoverable error everywhere else.
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.services import video as video_service

JPEG_MAGIC = b"\xff\xd8\xff"


@pytest.fixture
def ffmpeg_caches():
    """Clear the memoised binary lookups before and after a test."""
    video_service.ffprobe_path.cache_clear()
    video_service.ffmpeg_path.cache_clear()
    yield
    video_service.ffprobe_path.cache_clear()
    video_service.ffmpeg_path.cache_clear()


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------
def test_a_missing_binary_is_reported_as_unavailable(monkeypatch, ffmpeg_caches) -> None:
    monkeypatch.setattr(settings, "media_ffprobe_binary", "definitely-not-ffprobe")
    monkeypatch.setattr(settings, "media_ffmpeg_binary", "definitely-not-ffmpeg")

    assert video_service.ffprobe_path() is None
    assert video_service.pipeline_available() is False

    with pytest.raises(video_service.FFmpegUnavailableError) as excinfo:
        video_service.require_ffprobe()
    # The message must be actionable: it names the setting to fix.
    assert "MEDIA_FFPROBE_BINARY" in str(excinfo.value)

    with pytest.raises(video_service.FFmpegUnavailableError):
        video_service.require_ffmpeg()


def test_pipeline_is_available_when_both_binaries_resolve() -> None:
    if video_service.ffprobe_path() is None or video_service.ffmpeg_path() is None:
        pytest.skip("FFmpeg is not installed on this machine")
    assert video_service.pipeline_available() is True


# ---------------------------------------------------------------------------
# Inspection
# ---------------------------------------------------------------------------
def test_probe_reads_duration_and_resolution(sample_video) -> None:
    info = video_service.probe(sample_video)

    assert info.width == 320
    assert info.height == 240
    assert info.resolution == "320x240"
    assert info.duration_seconds == pytest.approx(2.0, abs=0.3)
    assert info.video_codec is not None


def test_probe_rejects_a_file_that_is_not_a_video(tmp_path) -> None:
    if video_service.ffprobe_path() is None:
        pytest.skip("FFmpeg is not installed on this machine")

    impostor = tmp_path / "clip.mp4"
    impostor.write_bytes(b"this is plain text, not a container")

    with pytest.raises(video_service.VideoInspectionError):
        video_service.probe(impostor)


# ---------------------------------------------------------------------------
# Thumbnails
# ---------------------------------------------------------------------------
def test_generate_thumbnail_writes_a_jpeg(tmp_path, sample_video) -> None:
    destination = tmp_path / "thumb.jpg"

    assert video_service.generate_thumbnail(
        sample_video, destination, duration_seconds=2.0
    )
    assert destination.is_file()
    assert destination.stat().st_size > 0
    assert destination.read_bytes().startswith(JPEG_MAGIC)


def test_generate_thumbnail_gives_up_on_an_unreadable_file(tmp_path) -> None:
    if video_service.ffmpeg_path() is None:
        pytest.skip("FFmpeg is not installed on this machine")

    source = tmp_path / "broken.mp4"
    source.write_bytes(b"not a video")
    destination = tmp_path / "thumb.jpg"

    # A missing thumbnail must be a recoverable outcome, never an exception,
    # so that it cannot fail an otherwise valid upload.
    assert video_service.generate_thumbnail(source, destination) is False
    assert not destination.exists()
