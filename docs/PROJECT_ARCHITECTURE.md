# Project Architecture

Author: **Pramish Sharma**
Last updated: Phase 15 — Production Deployment

---

## 1. High-level overview

```
+---------------------+        HTTPS/JSON        +----------------------+
|  Next.js frontend   |  --------------------->  |   FastAPI backend    |
|  (browser, :3000)   |  <---------------------  |   (uvicorn, :8000)   |
+---------------------+                          +----------+-----------+
                                                            |
                        +-----------------------------------+-----------------------------------+
                        |                                   |                                   |
                +-------v--------+                 +--------v--------+                 +--------v--------+
                |  PostgreSQL 16 |                 |     Redis       |                 |  Object storage |
                |  (durable data)|                 | (broker/cache)  |                 |  (local/MinIO)  |
                +----------------+                 +--------+--------+                 +-----------------+
                                                            |
                                                   +--------v---------+
                                                   |  Celery workers  |
                                                   | (publishing jobs)|
                                                   +------------------+
```

The frontend never talks to the database directly. All mutations go through the
FastAPI backend, which owns validation, authorization, and persistence.

---

## 2. Backend architecture

| Package              | Responsibility |
| -------------------- | -------------- |
| `app/core/config.py`  | Typed settings from environment (`pydantic-settings`) |
| `app/core/logging.py` | Structured JSON logging for all services |
| `app/core/security.py` | Password hashing (bcrypt), JWT issue/verify, opaque-token generation + SHA-256 digests |
| `app/core/crypto.py` | AES-256-GCM envelope encryption for OAuth tokens at rest; HKDF-SHA256 key derivation |
| `app/core/permissions.py` | Permission code catalogue (`resource:action`) used by route guards |
| `app/core/rate_limit.py` | In-house fixed-window request limiter: a Redis counter store with an in-process fallback, and the `rate_limit(bucket, …)` FastAPI dependency factory |
| `app/core/middleware.py` | Global `SecurityHeadersMiddleware` (CSP + hardening headers, prod-only HSTS) and `OriginGuardMiddleware` (the CSRF origin check) |
| `app/db/session.py`   | SQLAlchemy engine, `SessionLocal`, `session_scope()`, `get_db()` |
| `app/db/base.py`      | Declarative `Base` + `MetaData` naming convention, used by every model and Alembic |
| `app/db/enums.py`     | 18 domain enums (`str, Enum`), stored as their values |
| `app/db/types.py`     | `enum_column()` — `VARCHAR(32)` + named `CHECK` factory |
| `app/db/mixins.py`    | `UUIDPrimaryKeyMixin`, `TimestampMixin`, `SoftDeleteMixin`, `TenantMixin` |
| `app/models/`         | 25 ORM models; `__init__.py` registers them all on `Base.metadata` |
| `app/schemas/`        | Pydantic request/response models (`auth`, `workspace`, `media`, `account`, `post`, `schedule`, `ai`, `ai_model`, `analytics`, `notification`, `audit`) |
| `app/services/`       | Business logic: `auth` (credentials, sessions, tokens), `workspaces` (RBAC), `email`, `media` (upload sessions, library), `storage` (local objects + signed URLs), `video` (FFprobe/FFmpeg), `accounts` (OAuth connect/refresh/disconnect), `oauth/*` (per-platform providers), `publishing` (the "Publish Everywhere" orchestration + reliability layer), `publishers/*` (per-platform publishing adapters), `scheduling` (the durable schedule queue + the due-sweep that feeds the publishing layer), `ai_content` (prompt engineering + the generation/history/accept orchestration), `ai/*` (the provider abstraction over a model vendor's REST API), `analytics` (performance reads, aggregation, and the refresh sweep), `metrics/*` (per-platform read-only metrics providers), `notifications` (the user-addressed delivery core: in-app inbox, per-user email preferences, and the three background sub-sweeps), `audit` (the append-only audit trail: the transactional `record(...)` write path and the paged `list_events(...)` read path), and `ai_router` (the model registry + the priority-ordered failover router that every AI call flows through) |
| `app/api/deps.py`     | FastAPI dependencies: `get_current_session` → `get_current_user` → workspace + permission |
| `app/api/cookies.py`  | `HttpOnly` refresh-token cookie helpers |
| `app/api/v1/`         | Versioned HTTP API. Routers registered in `router.py` |
| `app/worker.py`       | Celery application (broker = Redis) + `publishing.publish_target`, `scheduling.dispatch_due`, `notifications.sweep` and `ai.health_sweep` (each on a Celery Beat schedule), and `analytics.refresh_workspace` tasks |
| `app/main.py`         | FastAPI app factory, the middleware chain (CORS → security headers → origin guard → GZip), lifespan |

Guiding rules:

- **Layered:** router → service → repository/model. Routers stay thin.
- **Versioned API:** everything lives under `/api/v1` so breaking changes are safe.
- **Long-running work never blocks a request:** publishing runs on Celery workers.
- **Failures are explicit:** health probes report `error` instead of silently passing.

---

## 3. Frontend architecture (Phase 4–7)

| Path                     | Responsibility |
| ------------------------ | -------------- |
| `app/layout.tsx`          | Root layout: provider stack (`ThemeProvider` → `AuthProvider` → `Toaster`), metadata template, inline anti-flash theme script |
| `app/page.tsx`            | Marketing landing page with auth-aware CTAs |
| `app/(auth)/**`           | Public auth routes: login, register, forgot-password, reset-password, verify-email |
| `app/(dashboard)/layout.tsx` | Wraps every authenticated route in `DashboardShell` |
| `app/(dashboard)/**`      | Dashboard, accounts, upload, compose, schedule, history, analytics, assistant, settings, billing |
| `components/ui/*`         | 19 design-system primitives (button, card, badge, input, select, alert, table, tabs, dropdown, toast, …) |
| `components/app-shell/*`  | `DashboardShell`, `Sidebar` (grouped nav + mobile slide-over), `Topbar` (workspace switcher, notification bell, theme toggle, account menu), `nav.ts` |
| `components/auth/*`       | Auth layout + forms (login, register, forgot/reset password, verify email, password input) |
| `components/settings/*`   | Settings tabs (account, password, sessions, notifications, system) |
| `components/notifications/*` | `NotificationBell`: the top-bar bell with an unread badge (polled), an inbox dropdown (newest first) with per-item mark-read + dismiss, **mark all read**, and click-through to the relevant page |
| `components/media/*`      | Media UI: `UploadPanel` (drag-and-drop, preview, chunked progress) and `MediaLibrary` (stats, search, grid, preview dialog, replace, delete) |
| `components/accounts/*`   | `AccountsManager`: per-platform connect cards (with a "not configured" state), the connected-account list (status, granted scopes, token expiry, health), Refresh / Reconnect / Disconnect controls, and the OAuth callback-result toast |
| `components/posts/*`      | `PublisherComposer`: the `/compose` wizard — pick a ready video, per-platform caption/title editors driven by each platform's live capability (character limits, media rules, title support), target-account selection, then either a "Publish Everywhere" run with per-platform job progress **or** a "Schedule for later" run that queues the post (both share one destination-validation step), plus a **Draft with AI** panel that fills the base caption from a short brief |
| `components/schedule/*`   | `SchedulingCalendar` (the `/schedule` page: upcoming/past tabs, day-grouped entries, inline reschedule + cancel) and the shared `ScheduleTimeField` (`datetime-local` + an IANA zone picker) used by both the calendar and the composer |
| `components/ai/*`         | `AIAssistant`: the `/assistant` page — a kind/brief/tone/style/platform generator, an editable result with copy + accept, and a suggestion history. A missing model is reported up front, and nothing here publishes |
| `components/*.tsx`        | Shared page pieces: brand, page header, empty state, module placeholder, platform icon, `RequireAuth` |
| `hooks/use-toast.ts`      | Module-level toast store so a toast can be raised from non-React code (e.g. the API client) |
| `lib/api.ts`              | The only place that talks to the API: typing, `Authorization: Bearer`, 401 → refresh → retry once |
| `lib/media-upload.ts`     | The client half of the resumable protocol: client-side pre-validation, declare → chunked `PUT` → `409` resync → complete → abort, with per-chunk retry |
| `lib/auth-context.tsx`    | Session state, login/register/logout, workspace list + active workspace, `hasPermission(code)` |
| `lib/theme-context.tsx`   | Dark-first theme, persisted in `localStorage`, toggles the `.dark` class |
| `lib/types.ts`, `lib/config.ts`, `lib/format.ts`, `lib/platforms.ts` | API DTOs, runtime config, formatters, platform catalogue |

Guiding rules:

- Server components by default; `"use client"` only where interactivity is required.
- A single design system (Tailwind tokens + shadcn/ui conventions) shared across every page.
- All backend calls go through `lib/api.ts` — no page hand-rolls tokens or URLs.
- Purely presentational primitives stay dependency-free; the app adds no runtime packages
  beyond what Phase 1 installed.
- A page whose backend does not exist yet renders a **phase-labelled placeholder** describing
  what will be built. Never mock data, fake charts, or simulated integrations.

---

## 4. Configuration & secrets

- All configuration is read from environment variables; `.env` is git-ignored.
- `.env.example` documents every variable.
- Secrets (OAuth client secrets, AI keys, payment keys) stay on the backend only and
  are never exposed via `NEXT_PUBLIC_*`.

### Service hostnames

| Context | PostgreSQL host | Redis host |
| --- | --- | --- |
| Inside Docker Compose | `postgres` | `redis` |
| Native Windows (no Docker) | `localhost` | `localhost` |

---

## 5. Data flow (target end state)

1. User authenticates → backend issues access + refresh tokens.
2. User connects social accounts → OAuth tokens stored encrypted (Phase 6 — implemented;
   see §11).
3. User uploads a video → transferred in resumable chunks, stored on the local storage backend,
   inspected with FFprobe, thumbnailed with FFmpeg, and served back through signed URLs
   (Phase 5 — implemented; see §10).
4. User selects platforms and publishes → backend validates each platform and creates
   one independent job per platform (Phase 7 — implemented; see §12).
5. Celery workers publish via platform-specific adapters; each job records its own status
   (Phase 7 — implemented; see §12).
6. User can instead queue the post for a future time → the durable `scheduled_posts` row is
   written now and a Celery Beat sweep dispatches it through the *same* publishing path when it
   comes due (Phase 8 — implemented; see §13).
7. Results and analytics are surfaced in the dashboard, and outcomes (publish success/failure, an
   upcoming schedule, an expiring token) are delivered as in-app notifications with an optional
   emailed copy (Phase 11 — see §16).

Failure isolation is a core requirement: **one platform failing must never cancel another**.

---

## 6. Deployment topology (Phase 15 — implemented)

The deployable stack is `docker-compose.prod.yml` (seven services). Only **nginx** is public; the
application containers are non-root and built multi-stage, and migrations run in the backend's start
command.

- **nginx** terminates HTTPS (TLSv1.2/1.3) and routes `/api/` → backend, `/` → frontend, streaming
  large resumable uploads (`proxy_request_buffering off`, 2 GiB cap, 600 s timeouts).
- **PostgreSQL** and **Redis** run as compose services on a private network — never published to the
  host — with a named volume and a `pg_dump`-based backup script.
- **Celery** workers scale independently from the API (`--scale worker=N`); **Beat** drives the three
  periodic sweeps and stays a single replica.
- **Object storage** is still local disk behind `LocalStorage`; S3-compatible storage can drop in
  behind the same interface.
- **CI** (GitHub Actions) runs the backend suite against Postgres/Redis services and the frontend
  `typecheck` → `lint` → `test` → `build` before an image is built.

Full runbook: [`DEPLOYMENT.md`](DEPLOYMENT.md).

---

## 7. Data architecture (Phase 2)

The full schema — every column, constraint, index, referential action and the
ER diagram — lives in [`DATABASE_SCHEMA.md`](DATABASE_SCHEMA.md). This section
covers the *rules* the code follows.

### 7.1 Tenancy

`workspaces` is the tenant boundary. Every business table carries a
`NOT NULL workspace_id`, so a row cannot exist without a tenant, and deleting a
workspace removes the entire tenant subtree in one statement.

All tenant reads go through `TenantMixin.scoped(workspace_id)`. Building a
tenant query with a bare `select()` is a bug, not a style choice.

Two tables deliberately have **no** `workspace_id` — `oauth_credentials` and
`publishing_logs`. They are reachable only through their parent
(`social_accounts`, `publishing_jobs`), which already carries the tenant. This
keeps one authoritative copy of each fact.

### 7.2 Transaction boundaries

| Context | Entry point | Commit |
| --- | --- | --- |
| HTTP request | `get_db()` dependency | the route/service, explicitly |
| Celery task, script, test | `session_scope()` | automatically on clean exit |
| Test case | `db` fixture in `app/tests/conftest.py` | never — always rolled back |

`session_scope()` commits on success, rolls back on any exception, and always
closes. `get_db()` rolls back if the handler raises and closes in `finally`, but
does **not** commit — so a request can span several deliberate transactions.

### 7.3 Schema evolution

`alembic/env.py` reads the connection string from application settings and
imports `app.models`, so autogenerate diffs the live database against the real
models. Constraints carry deterministic names (`ix_`/`uq_`/`ck_`/`fk_`/`pk_`),
otherwise autogenerate cannot reliably emit `drop_constraint`.

Enums are `VARCHAR(32)` + a named `CHECK`, not PostgreSQL native `ENUM`: adding
a value is an additive constraint swap that runs inside a transaction, whereas
`ALTER TYPE ... ADD VALUE` cannot. Reference data (roles, plans) is seeded by an
idempotent migration rather than at application startup.

### 7.4 Testing strategy

Database guarantees are tested against **real PostgreSQL**, never mocks:
constraints that are not executed are not verified. `app/tests/conftest.py`
provisions a dedicated `publisher_test` database, rebuilds it once per session
by running the migrations, and wraps each test in a transaction that is rolled
back — so tests are isolated and the seeded reference rows are always present.
External integrations (platform APIs, OAuth, email, AI providers) are driven
through **injected `httpx` transports** and monkeypatched senders, so the whole
backend suite runs offline and deterministically.

**Phase 14** extends this to the background layer and the frontend: the **Celery** tasks are driven
**as objects** (their body runs in-process, no broker) with `session_scope` patched to the test's
session, and the **frontend** has a **Vitest** (jsdom + Testing Library) suite (`npm test`). See
`docs/DEVELOPMENT_PROGRESS.md` (Phase 14) and the test inventory in `docs/SETUP_GUIDE.md`.

---

## 8. Authentication & authorization (Phase 3)

### 8.1 Request guard chain

Every protected route is guarded by a chain of FastAPI dependencies
(`app/api/deps.py`), each layer narrowing the caller:

```
Authorization: Bearer <jwt>
        │  decode + verify signature/iss/aud/exp
        ▼
get_current_session   →  live auth_sessions row (not revoked, not expired)
        │
        ▼
get_current_user      →  active, non-deleted User
        │
        ▼
get_workspace_context →  active membership in {workspace_id}, else 404
        │
        ▼
require_permission(*) →  role.permissions ⊇ {code}, else 403
```

The access token is stateless, but the session is not: `get_current_session`
loads the `auth_sessions` row named by the token's `sid` claim, so logout,
password changes, and refresh rotation revoke an access token **immediately**
instead of letting it survive until it expires. `get_optional_user` is the
non-raising variant for endpoints that change behaviour when signed in.

### 8.2 Token model

| Token | Form | Lifetime | Storage | Rotation |
| --- | --- | --- | --- | --- |
| Access | HS256 JWT (`sub`/`sid`/`jti`/`typ`/`iss`/`aud`) | 30 min | client memory | new one minted on each refresh |
| Refresh | opaque 256-bit random string | 7 days | `HttpOnly` cookie (`ocp_refresh_token`), SHA-256 digest in DB | rotated on every use |

Passwords are hashed with **bcrypt** (work factor 12). Inputs longer than
bcrypt's 72-byte limit are pre-hashed with SHA-256 so a long passphrase is
accepted rather than silently truncated.

### 8.3 Refresh-token rotation & theft detection

Each login starts a rotation **family** (`auth_sessions.family_id`). Every
`/auth/refresh` revokes the presented row and inserts a successor linked via
`replaced_by_id`. If an already-rotated token is presented again — a strong
signal it was stolen — the service revokes **the entire family** before
returning `401`.

### 8.4 Authorization model

- **Tenant isolation** — `get_workspace_context` resolves the workspace through
  `TenantMixin.scoped` plus an active-membership check, and answers `404` (never
  `403`) when the caller is not a member, so the API never confirms the
  existence of a foreign tenant.
- **Role-based access** — permissions are `resource:action` codes stored in
  `roles.permissions` (seeded by migration `70ed893bdb70`). `require_permission`
  rejects a route with `403` when the caller's role lacks the code. A
  `superuser` bypasses the permission check but **not** tenant isolation.
- **Workspace permissions** — endpoints are exercised by
  `app/api/v1/endpoints/workspaces.py`, which lists workspaces/members and lets
  an owner/admin remove a member.

---

## 9. Frontend session & routing model (Phase 4)

### 9.1 Where the tokens live

| Token | Client storage | Why |
| --- | --- | --- |
| Access (30 min JWT) | **A module-level variable in `lib/api.ts`** | A page reload loses it; nothing on disk means an XSS payload cannot read a credential |
| Refresh (7 days, rotating) | **`HttpOnly` cookie set by the backend** | JavaScript can never read it; `credentials: "include"` attaches it |

`localStorage` holds only two non-secret values: the active workspace id and the theme.

### 9.2 Request lifecycle

```
api.*  ──►  request<T>()  ──►  fetch(credentials: "include", Authorization: Bearer <access>)
                                    │
                                    ├─ 2xx  ──► parsed JSON (204 → null)
                                    ├─ 401  ──► refreshAccessToken()  (single-flight)
                                    │            ├─ success ──► retry the original request ONCE
                                    │            └─ failure ──► unauthorizedHandler() → clear auth
                                    └─ 4xx/5xx ──► throw ApiError(status, detail)
```

`refreshInFlight` de-duplicates concurrent refreshes: ten parallel 401s trigger **one**
`POST /auth/refresh`, and all ten retry with the resulting token.

### 9.3 Session bootstrap and guards

On mount, `AuthProvider` calls `POST /api/v1/auth/refresh` to trade the cookie for a session
— this is how a page reload restores a signed-in user. `status` is `loading → authenticated |
anonymous`.

- `RequireAuth` renders a spinner while `status === "loading"` and redirects to
  `/login?next=<path>` when anonymous. It is a **UX guard only**.
- `DashboardShell` = `RequireAuth` → `Sidebar` + `Topbar` + content. Every
  `app/(dashboard)/*` route therefore inherits the guard from one place.
- Consequences for rendering: `/dashboard` and friends are client-rendered behind the guard,
  so their content is intentionally absent from the server-rendered HTML. Public routes
  (`/`, `/login`, …) do server-render their headings.

### 9.4 Error handling contract

The backend answers with FastAPI's `{"detail": …}` shape. `ApiError` normalises both a string
`detail` and a validation array into one readable message, and pages surface it inline in an
`Alert` or as a toast. Status codes are mapped to user-facing copy: `409` → "an account with
that email already exists", `401` → "incorrect email or password" (deliberately identical for
unknown email and wrong password, matching the backend).

