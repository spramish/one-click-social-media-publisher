# API Documentation

Author: **Pramish Sharma**
Base URL (local): `http://localhost:8000`
API prefix: `/api/v1`
Interactive docs: `/docs` (Swagger UI), `/redoc` (ReDoc), `/openapi.json` (schema)

---

## Implemented endpoints

### Service (`GET /`)
Service metadata. Not part of the versioned API.

**200**
```json
{ "service": "One-Click Social Media Video Publisher", "docs": "/docs", "health": "/api/v1/health" }
```

---

### `GET /api/v1/health`
Liveness probe — the API process is up. No external dependencies are checked.

**200**
```json
{
  "status": "ok",
  "service": "One-Click Social Media Video Publisher",
  "environment": "development",
  "version": "0.1.0"
}
```

---

### `GET /api/v1/health/db`
Database readiness probe.

**200 (reachable)**
```json
{ "status": "ok", "database": "reachable" }
```

**200 (not reachable)** — the probe never raises, it reports the failure:
```json
{ "status": "error", "database": "unreachable", "detail": "connection refused ..." }
```

---

### `GET /api/v1/health/redis`
Redis readiness probe. Same shape as `/health/db` with a `redis` key.

**200**
```json
{ "status": "ok", "redis": "reachable" }
```

---

## Authentication (`/api/v1/auth`)

Token delivery: the **access token** is returned in the JSON body (short-lived — 30 min
by default) and is meant to be held in memory. The **refresh token** is delivered in an
`HttpOnly` cookie scoped to `/api/v1/auth` (name `ocp_refresh_token`) and is **never**
returned in a body for browser clients; `/refresh` and `/logout` also accept it in the
request body so CLIs and mobile apps can use the same endpoints.

All authenticated routes expect `Authorization: Bearer <access_token>`.

### `POST /api/v1/auth/register` → `201`
Create an account and sign in. A personal workspace is provisioned with the caller as its
`owner`; the email is normalised to lower case; the account starts **unverified**.

Request:
```json
{ "email": "a@b.com", "password": "correct-horse-battery", "full_name": "Ada", "workspace_name": "Ada's Studio" }
```
Response:
```json
{
  "access_token": "<jwt>",
  "token_type": "bearer",
  "expires_in": 1800,
  "user": { "id": "…", "email": "a@b.com", "full_name": "Ada", "is_active": true, "is_verified": false, "is_superuser": false, "created_at": "…", "last_login_at": null }
}
```
- `409` — email already registered.
- `422` — password shorter than `PASSWORD_MIN_LENGTH` (12) or malformed email.

### `POST /api/v1/auth/login` → `200`
Exchange credentials for tokens. Same body shape as `register`.
- `401` — **identical** response for unknown email, wrong password, and deactivated account
  (no account enumeration).

### `POST /api/v1/auth/refresh` → `200`
Rotate the refresh token. Reads the cookie, or `{"refresh_token": "…"}` in the body.
Returns a new access token and sets a new refresh cookie.
- `401` — missing/invalid token. Presenting an **already-rotated** token is treated as
  theft: every session in that rotation family is revoked before the `401` is returned.

### `POST /api/v1/auth/logout` → `200`
Revoke the session behind the presented refresh token. Works without a valid access token
and **always** returns `200` (it does not reveal whether the token existed). Clears the
refresh cookie.

### `POST /api/v1/auth/logout-all` → `200`
Revoke **every** session of the current user. Requires a valid access token. Clears the
refresh cookie. Body: `{"message": "Signed out of N session(s)."}`

### `GET /api/v1/auth/me` → `200`
The authenticated account (`UserPublic`). Never contains a hash or token.
- `401` — missing/invalid access token, or a revoked/expired session.

### `GET /api/v1/auth/sessions` → `200`
Active sessions for the current account, newest first. `current: true` marks the calling
session.
```json
[ { "id": "…", "user_agent": "…", "ip_address": "…", "created_at": "…", "last_used_at": "…", "expires_at": "…", "current": true } ]
```

### `DELETE /api/v1/auth/sessions/{session_id}` → `200`
Revoke a single device. A session belonging to another user (or already revoked) is
indistinguishable from a missing one — both return `404`.

### `POST /api/v1/auth/password/change` → `200`
Change the password of the signed-in account.
```json
{ "current_password": "…", "new_password": "…" }
```
Keeps only the calling session alive and revokes the rest. Returns how many sessions were
signed out.
- `400` — wrong current password, or the new password equals the old one.

### `POST /api/v1/auth/email/verification` → `202`
(Re)send the verification link for the signed-in account, invalidating any previous token.

### `POST /api/v1/auth/email/verify` → `200`
Confirm an address with the emailed one-time token (unauthenticated — the link is the proof).
```json
{ "token": "…" }
```
- `400` — unknown, already-used, or expired token.

### `POST /api/v1/auth/password/reset` → `202`
Request a reset link. The response is identical whether or not the address exists.
```json
{ "email": "a@b.com" }
```

### `POST /api/v1/auth/password/reset/confirm` → `200`
Complete a reset and revoke every existing session.
```json
{ "token": "…", "new_password": "…" }
```
- `400` — unknown/used/expired token, or the new password equals the old one.

---

## Workspaces (`/api/v1/workspaces`)

These endpoints prove the two authorization layers: **tenant isolation** (`404` for a
workspace the caller is not a member of) and **role-based access** (`403` for a missing
`resource:action` permission).

### `GET /api/v1/workspaces` → `200`
Every workspace the caller is an active member of, with the caller's role and effective
permissions.
```json
[ { "id": "…", "name": "Ada's Studio", "slug": "…", "created_at": "…", "is_owner": true, "role": "owner", "permissions": ["post:publish", "workspace:read", "…"] } ]
```

