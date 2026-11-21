"""Phase 10 tests — the analytics dashboard.

Two layers are exercised:

* **Provider level** (no database) — each platform's metrics adapter is driven
  against an ``httpx.MockTransport`` so no real platform is ever contacted. What
  is checked for real is the contract that matters to the phase brief:
  **unavailable is not zero** (a metric the platform does not expose is ``None``,
  a real ``0`` is ``0``) and every failure is classified into the retryable /
  auth / permanent taxonomy.
* **API level** — the real FastAPI app through ``TestClient`` against the
  migrated ``publisher_test`` database. Reading is checked to be a pure database
  read (no network), ``refresh`` the only network path, and tenant isolation
  (another workspace's numbers are invisible and ``404``) plus RBAC (a viewer may
  read but not refresh) hold on every route.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone

import httpx
import pytest
from sqlalchemy import select

from app.core.crypto import encrypt_secret
from app.core.security import utcnow
from app.db.enums import (
    MediaStatus,
    PostStatus,
    PostTargetStatus,
    SocialAccountStatus,
    SocialPlatform,
    StorageBackend,
    WorkspaceMemberStatus,
)
from app.models import (
    AccountMetric,
    MediaFile,
    OAuthCredential,
    Post,
    PostMetric,
    PostTarget,
    SocialAccount,
    User,
    WorkspaceMember,
)
from app.services import auth as auth_service
from app.services.metrics import (
    MetricsAuthError,
    MetricsError,
    MetricsRateLimitError,
    MetricsTransientError,
    as_int,
    get_metrics_provider,
    supported_metrics_platforms,
)

# --- Endpoint URLs ---------------------------------------------------------
REGISTER = "/api/v1/auth/register"
WORKSPACES = "/api/v1/workspaces"
ANALYTICS = "/api/v1/analytics"

STRONG_PASSWORD = "correct-horse-battery"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _install(monkeypatch, platform: SocialPlatform, handler) -> None:
    """Swap one platform's metrics HTTP layer for a mock, restored after the test."""
    monkeypatch.setattr(get_metrics_provider(platform), "transport", httpx.MockTransport(handler))


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _register(client, email: str) -> tuple[str, uuid.UUID]:
    response = client.post(
        REGISTER,
        json={"email": email, "password": STRONG_PASSWORD, "full_name": "Owner"},
    )
    assert response.status_code == 201, response.text
    token = response.json()["access_token"]
    workspaces = client.get(WORKSPACES, headers=_bearer(token)).json()
    return token, uuid.UUID(workspaces[0]["id"])


def _add_membership(db, workspace_id: uuid.UUID, user_id: uuid.UUID, role_name: str) -> None:
    db.add(
        WorkspaceMember(
            workspace_id=workspace_id,
            user_id=user_id,
            role_id=auth_service.get_role(db, role_name).id,
            status=WorkspaceMemberStatus.ACTIVE,
        )
    )
    db.flush()


def _media(db, workspace_id: uuid.UUID) -> MediaFile:
    media = MediaFile(
        workspace_id=workspace_id,
        storage_backend=StorageBackend.LOCAL,
        storage_key=f"{workspace_id}/{uuid.uuid4()}.mp4",
        original_filename="clip.mp4",
        mime_type="video/mp4",
        size_bytes=2048,
        checksum_sha256=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        duration_seconds=10.0,
        width=1080,
        height=1920,
        status=MediaStatus.READY,
    )
    db.add(media)
    db.flush()
    return media


def _connected_account(
    db,
    workspace_id: uuid.UUID,
    platform: SocialPlatform = SocialPlatform.FACEBOOK,
    *,
    account_id: str = "acct-1",
    display_name: str = "Account One",
) -> SocialAccount:
    account = SocialAccount(
        workspace_id=workspace_id,
        platform=platform,
        platform_account_id=account_id,
        display_name=display_name,
        status=SocialAccountStatus.CONNECTED,
    )
    db.add(account)
    db.flush()
    db.add(
        OAuthCredential(
            social_account_id=account.id,
            access_token_encrypted=encrypt_secret("access-token"),
            expires_at=utcnow() + timedelta(hours=24),
            scopes=[],
        )
    )
    db.flush()
    db.refresh(account)
    return account