---

## 10. Media pipeline (Phase 5)

### 10.1 Upload lifecycle

```
POST /media/{ws}/uploads        declare  ──validate MIME + size + filename──►  201 session(offset 0)
        │                                         (nothing stored yet)
        ▼
PUT  /media/{ws}/uploads/{id}?offset=n   ──append chunk──►  200 session(new offset)
        │   ✗ wrong offset ─► 409 + X-Upload-Expected-Offset: n'  ─► client reslices from n'
        │   ✗ chunk too big ─► 413        ✗ past declared size ─► 409
        ▼
POST /media/{ws}/uploads/{id}/complete  ──byte-count check, SHA-256, FFprobe, thumbnail,
        │                                    atomic adopt of the temp file, persist MediaFile row──► 201
        │   ✗ fewer bytes than declared ─► 409     ✗ not a readable video ─► 422     ✗ no FFmpeg ─► 503
        ▼
GET  /media/{ws}/{id}/files/{variant}?expires=&signature=   ── HMAC verified ──► stream (no session)
```

In-flight state lives as two files under `.uploads/` in the media root: a `.part` file that grows
with each chunk and a `.json` sidecar holding the declared size, received bytes, MIME type, and
expiry. `load_upload_session` hides another tenant's session behind `404` and discards an expired
one (`410`). Completing or aborting removes both files.