### `GET /api/v1/workspaces/{workspace_id}` → `200`
One workspace. Requires `workspace:read`. Non-members receive `404`.
- `404` — workspace missing, soft-deleted, or the caller is not an active member.
- `403` — membership exists but the role lacks `workspace:read` (should not happen — every
  seeded role grants it).

### `GET /api/v1/workspaces/{workspace_id}/members` → `200`
Members of this workspace only (scope comes from the resolver). Requires `workspace:read`.
```json
[ { "id": "…", "user_id": "…", "email": "…", "full_name": "…", "role": "editor", "status": "active", "joined_at": "…" } ]
```

### `DELETE /api/v1/workspaces/{workspace_id}/members/{member_id}` → `200`
Remove a membership. Requires `member:remove` (owner/admin only).
- `403` — role lacks `member:remove`.
- `404` — membership not found in this workspace.
- `409` — the owner cannot be removed.

### `GET /api/v1/workspaces/{workspace_id}/audit` → `200`
Read this workspace's **audit trail**, newest first. Requires `audit:read` (owner/admin
only) — the trail names accounts, addresses, and the actions they took, so non-members
receive `404` and members without the permission receive `403`. There is **no write
path**: an entry is produced by the action it describes, never by the API consumer.

Query: `limit` (1–200, default 50), `before` (ISO-8601 cursor — return entries created
strictly before this instant), `action` (prefix filter, max 80 chars, e.g. `auth.`).
`before` is the pagination cursor: pass the `created_at` of the last entry you saw to
fetch the next page without skipping rows written mid-read.
```json
{
  "items": [
    { "id": "…", "action": "auth.login.succeeded", "user_id": "…", "workspace_id": "…",
      "ip_address": "203.0.113.7", "user_agent": "Mozilla/5.0 …",
      "target_type": "session", "target_id": "…", "detail": null, "created_at": "…" } ],
  "count": 1, "limit": 50, "max_limit": 200
}
```
Action codes are dotted strings (`area.object.verb`) — for example `auth.registered`,
`auth.login.succeeded`, `auth.login.failed`, `auth.logout`, `auth.logout_all`,
`auth.password.changed`, `auth.password.reset`, `ai_model.created`, `ai_model.updated`,
`ai_model.deleted`.
A failed login against an unknown address is recorded too, but names no account.
- `403` — membership exists but the role lacks `audit:read`.
- `404` — workspace missing, soft-deleted, or the caller is not an active member.

---

## Media (`/api/v1/media`)

Every media route is scoped to a workspace and guarded by a media permission:
`media:read` (list/get/stats/capabilities), `media:upload` (the upload protocol), and
`media:delete` (delete). The workspace is resolved from the URL through
`require_permission`, so a workspace the caller is not an active member of — and any media or
upload id belonging to another tenant — answers **`404`, never `403`**.

`MediaFilePublic` never exposes a storage key or a filesystem path. Instead it carries
**signed, expiring URLs**:

```json
{
  "id": "…", "workspace_id": "…", "original_filename": "clip.mp4",
  "mime_type": "video/mp4", "size_bytes": 15161031,
  "duration_seconds": 5.0, "width": 1280, "height": 720,
  "status": "ready", "storage_backend": "local",
  "checksum_sha256": "…", "uploaded_by_id": "…",
  "created_at": "…", "updated_at": "…",
  "video_url": "…/files/original?expires=…&signature=…",
  "thumbnail_url": "…/files/thumbnail?expires=…&signature=…",
  "urls_expire_at": "…"
}
```

### `GET /api/v1/media/{workspace_id}/capabilities` → `200`
What this server's upload pipeline can do, read *before* offering an upload.
```json
{ "ffmpeg_available": true, "max_upload_bytes": 2147483648, "chunk_size_bytes": 5242880,
  "allowed_mime_types": ["video/mp4","video/quicktime","video/webm","video/x-matroska","video/x-msvideo"],
  "signed_url_ttl_seconds": 900, "thumbnail_max_width": 640 }
```

### `GET /api/v1/media/{workspace_id}/stats` → `200`
Aggregate storage usage for the workspace.
```json
{ "file_count": 3, "total_bytes": 18344921, "ready_count": 3, "failed_count": 0 }
```

### `GET /api/v1/media/{workspace_id}` → `200`
The library, newest first. Soft-deleted files are never listed.
Query: `status` (`pending|processing|ready|failed|deleted`), `search` (filename substring, max
255), `limit` (1–200, default 50), `offset` (default 0). Returns `[MediaFilePublic]`.

### `GET /api/v1/media/{workspace_id}/{media_id}` → `200`
One `MediaFilePublic` with freshly signed URLs.
- `404` — missing, soft-deleted, or another workspace's id.

### `POST /api/v1/media/{workspace_id}/uploads` → `201`
**Step 1 — declare.** Validates the MIME type, size, and filename *before any bytes move*.
A `replace_media_id` is resolved inside the caller's workspace (another tenant's id → `404`).
```json
{ "filename": "clip.mp4", "mime_type": "video/mp4", "size_bytes": 15161031, "replace_media_id": null }
```
Response (`UploadSessionPublic`): `id`, `declared_size_bytes`, `received_bytes: 0`, `offset: 0`,
`chunk_size_bytes`, `created_at`, `expires_at`.
- `413` — exceeds `MEDIA_MAX_UPLOAD_BYTES`.
- `415` — MIME type not on the allow-list.
- `422` — filename contains a path separator (validation error).

### `GET /api/v1/media/{workspace_id}/uploads/{upload_id}` → `200`
**Step 3 — resume.** Returns the session with `received_bytes`/`offset` so a client knows where to
continue. A finished or aborted session answers `404`; an expired one answers `410`.

### `PUT /api/v1/media/{workspace_id}/uploads/{upload_id}?offset={n}` → `200`
**Step 2 — transfer.** The raw chunk is the request body; `offset` must equal the bytes already
received. Returns the session with the new `offset`.
- `400` — empty body.
- `409` — the chunk does not continue the upload. The response carries
  **`X-Upload-Expected-Offset: <n>`**; resend from there.