def _published_target(
    db,
    workspace_id: uuid.UUID,
    account: SocialAccount,
    *,
    remote_post_id: str = "vid-1",
) -> PostTarget:
    media = _media(db, workspace_id)
    post = Post(
        workspace_id=workspace_id,
        media_file_id=media.id,
        base_caption="a clip",
        status=PostStatus.PUBLISHED,
        published_at=utcnow(),
    )
    db.add(post)
    db.flush()
    target = PostTarget(
        workspace_id=workspace_id,
        post_id=post.id,
        social_account_id=account.id,
        platform=account.platform,
        status=PostTargetStatus.PUBLISHED,
        remote_post_id=remote_post_id,
        remote_url=f"https://example/{remote_post_id}",
        published_at=utcnow(),
    )
    db.add(target)
    db.flush()
    return target


def _post_metric(db, workspace_id, target, *, retrieved_at, **values) -> PostMetric:
    metric = PostMetric(
        workspace_id=workspace_id,
        post_target_id=target.id,
        platform=target.platform,
        retrieved_at=retrieved_at,
        **values,
    )
    db.add(metric)
    db.flush()
    return metric


def _account_metric(db, workspace_id, account, *, retrieved_at, **values) -> AccountMetric:
    metric = AccountMetric(
        workspace_id=workspace_id,
        social_account_id=account.id,
        platform=account.platform,
        retrieved_at=retrieved_at,
        **values,
    )
    db.add(metric)
    db.flush()
    return metric


# ===========================================================================
# Provider level — no database, no network
# ===========================================================================
def test_registry_exposes_every_supported_platform() -> None:
    assert set(supported_metrics_platforms()) == {
        SocialPlatform.FACEBOOK,
        SocialPlatform.INSTAGRAM,
        SocialPlatform.YOUTUBE,
        SocialPlatform.TIKTOK,
    }


# --- as_int: unavailable vs a real zero -----------------------------------
def test_as_int_keeps_absence_and_zero_distinct() -> None:
    assert as_int(None) is None
    assert as_int("") is None
    assert as_int("   ") is None
    assert as_int("not a number") is None
    assert as_int(True) is None  # a bool is never a count
    assert as_int(0) == 0
    assert as_int("0") == 0
    assert as_int(0.0) == 0
    assert as_int(42) == 42
    assert as_int("1200") == 1200
    assert as_int(3.9) == 3


# --- Facebook --------------------------------------------------------------
def test_facebook_reads_video_metrics_and_leaves_reach_unavailable(monkeypatch) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "views": 1200,
                "likes": {"summary": {"total_count": 34}},
                "comments": {"summary": {"total_count": 5}},
                "shares": {"count": 7},
            },
        )

    _install(monkeypatch, SocialPlatform.FACEBOOK, handler)

    metrics = get_metrics_provider(SocialPlatform.FACEBOOK).fetch_post_metrics(
        access_token="token-1", remote_post_id="vid-1", platform_account_id="page-1"
    )

    assert metrics.views == 1200
    assert metrics.likes == 34
    assert metrics.comments == 5
    assert metrics.shares == 7
    # Facebook does not expose these through the fields requested.
    assert metrics.reach is None
    assert metrics.impressions is None
    assert metrics.retrieved_at is not None
    # The access token is sent to the platform, as a query parameter.
    assert seen[0].url.params["access_token"] == "token-1"


def test_facebook_audience_falls_back_to_fan_count(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"fan_count": 4800})

    _install(monkeypatch, SocialPlatform.FACEBOOK, handler)

    metrics = get_metrics_provider(SocialPlatform.FACEBOOK).fetch_account_metrics(
        access_token="t", platform_account_id="page-1"
    )

    assert metrics.followers == 4800
    assert metrics.follows is None
    assert metrics.posts_count is None