### 10.2 Storage abstraction

`LocalStorage` maps a server-built key to a path under `settings.media_root_path`
(`backend/var/media` by default). Keys are `{workspace_id}/{media_id}/source{ext}` and
`{workspace_id}/{media_id}/thumbnail.jpg`; `path_for()` rejects empty, absolute, backslash, or
`..` keys and any candidate outside the root. `get_storage()` is a FastAPI dependency, so tests
inject a temporary root with `app.dependency_overrides` and never touch real uploads. Only
`StorageBackend.LOCAL` is implemented; an S3/MinIO class can implement the same narrow surface
(`write_bytes`/`append_chunk`/`adopt`/`delete`/`path_for`) without any route or service change.

### 10.3 Signed URLs

`sign_media_url(media_id, variant)` returns `(expires_at, signature)` where the signature is
HMAC-SHA256 over the string `ocp-media-url-v1\n{media_id}\n{variant}\n{expires_at}` keyed by
`settings.secret_key`. `verify_media_signature` checks the expiry and compares in constant time
(`hmac.compare_digest`). Because the payload binds the media id, the variant, and the expiry, a
URL cannot be reused for another object, another variant, or after it lapses — which is why the
serving route needs no session. The default TTL is **900 s**; every read response mints fresh
URLs and reports `urls_expire_at` so the UI can warn.

