"""TikTok video + account metrics (TikTok Display API).

Two documented Display API reads back this provider:

* ``POST /v2/video/query/?fields=...`` for the metrics of one published video.
* ``GET /v2/user/info/?fields=...`` for the account's audience.

TikTok reports application-level errors inside an HTTP ``200`` response as an
``error.code`` that is ``"ok"`` on success, so a caller must inspect the body,
not only the status code. TikTok exposes no reach or impressions figure for a
video, so those are ``None`` — unavailable, never zero.
"""

from __future__ import annotations

from typing import Any

from app.core.security import utcnow
from app.db.enums import SocialPlatform
from app.services.metrics.base import (
    AccountMetrics,
    MetricsAuthError,
    MetricsError,
    MetricsProvider,
    PostMetrics,
    as_int,
)

VIDEO_QUERY_ENDPOINT = "https://open.tiktokapis.com/v2/video/query/"
USER_INFO_ENDPOINT = "https://open.tiktokapis.com/v2/user/info/"

#: Fields requested for one video's performance.
_POST_FIELDS = "id,like_count,comment_count,share_count,view_count"
#: Fields requested for the account's audience.
_ACCOUNT_FIELDS = "open_id,follower_count,following_count,likes_count,video_count"

#: TikTok's own error codes that mean the grant must be re-established.
_AUTH_ERROR_CODES = frozenset({"access_token_invalid", "scope_not_authorized", "scope_permission_missed"})


class TikTokMetricsProvider(MetricsProvider):
    """Reads TikTok video and account metrics from the Display API."""

    platform = SocialPlatform.TIKTOK
    display_name = "TikTok"

    def fetch_post_metrics(
        self,
        *,
        access_token: str,
        remote_post_id: str,
        platform_account_id: str,
    ) -> PostMetrics:
        payload = self._json(
            self._request(
                "POST",
                VIDEO_QUERY_ENDPOINT,
                params={"fields": _POST_FIELDS},
                json={"filters": {"video_ids": [remote_post_id]}},
                headers={"Authorization": f"Bearer {access_token}"},
            ),
            action="read the video's metrics",
        )
        self._ensure_ok(payload, action="read the video's metrics")

        video = self._first_video(payload)
        return PostMetrics(
            platform=self.platform,
            retrieved_at=utcnow(),
            views=as_int(video.get("view_count")),
            likes=as_int(video.get("like_count")),
            comments=as_int(video.get("comment_count")),
            shares=as_int(video.get("share_count")),
            # Not exposed by the Display API for a video.
            reach=None,
            impressions=None,
            raw=payload,
        )

    def fetch_account_metrics(
        self,
        *,
        access_token: str,
        platform_account_id: str,
    ) -> AccountMetrics:
        payload = self._json(
            self._request(
                "GET",
                USER_INFO_ENDPOINT,
                params={"fields": _ACCOUNT_FIELDS},
                headers={"Authorization": f"Bearer {access_token}"},
            ),
            action="read the account's audience metrics",
        )
        self._ensure_ok(payload, action="read the account's audience metrics")

        user = self._user(payload)
        return AccountMetrics(
            platform=self.platform,
            retrieved_at=utcnow(),
            followers=as_int(user.get("follower_count")),
            follows=as_int(user.get("following_count")),
            posts_count=as_int(user.get("video_count")),
            raw=payload,
        )

    # --- parsing --------------------------------------------------------
    def _ensure_ok(self, payload: dict[str, Any], *, action: str) -> None:
        """Raise for a non-``ok`` TikTok error envelope carried inside a 200."""
        error = payload.get("error")
        code = error.get("code") if isinstance(error, dict) else None
        if not code or code == "ok":
            return
        message = error.get("message") or code
        if code in _AUTH_ERROR_CODES:
            raise MetricsAuthError(
                f"TikTok rejected the credentials while trying to {action}. "
                f"Reconnect the account."
            )
        raise MetricsError(f"TikTok could not {action}: {message}")

    @staticmethod
    def _first_video(payload: dict[str, Any]) -> dict[str, Any]:
        """The first video in a query response, or raise when it is missing."""
        data = payload.get("data")
        videos = data.get("videos") if isinstance(data, dict) else None
        if not isinstance(videos, list) or not videos:
            raise MetricsError(
                "TikTok returned no video for that id. It may have been deleted or is private."
            )
        first = videos[0]
        if not isinstance(first, dict):  # pragma: no cover - defensive
            raise MetricsError("TikTok returned an unexpected video payload.")
        return first

    @staticmethod
    def _user(payload: dict[str, Any]) -> dict[str, Any]:
        """The ``user`` object of a user-info response, or raise when missing."""
        data = payload.get("data")
        user = data.get("user") if isinstance(data, dict) else None
        if not isinstance(user, dict):
            raise MetricsError("TikTok returned no user profile for this account.")
        return user


__all__ = ["TikTokMetricsProvider"]