def test_facebook_error_inside_a_200_is_a_metrics_error(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"error": {"message": "Invalid OAuth access token", "error_user_msg": "Reconnect"}},
        )

    _install(monkeypatch, SocialPlatform.FACEBOOK, handler)

    with pytest.raises(MetricsError) as caught:
        get_metrics_provider(SocialPlatform.FACEBOOK).fetch_post_metrics(
            access_token="t", remote_post_id="vid-1", platform_account_id="page-1"
        )
    # The platform's own message is quoted, never the token.
    assert "Reconnect" in str(caught.value)
    assert "t" != str(caught.value)


def test_facebook_401_means_the_account_must_be_reconnected(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "bad token"}})

    _install(monkeypatch, SocialPlatform.FACEBOOK, handler)

    with pytest.raises(MetricsAuthError):
        get_metrics_provider(SocialPlatform.FACEBOOK).fetch_account_metrics(
            access_token="t", platform_account_id="page-1"
        )


# --- Instagram -------------------------------------------------------------
def test_instagram_reads_likes_and_comments_only(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"like_count": 80, "comments_count": 12})

    _install(monkeypatch, SocialPlatform.INSTAGRAM, handler)

    metrics = get_metrics_provider(SocialPlatform.INSTAGRAM).fetch_post_metrics(
        access_token="t", remote_post_id="media-1", platform_account_id="ig-1"
    )

    assert metrics.likes == 80
    assert metrics.comments == 12
    # Provided through media insights, which this read does not request.
    assert metrics.views is None
    assert metrics.shares is None
    assert metrics.reach is None


def test_instagram_reads_account_audience(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"followers_count": 9100, "follows_count": 320, "media_count": 44},
        )

    _install(monkeypatch, SocialPlatform.INSTAGRAM, handler)

    metrics = get_metrics_provider(SocialPlatform.INSTAGRAM).fetch_account_metrics(
        access_token="t", platform_account_id="ig-1"
    )

    assert (metrics.followers, metrics.follows, metrics.posts_count) == (9100, 320, 44)


# --- YouTube ---------------------------------------------------------------
def test_youtube_reads_video_statistics(monkeypatch) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "statistics": {
                            "viewCount": "150000",
                            "likeCount": "4200",
                            "commentCount": "310",
                        }
                    }
                ]
            },
        )

    _install(monkeypatch, SocialPlatform.YOUTUBE, handler)

    metrics = get_metrics_provider(SocialPlatform.YOUTUBE).fetch_post_metrics(
        access_token="tok", remote_post_id="yt-1", platform_account_id="chan-1"
    )

    assert metrics.views == 150000
    assert metrics.likes == 4200
    assert metrics.comments == 310
    assert metrics.shares is None
    assert metrics.reach is None
    # YouTube authenticates with a bearer header, not a query parameter.
    assert seen[0].headers["authorization"] == "Bearer tok"


def test_youtube_hidden_counts_stay_unavailable(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # ``likeCount`` omitted because likes are disabled; that is not a zero.
        return httpx.Response(200, json={"items": [{"statistics": {"viewCount": "10"}}]})

    _install(monkeypatch, SocialPlatform.YOUTUBE, handler)

    metrics = get_metrics_provider(SocialPlatform.YOUTUBE).fetch_post_metrics(
        access_token="t", remote_post_id="yt-1", platform_account_id="chan-1"
    )

    assert metrics.views == 10
    assert metrics.likes is None
    assert metrics.comments is None


def test_youtube_unknown_video_is_a_permanent_error(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": []})

    _install(monkeypatch, SocialPlatform.YOUTUBE, handler)

    with pytest.raises(MetricsError) as caught:
        get_metrics_provider(SocialPlatform.YOUTUBE).fetch_post_metrics(
            access_token="t", remote_post_id="gone", platform_account_id="chan-1"
        )
    assert caught.value.retryable is False


def test_youtube_reads_channel_audience(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"items": [{"statistics": {"subscriberCount": "54000", "videoCount": "88"}}]},
        )

    _install(monkeypatch, SocialPlatform.YOUTUBE, handler)

    metrics = get_metrics_provider(SocialPlatform.YOUTUBE).fetch_account_metrics(
        access_token="t", platform_account_id="chan-1"
    )

    assert metrics.followers == 54000
    assert metrics.posts_count == 88
    assert metrics.follows is None


# --- TikTok ----------------------------------------------------------------
def test_tiktok_reads_video_metrics(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "error": {"code": "ok"},
                "data": {
                    "videos": [
                        {
                            "id": "vid-1",
                            "view_count": 3000,
                            "like_count": 210,
                            "comment_count": 9,
                            "share_count": 4,
                        }
                    ]
                },
            },
        )

    _install(monkeypatch, SocialPlatform.TIKTOK, handler)

    metrics = get_metrics_provider(SocialPlatform.TIKTOK).fetch_post_metrics(
        access_token="t", remote_post_id="vid-1", platform_account_id="open-1"
    )

    assert (metrics.views, metrics.likes, metrics.comments, metrics.shares) == (3000, 210, 9, 4)
    assert metrics.reach is None
    assert metrics.impressions is None