### 10.4 FFmpeg as a soft dependency

`app/services/video.py` resolves the configured binaries with `shutil.which` (cached). If either
is missing, `require_ffprobe`/`require_ffmpeg` raise `FFmpegUnavailableError`, which the media
routes translate to **`503`** — a valid request the server cannot currently process, not a client
error. `GET …/capabilities` exposes `ffmpeg_available` so the frontend warns *before* an upload,
and a thumbnail that cannot be produced never fails an otherwise valid upload. Nothing is ever
recorded as `ready` with guessed metadata.

---

## 11. OAuth account connections (Phase 6)

### 11.1 Authorization-code flow

```
POST /api/v1/accounts/{ws}/{platform}/authorize        (account:connect)
        │  mint 256-bit state · store SHA-256(state) + encrypted PKCE verifier · 600 s TTL
        ▼
browser ──► platform consent screen ──► GET /api/v1/accounts/{platform}/callback?code&state
        │  (deliberately UNAUTHENTICATED — the single-use state *is* the authorization)
        │  consume state (mark consumed) ──► exchange code for tokens ──► fetch account info
        │  encrypt tokens (AES-256-GCM) ──► upsert social_accounts + oauth_credentials
        ▼
302 ──► OAUTH_SUCCESS_REDIRECT?connected=<platform>&accounts=<n>
    └─► OAUTH_FAILURE_REDIRECT?platform=<platform>&error=<reason>   (always a redirect)
```

Because the callback lands in a browser, it **always** answers with a redirect — never a JSON
error. `state` is single-use, workspace-bound, platform-bound, and expiring; the row keeps only
the SHA-256 digest, so a database leak cannot be replayed against the platform.

### 11.2 Provider abstraction

`app/services/oauth/base.py` defines an `OAuthProvider` ABC (authorization URL, callback URL,
PKCE, token exchange, refresh, revoke, user-info parsing). Concrete providers register
themselves in `app/services/oauth/__init__.py` (`get_provider`, `supported_platforms`):

| Provider | Base | Notes |
| --- | --- | --- |
| `FacebookProvider` / `InstagramProvider` | `MetaOAuthProvider` (`meta.py`) | Facebook Login + Graph v21.0; per-Page tokens from `/me/accounts`; long-lived exchange via `fb_exchange_token` (Meta issues **no** refresh token, so the long-lived token occupies the refresh slot) |
| `YouTubeProvider` | Google OAuth 2.0 | `access_type=offline` + `prompt=consent`; PKCE; `refresh_token` grant; revoke at `oauth2.googleapis.com/revoke` |
| `TikTokProvider` | Login Kit v2 | comma-separated scopes; **mandatory** PKCE; non-standard error envelope (`code: "ok"`); revoke at `/v2/oauth/revoke` |

The provider takes an injectable `httpx` client, so tests stub the network with
`httpx.MockTransport` and run deterministically offline. An unconfigured provider (missing
client credentials) raises `OAuthNotConfiguredError` → **`503`**: an operator problem, not a
caller problem.

### 11.3 Token storage & freshness

Tokens are sealed with AES-256-GCM (`app/core/crypto.py`); the key is derived via HKDF-SHA256
from `OAUTH_ENCRYPTION_KEY` (else `SECRET_KEY`). Ciphertext carries a version prefix
(`v1.<base64url(nonce‖ciphertext‖tag)>`) so the key format can rotate later. Authenticated
encryption means a tampered ciphertext or a value sealed under a different key both raise
`EncryptionError` → **`409`**.

`ensure_fresh_tokens` refreshes an access token `OAUTH_REFRESH_MARGIN_SECONDS` before it expires.
An account whose grant cannot be refreshed (no refresh token, undecryptable credential) is
flagged `needs_reconnect` and any attempt to use it answers **`409`**, prompting a fresh
authorization instead of a silent failure.

### 11.4 Rendering rules on the client

- Tokens are **never** sent to the browser: `SocialAccountPublic` exposes only
  `token_expires_at`, `token_expires_in_seconds`, and `needs_reconnect`.
- `POST …/authorize` returns the consent URL; the page sets `window.location.href` to it. The
  callback result is read from the query string once, surfaced as a toast, and the query string
  is scrubbed with `window.history.replaceState` so a reload does not repeat it.
- A platform whose credentials are absent renders as **"not configured"** (from
  `/capabilities`), so a missing `.env` value is visible before anyone clicks Connect.

External prerequisites — developer applications, redirect URIs, required scopes, app review,
publishing audits, and account eligibility — are documented in
[`OAUTH_SETUP.md`](OAUTH_SETUP.md).

---

## 12. Multi-platform publishing engine (Phase 7)

### 12.1 Publisher abstraction

`app/services/publishers/base.py` defines a `Publisher` ABC (capability declaration, caption/title
building, media + caption validation, and `publish()`), plus the value objects `PublisherCapability`,
`MediaAsset`, `PublishRequest`, and `PublishResult`. Concrete adapters register themselves in
`app/services/publishers/__init__.py` (`get_publisher`, `supported_platforms`) — the same registry
shape as the Phase 6 OAuth layer, and like it an unregistered platform fails loudly rather than
silently no-op'ing:

| Publisher | Base | Title | Success is polled to |
| --- | --- | --- | --- |
| `InstagramPublisher` | `MetaPublisher` (`meta.py`) | not supported | container → `FINISHED` → `media_publish` → permalink |
| `FacebookPublisher` | `MetaPublisher` (`meta.py`) | optional | video object → `ready` |
| `YouTubePublisher` | — | **required** | resumable upload → `uploadStatus: processed` |
| `TikTokPublisher` | — | not supported (caption *is* the title) | init → chunked `PUT` → `PUBLISH_COMPLETE` |

Each adapter takes an injectable `transport: ClassVar[httpx.BaseTransport | None]`, so tests stub
the network with `httpx.MockTransport` and run deterministically offline — exactly as the OAuth
providers do.