- `409` — the chunk would push the upload past the size it declared.
- `413` — one request is larger than the server accepts (4 × the advertised chunk size).

### `POST /api/v1/media/{workspace_id}/uploads/{upload_id}/complete` → `201`
**Step 4 — finalise.** Verifies the received byte count against the declared size, hashes the
file, FFprobes it, generates a thumbnail, adopts the temporary file to its permanent key, and
persists the `MediaFile` row. Returns a `MediaFilePublic`.
- `409` — fewer bytes arrived than were declared.
- `422` — the bytes are not a readable video container (FFprobe reads the real file).
- `503` — FFmpeg/FFprobe is not available on this server.

### `DELETE /api/v1/media/{workspace_id}/uploads/{upload_id}` → `200`
**Step 5 — abort.** Discards the partial file and its session record.
Body: `{"message": "Upload aborted."}`. A finished/aborted session answers `404`.

### `DELETE /api/v1/media/{workspace_id}/{media_id}` → `200`
Soft-deletes the row and frees its bytes.
Body: `{"message": "Media file deleted."}`.
- `409` — a live post still references the file (`MediaInUseError`).
- `404` — missing or another workspace's id.

### `GET /api/v1/media/{workspace_id}/{media_id}/files/{variant}` → `200`
Serves the stored bytes. `variant` is `original` (the master file, `media_type` = its MIME type)
or `thumbnail` (`image/jpeg`). **Deliberately unauthenticated:** it requires `?expires=<unix>` and
`?signature=<hmac>` and is served only when the signature verifies — possession of a valid URL
*is* the authorization, and the URL stops working at `expires`.
- `403` — the signature is invalid or the link has expired.
- `404` — no such media object (including a tampered workspace segment), or no thumbnail.
- Response headers: `Cache-Control: private, no-store` (a replacement reuses the media id, so a
  cached copy would be stale rather than merely old).

---

## Accounts (`/api/v1/accounts`)

Connected social accounts for a workspace — Instagram, Facebook, YouTube, and TikTok — via each
platform's **official** OAuth 2.0 authorization-code flow. Every workspace route is guarded:
`account:read` (capabilities, list, get), `account:connect` (authorize, refresh), and
`account:disconnect` (disconnect). The workspace is resolved from the URL through
`require_permission`, so a workspace the caller is not an active member of — and any account id
belonging to another tenant — answers **`404`, never `403`**.

`SocialAccountPublic` never exposes a token. It carries identity, status, and credential
*metadata* only:

```json
{
  "id": "…", "workspace_id": "…", "platform": "youtube",
  "platform_account_id": "UC…", "display_name": "Ada's Channel", "username": "@ada",
  "avatar_url": "https://…", "status": "connected",
  "token_expires_at": "…", "token_expires_in_seconds": 3599,
  "needs_reconnect": false, "scopes": ["https://www.googleapis.com/auth/youtube.upload"],
  "connected_by_id": "…", "last_synced_at": "…", "created_at": "…", "updated_at": "…"
}
```

Error translation is centralised, so every failure has one documented status:

| Status | Meaning |
| --- | --- |
| `503` | This server has no client credentials for the platform (`OAuthNotConfiguredError`). |
| `404` | Unknown account, or another workspace's id. |
| `409` | The grant is unusable (no credential, undecryptable credential, or `needs_reconnect`). |
| `400` | Bad/expired/reused `state`, or the platform refused the authorization. |
| `502` | The platform was reachable but the call failed (`OAuthApiError`). |

### `GET /api/v1/accounts/{workspace_id}/capabilities` → `200`
Which platforms this server can connect, and how many are already connected here. The frontend
reads this before rendering the connect buttons, so a missing credential shows as "not
configured" rather than failing on click.
```json
{ "platforms": [ { "platform": "instagram", "display_name": "Instagram", "configured": true, "connected_count": 1 } ] }
```

### `POST /api/v1/accounts/{workspace_id}/{platform}/authorize` → `200`
Mint a single-use, expiring `state`, persist its SHA-256 digest (plus the encrypted PKCE verifier
where the platform uses PKCE), and return the platform's consent URL for the browser to visit.
```json
{ "platform": "tiktok", "authorization_url": "https://www.tiktok.com/v2/auth/authorize?…", "expires_at": "…", "state_ttl_seconds": 600 }
```
- `401` — no valid access token.
- `403` — the role lacks `account:connect`.
- `503` — this server has no client credentials for the platform.

### `GET /api/v1/accounts/{platform}/callback` → `302`
The browser redirect target. **Deliberately unauthenticated:** the `state` value is the
authorization, validated and consumed server-side. Success redirects to `OAUTH_SUCCESS_REDIRECT`
with `?connected=<platform>&accounts=<n>`; any failure redirects to `OAUTH_FAILURE_REDIRECT` with
`?platform=<platform>&error=<reason>`. It **always** answers with a redirect, never a JSON error.
- A reused, expired, or wrong-platform `state` is rejected (single-use enforced).
- This route is registered before `/{workspace_id}/{account_id}` so the literal `callback`
  segment is not swallowed by the two-segment pattern.

### `GET /api/v1/accounts/{workspace_id}` → `200`
The workspace's connected accounts, newest first. Disconnected (soft-deleted) accounts are not
listed.
Query: `platform` (`instagram|facebook|youtube|tiktok`), `status` (`connected|expired|revoked|error`).
Returns `[SocialAccountPublic]`.

### `GET /api/v1/accounts/{workspace_id}/{account_id}` → `200`
One `SocialAccountPublic`.
- `404` — missing or another workspace's id.

### `POST /api/v1/accounts/{workspace_id}/{account_id}/refresh` → `200`
Exchange the stored refresh token (or re-exchange the long-lived Meta token) for a fresh access
token and return the updated `SocialAccountPublic`.
- `409` — nothing to refresh with (no refresh token, or the stored credential can no longer be
  decrypted); the caller must reconnect.