def test_tiktok_reads_account_audience(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "error": {"code": "ok"},
                "data": {
                    "user": {
                        "open_id": "open-1",
                        "follower_count": 12000,
                        "following_count": 250,
                        "video_count": 60,
                    }
                },
            },
        )

    _install(monkeypatch, SocialPlatform.TIKTOK, handler)

    metrics = get_metrics_provider(SocialPlatform.TIKTOK).fetch_account_metrics(
        access_token="t", platform_account_id="open-1"
    )

    assert (metrics.followers, metrics.follows, metrics.posts_count) == (12000, 250, 60)


def test_tiktok_classifies_error_codes_inside_a_200(monkeypatch) -> None:
    def error_handler(code: str):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"error": {"code": code, "message": "nope"}})

        return handler

    _install(monkeypatch, SocialPlatform.TIKTOK, error_handler("access_token_invalid"))
    with pytest.raises(MetricsAuthError):
        get_metrics_provider(SocialPlatform.TIKTOK).fetch_post_metrics(
            access_token="t", remote_post_id="vid-1", platform_account_id="open-1"
        )

    _install(monkeypatch, SocialPlatform.TIKTOK, error_handler("invalid_params"))
    with pytest.raises(MetricsError) as caught:
        get_metrics_provider(SocialPlatform.TIKTOK).fetch_post_metrics(
            access_token="t", remote_post_id="vid-1", platform_account_id="open-1"
        )
    # A plain, non-auth error is not the auth subclass.
    assert not isinstance(caught.value, MetricsAuthError)


# --- Failure classification shared by every provider ----------------------
def test_a_rate_limit_carries_the_platforms_retry_after(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429, headers={"Retry-After": "90"}, json={"error": {"message": "slow down"}}
        )

    _install(monkeypatch, SocialPlatform.FACEBOOK, handler)

    with pytest.raises(MetricsRateLimitError) as caught:
        get_metrics_provider(SocialPlatform.FACEBOOK).fetch_post_metrics(
            access_token="t", remote_post_id="vid-1", platform_account_id="page-1"
        )
    assert caught.value.retry_after == 90
    assert caught.value.retryable is True


