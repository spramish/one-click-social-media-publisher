"""FFmpeg integration: video inspection and thumbnail generation.

FFmpeg is an **external** dependency, so this module treats its absence as a
first-class, recoverable condition rather than an afterthought:

* :func:`ffprobe_path` / :func:`ffmpeg_path` resolve the configured binary on
  ``PATH`` once and cache the result;
* :func:`require_ffprobe` / :func:`require_ffmpeg` raise
  :class:`FFmpegUnavailableError` with an actionable message, which the media
  routes turn into ``503 Service Unavailable``.

Nothing here invents metadata. If FFmpeg cannot read a file, the caller is told
so — no upload is ever recorded as ``ready`` with guessed duration or
resolution.

Configure ``MEDIA_FFMPEG_BINARY`` / ``MEDIA_FFPROBE_BINARY`` when the binaries
are not on ``PATH``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class FFmpegError(Exception):
    """Base class for FFmpeg integration failures."""


class FFmpegUnavailableError(FFmpegError):
    """The configured FFmpeg/FFprobe binary could not be found."""


class VideoInspectionError(FFmpegError):
    """The file exists but FFprobe could not read video metadata from it."""


@dataclass(frozen=True, slots=True)
class VideoInfo:
    """Everything the platform needs to know about an uploaded video."""

    duration_seconds: float | None
    width: int | None
    height: int | None
    video_codec: str | None
    audio_codec: str | None
    container: str | None

    @property
    def resolution(self) -> str | None:
        """``"1920x1080"`` when both dimensions are known."""
        if self.width and self.height:
            return f"{self.width}x{self.height}"
        return None


# ---------------------------------------------------------------------------
# Binary discovery
# ---------------------------------------------------------------------------
@lru_cache
def ffprobe_path() -> str | None:
    """Absolute path of the configured ``ffprobe`` binary, or ``None``."""
    return shutil.which(settings.media_ffprobe_binary)


@lru_cache
def ffmpeg_path() -> str | None:
    """Absolute path of the configured ``ffmpeg`` binary, or ``None``."""
    return shutil.which(settings.media_ffmpeg_binary)


def require_ffprobe() -> str:
    """The ``ffprobe`` path, or raise :class:`FFmpegUnavailableError`."""
    path = ffprobe_path()
    if path is None:
        raise FFmpegUnavailableError(
            f"'{settings.media_ffprobe_binary}' was not found. Install FFmpeg and put it on "
            "PATH, or point MEDIA_FFPROBE_BINARY at the executable."
        )
    return path


def require_ffmpeg() -> str:
    """The ``ffmpeg`` path, or raise :class:`FFmpegUnavailableError`."""
    path = ffmpeg_path()
    if path is None:
        raise FFmpegUnavailableError(
            f"'{settings.media_ffmpeg_binary}' was not found. Install FFmpeg and put it on "
            "PATH, or point MEDIA_FFMPEG_BINARY at the executable."
        )
    return path


def pipeline_available() -> bool:
    """True when both binaries are present, so uploads can be processed."""
    return ffprobe_path() is not None and ffmpeg_path() is not None


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    """Run an FFmpeg binary, never via a shell, always with a timeout."""
    return subprocess.run(  # noqa: S603 - fixed argv, no shell, resolved binary
        command,
        capture_output=True,
        text=True,
        timeout=settings.media_ffmpeg_timeout_seconds,
        check=False,
        stdin=subprocess.DEVNULL,
    )


# ---------------------------------------------------------------------------
# Inspection
# ---------------------------------------------------------------------------
def _as_float(value: object) -> float | None:
    """Parse a duration that FFprobe may report as a string or ``"N/A"``."""
    if value is None:
        return None
    try:
        parsed = float(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def probe(path: Path) -> VideoInfo:
    """Read duration, resolution, and codecs from ``path`` with FFprobe.

    Raises:
        FFmpegUnavailableError: ``ffprobe`` is not installed.
        VideoInspectionError: the file is not a readable video container.
    """
    binary = require_ffprobe()
    try:
        result = _run(
            [
                binary,
                "-v",
                "error",
                "-hide_banner",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(path),
            ]
        )
    except subprocess.TimeoutExpired as exc:
        raise VideoInspectionError(
            f"FFprobe timed out after {settings.media_ffmpeg_timeout_seconds}s reading the file"
        ) from exc

    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        reason = detail[-1] if detail else "unknown error"
        raise VideoInspectionError(f"FFprobe could not read this file: {reason}")

    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise VideoInspectionError("FFprobe returned output that is not valid JSON") from exc

    streams = payload.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise VideoInspectionError("This file does not contain a video stream")

    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    container = (payload.get("format") or {}).get("format_name")

    # Stream-level duration is the most precise; the container value is the
    # fallback for formats that only report it once.
    duration = _as_float(video.get("duration")) or _as_float(
        (payload.get("format") or {}).get("duration")
    )

    info = VideoInfo(
        duration_seconds=duration,
        width=int(video["width"]) if video.get("width") else None,
        height=int(video["height"]) if video.get("height") else None,
        video_codec=video.get("codec_name"),
        audio_codec=(audio or {}).get("codec_name"),
        container=container,
    )
    logger.info(
        "video inspected",
        extra={
            "duration_seconds": info.duration_seconds,
            "resolution": info.resolution,
            "video_codec": info.video_codec,
            "container": info.container,
        },
    )
    return info


# ---------------------------------------------------------------------------
# Thumbnails
# ---------------------------------------------------------------------------
def generate_thumbnail(
    source: Path,
    destination: Path,
    *,
    max_width: int | None = None,
    duration_seconds: float | None = None,
) -> bool:
    """Write a JPEG frame from ``source`` to ``destination``.

    Seeks one second in (a first frame is very often black), falling back to
    the very first frame for clips shorter than a second. Returns ``False``
    when no frame could be produced — a missing thumbnail must not fail an
    otherwise valid upload.

    Raises:
        FFmpegUnavailableError: ``ffmpeg`` is not installed.
    """
    binary = require_ffmpeg()
    width = max_width or settings.media_thumbnail_max_width
    destination.parent.mkdir(parents=True, exist_ok=True)

    # ``min(width,iw)`` avoids upscaling a video that is already smaller.
    scale = f"scale='min({width},iw)':-2"
    candidates: list[list[str]] = []
    if duration_seconds is None or duration_seconds >= 1.5:
        candidates.append(["-ss", "1"])
    candidates.append([])

    for seek in candidates:
        command = [
            binary,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            *seek,
            "-i",
            str(source),
            "-frames:v",
            "1",
            "-vf",
            scale,
            "-q:v",
            "3",
            str(destination),
        ]
        try:
            result = _run(command)
        except subprocess.TimeoutExpired:
            logger.warning("thumbnail generation timed out", extra={"source": str(source)})
            continue

        if result.returncode == 0 and destination.is_file() and destination.stat().st_size > 0:
            logger.info("thumbnail generated", extra={"destination": str(destination)})
            return True

        if result.returncode != 0:
            logger.warning(
                "thumbnail attempt failed",
                extra={"stderr": (result.stderr or "").strip()[-500:]},
            )

    return False


__all__ = [
    "FFmpegError",
    "FFmpegUnavailableError",
    "VideoInfo",
    "VideoInspectionError",
    "ffmpeg_path",
    "ffprobe_path",
    "generate_thumbnail",
    "pipeline_available",
    "probe",
    "require_ffmpeg",
    "require_ffprobe",
]