### 12.2 Validation & error taxonomy

Every adapter declares one `PublisherCapability` (size, duration, min resolution, caption length,
title support, allowed MIME types); `validate_media` / `validate_caption` run **before** any byte
is uploaded, so an oversized or unsupported video is rejected locally instead of failing at the
platform. Failures use a classified taxonomy whose base `PublishError` carries a `retryable` flag:
`PublishTransientError` (retry), `PublishRateLimitError` (retry, carries `retry_after`),
`PublishAuthError`, `PublishPermanentError`, `MediaValidationError`, and `CaptionValidationError`.

### 12.3 "Publish Everywhere" orchestration

`app/services/publishing.py` implements the flow, and it is deliberately **two steps**:

```
POST /api/v1/posts/{ws}                     compose  ──►  a draft post (video + captions)   ─► 201
        ▼
POST /api/v1/posts/{ws}/{post_id}/publish   validate user → connected accounts → media → each target
        │                                      ▼
        └── one independent publishing_jobs row per selected target (post_targets)
                     ▼
        each job dispatched to Celery (publishing.publish_target)  ──► 202
```

A target that cannot be validated (account missing, video not ready, media outside the platform's
rules) is recorded with its reason and **skipped**, and only the publishable targets are dispatched
— so `POST …/publish` answers `202` with the per-target outcome. Each job runs in its own Celery
task, so **a failure on one platform can never cancel another**.

### 12.4 Reliability layer

The orchestration owns the guarantees the platform adapters cannot:

- **Idempotency** — a publish is keyed by a content checksum (sha256 of workspace + video checksum
  + account + text, falling back to a deterministic tuple), so re-submitting the same video to the
  same account is refused rather than double-posted.
- **Retry with exponential backoff** — `base * 2 ** (attempt - 1)`, capped, honouring a platform's
  `Retry-After` when present; a job that exhausts `publishing_max_attempts` is **dead-lettered**.
- **Terminal-state short-circuit** — `run_job` returns immediately when the job is already
  terminal, so a redelivered Celery task cannot publish twice.
- **Success confirmation** — a target is marked published **only** when the platform reports its
  final success state (§12.1); an accepted upload is never treated as a published post.
- **Redaction** — credential-shaped keys are scrubbed from the persisted `publishing_logs` trail.
- **Aggregation** — the parent post becomes `published` only when every target is confirmed,
  otherwise `partially_published`.

### 12.5 Endpoints & the `/compose` page

Six routes live under `/api/v1/posts`, guarded by `post:read` / `post:create` / `post:publish` and
isolated by `TenantMixin.scoped` (a foreign workspace answers `404`, never `403`): platform
`capabilities`, `create` (compose, `201`), `publish` (Publish Everywhere, `202`), the post list and
detail, and a per-job status endpoint that returns the redacted log trail. The `/compose` page
(`components/posts/composer.tsx`) is the real UI on those endpoints: it reads each platform's live
capability to size the caption editors, and shows one progress row per target as its Celery job
advances.

Full request/response shapes, status codes, and the per-platform matrix live in
[`API_DOCUMENTATION.md`](API_DOCUMENTATION.md) and
[`DEVELOPMENT_PROGRESS.md`](DEVELOPMENT_PROGRESS.md).

---

## 13. Content scheduling (Phase 8)

### 13.1 Two halves: queue it now, dispatch it later

A schedule is the *when*, a post is the *what* (a unique constraint keeps exactly one
`scheduled_posts` row per post). `app/services/scheduling.py` owns the queue, and it deliberately
does **not** dispatch anything on request:

```
POST /api/v1/posts/{ws}/schedule              queue  (schedule:create)
        │  validate destinations via publishing.record_targets → store them PENDING (no job)
        │  write scheduled_posts row (pending) · post → scheduled
        ▼
   ── the entry now lives in PostgreSQL, so it survives an API or worker restart ──
        ▼
scheduling.dispatch_due   (Celery Beat, every settings.scheduling_sweep_interval_seconds)
        │  SELECT … FOR UPDATE SKIP LOCKED  the due `pending` rows (bounded by scheduling_batch_size)
        ├─ overdue > scheduling_missed_grace_seconds ─► status = missed, targets cancelled, post → draft
        └─ otherwise ─► publishing.queue_targets(...)  →  schedule → dispatched
```

### 13.2 Fires exactly once

The claim is a single `SELECT … FOR UPDATE SKIP LOCKED` inside the worker's transaction: two
overlapping ticks, or two workers, each take **disjoint** rows, and a row already locked by another
transaction is skipped rather than waited on. An entry is only ever selected while `pending`, so
between the atomic claim and the status change to `dispatched` there is no window in which a second
sweep can publish the same post. This is the scheduling counterpart of the Phase 7 `run_job`
terminal short-circuit that makes a re-delivered Celery message a no-op.

### 13.3 It reuses the publishing reliability layer

`publishing.publish_everywhere` was refactored into two composable pieces so the manual and
scheduled paths share one implementation:

- `record_targets(...)` — validate the user, video, accounts, and platforms, then persist the
  destinations as `pending` targets **with no job**. This is what `schedule_post` calls.
- `queue_targets(...)` — build the per-target jobs (content-derived idempotency keys, attempts
  counter) and dispatch each to Celery. This is what the due-sweep calls.

Consequently a scheduled publish cannot bypass retries, exponential backoff, rate-limit handling,
duplicate suppression, or dead-lettering: it *is* the Phase 7 path, just started later.

### 13.4 Time zones are explicit, never guessed

`scheduled_for` is persisted in **UTC** (`DateTime(timezone=True)`). The caller's IANA zone is
validated with the Python **stdlib** `zoneinfo` (no new dependency) and stored in
`ScheduledPost.timezone`; `SchedulePublic` returns both the UTC instant and `local_time` — the same
instant rendered in that zone — so the UI can show the wall-clock time the user actually picked.
`to_utc_instant` reads a naive input in the chosen zone and converts an aware one. `local_time`
falls back to UTC for a row whose stored zone is somehow unresolvable, so reading the calendar can
never fail.

### 13.5 Missed jobs and cancellation

- **Missed.** An entry that comes due more than `scheduling_missed_grace_seconds` after its time
  (the worker was down, the machine slept) is marked `missed`, its pending targets are cancelled,
  and a still-`scheduled` post reverts to `draft` so the user can simply re-queue it — a post fired
  hours late is worse than one the user is told to fix.
- **Unusable.** A post whose video or account became invalid between scheduling and dispatch is
  left un-dispatched (`missed`) for the operator rather than publishing something broken.
- **Cancel is total.** `cancel` cancels the entry, its pending destinations, and any
  not-yet-terminal job, so nothing is left behind for the sweep to pick up.

### 13.6 The `/schedule` page