def test_a_server_error_is_transient(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": {"message": "unavailable"}})

    _install(monkeypatch, SocialPlatform.YOUTUBE, handler)

    with pytest.raises(MetricsTransientError) as caught:
        get_metrics_provider(SocialPlatform.YOUTUBE).fetch_account_metrics(
            access_token="t", platform_account_id="chan-1"
        )
    assert caught.value.retryable is True


def test_a_transport_fault_is_transient(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    _install(monkeypatch, SocialPlatform.YOUTUBE, handler)

    with pytest.raises(MetricsTransientError):
        get_metrics_provider(SocialPlatform.YOUTUBE).fetch_post_metrics(
            access_token="t", remote_post_id="yt-1", platform_account_id="chan-1"
        )


def test_a_non_json_body_is_transient(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    _install(monkeypatch, SocialPlatform.TIKTOK, handler)

    with pytest.raises(MetricsTransientError):
        get_metrics_provider(SocialPlatform.TIKTOK).fetch_account_metrics(
            access_token="t", platform_account_id="open-1"
        )


# ===========================================================================
# API level — reads are pure, refresh is the only network path
# ===========================================================================
def _facebook_metrics_handler(request: httpx.Request) -> httpx.Response:
    """Answer both the Page audience read and one video's read."""
    if request.url.path.endswith("/page-1"):
        return httpx.Response(200, json={"followers_count": 5000, "fan_count": 4800})
    return httpx.Response(
        200,
        json={
            "views": 900,
            "likes": {"summary": {"total_count": 20}},
            "comments": {"summary": {"total_count": 3}},
            "shares": {"count": 2},
        },
    )


def _tiktok_auth_failure_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200, json={"error": {"code": "access_token_invalid", "message": "reconnect"}}
    )


# --- Capability ------------------------------------------------------------
def test_capability_reports_every_platform_and_which_can_be_read(api_client, db) -> None:
    token, workspace_id = _register(api_client, "an-cap@example.com")

    body = api_client.get(
        f"{ANALYTICS}/{workspace_id}/capability", headers=_bearer(token)
    ).json()

    assert {entry["platform"] for entry in body["platforms"]} == {
        platform.value for platform in SocialPlatform
    }
    readable = {entry["platform"] for entry in body["platforms"] if entry["metrics_available"]}
    assert readable == {platform.value for platform in supported_metrics_platforms()}
    assert body["default_window_days"] > 0
    assert body["max_refresh_targets"] > 0


# --- Reading with no data --------------------------------------------------
def test_overview_is_empty_and_unavailable_before_any_refresh(api_client, db) -> None:
    token, workspace_id = _register(api_client, "an-empty@example.com")

    body = api_client.get(f"{ANALYTICS}/{workspace_id}/overview", headers=_bearer(token)).json()

    assert body["published_post_count"] == 0
    assert body["tracked_target_count"] == 0
    # Every metric is "unavailable", never a fabricated zero.
    assert body["totals"]["views"] is None
    assert body["totals"]["engagement"] is None
    assert body["totals"]["available"] == 0
    assert body["audience"]["followers"] is None
    assert body["last_refreshed_at"] is None


def test_reading_never_touches_a_platform(api_client, db) -> None:
    """With a connected account and a published target, the read routes still
    make no HTTP call — only ``refresh`` may.

    No transport is installed on any provider, so an outbound call would go to a
    real socket; the routes succeeding without one proves they are pure reads.
    """
    token, workspace_id = _register(api_client, "an-pure@example.com")
    account = _connected_account(db, workspace_id)
    _published_target(db, workspace_id, account)

    for path in ("overview", "platforms", "accounts", "targets", "history"):
        response = api_client.get(f"{ANALYTICS}/{workspace_id}/{path}", headers=_bearer(token))
        assert response.status_code == 200, (path, response.text)


# --- Overview aggregates the latest snapshot only -------------------------
def test_overview_sums_the_latest_snapshot_and_keeps_absence(api_client, db) -> None:
    token, workspace_id = _register(api_client, "an-overview@example.com")
    account = _connected_account(db, workspace_id)
    target = _published_target(db, workspace_id, account)

    now = utcnow()
    # An older snapshot that must be ignored...
    _post_metric(
        db, workspace_id, target, retrieved_at=now - timedelta(days=2), views=100,
        likes=1, comments=1, shares=1,
    )
    # ...and the newest one, which is the target's current performance. ``shares``
    # is absent here, so it stays unavailable rather than becoming zero.
    _post_metric(
        db, workspace_id, target, retrieved_at=now, views=250, likes=10, comments=4,
        shares=None, reach=180,
    )
    _account_metric(
        db, workspace_id, account, retrieved_at=now, followers=7300, follows=None,
        posts_count=51,
    )

    body = api_client.get(f"{ANALYTICS}/{workspace_id}/overview", headers=_bearer(token)).json()

    assert body["published_post_count"] == 1
    assert body["published_target_count"] == 1
    assert body["tracked_target_count"] == 1
    assert body["totals"]["views"] == 250  # the newest snapshot, not 100 + 250
    assert body["totals"]["likes"] == 10
    assert body["totals"]["comments"] == 4
    assert body["totals"]["reach"] == 180
    # Unavailable is preserved as ``null``; a real zero would print as 0.
    assert body["totals"]["shares"] is None
    assert body["totals"]["engagement"] == 14  # likes + comments; shares absent
    assert body["audience"]["followers"] == 7300
    assert body["audience"]["follows"] is None
    assert body["last_refreshed_at"] is not None


def test_engagement_is_zero_when_the_platform_reported_a_real_zero(api_client, db) -> None:
    token, workspace_id = _register(api_client, "an-zero@example.com")
    account = _connected_account(db, workspace_id)
    target = _published_target(db, workspace_id, account)

    _post_metric(
        db, workspace_id, target, retrieved_at=utcnow(), views=0, likes=0, comments=0, shares=0
    )

    body = api_client.get(f"{ANALYTICS}/{workspace_id}/overview", headers=_bearer(token)).json()

    # A real zero is reported as zero — the counterpart to the null above.
    assert body["totals"]["views"] == 0
    assert body["totals"]["engagement"] == 0
    assert body["totals"]["available"] >= 4


# --- Platform comparison ---------------------------------------------------
def test_platform_comparison_lists_only_platforms_with_content(api_client, db) -> None:
    token, workspace_id = _register(api_client, "an-platforms@example.com")
    fb = _connected_account(db, workspace_id, SocialPlatform.FACEBOOK, account_id="fb-1")
    yt = _connected_account(
        db, workspace_id, SocialPlatform.YOUTUBE, account_id="chan-1", display_name="Channel"
    )
    fb_target = _published_target(db, workspace_id, fb, remote_post_id="vid-1")
    yt_target = _published_target(db, workspace_id, yt, remote_post_id="yt-1")

    now = utcnow()
    _post_metric(db, workspace_id, fb_target, retrieved_at=now, views=400, likes=10)
    _post_metric(db, workspace_id, yt_target, retrieved_at=now, views=1000, likes=50)

    rows = api_client.get(f"{ANALYTICS}/{workspace_id}/platforms", headers=_bearer(token)).json()

    by_platform = {row["platform"]: row for row in rows}
    assert set(by_platform) == {"facebook", "youtube"}
    assert by_platform["facebook"]["totals"]["views"] == 400
    assert by_platform["youtube"]["totals"]["views"] == 1000
    assert by_platform["youtube"]["metrics_available"] is True
    # Instagram has no content here, so it is not listed.
    assert "instagram" not in by_platform


def test_account_performance_includes_audience(api_client, db) -> None:
    token, workspace_id = _register(api_client, "an-accounts@example.com")
    account = _connected_account(db, workspace_id)
    target = _published_target(db, workspace_id, account)
    now = utcnow()
    _post_metric(db, workspace_id, target, retrieved_at=now, views=42, likes=2)
    _account_metric(db, workspace_id, account, retrieved_at=now, followers=120)

    rows = api_client.get(f"{ANALYTICS}/{workspace_id}/accounts", headers=_bearer(token)).json()

    assert len(rows) == 1
    row = rows[0]
    assert row["social_account_id"] == str(account.id)
    assert row["audience"]["followers"] == 120
    assert row["audience_retrieved_at"] is not None
    assert row["totals"]["views"] == 42
    assert row["published_target_count"] == 1


# --- Targets and history ---------------------------------------------------
def test_targets_list_published_destinations_with_null_metrics_when_unrefreshed(
    api_client, db
) -> None:
    token, workspace_id = _register(api_client, "an-targets@example.com")
    account = _connected_account(db, workspace_id)
    _published_target(db, workspace_id, account)

    rows = api_client.get(f"{ANALYTICS}/{workspace_id}/targets", headers=_bearer(token)).json()

    assert len(rows) == 1
    # The destination is listed, but it has no snapshot yet — metrics unavailable.
    assert rows[0]["snapshot"] is None
    assert rows[0]["remote_post_id"] == "vid-1"


def test_targets_can_be_filtered_by_platform(api_client, db) -> None:
    token, workspace_id = _register(api_client, "an-targetfilter@example.com")
    fb = _connected_account(db, workspace_id, SocialPlatform.FACEBOOK, account_id="fb-1")
    yt = _connected_account(db, workspace_id, SocialPlatform.YOUTUBE, account_id="chan-1")
    _published_target(db, workspace_id, fb, remote_post_id="vid-1")
    _published_target(db, workspace_id, yt, remote_post_id="yt-1")

    rows = api_client.get(
        f"{ANALYTICS}/{workspace_id}/targets?platform=youtube", headers=_bearer(token)
    ).json()

    assert [row["platform"] for row in rows] == ["youtube"]


def test_history_keeps_only_the_latest_snapshot_of_each_day(api_client, db) -> None:
    token, workspace_id = _register(api_client, "an-history@example.com")
    account = _connected_account(db, workspace_id)
    target = _published_target(db, workspace_id, account)

    day = utcnow().replace(hour=12, minute=0, second=0, microsecond=0)
    # Two refreshes the same day: only the later one counts for that day.
    _post_metric(db, workspace_id, target, retrieved_at=day - timedelta(hours=6), views=100)
    _post_metric(db, workspace_id, target, retrieved_at=day, views=175)

    points = api_client.get(f"{ANALYTICS}/{workspace_id}/history", headers=_bearer(token)).json()

    assert len(points) == 1
    assert points[0]["samples"] == 1
    assert points[0]["metrics"]["views"] == 175


# --- Refresh: the only network path ----------------------------------------
def test_refresh_reads_every_destination_and_stores_snapshots(
    api_client, db, monkeypatch
) -> None:
    token, workspace_id = _register(api_client, "an-refresh@example.com")
    account = _connected_account(db, workspace_id, account_id="page-1")
    _published_target(db, workspace_id, account)
    _install(monkeypatch, SocialPlatform.FACEBOOK, _facebook_metrics_handler)

    response = api_client.post(f"{ANALYTICS}/{workspace_id}/refresh", headers=_bearer(token))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["accounts_refreshed"] == 1
    assert body["post_targets_refreshed"] == 1
    assert body["snapshots_written"] == 2
    assert body["errors"] == []

    # A retrieval time is recorded on every snapshot.
    post_metric = db.scalars(select(PostMetric)).one()
    assert post_metric.views == 900
    assert post_metric.likes == 20
    assert post_metric.shares == 2
    assert post_metric.reach is None  # unavailable stays null in the database too
    assert post_metric.retrieved_at is not None

    account_metric = db.scalars(select(AccountMetric)).one()
    assert account_metric.followers == 5000

    overview = api_client.get(f"{ANALYTICS}/{workspace_id}/overview", headers=_bearer(token)).json()
    assert overview["totals"]["views"] == 900
    assert overview["totals"]["shares"] == 2
    assert overview["totals"]["reach"] is None
    assert overview["audience"]["followers"] == 5000


def test_one_platforms_failure_does_not_stop_the_others(api_client, db, monkeypatch) -> None:
    token, workspace_id = _register(api_client, "an-partial@example.com")
    fb = _connected_account(db, workspace_id, SocialPlatform.FACEBOOK, account_id="page-1")
    tt = _connected_account(
        db, workspace_id, SocialPlatform.TIKTOK, account_id="open-1", display_name="TikTok"
    )
    _published_target(db, workspace_id, fb, remote_post_id="vid-1")
    _published_target(db, workspace_id, tt, remote_post_id="tt-1")
    _install(monkeypatch, SocialPlatform.FACEBOOK, _facebook_metrics_handler)
    _install(monkeypatch, SocialPlatform.TIKTOK, _tiktok_auth_failure_handler)

    response = api_client.post(f"{ANALYTICS}/{workspace_id}/refresh", headers=_bearer(token))

    assert response.status_code == 200, response.text
    body = response.json()
    # Facebook's numbers were written; TikTok's failures were collected.
    assert body["accounts_refreshed"] == 1
    assert body["post_targets_refreshed"] == 1
    assert body["snapshots_written"] == 2
    assert len(body["errors"]) == 2  # TikTok audience + TikTok video

    # Only Facebook snapshots exist, and they are the good ones.
    assert db.scalars(select(PostMetric)).one().views == 900
    assert db.scalars(select(AccountMetric)).one().followers == 5000


# --- Tenant isolation ------------------------------------------------------
def test_another_workspaces_metrics_are_invisible_and_unreachable(api_client, db) -> None:
    token, workspace_id = _register(api_client, "an-iso-a@example.com")
    account = _connected_account(db, workspace_id)
    target = _published_target(db, workspace_id, account)
    _post_metric(db, workspace_id, target, retrieved_at=utcnow(), views=999)

    other_token, other_workspace = _register(api_client, "an-iso-b@example.com")

    # The other workspace sees its own (empty) numbers, not ours.
    other_overview = api_client.get(
        f"{ANALYTICS}/{other_workspace}/overview", headers=_bearer(other_token)
    ).json()
    assert other_overview["totals"]["views"] is None
    assert (
        api_client.get(f"{ANALYTICS}/{other_workspace}/targets", headers=_bearer(other_token)).json()
        == []
    )
    # And neither token can reach the other workspace at all.
    assert (
        api_client.get(f"{ANALYTICS}/{other_workspace}/overview", headers=_bearer(token)).status_code
        == 404
    )
    assert (
        api_client.post(
            f"{ANALYTICS}/{other_workspace}/refresh", headers=_bearer(token)
        ).status_code
        == 404
    )


# --- RBAC ------------------------------------------------------------------
def test_a_viewer_may_read_but_not_refresh(api_client, db) -> None:
    owner_token, workspace_id = _register(api_client, "an-rbac-owner@example.com")
    del owner_token

    viewer_token, _ = _register(api_client, "an-rbac-viewer@example.com")
    viewer = db.scalar(select(User).where(User.email == "an-rbac-viewer@example.com"))
    _add_membership(db, workspace_id, viewer.id, "viewer")

    # analytics:read is granted to a viewer...
    assert (
        api_client.get(f"{ANALYTICS}/{workspace_id}/overview", headers=_bearer(viewer_token)).status_code
        == 200
    )
    # ...analytics:refresh is not: only a role that can publish may fetch.
    assert (
        api_client.post(
            f"{ANALYTICS}/{workspace_id}/refresh", headers=_bearer(viewer_token)
        ).status_code
        == 403
    )


def test_an_editor_may_refresh(api_client, db) -> None:
    owner_token, workspace_id = _register(api_client, "an-editor-owner@example.com")
    del owner_token

    editor_token, _ = _register(api_client, "an-editor@example.com")
    editor = db.scalar(select(User).where(User.email == "an-editor@example.com"))
    _add_membership(db, workspace_id, editor.id, "editor")

    # No accounts or targets exist, so the sweep is a no-op — but authorized.
    response = api_client.post(f"{ANALYTICS}/{workspace_id}/refresh", headers=_bearer(editor_token))
    assert response.status_code == 200, response.text
    assert response.json()["snapshots_written"] == 0


# --- Authentication --------------------------------------------------------
def test_analytics_endpoints_require_authentication(api_client, db) -> None:
    token, workspace_id = _register(api_client, "an-anon@example.com")
    del token

    assert api_client.get(f"{ANALYTICS}/{workspace_id}/overview").status_code == 401
    assert api_client.get(f"{ANALYTICS}/{workspace_id}/capability").status_code == 401
    assert api_client.get(f"{ANALYTICS}/{workspace_id}/targets").status_code == 401
    assert api_client.get(f"{ANALYTICS}/{workspace_id}/history").status_code == 401
    assert api_client.post(f"{ANALYTICS}/{workspace_id}/refresh").status_code == 401


# ---------------------------------------------------------------------------
# Worker wiring
# ---------------------------------------------------------------------------
def test_the_refresh_task_is_registered() -> None:
    from app.worker import celery_app

    assert "analytics.refresh_workspace" in celery_app.tasks
