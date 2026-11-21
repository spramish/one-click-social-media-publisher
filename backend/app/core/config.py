"""Application configuration.

All settings are read from environment variables (and an optional ``.env``
file) via pydantic-settings. Access the shared instance with
``from app.core.config import settings``.
"""

from functools import lru_cache
from pathlib import Path
from typing import Self

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Placeholder shipped in ``.env.example``. Boot refuses to start with it when
#: ``ENVIRONMENT=production`` so a real secret can never be forgotten.
INSECURE_SECRET_KEY = "change-me-to-a-long-random-secret"


class Settings(BaseSettings):
    """Typed, validated application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application --------------------------------------------------------
    project_name: str = "One-Click Social Media Video Publisher"
    environment: str = "development"
    debug: bool = True

    # --- Security / tokens --------------------------------------------------
    secret_key: str = INSECURE_SECRET_KEY
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "one-click-social-media-video-publisher"
    jwt_audience: str = "one-click-social-media-video-publisher-api"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7
    email_verification_expire_hours: int = 24
    password_reset_expire_minutes: int = 60
    password_min_length: int = 12
    #: bcrypt work factor. 12 is the OWASP-recommended minimum; the test suite
    #: lowers it to keep hashing off the critical path.
    password_hash_rounds: int = 12
    #: Oldest sessions are revoked once a user exceeds this many active ones.
    max_active_sessions: int = 10

    # --- Refresh-token cookie -----------------------------------------------
    #: The refresh token never appears in a JSON response body for browser
    #: clients — it travels in an ``HttpOnly`` cookie so JavaScript cannot read
    #: it. The JSON body carries only the short-lived access token.
    refresh_cookie_name: str = "ocp_refresh_token"
    refresh_cookie_path: str = "/api/v1/auth"
    refresh_cookie_secure: bool = False
    refresh_cookie_samesite: str = "lax"
    refresh_cookie_domain: str | None = None

    # --- Backend ------------------------------------------------------------
    backend_host: str = "0.0.0.0"
    backend_port: int = 8000
    api_v1_prefix: str = "/api/v1"
    cors_origins: str = "http://localhost:3000"

    # --- PostgreSQL ---------------------------------------------------------
    postgres_user: str = "publisher"
    postgres_password: str = "publisher_dev_password"
    postgres_db: str = "publisher_db"
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    database_url: str = (
        "postgresql+psycopg://publisher:publisher_dev_password@localhost:5432/publisher_db"
    )

    # --- Redis --------------------------------------------------------------
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_url: str = "redis://localhost:6379/0"

    # --- Celery -------------------------------------------------------------
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # --- Frontend -----------------------------------------------------------
    #: Used to build the links inside verification / password-reset emails.
    frontend_base_url: str = "http://localhost:3000"

    # --- Media storage (Phase 5) --------------------------------------------
    #: Where uploaded videos and their thumbnails live when the file uses the
    #: ``local`` storage backend. A relative path resolves against the process
    #: working directory (``backend/`` when started from there).
    media_root: str = "var/media"
    #: Largest accepted upload, in bytes. Enforced twice: the declared size when
    #: the upload session is created, and the real byte count as chunks arrive.
    media_max_upload_bytes: int = 2 * 1024 * 1024 * 1024
    #: Accepted container MIME types. A file whose declared type is not in this
    #: list is rejected before a single byte is written to storage.
    media_allowed_mime_types: str = (
        "video/mp4,video/quicktime,video/webm,video/x-matroska,video/x-msvideo"
    )
    #: Chunk size advertised to clients using the resumable upload protocol.
    media_chunk_size_bytes: int = 5 * 1024 * 1024
    #: How long an unfinished upload may stay resumable before it is abandoned.
    media_upload_session_ttl_hours: int = 6
    #: Lifetime of a signed media URL.
    media_signed_url_ttl_seconds: int = 900
    #: Widest thumbnail FFmpeg produces; the height keeps the aspect ratio.
    media_thumbnail_max_width: int = 640
    #: FFmpeg binaries. Set absolute paths when they are not on ``PATH``.
    media_ffmpeg_binary: str = "ffmpeg"
    media_ffprobe_binary: str = "ffprobe"
    #: Wall-clock limit for a single inspection or thumbnail operation.
    media_ffmpeg_timeout_seconds: int = 120

    # --- OAuth account connections (Phase 6) --------------------------------
    #: Public origin the *browser* reaches this API on. Every OAuth
    #: ``redirect_uri`` is built from it, so it must match the value registered
    #: with each platform to the character.
    backend_public_url: str = "http://localhost:8000"
    #: Where the callback sends the browser once a connection succeeds/fails.
    oauth_success_redirect: str = "http://localhost:3000/accounts"
    oauth_failure_redirect: str = "http://localhost:3000/accounts"
    #: How long an authorization attempt may stay open before its state expires.
    oauth_state_ttl_seconds: int = 600
    #: Wall-clock limit for a single platform API call.
    oauth_http_timeout_seconds: int = 30
    #: A token is refreshed this long before it actually expires, so a publish
    #: that starts just before expiry does not fail mid-flight.
    oauth_refresh_margin_seconds: int = 300
    #: Optional dedicated key for encrypting tokens at rest. When empty the
    #: application key is used, with a different HKDF context (so the signing
    #: key and the encryption key are never the same bytes).
    oauth_encryption_key: str | None = None

    # --- Meta (Facebook + Instagram Graph API) ------------------------------
    #: Graph API version segment used in every Meta URL.
    meta_graph_version: str = "v21.0"
    #: Instagram publishing goes through a Facebook app, so both platforms share
    #: one client pair unless separate values are provided.
    facebook_client_id: str = ""
    facebook_client_secret: str = ""
    instagram_client_id: str = ""
    instagram_client_secret: str = ""

    # --- Google (YouTube Data API v3) ---------------------------------------
    youtube_client_id: str = ""
    youtube_client_secret: str = ""

    # --- TikTok (Login Kit + Content Posting API) ---------------------------
    tiktok_client_key: str = ""
    tiktok_client_secret: str = ""

    # --- Publishing engine (Phase 7) ----------------------------------------
    #: Wall-clock limit for a single platform publishing API call.
    publishing_http_timeout_seconds: int = 120
    #: Lifetime of the signed media URL a pull-based platform (Meta) fetches the
    #: video from. It must outlive the platform's own download window.
    publishing_media_url_ttl_seconds: int = 3600
    #: How long to wait between two polls of a platform's processing status.
    publishing_poll_interval_seconds: int = 5
    #: How many times a processing status is polled before the job is treated as
    #: a transient failure (the platform is simply slow, not broken).
    publishing_poll_max_attempts: int = 60
    #: First retry delay. Each subsequent retry doubles it (exponential backoff).
    publishing_retry_base_seconds: int = 30
    #: Ceiling on the exponential backoff, so a job never sleeps for hours.
    publishing_retry_max_seconds: int = 3600
    #: How many attempts a publishing job gets before it is dead-lettered.
    publishing_max_attempts: int = 5
    #: Default upload visibility when a post does not override it. YouTube
    #: unverified apps are forced to ``private`` regardless; TikTok unaudited
    #: apps must use ``SELF_ONLY``.
    publishing_default_privacy: str = "public"

    # --- Content scheduling (Phase 8) ---------------------------------------
    #: How often the Celery Beat sweep looks for entries that have come due.
    #: This is the worst-case lateness of a scheduled publish on a healthy
    #: worker, so it is deliberately much shorter than the missed-job grace.
    scheduling_sweep_interval_seconds: int = 60
    #: How many due entries one sweep claims. Bounds the transaction and the
    #: burst of jobs a single tick can enqueue.
    scheduling_batch_size: int = 100
    #: How late a due entry may be dispatched before it is abandoned as
    #: ``missed`` (the worker was down, the machine was asleep, ...). A post
    #: fired hours or days late is worse than one the user is told to re-queue,
    #: so an overdue entry does not silently publish stale content.
    scheduling_missed_grace_seconds: int = 3600
    #: Time zone assumed when a caller does not supply one. Stored per schedule
    #: so the UI can always show the local time the user actually picked.
    scheduling_default_timezone: str = "UTC"

    # --- AI content generation (Phase 9) ------------------------------------
    #: OpenAI API key. Empty means the AI features are *unconfigured*: the
    #: endpoints answer ``503`` and the UI says so, rather than the user finding
    #: out only after a brief has been typed. Mirrors the OAuth "capability gap"
    #: rule — a missing credential is a configuration state, not a crash.
    openai_api_key: str = ""
    #: API origin. Override to point at an OpenAI-compatible gateway (Azure
    #: OpenAI, a local proxy) without a code change.
    openai_base_url: str = "https://api.openai.com/v1"
    #: Default model. Deliberately a small, fast, inexpensive one — caption and
    #: hashtag generation does not need a frontier model. Override with
    #: ``OPENAI_MODEL``.
    openai_model: str = "gpt-4o-mini"
    #: Wall-clock limit for a single completion call.
    openai_http_timeout_seconds: int = 60
    #: Ceiling on generated text, so a runaway response cannot fill the column
    #: (or the bill).
    openai_max_output_tokens: int = 800
    #: Sampling temperature. Higher is more creative; captions want some range.
    openai_temperature: float = 0.8
    #: Longest accepted user brief, in characters.
    ai_max_brief_chars: int = 2000

    # --- Analytics dashboard (Phase 10) -------------------------------------
    #: Wall-clock limit for a single platform metrics call. Shorter than the
    #: publishing timeout: a dashboard refresh reads numbers, it does not upload.
    analytics_http_timeout_seconds: int = 30
    #: Default look-back window for the historical performance chart, in days.
    analytics_default_window_days: int = 30
    #: Hard ceiling on the number of daily points the history endpoint returns,
    #: so an over-wide window cannot turn into an unbounded query or payload.
    analytics_max_history_points: int = 180
    #: How many published targets / connected accounts a single refresh may pull
    #: before it stops, bounding one request's work and outbound API calls.
    analytics_max_refresh_targets: int = 200

    # --- AI model routing (Phase 12) ----------------------------------------
    #: How many times one model is attempted for a *transient* failure (a
    #: timeout or a 5xx) before the router moves on to the next model. A rate
    #: limit skips the retry and fails over immediately: retrying a throttled
    #: account just burns the same quota.
    ai_router_max_attempts: int = 2
    #: First backoff delay between two attempts at the same model. Each further
    #: attempt doubles it (exponential backoff).
    ai_router_backoff_base_seconds: float = 1.0
    #: Ceiling on that backoff, so a task never sleeps for long.
    ai_router_backoff_max_seconds: float = 8.0
    #: How often the worker re-probes every enabled model's availability. Fresh
    #: results also arrive from ordinary routing, so this only refreshes models
    #: that are not receiving traffic.
    ai_health_check_interval_seconds: int = 600
    #: Default local Ollama origin and model. Used when a registry row leaves
    #: ``base_url`` blank and as the documented local fallback.
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1"
    #: Wall-clock limit for a local model. Generous: Ollama may load a model
    #: into memory on the first call, and a local host has no per-token bill.
    ollama_timeout_seconds: int = 120

    # --- Notifications (Phase 11) -------------------------------------------
    #: How often the Celery Beat notification sweep runs. One tick dispatches
    #: pending emails, sends upcoming-publish reminders and warns about expiring
    #: tokens; each sub-sweep is idempotent, so the interval only bounds how
    #: promptly a notice appears.
    notifications_sweep_interval_seconds: int = 60
    #: How many notifications one sweep considers for email delivery. Bounds the
    #: transaction and the burst of messages a single tick can send.
    notifications_email_batch_size: int = 100
    #: How far ahead of a scheduled post the reminder fires. A reminder only
    #: makes sense if there is time to act on it, and only fires once per entry.
    notifications_reminder_lead_seconds: int = 3600
    #: Warn when a connected account's access token expires within this window.
    notifications_token_warning_days: int = 7

    # --- Rate limiting & hardening (Phase 13) -------------------------------
    #: Master switch for per-route rate limiting. Disabled in the test suite so
    #: the many requests one test file makes from a single address cannot trip a
    #: limit; the limiter's own tests turn it back on.
    rate_limit_enabled: bool = True
    #: Length of one fixed window. Every bucket counts hits inside this window.
    rate_limit_window_seconds: int = 60
    #: Credential endpoints (login, register, refresh, password reset). Tight,
    #: because each hit is a guess at a secret.
    rate_limit_auth_per_minute: int = 10
    #: AI generation. Bounded because every call spends money or quota, but
    #: looser than auth: a real user may draft several captions in a row.
    rate_limit_ai_per_minute: int = 30

    # --- Observability (Phase 15) -------------------------------------------
    #: Sentry DSN for error tracking. Empty (the default) disables it entirely,
    #: so local development and the test suite never ship events anywhere.
    sentry_dsn: str | None = None
    #: Fraction of requests traced for performance. 0.0 keeps a free tier quiet;
    #: unhandled exceptions are always captured whatever this is set to.
    sentry_traces_sample_rate: float = 0.0

    # --- Email --------------------------------------------------------------
    #: ``console`` logs the message (development); ``smtp`` delivers it.
    email_backend: str = "console"
    email_from_address: str = "no-reply@example.com"
    email_from_name: str = "One-Click Publisher"
    smtp_host: str = "localhost"
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_use_starttls: bool = True

    @field_validator("refresh_cookie_samesite")
    @classmethod
    def _validate_samesite(cls, value: str) -> str:
        allowed = {"lax", "strict", "none"}
        normalised = value.strip().lower()
        if normalised not in allowed:
            raise ValueError(f"refresh_cookie_samesite must be one of {sorted(allowed)}")
        return normalised

    @field_validator("email_backend")
    @classmethod
    def _validate_email_backend(cls, value: str) -> str:
        allowed = {"console", "smtp"}
        normalised = value.strip().lower()
        if normalised not in allowed:
            raise ValueError(f"email_backend must be one of {sorted(allowed)}")
        return normalised

    @field_validator("media_allowed_mime_types")
    @classmethod
    def _validate_media_mime_types(cls, value: str) -> str:
        """Normalise the allow-list so comparisons are case- and space-proof."""
        normalised = ",".join(part.strip().lower() for part in value.split(",") if part.strip())
        if not normalised:
            raise ValueError("media_allowed_mime_types must contain at least one MIME type")
        return normalised

    @field_validator("media_chunk_size_bytes")
    @classmethod
    def _validate_media_chunk_size(cls, value: int) -> int:
        # Below 256 KiB the resumable protocol costs more in round-trips than it
        # saves in retries, which defeats the point of chunking.
        if value < 256 * 1024:
            raise ValueError("media_chunk_size_bytes must be at least 262144 (256 KiB)")
        return value

    @field_validator("sentry_traces_sample_rate")
    @classmethod
    def _validate_sentry_sample_rate(cls, value: float) -> float:
        # Sentry rejects anything outside [0, 1] when it initialises.
        if not 0.0 <= value <= 1.0:
            raise ValueError("sentry_traces_sample_rate must be between 0 and 1")
        return value

    @field_validator("openai_temperature")
    @classmethod
    def _validate_openai_temperature(cls, value: float) -> float:
        # The OpenAI API rejects anything outside [0, 2]; catching it at boot
        # beats a 400 on every generation.
        if not 0.0 <= value <= 2.0:
            raise ValueError("openai_temperature must be between 0 and 2")
        return value

    @model_validator(mode="after")
    def _reject_insecure_production_config(self) -> Self:
        """Fail fast instead of booting production with a weak secret or cookie."""
        if self.environment != "production":
            return self
        if self.secret_key == INSECURE_SECRET_KEY or len(self.secret_key) < 32:
            raise ValueError(
                "SECRET_KEY must be replaced with a random value of at least 32 "
                "characters when ENVIRONMENT=production"
            )
        if not self.refresh_cookie_secure:
            raise ValueError("REFRESH_COOKIE_SECURE must be true when ENVIRONMENT=production")
        return self

    @property
    def cors_origins_list(self) -> list[str]:
        """CORS origins as a list (comma-separated env value)."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def media_allowed_mime_types_list(self) -> list[str]:
        """Accepted upload MIME types as a list (comma-separated env value)."""
        return [item.strip() for item in self.media_allowed_mime_types.split(",") if item.strip()]

    @property
    def media_root_path(self) -> Path:
        """Absolute path of the local media root.

        A relative ``MEDIA_ROOT`` is resolved against the current working
        directory (``backend/`` in every documented way of starting the app), so
        the same default works from a shell, from pytest, and from a container
        with an absolute ``MEDIA_ROOT``.
        """
        root = Path(self.media_root).expanduser()
        return root if root.is_absolute() else (Path.cwd() / root)


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (re-read by clearing the cache)."""
    return Settings()


settings: Settings = get_settings()