### `DELETE /api/v1/accounts/{workspace_id}/{account_id}` → `200`
Revoke the grant upstream where the platform supports it, then destroy the stored credential.
The account row is soft-deleted (`status=revoked`) so historical post targets keep their reference.
```json
{ "message": "Account disconnected and the platform grant revoked.", "revoked_upstream": true }
```
- `revoked_upstream: false` — the platform does not support programmatic revocation (or it
  failed); the local disconnect still happened.

---

## Posts (`/api/v1/posts`)

Compose a post from an uploaded video and publish it — **everywhere at once** — to Instagram,
Facebook, YouTube, and TikTok. Every route is scoped to a workspace and guarded by a post
permission: `post:read` (capabilities, list, get, jobs), `post:create` (compose), and
`post:publish` (publish). The workspace is resolved from the URL through `require_permission`, so a
workspace the caller is not an active member of — and any post, job, or `social_account_id`
belonging to another tenant — answers **`404`, never `403`**.

Two rules shape every response:

- **Nothing is reported published until the platform confirms it.** `POST …/publish` only
  *dispatches*: it returns `202` with every target `queued`. A target turns `published` only once
  the worker records the platform's own terminal status, which the client reads back from the
  per-target `job`.
- **One platform's failure never cancels another.** Each destination is its own target with its
  own job, so a mixed outcome is visible as a `partially_published` post with a reason per failed
  target.

`PostPublic` carries a post and every destination it targets, each with its own execution state:

```json
{
  "id": "…", "workspace_id": "…", "media_file_id": "…", "created_by_id": "…",
  "title": "Launch teaser", "base_caption": "Big news today!",
  "status": "publishing", "published_at": null,
  "targets": [
    {
      "id": "…", "post_id": "…", "social_account_id": "…", "platform": "youtube",
      "status": "queued", "caption": "Big news today!", "title": "Launch teaser",
      "remote_post_id": null, "remote_url": null, "published_at": null,
      "error_message": null,
      "job": {
        "id": "…", "status": "queued", "attempt_count": 0, "max_attempts": 5,
        "celery_task_id": "…", "scheduled_for": "…", "started_at": null,
        "finished_at": null, "next_retry_at": null, "error_class": null,
        "error_message": null, "created_at": "…", "updated_at": "…"
      },
      "created_at": "…", "updated_at": "…"
    }
  ],
  "created_at": "…", "updated_at": "…"
}
```

Error translation is centralised, so every failure has one documented status:

| Status | Meaning |
| --- | --- |
| `404` | Unknown post, job, or media — or another workspace's id. |
| `409` | The video is not `ready` yet, or *every* selected destination was rejected so nothing was dispatched (the message lists the reasons). |
| `422` | No destinations were selected. |
| `400` | A generic publishing-domain error. |

### `GET /api/v1/posts/{workspace_id}/capabilities` → `200`
Every platform's documented publishing limits, read by the composer *before* validating. This keeps
the publishers the single source of truth — the frontend never hard-codes a size or caption ceiling.
```json
[ { "platform": "instagram", "display_name": "Instagram", "max_size_bytes": 1073741824,
    "max_duration_seconds": 900, "min_width": 320, "min_height": 320,
    "max_caption_length": 2200, "requires_title": false, "supports_title": false,
    "allowed_mime_types": ["video/mp4", "video/quicktime"],
    "notes": "Reels look best at 9:16 (1080x1920). A professional account is required." } ]
```

### `GET /api/v1/posts/{workspace_id}/jobs/{job_id}` → `200`
One job with its full, append-only log trail (`PublishingJobDetail`). The logs keep the classified
error and the platform's **redacted** raw response per attempt — credential-shaped keys never appear.
Another workspace's job id answers `404`.
```json
{ "id": "…", "status": "succeeded", "attempt_count": 1, "max_attempts": 5,
  "workspace_id": "…", "post_target_id": "…", "platform": "youtube", "logs": [
    { "id": "…", "level": "info", "message": "Attempt 1 of 5 started.",
      "context": { "attempt": 1, "max_attempts": 5 }, "response_payload": null, "created_at": "…" } ] }
```

### `GET /api/v1/posts/{workspace_id}` → `200`
The workspace's posts, newest first, each with its targets and their jobs. Disconnected/soft-deleted
rows are never listed.
Query: `status` (`draft|scheduled|publishing|published|partially_published|failed|cancelled`),
`limit` (1–200, default 50), `offset` (default 0). Returns `[PostPublic]`.

### `GET /api/v1/posts/{workspace_id}/{post_id}` → `200`
One `PostPublic` with every target and its job.
- `404` — missing or another workspace's id.

### `POST /api/v1/posts/{workspace_id}` → `201`
**Compose a draft.** Records the video plus the base caption; nothing is published yet.
Requires `post:create`.
```json
{ "media_id": "…", "title": "Launch teaser", "base_caption": "Big news today!" }
```
Response: a `PostPublic` with `status: "draft"` and no targets.
- `404` — the video is not in this workspace.
- `409` — the video has not finished processing (`status != ready`).