`components/schedule/scheduling-calendar.tsx` is the real UI: an upcoming/past tab, entries grouped
by day (each day label rendered in the entry's own zone), a status badge, and inline reschedule
(through the shared `ScheduleTimeField`) and cancel controls. The composer
(`components/posts/composer.tsx`) offers **Publish Everywhere** or **Schedule for later** over one
shared destination-validation step.

---

## 14. AI content generation (Phase 9)

Help a user write the copy for a composed post — a caption, a hashtag set, a longer description, a
rewrite of existing text, or content ideas — tuned to a chosen platform, tone, and style. Like the
OAuth and publishing layers, the model vendor sits **behind an abstraction** so it is swappable and
testable offline.

### 14.1 Provider abstraction

`app/services/ai/base.py` defines `AIProvider` (`ABC`) with a class-level `transport: httpx.BaseTransport | None`
hook, an error taxonomy (`AINotConfiguredError`, `AITransientError`, `AIRateLimitError`,
`AIProviderError`, `AIContentRejectedError`, each carrying a `retryable` marker where relevant), and
two frozen value objects — `CompletionRequest` (system + prompt + limits) and `AICompletion` (text,
model, token usage). `app/services/ai/openai.py` is the one implementation
(`POST {base_url}/chat/completions`, `Authorization: Bearer <key>`), and
`app/services/ai/__init__.py` holds the registry (`get_provider`, `supported_providers`). This is the
identical shape used by `services/publishers/*` and `services/oauth/*`.

The value of the split: every test drives a `httpx.MockTransport` with **no network call**, the key
never leaves the provider, and a second vendor is a new module plus one registry line.

### 14.2 Prompts are the service's job

`app/services/ai_content.py` owns the prompt engineering — a system prompt, per-`AIContentKind`
instructions, and per-`SocialPlatform` guidance — and the orchestration: `capability()`, `generate()`,
`list_history()`, `accept()`. The provider only transports a message pair, so changing vendor cannot
change what the assistant is asked to produce.

A brief and a `rewrite`'s source text are validated against configuration bounds
(`AI_MAX_BRIEF_CHARS`) before any call is made, and every generation is persisted to the existing
`ai_generated_content` table (Phase 2) with `is_accepted = false`.

### 14.3 Nothing is published, ever

The Phase 9 brief is explicit: *never publish AI-generated content automatically*. The endpoints
therefore have no dispatch path at all —

- `POST /ai/{ws}/generate` writes one `ai_generated_content` row and returns `201`; it creates **no**
  `Post`, `PostTarget`, or `PublishingJob`;
- `POST /ai/{ws}/history/{id}/accept` flips `is_accepted` and nothing else.

The only way generated text reaches a platform is the user copying it into the Phase 7 composer and
running an explicit publish. The tests assert this: after a generation, the `posts` and
`publishing_jobs` tables are still empty.

### 14.4 A missing model is a capability gap

With no `OPENAI_API_KEY` the capability reports `configured: false` and generation answers `503` —
the same "capability gap, not a crash" rule as an unconfigured OAuth platform or publisher. The
`/assistant` page says so up front and the composer's **Draft with AI** panel explains it inline, so
a user never types a brief only to discover the feature is off. The key is read only inside the
provider, never returned, and never written to a log or an exception message.

### 14.5 The `/assistant` page and the composer helper

`components/ai/ai-assistant.tsx` is the real UI: a kind selector, a brief field (with a draft field
only for `rewrite`), tone/style/platform pickers fed by the live capability, a result shown in an
editable field with copy and accept actions, and a history list. `components/ui/select.tsx` is a
small styled native `<select>` added for those pickers. The composer
(`components/posts/composer.tsx`) adds a **Draft with AI** panel that generates one caption and puts
it in the base caption field, which the user then edits and publishes normally.

---

## 15. Analytics dashboard (Phase 10)

Report how published videos are performing, using **only** the figures each platform's official API
returns: published post count, views, likes, comments, shares, engagement, audience metrics,
account-level performance, per-platform comparisons, and a historical chart. Like the OAuth,
publishing, and AI layers, each platform's read sits **behind an abstraction** so it is swappable
and testable offline.

### 15.1 Provider abstraction

`app/services/metrics/base.py` defines `MetricsProvider` (`ABC`) with a class-level
`transport: httpx.BaseTransport | None` hook, an error taxonomy (`MetricsTransientError`,
`MetricsRateLimitError`, `MetricsAuthError`, `MetricsUnavailableError`, each carrying a `retryable`
marker), and two frozen value objects — `PostMetrics` (views/likes/comments/shares/reach/
impressions) and `AccountMetrics` (followers/follows/posts_count). `app/services/metrics/` holds one
implementation per platform (`facebook.py`, `instagram.py`, `youtube.py`, `tiktok.py`) plus the
registry (`get_metrics_provider`, `supported_metrics_platforms`). This is the identical shape used
by `services/publishers/*`, `services/oauth/*`, and `services/ai/*`.

The value of the split: every test drives a `httpx.MockTransport` with **no network call**, and a
platform that exposes metrics differently is a new module plus one registry line.

### 15.2 Unavailable is not zero

The brief's single hard rule is preserved at every layer. Each metric is an `int | None` — from the
provider through the ORM (`post_metrics` / `account_metrics`), the schema, and the frontend DTO. A
platform that does not return a figure leaves the column `NULL`; an aggregate over all-unavailable
values is `None`; and the UI renders a muted **N/A** (with an explanatory tooltip), never a `0`. A
genuine `0` is stored and shown as `0`. The module helper `as_int` treats an absent, empty, or
non-numeric value as `None` and a real `0` as `0`, so only the provider decides which case applies.

### 15.3 Immutable, timestamped snapshots

`post_metrics` and `account_metrics` are append-only: a refresh **inserts** a row and never
overwrites history. Each row records `retrieved_at` — when the platform answered — so the chart
plots measurements at the time they were taken, not when a worker happened to run. The history
endpoint keeps only each day's **newest** snapshot per target, so several refreshes in one day do
not multiply a target's numbers.

### 15.4 Reading is separated from refreshing

`app/services/analytics.py` splits the two concerns cleanly:

- **Reads** — `overview()`, `platform_performance()`, `account_performance()`,
  `target_performance()`, and `history()` answer the dashboard from the snapshot tables and are
  **pure**: no network. Opening `/analytics` never contacts a platform.
- **Refresh** — `refresh()` is the only function that touches the network; it reads each published
  target and connected account from its platform and appends a snapshot per destination. A failure
  on one destination is collected in `errors` and the sweep continues, so a revoked grant cannot
  freeze the whole dashboard; a platform with no provider is counted as **skipped**, not failed.

Because an aggregate cannot reuse `TenantMixin.scoped` (it returns `select(cls)`), every query
filters `workspace_id` by hand — always with the id from the caller's resolved workspace context,
never from request input.

### 15.5 Endpoints and the `/analytics` page

Seven routes under `/api/v1/analytics/{workspace_id}`: `GET …/capability`, `GET …/overview`,
`GET …/platforms`, `GET …/accounts`, `GET …/targets`, `GET …/history`, and `POST …/refresh`. The
`GET`s require `analytics:read` (every role); `refresh` requires `analytics:refresh`
(owner/admin/editor). The `_translate` helper maps `MetricsUnavailableError` → `503`,
`MetricsAuthError` → `502`, a rate-limit or transient error → `503`, another metrics error →
`502`, and a analytics-domain error → `400`.

`components/analytics/analytics-dashboard.tsx` is the real UI: overview cards, a platform-comparison
card (hand-built bars plus a full metric table), a history card with metric/platform/range pickers
and a **dependency-free SVG sparkline** (gridlines, axis labels, per-point tooltips, and a **gap**
for a missing day), an audience table, and a recent-targets table. The sparkline renders an
explanatory empty state when a whole series is unavailable, and every metric that is `null` shows
as **N/A** — the UI never invents a number the platform did not report.

---

## 16. Notifications (Phase 11)

Tell a user what happened without them watching a screen: a publish that **succeeded**, a publish
that **failed**, a **scheduled post about to fire**, and a **connected account whose token is about
to expire**. Each is an **in-app** record they can read, mark read, and dismiss, plus an **optional
email copy** delivered by background processing.

### 16.1 Addressed to the user, not the workspace

`notifications` already existed (Phase 2) and predates this phase. It is keyed on `user_id` — the
inbox is **per-account**, so a notice outlives the workspace it referred to
(`notifications.workspace_id` stays nullable with `ON DELETE SET NULL`). Every read and mutation is
scoped on the caller's own `user_id`, so a foreign notification id answers **`404`, never `403`**
— the same tenant-isolation rule the workspace routes follow.

### 16.2 In-app is the source of truth; email is an opt-in copy

The row is **always** written; the four `notification_preferences` booleans only decide whether a
*copy* is also emailed. Turning one off therefore can never hide something from the bell — the only
loss is the email. The preference row is created lazily and defaults to all-on
(`server_default=true`), and `subscription` / `system` notices are **in-app only** (no email flag).

### 16.3 One sweep, three sub-sweeps, idempotent by durable markers

`app/services/notifications.py` owns delivery; `app/worker.py` drives it from a single Celery Beat
entry (`notifications.sweep`, every `NOTIFICATIONS_SWEEP_INTERVAL_SECONDS`):

```
notifications.sweep
  ├─ dispatch_emails()          notifications WHERE emailed_at IS NULL  →  marshal preferences  →  send_email  →  stamp emailed_at
  ├─ send_schedule_reminders()  scheduled_posts due within the lead window, reminder_sent_at IS NULL  →  row  →  stamp reminder_sent_at
  └─ warn_expiring_tokens()     social_accounts expiring within the warning window, token_warning_sent_at IS NULL  →  row  →  stamp token_warning_sent_at
```

Each sub-sweep stamps a **durable marker** as the work completes, so a repeated sweep or two
overlapping workers cannot deliver the same notice twice. The in-app row is committed **before** any
email goes out, so a transport failure leaves the marker unset for the next tick and is reported in
the returned `SweepResult` rather than raised into a request. Phase 7's publishing engine already
writes the `publish_success` / `publish_failure` rows through its `_notify()`; Phase 11 **delivers**
them instead of duplicating their creation.

### 16.4 Endpoints, the bell, and settings

Seven routes under `/api/v1/notifications` — authenticated but **not** workspace-scoped, because the
inbox belongs to the account: `GET ""` (paged ≤ 100), `GET /unread-count`, `POST /read-all`,
`POST /{id}/read`, `DELETE /{id}`, `GET /preferences`, `PUT /preferences`. No new permission code is
introduced: reading and changing your own inbox needs only a valid session.

`components/notifications/notification-bell.tsx` is the top-bar bell: it polls the unread count, and
opens an inbox dropdown listing the newest notices with per-item mark-read + dismiss, a **mark all
read** action, and click-through routing by type (`publish_*` → `/history`, `schedule_reminder` →
`/schedule`, `token_expiring` → `/accounts`). The four email opt-outs live in the **Notifications**
tab of `components/settings/settings-tabs.tsx`, toggled optimistically through
`api.notifications.updatePreferences`. Nothing in the UI fabricates a notice — it renders only what
the API returns.

---

## 17. AI model management & automatic failover (Phase 12)

Keep the assistant **free and self-healing**: an administrator registers the AI models the platform
may use, and every generation call flows through **one router** that walks the models in priority
order and falls over to the next when one fails.

### 17.1 One router, one call path

`app/services/ai_router.py` is the only thing that talks to a model provider. Phase 9's
`ai_content` — and therefore both `/api/v1/ai/generate` and `/api/v1/ai/history` acceptance — calls
`complete(db, *, request, kind, workspace_id, user_id, task_id)`; nothing reaches
`app/services/ai/*` providers directly. Two model `kind`s are supported, mapped to a transport:

```
kind = openai_compatible  →  provider "openai"   (chat-completions; api_key from an env var)
kind = ollama             →  provider "ollama"   (local /api/chat; no key needed)
```

The models are **the same for both purposes** the platform has: `content` generation and any future
AI task declare the `kind` they need, and `AIModel.serves(kind)` decides whether a registered model
is eligible (an empty `supports` list means "serves everything").

### 17.2 The registry (two tables)

| Table | Role |
| ----- | ---- |
| `ai_models` | The registry. `name` is unique; `priority` orders the walk (lower = tried first); `enabled` is a manual kill-switch; `kind` / `model` / `base_url` / `credential_env` describe how to reach it; health columns (`status`, `last_checked_at`, `last_ok_at`, `consecutive_failures`, `last_error`) are written by the health sweep; `supports` (JSONB) lists the kinds it accepts. Index `ix_ai_models_enabled_priority`. |
| `ai_task_attempts` | Append-only. One row per attempt: `task_id`, `model_name` (denormalised), `status`, `attempt`, `latency_ms`, `error_class`, `error`. This single log is the **usage log**, the **execution trail**, the **task history**, and the **performance sample** — there is no separate table for each. All FKs (`workspace_id`, `user_id`, `model_id`) are `ON DELETE SET NULL` so history outlives what it referenced. |

**Credentials are never stored.** The row records only `credential_env` — the *name* of an
environment variable. The key is read from the process environment at call time and never logged,
returned, or written to the database; `credential_ready(model)` reports only whether it is present.

### 17.3 The failover walk

`_route()` builds the candidate list (`enabled`, `serves(kind)`, ordered by `priority`) and tries
each in turn, **re-sending the identical request unchanged**:

```
for candidate in candidates:
    attempt a call through its provider
      success            → commit, return  (switched = index > 0)
      content refusal    → STOP, re-raise   (a model's opinion, not an outage)
      rate limit         → no retry, next model
      transient failure  → retry up to AI_ROUTER_MAX_ATTEMPTS with exponential backoff, then next
      not configured     → next model
only after every candidate fails → raise the last error
```

Rules that make this safe: only **model-local** failures switch (a request rejected for being
malformed would fail everywhere and is not retried blindly); a **content refusal is never failed
over** — it is the model's answer, so it surfaces to the caller; retries are **bounded** with
exponential backoff (`AI_ROUTER_BACKOFF_BASE_SECONDS` → `AI_ROUTER_BACKOFF_MAX_SECONDS`); a
rate-limited model **skips** the retry and moves straight on; a duplicate `task_id` is **refused**
(`AITaskAlreadyCompletedError`) so a task can never be executed twice. `complete()` wraps the walk
in `try/finally: db.commit()` so the attempt log survives even when every model fails.

### 17.4 Monitoring is non-blocking

Health is **advisory, never a gate**. A Celery Beat entry (`ai.health_sweep`, every
`AI_HEALTH_CHECK_INTERVAL_SECONDS`) probes each enabled model and records `status`,
`last_checked_at`, `last_ok_at`, `consecutive_failures`, and `last_error`. The router **always walks
the whole ordered list regardless of the recorded status**, so a primary that the sweep marked down
and then recovered resumes automatically at the next successful call — no manual reset. An admin can
force a check with `POST /models/health-check`.

### 17.5 Fallback notification

When a call succeeds only because the router switched away from the top-priority model, it writes a
`model_fallback` notification (Phase 11's delivery core) to the requesting user, naming the model
used and the preferred one. `components/notifications/notification-bell.tsx` routes that type to
`/ai-models`, so the admin sees the degradation in the same bell as everything else.

### 17.6 Endpoints and the AI Models page

Eight routes under `/api/v1/ai/{workspace_id}` (all require the `ai:manage` permission —
owner/admin):

```
GET    /models                 list the registry
POST   /models                 create a model
PATCH  /models/{id}            update (priority, enabled, endpoint, …)
DELETE /models/{id}            remove
POST   /models/health-check    force a health probe
GET    /models/performance     per-model success rate / latency
GET    /tasks                  recent AI tasks (paged)
GET    /tasks/{id}             one task's attempt trail
```

These routes **authorize** against the caller's workspace but do **not partition** the registry —
it is instance-wide, because the AI platform is shared. A model id that is not visible to the
caller answers **`404`, never `403`**, matching the workspace-tenant rule. `frontend/app/(dashboard)/ai-models/page.tsx`
and `components/ai/model-registry.tsx` are the admin UI: the model list with priority/enable/edit
controls, a performance view, health-check action, and the recent-task history.

### 17.7 Not included, by design

No **LiteLLM** dependency (a small in-house registry + router was chosen over the framework — see
`DEVELOPMENT_PROGRESS.md`), no **billing/subscription** of any kind (the platform stays free), no
automatic **model discovery**, and no **weighted** routing (the order is strictly by `priority`).

---

## 18. Security hardening & performance (Phase 13)

Harden the running API and remove avoidable latency without changing what it does: throttle abusive
callers, add the browser security headers and the CSRF guard a cookie-authenticated API needs, keep a
durable trail of who did what, and compress responses. Most of the plan's security items were already
implemented in earlier phases and were **re-checked** here, not rewritten; only rate limiting, the
origin guard, security headers, and the audit trail are new code.

### 18.1 Middleware chain

`app/main.py` wires four global middlewares. Starlette runs them **outermost first**, so a request
passes CORS → security headers → origin guard → GZip → the application (and a response unwinds the
reverse way):

| Order | Middleware | Responsibility |
| --- | --- | --- |
| 1 | `CORSMiddleware` | Allow-list from `CORS_ORIGINS` (Phase 2 — reviewed) |
| 2 | `SecurityHeadersMiddleware` | Adds `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Permissions-Policy: geolocation=(), microphone=(), camera=()`, `Cross-Origin-Opener-Policy: same-origin`, and a deny-by-default CSP; HSTS only in production |
| 3 | `OriginGuardMiddleware` | Refuses a cross-origin state-changing request with `403` |
| 4 | `GZipMiddleware` | Compresses responses of 1 KB or more (`minimum_size=1024`) |

The CSP is `default-src 'none'; frame-ancestors 'none'; base-uri 'none'`. `/docs`, `/redoc`, and
`/openapi.json` are **exempt**, because Swagger UI ships its own scripts.
`Strict-Transport-Security` (one year, `includeSubDomains`) is added **only** when
`ENVIRONMENT=production`.

### 18.2 Rate limiting (`app/core/rate_limit.py`)

An in-house **fixed-window** limiter. `RateLimiter` counts through a `CounterStore` (`Protocol`): the
default `RedisStore` uses `INCR` + `EXPIRE … nx=True`, so the window is **shared across worker
processes**; if Redis is unreachable the limiter degrades to an in-process `MemoryStore` (logged
once) rather than failing open or closed. `client_identity(request)` keys the caller on the first hop
of `X-Forwarded-For`, else the socket peer. `rate_limit(bucket, *, limit, window_seconds=None)` is the
dependency factory; an over-limit request answers **429** with a `Retry-After` header.

| Bucket | Endpoints | Limit (per caller) |
| --- | --- | --- |
| `auth` | `/register`, `/login`, `/refresh`, `/logout`, `/password/reset`, `/password/reset/confirm` | `RATE_LIMIT_AUTH_PER_MINUTE` (10/min) |
| AI | `POST /api/v1/ai/{workspace_id}/generate` | `RATE_LIMIT_AI_PER_MINUTE` (30/min) |

The limiter is a security control **and** a cost control: every AI generation spends money or quota,
so it is throttled even though a single caller may legitimately call it repeatedly.

### 18.3 Audit trail (`app/models/audit.py`, `app/services/audit.py`)

`audit_logs` is **append-only** — the application never updates or deletes a row.
`services/audit.record(...)` stages a row **on the caller's session**, so the entry lands in the
**same transaction** as the change it describes, and never raises; `list_events(...)` reads
newest-first with a clamped page size (default `50`, max `200`). One read route serves it,
`GET /api/v1/workspaces/{workspace_id}/audit` (gated by the new `audit:read` permission, granted to
the `owner`/`admin` roles), with `limit`, `before` (cursor timestamp) and `action` (prefix) filters.
There is **no write path in the API** — entries are only ever staged by the services that make the
change.

Sign-in events (`auth.login.succeeded` / `auth.login.failed`) are filed under the user's **primary
workspace** (their oldest active membership), because the read route is workspace-scoped and a
sign-in is not otherwise tied to one; an unknown address on a failed login records no user and no
workspace. A failed login is committed **before** the `401` is raised, so the rejection is still
recorded.

### 18.4 Performance review

| Area | Outcome |
| --- | --- |
| Database queries | Reviewed — `audit_logs` ships three composite indexes (`(workspace_id, created_at)`, `(user_id, created_at)`, `(action, created_at)`) matching its read patterns; the Phase 2–12 models already index their hot read paths, so no new index was required |
| API response times | `GZipMiddleware` compresses responses ≥ 1 KB |
| Caching | The limiter's counters live in **Redis**, reusing the cache the project already runs |
| JSONB GIN indexes | Considered and deliberately **not** added: no query filters on a JSONB column yet (the audit `detail` and `ai_models.supports` are read, never filtered) |
| Video, workers, storage, frontend | Reviewed — already bounded/configured in Phases 5, 8, 11; no change required |