### `POST /api/v1/posts/{workspace_id}/{post_id}/publish` → `202`
**Publish everywhere.** Validates the video, every selected account, and every selected platform,
then creates **one independent job per destination** and dispatches each to Celery. Requires
`post:publish`.
```json
{ "targets": [
    { "social_account_id": "…", "caption": "Optional per-platform override", "title": "Optional title" },
    { "social_account_id": "…" } ] }
```
Response: a `PostPublic` with every target `queued` (or `skipped` with a reason). Poll
`GET …/{post_id}` (or the per-target `job`) until every target is terminal; the post is `published`
only when all of them confirmed, otherwise `partially_published`.
- `422` — no targets were selected.
- `409` — the video is not ready, **or** every destination was rejected (the message lists each
  platform's reason); nothing was dispatched.
- A destination that fails validation is recorded `skipped` with its reason and does **not** affect
  the others. A duplicate publish (same video, account, and text) is refused with a reason.

---

## Scheduling (`/api/v1/posts/{workspace_id}/schedule`)

Queue a composed post for a future time and manage the queue. These routes share the `/posts`
prefix (a schedule *is* a post's "when") and are guarded by scheduling permissions: `schedule:read`
(list), `schedule:create` (queue + reschedule), and `schedule:cancel` (cancel). The workspace is
resolved from the URL through `require_permission`, so a workspace the caller is not an active
member of — and any `post_id` belonging to another tenant — answers **`404`, never `403`**.

Two rules shape every response:

- **Scheduling dispatches nothing.** `POST …/schedule` records the entry and its *pending*
  destinations and returns `201`. The post is published only when the worker's due-sweep reaches
  it, at which point it goes through the **identical** Phase 7 job path (same per-target jobs,
  idempotency keys, retries, dead-letter handling).
- **The schedule is durable.** It lives in PostgreSQL, so it survives an API or worker restart; a
  Celery Beat sweep (`scheduling.dispatch_due`) claims due rows atomically and fires each **exactly
  once**.

The schedule is deliberately small — *when* it fires (UTC, plus the zone the user picked), its
state, and the post it belongs to. Destinations are not repeated here; they live on the post's
targets.

```json
{
  "id": "…", "workspace_id": "…", "post_id": "…",
  "scheduled_for": "2030-01-01T09:30:00+00:00", "timezone": "Europe/London",
  "local_time": "2030-01-01T09:30:00+00:00", "status": "pending",
  "dispatched_at": null, "cancelled_at": null, "cancelled_by_id": null,
  "post_status": "scheduled", "post_title": "Launch teaser", "media_file_id": "…",
  "created_at": "…", "updated_at": "…"
}
```

`ScheduleStatus` is `pending | queued | dispatched | cancelled | missed`.

Error translation is centralised, so every failure has one documented status:

| Status | Meaning |
| --- | --- |
| `404` | Unknown schedule, post, or media — or another workspace's id. |
| `409` | The post is already live or already queued, the entry is no longer reschedulable/cancellable, the video is not `ready`, or every destination was rejected. |
| `422` | The chosen time is in the past, the IANA zone is unknown, or no destination was selected. |
| `400` | A generic scheduling/publishing-domain error. |

### `GET /api/v1/posts/{workspace_id}/schedule` → `200`
The workspace's calendar. `upcoming` (the queue, soonest first) is the default window; `past`
(fired, cancelled, or missed, newest first); `all`.
Query: `window` (`upcoming|past|all`), `status` (any `ScheduleStatus`; overrides the window),
`limit` (1–200, default 100), `offset` (default 0). Returns `[SchedulePublic]`.

### `POST /api/v1/posts/{workspace_id}/schedule` → `201`
**Queue a composed post.** Validates every destination through the publishing layer and records it
*pending* (no job), then writes the schedule. Requires `schedule:create`.
```json
{ "post_id": "…", "scheduled_for": "2030-01-01T09:30:00", "timezone": "Europe/London",
  "targets": [ { "social_account_id": "…", "caption": "Optional override", "title": "Optional" } ] }
```
A naive `scheduled_for` is read in `timezone`; an aware value is converted. Either way UTC is what
is stored. Response: the `SchedulePublic` with `status: "pending"` and the post `scheduled`.
- `404` — the post is not in this workspace.
- `409` — the post is already live (`publishing`/`published`/`partially_published`/`cancelled`) or
  already queued; the video is not ready; **or** every destination was rejected.
- `422` — a past `scheduled_for`, an unknown IANA zone, or no targets.

### `PATCH /api/v1/posts/{workspace_id}/schedule/{post_id}` → `200`
**Reschedule.** Move an entry that has not fired yet to a different time. Requires `schedule:create`.
```json
{ "scheduled_for": "2030-02-01T18:00:00", "timezone": "America/New_York" }
```
- `404` — the post is not scheduled in this workspace.
- `409` — the entry has already been dispatched or cancelled.
- `422` — a past `scheduled_for` or an unknown zone.

### `DELETE /api/v1/posts/{workspace_id}/schedule/{post_id}` → `200`
**Cancel.** Cancels the entry, its pending destinations, and any not-yet-terminal job, so nothing is
left for the sweep to fire. Requires `schedule:cancel`. Returns the `SchedulePublic` with
`status: "cancelled"`.
- `404` — the post is not scheduled in this workspace.
- `409` — the entry has already been dispatched or cancelled.

---

## AI assistant (`/api/v1/ai`)

Help draft the copy for a composed post. Every route is guarded by the single `ai:generate`
permission, which the Phase 2 seed grants to owner, admin, and editor (not viewer). The workspace
is resolved from the URL through `require_permission`, so a workspace the caller is not an active
member of — and any `post_id` belonging to another tenant — answers **`404`, never `403`**.

Two rules shape every response:

- **Nothing is published.** `POST …/generate` returns `201` with the stored suggestion;
  `POST …/history/{id}/accept` only records that the user chose it. Publishing always takes the
  ordinary, explicit composer action.
- **A missing model is a configuration state, not a crash.** With no `OPENAI_API_KEY` the
  capability reports `configured: false` and generation answers `503`; the UI says so up front.

The API key lives only on the server — it is never returned, and the client only ever sees a
boolean `configured` flag.

`AIContentKind` is `caption | hashtags | description | rewrite | suggestion`. Error translation is
centralised, so every failure has one documented status:

| Status | Meaning |
| --- | --- |
| `404` | Unknown `post_id` or `content_id` — or another workspace's id. |
| `422` | Unknown tone/style, an empty or over-long brief, a `rewrite` without a draft, or a model content-policy refusal. |
| `503` | No model is configured, or the provider is transiently unavailable (rate-limited, unreachable, or erroring). |
| `502` | The provider rejected the call for another reason (e.g. an invalid credential). |

### `GET /api/v1/ai/{workspace_id}/capability` → `200`
What the assistant can do on this server, and whether it is configured.
```json
{ "provider": "openai", "model": "gpt-4o-mini", "configured": true,
  "kinds": ["caption", "hashtags", "description", "rewrite", "suggestion"],
  "tones": ["professional", "casual", "…"], "styles": ["short", "storytelling", "…"],
  "max_brief_chars": 2000, "max_output_tokens": 800 }
```

### `POST /api/v1/ai/{workspace_id}/generate` → `201`
**Generate one suggestion and store it for review.** Nothing is published.
```json
{ "kind": "caption", "brief": "A 30-second clip of a sunrise hike",
  "tone": "inspirational", "style": "short", "platform": "instagram",
  "post_id": null, "draft": null }
```
`brief` is required; `draft` is required only when `kind` is `rewrite`. `tone`, `style`, and
`platform` are optional and shape the prompt. Response: the `AIContentPublic` with
`is_accepted: false`.
- `404` — `post_id` is not in this workspace.
- `422` — an unknown tone/style, an empty or over-long brief, a `rewrite` without a draft, or a
  policy refusal.
- `503` — no model configured, or a transient upstream failure.

### `GET /api/v1/ai/{workspace_id}/history` → `200`
Past suggestions, newest first. Query: `kind` (any `AIContentKind`), `limit` (1–200, default 50),
`offset` (default 0). Returns `[AIContentPublic]`.

### `POST /api/v1/ai/{workspace_id}/history/{content_id}/accept` → `200`
**Record that the user accepted a suggestion.** Publishes nothing. Returns the `AIContentPublic`
with `is_accepted: true`.
- `404` — the suggestion is not in this workspace.

An `AIContentPublic` looks like:
```json
{ "id": "…", "workspace_id": "…", "post_id": null, "created_by_id": "…",
  "kind": "caption", "model": "gpt-4o-mini", "prompt": "…", "output": "…",
  "tone": "inspirational", "is_accepted": false, "created_at": "…", "updated_at": "…" }
```

---

## Analytics dashboard (`/api/v1/analytics`)

Read how published videos are performing. Every figure comes from each platform's **own** API and
is stored as an immutable snapshot; **reading is separate from refreshing** — every route except
`refresh` reads stored snapshots only and never contacts a platform.

The rule the whole module preserves is **unavailable is not zero**. Every metric is nullable:
`null` means the platform did **not** report the figure, while `0` is a genuine zero. Each metric
set carries an `available` count (how many of its fields were provided), so the UI can render a
missing figure as **N/A** rather than a misleading `0`. Nothing is estimated or back-filled.

`GET` routes require `analytics:read` (seeded to **every** role, including viewer); `POST …/refresh`
requires `analytics:refresh` (owner/admin/editor). The workspace is resolved from the URL through
`require_permission`, so a foreign workspace — and any foreign target/account id — answers
**`404`, never `403`**.

Error translation is centralised, so every failure has one documented status:

| Status | Meaning |
| --- | --- |
| `404` | Unknown workspace, or a workspace the caller is not a member of. |
| `503` | The platform has no public metrics API (`metrics_available: false`), is rate-limited, or is transiently unavailable. |
| `502` | The platform rejected the credential (reconnect needed) or returned another error. |
| `400` | A malformed analytics request. |

A `MetricSet` carries `views | likes | comments | shares | reach | impressions | engagement`,
each `int | null`, plus `available`. `engagement` is `likes + comments + shares` and is `null`
only when all three are unavailable. `AudienceTotals` carries
`followers | follows | posts_count` (`int | null`) plus `available`.

### `GET /api/v1/analytics/{workspace_id}/capability` → `200`
What this server can report on, per platform.
```json
{ "platforms": [
    { "platform": "instagram", "display_name": "Instagram", "metrics_available": true,
      "connected_accounts": 2 } ],
  "default_window_days": 30, "max_history_points": 180, "max_refresh_targets": 200 }
```
`metrics_available` is `false` for a platform with no public metrics provider.

### `GET /api/v1/analytics/{workspace_id}/overview` → `200`
Headline figures. Reads stored snapshots only; it never contacts a platform.
```json
{ "published_post_count": 12, "published_target_count": 20, "tracked_target_count": 18,
  "totals": { "views": 15320, "likes": 940, "comments": 85, "shares": 30, "reach": null,
              "impressions": null, "engagement": 1055, "available": 5 },
  "audience": { "followers": 4821, "follows": null, "posts_count": 210, "available": 2 },
  "last_refreshed_at": "2026-11-18T18:00:00Z" }
```

### `GET /api/v1/analytics/{workspace_id}/platforms` → `200`
One row per platform with content, for comparison. Returns `[PlatformPerformancePublic]`:
`platform`, `display_name`, `metrics_available`, `published_target_count`,
`tracked_target_count`, `totals` (a `MetricSet`), `last_refreshed_at`.

### `GET /api/v1/analytics/{workspace_id}/accounts` → `200`
Account-level performance. Returns `[AccountPerformancePublic]`: `social_account_id`, `platform`,
`display_name`, `username`, `status`, `published_target_count`, `audience` (an `AudienceTotals`),
`audience_retrieved_at`, `totals` (a `MetricSet`).

### `GET /api/v1/analytics/{workspace_id}/targets` → `200`
Each published destination with its **latest** stored metrics. Query: `platform`, `limit`
(1–200, default 50), `offset` (default 0). Returns `[TargetPerformancePublic]`: `post_target_id`,
`post_id`, `platform`, `remote_post_id`, `remote_url`, `published_at`, and `snapshot`
(`{ "metrics": MetricSet, "retrieved_at": "…" }`, or `null` when the target has never been
refreshed — the destination is still listed, its metrics simply unavailable).

### `GET /api/v1/analytics/{workspace_id}/history` → `200`
A daily series for the charts, oldest day first. Query: `days` (window, capped at
`max_history_points`), `platform`. Each day keeps only that day's **newest** snapshot per target,
so several refreshes in one day do not multiply a target's numbers. Returns `[HistoryPointPublic]`:
`day` (`YYYY-MM-DD`), `samples`, `metrics` (a `MetricSet`).

### `POST /api/v1/analytics/{workspace_id}/refresh` → `200`
**The only route that contacts a platform.** Reads each published target and connected account from
its platform and **appends** a new snapshot per destination. Requires `analytics:refresh`. It never
raises for one destination's failure — those are collected in `errors` and the sweep continues.
```json
{ "post_targets_refreshed": 18, "post_targets_skipped": 2,
  "accounts_refreshed": 4, "accounts_skipped": 0, "snapshots_written": 22,
  "errors": ["TikTok: TikTok rejected the credentials while trying to read metrics. Reconnect the account."] }
```
- `502` — a credential was rejected and the account must be reconnected.
- `503` — a platform is rate-limited or transiently unavailable.

---

## Notifications (`/api/v1/notifications`)

The bell, the inbox, and the email preferences. A notification is addressed to the signed-in
**user**, not a workspace, so these routes are authenticated but **not** workspace-scoped: every
query is filtered by the caller's own `user_id`, and a foreign id answers **`404`, never `403`**.

Two rules hold throughout: the **in-app row is the source of truth** (the email preferences only
decide whether a *copy* is also mailed, so switching one off never hides something from the bell),
and **reads never contact a platform or a mail server** — email is delivered by the background sweep
(`notifications.sweep`).

`is_read` is derived from `read_at`. `type` is one of `publish_success`, `publish_failure`,
`schedule_reminder`, `token_expiring`, `model_fallback`, `subscription`, `system`.

### `GET /api/v1/notifications` → `200`
A page of the caller's notifications, newest first. `total` counts rows matching the filters;
`unread_count` is the caller's **overall** unread figure, so the badge stays correct however the
inbox is filtered. Query: `unread_only` (bool), `type`, `limit` (1–100, default 50), `offset`.
```json
{ "items": [
    { "id": "…", "workspace_id": "…", "type": "publish_success",
      "title": "Published to Facebook", "body": "Your video is live.", "payload": { "post_id": "…" },
      "is_read": false, "read_at": null, "created_at": "2026-11-21T09:00:00Z" } ],
  "total": 1, "unread_count": 1, "limit": 50, "offset": 0 }
```

### `GET /api/v1/notifications/unread-count` → `200`
Just the badge number — cheaper than loading the inbox.
```json
{ "count": 3 }
```

### `POST /api/v1/notifications/read-all` → `200`
Mark every unread notification read.
```json
{ "updated": 3 }
```

### `POST /api/v1/notifications/{notification_id}/read` → `200`
Mark one notification read (idempotent). Returns the updated `Notification`. A foreign id is `404`.

### `DELETE /api/v1/notifications/{notification_id}` → `204`
Dismiss one notification. A foreign id is `404`.

### `GET /api/v1/notifications/preferences` → `200`
The caller's email opt-outs, created with all four flags `true` on first access.
```json
{ "email_publish_success": true, "email_publish_failure": true,
  "email_schedule_reminder": true, "email_token_expiring": true,
  "updated_at": "2026-11-21T09:00:00Z" }
```

### `PUT /api/v1/notifications/preferences` → `200`
A partial update — only the flags present are changed. These gate the **email copy** only.
```json
// request
{ "email_publish_failure": false }
```

---

## AI models (`/api/v1/ai/{workspace_id}`)

The free AI-model registry and the failover history behind the Phase 9 assistant. These routes
configure `app/services/ai_router.py`: the router walks the enabled models in `priority` order and
fails over automatically. Every route requires `ai:manage` (the `owner` and `admin` system roles).

Two rules hold throughout. **The registry is instance-wide** — model configuration governs the whole
server — but the API's only authorization primitive is a workspace membership, so the workspace in
the path authorizes the caller without partitioning the registry. And **no route ever returns or
accepts a credential**: a model names the *environment variable* holding its key (`credential_env`),
and `credential_ready` is a live boolean meaning "that variable resolves in this process right now".

### `GET /api/v1/ai/{workspace_id}/models` → `200`
The registry in router order (lowest `priority` first). Query: `enabled_only` (bool, default false).
```json
[ { "id": "…", "name": "groq-llama", "display_name": "Groq Llama 3.1",
    "kind": "openai_compatible", "model": "llama-3.1-8b-instant",
    "base_url": "https://api.groq.com/openai/v1", "credential_env": "GROQ_API_KEY",
    "credential_ready": true, "priority": 10, "enabled": true, "supports": null,
    "timeout_seconds": 60, "max_output_tokens": 800, "temperature": 0.8,
    "status": "healthy", "last_checked_at": "…", "last_ok_at": "…",
    "consecutive_failures": 0, "last_error": null,
    "created_at": "…", "updated_at": "…" } ]
```

### `POST /api/v1/ai/{workspace_id}/models` → `201`
Register a model. `409` when the slug (`name`) is taken, `422` for a malformed body. `base_url` may
be blank — the service fills in the default for the kind (`OLLAMA_BASE_URL` / `OPENAI_BASE_URL`);
`credential_env` is omitted for a model that needs no key (Ollama).
```json
// request
{ "name": "groq-llama", "display_name": "Groq Llama 3.1", "kind": "openai_compatible",
  "model": "llama-3.1-8b-instant", "base_url": "https://api.groq.com/openai/v1",
  "credential_env": "GROQ_API_KEY", "priority": 10 }
```

### `PATCH /api/v1/ai/{workspace_id}/models/{model_id}` → `200`
A partial update — only the fields present are applied (so reordering the failover list is a
sequence of `{"priority": n}` calls, and taking a model out of service is `{"enabled": false}`).
`credential_env: ""` clears the reference. Returns the updated `AIModelPublic`. `404` / `409` as above.

### `DELETE /api/v1/ai/{workspace_id}/models/{model_id}` → `204`
Remove a model. Its past attempts are kept (`ai_task_attempts.model_id` is `SET NULL`, and the row
keeps the denormalised `model_name`), so the history still names it. `404` when unknown.

### `POST /api/v1/ai/{workspace_id}/models/health-check` → `200`
Probe every enabled model **now** (the same sweep the `ai.health_sweep` Celery Beat task runs) and
refresh each one's `status`.
```json
{ "checked": 2, "healthy": 1, "unhealthy": 1 }
```

### `GET /api/v1/ai/{workspace_id}/models/performance` → `200`
Success rate and average latency per model, aggregated from the attempt log. Query: `window_days`
(1–365, default 30).
```json
[ { "model_name": "groq-llama", "total": 12, "succeeded": 11, "failed": 1,
    "success_rate": 0.916667, "avg_latency_ms": 840 } ]
```

### `GET /api/v1/ai/{workspace_id}/tasks` → `200`
Recent routed tasks, newest first, each collapsed from its attempts. Query: `limit` (1–200, default
50), `offset`.
```json
{ "items": [
    { "task_id": "…", "kind": "caption", "status": "succeeded", "attempts": 2,
      "model_name": "ollama-llama", "created_at": "…", "finished_at": "…" } ],
  "total": 1, "limit": 50, "offset": 0 }
```

### `GET /api/v1/ai/{workspace_id}/tasks/{task_id}` → `200`
The full failover chain for one task — which models were tried, in what order, with latency and the
error each produced. An unknown task answers an empty page, not `404`.
```json
{ "items": [
    { "id": "…", "task_id": "…", "model_id": "…", "model_name": "groq-llama",
      "kind": "caption", "status": "failed", "attempt": 1, "latency_ms": null,
      "error_class": "AIRateLimitError", "error": "…", "created_at": "…" },
    { "id": "…", "task_id": "…", "model_id": "…", "model_name": "ollama-llama",
      "kind": "caption", "status": "succeeded", "attempt": 2, "latency_ms": 2310,
      "error_class": null, "error": null, "created_at": "…" } ],
  "total": 2, "limit": 100, "offset": 0 }
```

---

## Planned endpoints (later phases)

No endpoints are deferred at present; every route listed above is implemented. The AI-model
registry (`/api/v1/ai/{workspace_id}`) is Phase 12 and the workspace audit trail
(`/api/v1/workspaces/{workspace_id}/audit`) is Phase 13; later phases (testing and
deployment) add no new API surface.

---

## Conventions

- **Versioning:** breaking changes ship as `/api/v2`, never silently.
- **Errors:** FastAPI's standard error envelope (`{"detail": ...}`); domain errors get
  explicit status codes.
- **Auth (Phase 3, implemented):** `Authorization: Bearer <access_token>` on all
  non-public routes. The refresh token travels only in the `HttpOnly` cookie and is rotated
  on every use.
- **OAuth (Phase 6, implemented):** the `/accounts/{platform}/callback` route is intentionally
  unauthenticated — the single-use, expiring `state` is the authorization. Connector tokens are
  stored AES-256-GCM encrypted and are never returned in any response.
- **Publishing (Phase 7, implemented):** `POST …/{post_id}/publish` dispatches one independent job
  per destination and returns `202`; a target is only `published` once the platform confirms it.
  Duplicate publishes are refused via a content-derived idempotency key, and transient failures are
  retried with exponential backoff (honouring `Retry-After`) before being dead-lettered.
- **Scheduling (Phase 8, implemented):** `POST …/schedule` records the entry and its *pending*
  destinations and dispatches nothing; a Celery Beat sweep fires due entries through the same
  publishing path, so a scheduled publish gets the identical jobs, idempotency keys, retries, and
  dead-letter handling. The `scheduled_for` instant is stored in UTC and the caller's IANA zone is
  validated and stored alongside it, so `local_time` is always the wall-clock time the user picked.
- **AI assistant (Phase 9, implemented):** `POST /api/v1/ai/{ws}/generate` stores a suggestion and
  returns `201`; nothing is dispatched and accepting a suggestion is a record-only action.
  Generation is a synchronous, user-triggered call behind a swappable provider, and a missing model
  answers `503` rather than crashing. The provider API key is server-side only.
- **Analytics (Phase 10, implemented):** every metric is nullable — `null` means the platform did
  **not** report it and is shown as **N/A**, never `0`. All `GET` routes read stored snapshots only
  (`analytics:read`); `POST …/refresh` is the single network path (`analytics:refresh`) and appends
  a timestamped snapshot per destination, reporting one platform's failure in `errors` without
  aborting the sweep. A platform with no public metrics API reports `metrics_available: false`.
- **Security hardening (Phase 13, implemented):** every response carries `X-Content-Type-Options:
  nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Permissions-Policy`, and
  `Cross-Origin-Opener-Policy: same-origin`; a deny-by-default `Content-Security-Policy` is added
  everywhere except the interactive docs. Credential and AI-generation routes are rate-limited per
  caller and answer **`429` with `Retry-After`** when exceeded. A state-changing request
  (`POST`/`PUT`/`PATCH`/`DELETE`) whose `Origin` matches neither a configured CORS origin nor the
  request host is refused with **`403` Cross-origin request refused**.
- **Audit trail (Phase 13, implemented):** security-relevant actions are appended to an append-only
  `audit_logs` table on the caller's own transaction (same commit as the change) and read back
  newest-first through `GET /api/v1/workspaces/{workspace_id}/audit` (owner/admin only). Entries are
  never updated or deleted by the API.
- **Time:** all timestamps are ISO-8601 UTC; the frontend renders the user's time zone. A schedule
  also carries `local_time`, the same instant rendered in its own `timezone`.
