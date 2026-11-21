# Development Progress

Author: **Pramish Sharma**

Legend: ✅ complete · 🚧 in progress · ⏳ pending · ⛔ blocked (external)

---

| Phase | Title | Status |
| --- | --- | --- |
| 1 | Project Foundation | ✅ Implemented + verified in **native mode** (Docker/Git still absent — see PROJECT_STATE) |
| 2 | Database Architecture | ✅ Implemented + verified — 16 tables, 2 migrations, 57/57 tests green |
| 3 | Authentication & Authorization | ✅ Implemented + verified — JWT + rotating refresh tokens, RBAC, 106/106 tests green |
| 4 | Professional Frontend Dashboard | ✅ Implemented + verified — 17 routes, typecheck/lint/build green, auth UI wired to the Phase 3 API |
| 5 | Video Upload & Media Management | ✅ Implemented + verified — resumable uploads, FFmpeg inspect + thumbnail, signed URLs, media library UI, 160/160 tests green |
| 6 | Social Media Account Connections | ✅ Implemented + verified — OAuth 2.0 for Instagram/Facebook/YouTube/TikTok, encrypted tokens, 200/200 tests green |
| 7 | Multi-Platform Publishing Engine | ✅ Implemented + verified — publisher adapters + "Publish Everywhere" orchestration, retries/backoff/idempotency/dead-letter, `/compose` UI, 234/234 tests green |
| 8 | Content Scheduling | ✅ Implemented + verified — durable `scheduled_posts` queue, Celery Beat due-sweep reusing the Phase 7 dispatch path, explicit time zones, cancellable/reschedulable, `/schedule` UI, 253/253 tests green |
| 9 | AI Content Generation | ✅ Implemented + verified — swappable `AIProvider` abstraction over the OpenAI REST API, capability/generate/history/accept, `ai:generate` RBAC, `/assistant` UI + composer "Draft with AI", 281/281 tests green |
| 10 | Analytics Dashboard | ✅ Implemented + verified — per-platform metrics providers over each platform's own API, append-only `post_metrics`/`account_metrics` snapshots (`NULL` ≠ `0`), overview/platform/account/history/refresh endpoints, `/analytics` UI with a hand-built SVG chart, 319/319 tests green |
| 11 | Notifications | ✅ Implemented + verified — user-addressed in-app inbox + top-bar bell, background email sweep with durable `emailed_at`/`reminder_sent_at`/`token_warning_sent_at` markers, scheduled-post reminders + token-expiry warnings, per-user email opt-out preferences, 335/335 tests green |
| 12 | Free AI Model Management & Automatic Failover | ✅ Implemented + verified — instance-wide `ai_models` registry walked in priority order with automatic failover (OpenAI-compatible providers + local Ollama), append-only `ai_task_attempts` log, on-demand + scheduled health sweep, `model_fallback` notification, `/ai-models` UI, 363/363 tests green |
| 13 | Security & Performance | ✅ Implemented + verified — in-house fixed-window rate limiter (Redis counters, in-process fallback), security headers + CSRF origin guard, GZip responses, append-only `audit_logs` trail behind `audit:read`, 383/383 tests green |
| 14 | Complete Testing | ✅ Implemented + verified — 9 Celery worker/Beat tests (tasks driven in-process) + a Vitest frontend suite (47 tests across 6 files); 394 backend / 47 frontend tests green |
| 15 | Production Deployment | ✅ Implemented — non-root multi-stage `Dockerfile.prod` ×2, 7-service `docker-compose.prod.yml` (only nginx public), nginx TLS proxy with streamed 2 GiB uploads, opt-in Sentry, backup/restore scripts, GitHub Actions CI, `DEPLOYMENT.md` runbook (container artefacts reviewed, not executed — Docker absent) |
| — | Sandbox mode (`DEMO_MODE`) *(post-plan addition)* | ✅ Implemented + verified — in-process sandbox implementations for OAuth / publishers / metrics behind one `DEMO_MODE` flag, so the user-panel Connect → Publish → Analytics flow works with no developer apps; refused when `ENVIRONMENT=production`; 402 backend tests green (`test_sandbox.py`, 8 tests) |

> **Sandbox mode** is not a master-plan phase — it is a later, user-requested addition so the
> account-connection and publishing flows can be demonstrated in the user panel without real platform
> credentials. Details: `docs/PROJECT_STATE.md` (Completed features) and `docs/SETUP_GUIDE.md` §4.4.1.

---

## Phase 1 — Project Foundation

**Goal:** frontend, backend, PostgreSQL, and Redis run locally and communicate.

### Deliverables

| # | Deliverable | Where |
| --- | --- | --- |
| 1 | Folder structure | `backend/`, `frontend/`, `docs/` |
| 2 | Environment config | `.env.example`, `frontend/.env.local.example` |
| 3 | Docker Compose stack | `docker-compose.yml` |
| 4 | FastAPI app | `backend/app/main.py` |
| 5 | Settings | `backend/app/core/config.py` |
| 6 | Structured logging | `backend/app/core/logging.py` |
| 7 | DB engine + session | `backend/app/db/session.py` |
| 8 | Declarative base | `backend/app/db/base.py` |
| 9 | Health endpoints | `backend/app/api/v1/endpoints/health.py` |
| 10 | Celery app | `backend/app/worker.py` |
| 11 | Alembic migrations scaffolding | `backend/alembic/` |
| 12 | Backend tests | `backend/app/tests/test_health.py` |
| 13 | Next.js app + dashboard shell | `frontend/app/`, `frontend/components/` |
| 14 | Frontend → backend health check | `frontend/components/system-health.tsx` |

### Success criteria

- [x] Backend starts and serves `/api/v1/health` (verified against a live uvicorn process).
- [x] Backend test suite passes (5/5).
- [x] Frontend builds and renders the dashboard shell (`npm run build`, `npm run dev` → HTTP 200).
- [x] `/api/v1/health/db` and `/api/v1/health/redis` return `ok` (native PostgreSQL 16.16 + Redis 8.10.3).
- [x] Dashboard renders backend, database, and Redis as **Operational**.
- [ ] `docker compose up` brings up postgres, redis, backend, worker, frontend. ⛔ *Docker not installed and needs admin — replaced by native mode for now.*

### Not included in Phase 1 (by design)

Authentication, ORM models, upload, scheduling, AI, analytics, billing.

---

## Phase 2 — Database Architecture

**Goal:** a professional relational schema — UUID keys, foreign keys, constraints,
indexes, SQLAlchemy models, Alembic migrations, transaction management, tenant
isolation — plus a documented ER diagram.

### Deliverables

| # | Deliverable | Where |
| --- | --- | --- |
| 1 | Deterministic constraint naming | `app/db/base.py` |
| 2 | Domain enums (14) | `app/db/enums.py` |
| 3 | `VARCHAR` + `CHECK` enum column factory | `app/db/types.py` |
| 4 | Reusable mixins (UUID / timestamps / soft delete / tenant) | `app/db/mixins.py` |
| 5 | Transaction management (`session_scope`, `get_db`) | `app/db/session.py` |
| 6 | 16 ORM models | `app/models/*.py` |
| 7 | Model registry | `app/models/__init__.py` |
| 8 | Initial schema migration | `alembic/versions/0d24e6e89e59_create_core_tables.py` |
| 9 | Seed migration (roles + plans) | `alembic/versions/70ed893bdb70_seed_system_roles_and_plans.py` |
| 10 | Alembic ↔ models wiring | `alembic/env.py` |
| 11 | Model unit tests (26) | `app/tests/test_models.py` |
| 12 | DB integration tests (26) | `app/tests/test_db_integration.py` |
| 13 | Test harness (engine / transaction rollback / auto test DB) | `app/tests/conftest.py` |
| 14 | ER diagram + schema reference | `docs/DATABASE_SCHEMA.md` |

### Tables implemented

`users`, `workspaces`, `workspace_members`, `roles`, `social_accounts`,
`oauth_credentials`, `media_files`, `posts`, `post_targets`, `publishing_jobs`,
`publishing_logs`, `scheduled_posts`, `ai_generated_content`, `notifications`,
`plans`, `subscriptions` — **16 tables**.

### Success criteria

- [x] All 16 models configure cleanly (`configure_mappers()`; `tables: 16`).
- [x] Alembic autogenerate produces the full schema; `upgrade head` applies it (exit 0).
- [x] `alembic check` → `No new upgrade operations detected` (models ↔ DB in sync).
- [x] 17 tables present in PostgreSQL (16 app + `alembic_version`).
- [x] Reference data seeded: 4 roles (owner/admin/editor/viewer) + 4 plans
      (free/starter/pro/business).
- [x] Unique, CHECK, and foreign-key behaviour proven by integration tests against live
      PostgreSQL (not mocks).
- [x] Tenant isolation proven: `scoped()` returns only the requested workspace and hides
      soft-deleted rows by default.
- [x] Cascade behaviour proven: deleting a workspace removes the whole tenant subtree;
      `RESTRICT` and `SET NULL` rules behave as designed.
- [x] Full suite: **57 passed** (5 Phase 1 + 52 Phase 2), 0 failed.
- [x] ER diagram documented.

### Not included in Phase 2 (by design)

Authentication endpoints, RBAC enforcement in routes, Pydantic schemas, CRUD services,
media upload, publishing adapters, scheduling dispatch, AI calls, billing integration.
`media_files.storage_backend` records where a file lives; no storage client is wired yet.

---

## Phase 3 — Authentication & Authorization

**Goal:** secure, production-shaped authentication — registration, login, logout, email
verification, password hashing, password reset, short-lived access tokens, refresh-token
rotation with reuse detection, session management, protected routes, role-based access,
and workspace permissions — with no plain-text secrets ever stored or sent to the client.

### Deliverables

| # | Deliverable | Where |
| --- | --- | --- |
| 1 | Password hashing (bcrypt, work factor 12) + JWT issue/decode + opaque token digests | `app/core/security.py` |
| 2 | Permission code catalogue (`resource:action`) | `app/core/permissions.py` |
| 3 | Auth settings (token TTLs, cookie, hashing rounds, prod guards) | `app/core/config.py` |
| 4 | `AuthSession` + `VerificationToken` models | `app/models/auth.py` |
| 5 | Auth service (register, authenticate, rotation, reset, verify) | `app/services/auth.py` |
| 6 | Workspace/role service | `app/services/workspaces.py` |
| 7 | Pluggable email delivery (`console` / `smtp`) | `app/services/email.py` |
| 8 | Request/response schemas | `app/schemas/auth.py`, `app/schemas/workspace.py` |
| 9 | Auth dependencies (session → user → workspace → permission) | `app/api/deps.py` |
| 10 | Refresh-token `HttpOnly` cookie helpers | `app/api/cookies.py` |
| 11 | Auth endpoints | `app/api/v1/endpoints/auth.py` |
| 12 | Workspace + membership endpoints (RBAC proof) | `app/api/v1/endpoints/workspaces.py` |
| 13 | Auth migration | `alembic/versions/14ae69daee8c_add_auth_sessions_and_verification_.py` |
| 14 | Auth integration tests (32) | `app/tests/test_auth.py` |
| 15 | Security-primitive unit tests (15) | `app/tests/test_security.py` |

### Security decisions

- **Passwords** are hashed with **bcrypt** (work factor 12, OWASP minimum). Inputs over
  bcrypt's 72-byte cap are pre-hashed (SHA-256) so long passphrases are not silently
  truncated. The clear-text password is never echoed in any response model.
- **Access tokens** are short-lived (30 min) HS256 JWTs carrying `sub`, `sid`, `jti`,
  `typ`, `iss`, `aud`, `iat`, `exp`. They are validated against a **live session row**, so
  logout and password changes kill them immediately instead of surviving until `exp`.
- **Refresh tokens** are opaque 256-bit values delivered in an `HttpOnly` cookie scoped to
  `/api/v1/auth`; only the SHA-256 digest is stored. Every use **rotates** the token, and
  replaying a rotated token is treated as theft — the whole rotation `family_id` is revoked.
- **Account enumeration** is prevented: login failures and password-reset requests return
  identical responses whether or not the address exists.
- **Tenant isolation** answers `404` (never `403`) for a workspace the caller is not a
  member of, so the API does not confirm the existence of foreign tenants.
- **Production guards** refuse to boot with the placeholder `SECRET_KEY`, without
  `REFRESH_COOKIE_SECURE=true`, or with the `console` email backend.

### Tables implemented

`auth_sessions`, `verification_tokens` — **2 tables** (18 total).

### Success criteria

- [x] Registration creates the account, provisions a personal workspace (caller = `owner`),
      normalises the email, and returns an access token — no secret in the body.
- [x] Login succeeds for valid credentials and returns the same `401` for unknown email,
      wrong password, and deactivated account.
- [x] Refresh rotates the token; a replayed (already-rotated) token revokes its whole family.
- [x] Logout revokes the session immediately; `logout-all` revokes every session.
- [x] Email verification and password reset consume single-use, expiring tokens.
- [x] Password change keeps only the calling session alive and rejects reusing the old one.
- [x] Protected routes reject missing/invalid access tokens with `401`.
- [x] Workspace routes enforce membership (404 for non-members) and role permissions
      (403 for a missing `resource:action` code).
- [x] `alembic check` → `No new upgrade operations detected`; migration `14ae69daee8c` applied.
- [x] Full suite: **106 passed** (5 Phase 1 + 52 Phase 2 + 49 Phase 3), 0 failed.
- [x] Live HTTP smoke test confirmed every endpoint on a real uvicorn process.

### Not included in Phase 3 (by design)

Password-history / breach-list checks, MFA, OAuth social login, rate limiting, and the
frontend auth UI (Phase 4). The `console` email backend is the development default; real
delivery needs `EMAIL_BACKEND=smtp`.

---

## Phase 4 — Professional Frontend Dashboard

**Goal:** a modern, professional SaaS interface on top of the Phase 3 API — consistent design
system, responsive desktop/mobile layouts, loading indicators, error messages, empty states,
toast notifications, form validation, and accessible, reusable components. No decorative
filler and no inconsistent layouts.

### Deliverables

| # | Deliverable | Where |
| --- | --- | --- |
| 1 | Typed API client (in-memory access token, cookie refresh, 401 → refresh → retry once) | `lib/api.ts` |
| 2 | Shared API/domain types | `lib/types.ts` |
| 3 | Frontend config (`NEXT_PUBLIC_API_URL`, `PASSWORD_MIN_LENGTH`, storage keys) | `lib/config.ts` |
| 4 | Auth context (bootstrap, login/register/logout, workspace list + active workspace, `hasPermission`) | `lib/auth-context.tsx` |
| 5 | Theme context (dark-first, persisted, `.dark` class toggle) | `lib/theme-context.tsx` |
| 6 | Date/time formatters | `lib/format.ts` |
| 7 | Platform catalogue (Instagram, Facebook, YouTube, TikTok + official API names) | `lib/platforms.ts` |
| 8 | Toast store + `<Toaster />` | `hooks/use-toast.ts`, `components/ui/toaster.tsx` |
| 9 | UI primitives (15 new: input, label, textarea, separator, skeleton, alert, avatar, table, tabs, dropdown-menu, progress, switch, spinner, field, toaster) | `components/ui/*` |
| 10 | Landing page | `app/page.tsx` |
| 11 | Auth pages (login, register, forgot-password, reset-password, verify-email) | `app/(auth)/**` |
| 12 | Auth form components + split-screen layout | `components/auth/*` |
| 13 | Dashboard shell (`RequireAuth`, sidebar, topbar, mobile slide-over, workspace switcher, account menu) | `components/app-shell/*`, `components/require-auth.tsx` |
| 14 | Dashboard route group + layout | `app/(dashboard)/layout.tsx` |
| 15 | Main dashboard (real workspace + member data, getting-started checklist, quick actions) | `app/(dashboard)/dashboard/page.tsx` |
| 16 | Settings (account, password, sessions, system health) | `app/(dashboard)/settings/page.tsx`, `components/settings/settings-tabs.tsx` |
| 17 | Module placeholder pages for Phases 5–12 | `app/(dashboard)/{upload,accounts,compose,schedule,history,analytics,assistant,billing}/page.tsx` |
| 18 | Shared page components (page header, empty state, module placeholder, brand, platform icon) | `components/*.tsx` |

### Design decisions

- **Tokens never touch `localStorage`.** The access token lives in a module-level variable and
  the refresh token stays in the backend's `HttpOnly` cookie. A 401 triggers one automatic
  refresh-and-retry, de-duplicated through a shared `refreshInFlight` promise so parallel
  requests do not each mint a token.
- **The route guard is UX only.** `RequireAuth` redirects anonymous visitors, but every API
  route re-authorises server-side; the client guard is never treated as a security boundary.
- **No extra dependencies.** Only `@radix-ui/react-slot` is installed, so the dropdown menu,
  tabs, switch, progress bar, and toaster are implemented dependency-free rather than adding
  packages to a build that was already green.
- **Honest placeholders.** Pages whose backend does not exist yet list the exact capabilities
  and phase that will deliver them. No mock data, fake charts, or simulated API calls — the UI
  never implies working functionality that is not there.

### Success criteria

- [x] Landing page, register, login, main dashboard, sidebar, header + account menu, connected
      accounts, upload, composer, scheduling, history, analytics, AI assistant, settings, and
      subscription pages all exist and render.
- [x] Responsive desktop and mobile layouts (collapsible sidebar, responsive grids/tables).
- [x] One design system: Tailwind tokens + shadcn/ui conventions, dark-first with a toggle.
- [x] Loading indicators (skeletons + spinners), error messages, empty states, toast
      notifications, and inline form validation are wired on the pages that call the API.
- [x] Accessible: labelled inputs, `aria-invalid`/`role="alert"` errors, focus-visible rings,
      `aria-live` toasts, keyboard-dismissable menus.
- [x] Real auth flow against the Phase 3 API; `?next=` honoured after sign-in.
- [x] `npm run typecheck`, `npm run lint`, `npm run build` all clean (**17 routes**).
- [x] Live contract check against a real uvicorn process (register, workspaces, members,
      cookie-only refresh, sessions, 409, 401).

### Not included in Phase 4 (by design)

Media upload, OAuth account connection, publishing, scheduling, AI generation, analytics data,
notifications, and billing — each page for those modules shows a phase-labelled placeholder.
Frontend unit/E2E tests arrive in Phase 14. No backend file was modified.

---

## Phase 5 — Video Upload & Media Management

**Goal:** accept a video safely and turn it into a manageable asset — validated and
resumable upload, FFmpeg inspection and thumbnailing, secure signed access, and a real
media library in the dashboard. Nothing is trusted from the client: the size is the number
of bytes written, and duration/resolution come from FFmpeg reading the stored file.

### Deliverables

| # | Deliverable | Where |
| --- | --- | --- |
| 1 | `MEDIA_*` settings (root, max size, MIME allow-list, chunk size, session TTL, signed-URL TTL, thumbnail width, binary paths, timeout) | `app/core/config.py` |
| 2 | Media permission codes (`media:read` / `media:upload` / `media:delete`) | `app/core/permissions.py` |
| 3 | Storage backend + safe keys + HMAC signed URLs | `app/services/storage.py` |
| 4 | Validation + resumable upload sessions + finalise + list/delete | `app/services/media.py` |
| 5 | FFprobe inspection + FFmpeg thumbnail (soft dependency) | `app/services/video.py` |
| 6 | Request/response schemas (no `storage_key` ever leaves the server) | `app/schemas/media.py` |
| 7 | 11 media routes: capabilities, stats, list, get, declare/chunk/resume/complete/abort, delete, signed serve | `app/api/v1/endpoints/media.py` |
| 8 | Router registration | `app/api/v1/router.py` |
| 9 | Test harness storage override (temp media root) | `app/tests/conftest.py` |
| 10 | Media integration tests (27) | `app/tests/test_media.py` |
| 11 | Storage + signed-URL unit tests (21) | `app/tests/test_storage.py` |
| 12 | FFmpeg unit tests (6) | `app/tests/test_video.py` |
| 13 | Media DTOs mirroring the backend schemas | `frontend/lib/types.ts` |
| 14 | Resumable client (declare → chunk → 409 resync → complete → abort) + validation | `frontend/lib/media-upload.ts` |
| 15 | `Blob` body support + `api.media.*` group | `frontend/lib/api.ts` |
| 16 | `formatBytes` / `formatDuration` | `frontend/lib/format.ts` |
| 17 | Drag-and-drop uploader with preview + progress | `frontend/components/media/upload-panel.tsx` |
| 18 | Media library: stats, search, grid, preview, replace, delete | `frontend/components/media/media-library.tsx` |
| 19 | Real `/upload` page | `app/(dashboard)/upload/page.tsx` |

### Upload protocol (five stages)

| Stage | Request | Success | Notable failures |
| --- | --- | --- | --- |
| Declare | `POST /media/{ws}/uploads` | `201` + session (`offset: 0`) | `413` too large · `415` bad MIME · `404` bad `replace_media_id` |
| Transfer | `PUT /media/{ws}/uploads/{id}?offset=n` | `200` + new `offset` | `409` out of order (+ `X-Upload-Expected-Offset`) · `413` chunk too big · `409` past declared size |
| Resume | `GET /media/{ws}/uploads/{id}` | `200` + `received_bytes`/`offset` | `404` unknown/other tenant · `410` expired |
| Complete | `POST /media/{ws}/uploads/{id}/complete` | `201` + `MediaFile` | `409` incomplete · `422` unreadable video · `503` no FFmpeg |
| Abort | `DELETE /media/{ws}/uploads/{id}` | `200` | `404` unknown/already finished |

### Security decisions

- **Signed URLs, never paths.** Files are served by an unauthenticated route whose HMAC-SHA256
  signature binds `media_id` + variant + expiry; the signature *is* the authorization and the
  URL stops working at `exp`. A tampered signature answers `403`; the tenant lookup still runs.
- **Validate before transferring.** MIME type and size are checked at declare time, so an
  unacceptable file is refused before a single byte is stored — and checked again per chunk.
- **Object keys are server-controlled.** `{workspace_id}/{media_id}/source{ext}` is built by the
  server; the user's filename is display data only. `path_for()` refuses any key that would
  escape the media root.
- **FFprobe is the arbiter.** A renamed archive that passes the declared-MIME check is still
  rejected at completion (`422`) because FFprobe reads the real bytes.
- **Tenant isolation.** Another workspace's media or upload id answers `404`, never `403`.

### Success criteria

- [x] Declare → chunk → complete produces a `ready` file with checksum, duration, and resolution
      read from the bytes (verified live with real FFmpeg).
- [x] Multi-chunk transfer works; an out-of-order chunk is refused with `409` +
      `X-Upload-Expected-Offset`, and the upload resumes from the server's offset.
- [x] Oversized declare `413`, unsupported MIME `415`, incomplete finalise `409`, expired session
      `410`, delete-in-use `409`, cross-tenant `404`.
- [x] Signed video and thumbnail URLs serve anonymously; a tampered signature answers `403`.
- [x] Thumbnail generated as JPEG (max width 640); a failed thumbnail never fails the upload.
- [x] Replacing a file keeps its `id` and swaps the bytes (replacement is `replace_media_id` on
      declare, resolved inside the caller's workspace; the previous objects are removed).
- [x] `/upload` is a real drag-and-drop uploader with preview, progress, search, replace, delete,
      and an FFmpeg-missing banner.
- [x] Full suite: **160 passed** (5 + 52 + 49 + 54), 0 failed.
- [x] `npm run typecheck` / `lint` / `build` clean (**17 routes**).

### Not included in Phase 5 (by design)

Server-side transcoding to platform-specific formats (Phase 7 processing), S3/MinIO storage
(the interface is ready but only `LocalStorage` is implemented), and upload resumption across a
browser restart (the session survives on the server; the client re-declares on a full reload).
Frontend E2E tests arrive in Phase 14.

---

## Phase 6 — Social Media Account Connections (OAuth)

**Goal:** let a workspace link Instagram, Facebook, YouTube, and TikTok accounts through each
platform's **official** OAuth 2.0 authorization-code flow, store the resulting tokens
**encrypted at rest**, keep them fresh, expose read-only account metadata, and support clean
disconnection with upstream revocation — without ever leaking a token to the browser.

### Deliverables

| # | Deliverable | Where |
| --- | --- | --- |
| 1 | OAuth settings (public URL, success/failure redirects, state TTL, HTTP timeout, refresh margin, encryption key, Meta Graph version, per-platform client credentials) | `app/core/config.py` |
| 2 | Account permission codes (`account:read` / `account:connect` / `account:disconnect`) | `app/core/permissions.py` |
| 3 | AES-256-GCM envelope encryption + HKDF-SHA256 key derivation | `app/core/crypto.py` |
| 4 | Provider ABC (authorization/callback URLs, PKCE, token exchange, refresh, revoke) + registry | `app/services/oauth/base.py`, `app/services/oauth/__init__.py` |
| 5 | Meta Graph base (Facebook Login, `/me/accounts`, `fb_exchange_token`) | `app/services/oauth/meta.py` |
| 6 | Facebook + Instagram providers | `app/services/oauth/facebook.py`, `app/services/oauth/instagram.py` |
| 7 | YouTube (Google OAuth + Data API v3) provider | `app/services/oauth/youtube.py` |
| 8 | TikTok (Login Kit v2 + Content Posting API) provider | `app/services/oauth/tiktok.py` |
| 9 | Connection service (authorize, callback, refresh, `ensure_fresh_tokens`, disconnect) | `app/services/accounts.py` |
| 10 | Response schemas (no token field anywhere) | `app/schemas/account.py` |
| 11 | 7 account routes + `_translate` error mapping | `app/api/v1/endpoints/accounts.py` |
| 12 | Router registration | `app/api/v1/router.py` |
| 13 | `OAuthAuthorizationState` model (state digest, encrypted PKCE verifier, TTL, consumption) | `app/models/social_account.py` |
| 14 | New table migration | `alembic/versions/5347775d077d_add_oauth_authorization_states.py` |
| 15 | Provider-level tests (20) | `app/tests/test_oauth.py` |
| 16 | API-level tests (20) | `app/tests/test_accounts.py` |
| 17 | Account DTOs mirroring the backend schemas | `frontend/lib/types.ts` |
| 18 | `api.accounts.*` client group | `frontend/lib/api.ts` |
| 19 | Accounts manager (connect cards, account list, refresh/reconnect/disconnect, callback toast) | `frontend/components/accounts/accounts-manager.tsx` |
| 20 | Real `/accounts` page | `app/(dashboard)/accounts/page.tsx` |
| 21 | Per-platform setup + eligibility documentation | `docs/OAUTH_SETUP.md` |

### Platform matrix

| Platform | Provider base | PKCE | Refresh mechanism | Revocation |
| --- | --- | --- | --- | --- |
| Instagram | Meta Graph v21.0 (Facebook Login) | — | Long-lived token re-exchange (no refresh token) | `DELETE /me/permissions` |
| Facebook | Meta Graph v21.0 | — | Long-lived token re-exchange (no refresh token) | `DELETE /me/permissions` |
| YouTube | Google OAuth 2.0 (`access_type=offline`, `prompt=consent`) | S256 | `refresh_token` grant | `oauth2.googleapis.com/revoke` |
| TikTok | Login Kit v2 (comma-separated scopes) | S256 (required) | `refresh_token` grant | `POST /v2/oauth/revoke` |

### Security decisions

- **Single-use state, stored as a digest.** A 256-bit `state` is bound to the workspace and
  persisted only as its **SHA-256 hash** with a 600 s TTL; a second use, an expired value, or a
  state minted for a different platform is refused. This blocks consent-URL injection and CSRF.
- **PKCE where it matters.** `S256` is mandatory on TikTok and used on Google; the verifier is
  stored **encrypted**, never in clear text.
- **Tokens are encrypted at rest.** AES-256-GCM with an HKDF-SHA256-derived key
  (`OAUTH_ENCRYPTION_KEY`, else `SECRET_KEY`); the `v1.<base64url(nonce‖ciphertext‖tag)>` format
  is versioned for future rotation. A tampered ciphertext or a foreign key both raise.
- **The browser never sees a token.** Response schemas expose only `token_expires_at`,
  `token_expires_in_seconds`, and `needs_reconnect`; client secrets stay server-side only.
- **An unusable grant is explicit.** An account whose token cannot be refreshed is flagged
  `needs_reconnect` and answers `409`, so the UI can prompt a fresh authorization instead of
  silently failing.
- **Disconnect is honest.** The credential is destroyed locally and the account soft-deleted
  (`status=revoked`) so historical post targets keep their reference; `revoked_upstream` reports
  whether the platform actually confirmed the revocation.

### Success criteria

- [x] Authorization start returns a consent URL carrying `state`; an unconfigured platform
      answers `503`, and an unauthenticated caller answers `401`.
- [x] Callback exchanges the code, fetches account info, encrypts the tokens, and creates the
      account — proving the round-trip offline through an injected `httpx` transport.
- [x] Unknown, replayed, and foreign-platform `state` values are rejected; a platform refusal
      surfaces as an error redirect.
- [x] Responses and listings never expose a token value.
- [x] Refresh rotates the stored credential; an unrefreshable grant is flagged `needs_reconnect`.
- [x] Disconnect attempts upstream revocation and removes the account locally even if revocation
      fails; `revoked_upstream` reflects the outcome.
- [x] Tenant isolation: another workspace's account id answers `404`, never `403`; a viewer can
      read but not connect.
- [x] `alembic check` → `No new upgrade operations detected`; migration `5347775d077d` applied.
- [x] Full suite: **200 passed** (5 + 52 + 49 + 54 + 40), 0 failed.
- [x] `npm run typecheck` / `lint` / `build` clean (**17 routes**; `/accounts` now 8.76 kB).

### Not included in Phase 6 (by design)

Actual video publishing (Phase 7) — Phase 6 only obtains and manages the grant. No mediator
service, no token-sharing portal, and no auto-posting on connect. Per-platform app review,
publishing audits, and account eligibility are **external** prerequisites documented in
`docs/OAUTH_SETUP.md`; the code is complete and tests run without them.

---

## Phase 7 — Multi-Platform Publishing Engine

**Goal:** publish one uploaded video to **Instagram, Facebook, YouTube, and TikTok** from a single
action. A modular architecture with a base publisher interface and a per-platform adapter, a
"Publish Everywhere" orchestration that validates the user, the video, the connected accounts, and
each selected platform before creating one **independent** job per destination, and a reliability
layer (retries, exponential backoff, idempotency, rate-limit handling, duplicate-publishing
protection, timeouts, error classification, dead-letter handling, job monitoring). One platform's
failure must never cancel another's success, and a post must **not** be reported as published until
the platform itself confirms it.

### Deliverables

| # | Deliverable | Where |
| --- | --- | --- |
| 1 | `Publisher` ABC + `PublisherCapability` / `MediaAsset` / `PublishRequest` / `PublishResult` value objects + error taxonomy + HTTP classification + status polling | `app/services/publishers/base.py` |
| 2 | Publisher registry (`get_publisher`, `supported_platforms`) | `app/services/publishers/__init__.py` |
| 3 | Shared Meta Graph publishing base | `app/services/publishers/meta.py` |
| 4 | Facebook + Instagram publishers | `app/services/publishers/facebook.py`, `app/services/publishers/instagram.py` |
| 5 | YouTube (Data API v3) publisher | `app/services/publishers/youtube.py` |
| 6 | TikTok (Content Posting API) publisher | `app/services/publishers/tiktok.py` |
| 7 | "Publish Everywhere" orchestration (compose, validate, dispatch, execute, aggregate) | `app/services/publishing.py` |
| 8 | Celery task `publishing.publish_target` | `app/worker.py` |
| 9 | Publishing settings block (`PUBLISHING_*`) | `app/core/config.py` |
| 10 | Post permission codes (`post:read/create/update/delete/publish`) | `app/core/permissions.py` |
| 11 | Request/response schemas | `app/schemas/post.py` |
| 12 | 6 post routes + `_translate` error mapping | `app/api/v1/endpoints/posts.py` |
| 13 | Router registration | `app/api/v1/router.py` |
| 14 | Adapter-level tests (16) | `app/tests/test_publishers.py` |
| 15 | Orchestration/API tests (18) | `app/tests/test_publishing.py` |
| 16 | Post DTOs mirroring the backend schemas | `frontend/lib/types.ts` |
| 17 | `api.posts.*` client group | `frontend/lib/api.ts` |
| 18 | Publisher composer (video picker, captions, destinations, live progress) | `frontend/components/posts/composer.tsx` |
| 19 | Real `/compose` page | `app/(dashboard)/compose/page.tsx` |

### Platform matrix (published from the adapters)

| Platform | Publisher base | Title | Confirmation polled | Notes |
| --- | --- | --- | --- | --- |
| Instagram | `MetaPublisher` | unsupported | container → `FINISHED` → `media_publish` → permalink | Reels via `media_type=REELS` |
| Facebook | `MetaPublisher` | optional | video object → `ready` | Page token; Facebook pulls the video from a signed URL |
| YouTube | — | **required** | resumable upload → `uploadStatus: processed` | Unverified apps are forced `private` by Google |
| TikTok | — | unsupported | init → chunked `PUT` → `PUBLISH_COMPLETE` | Caption *is* the title; unaudited apps must use `SELF_ONLY` |

### Reliability decisions

- **Success requires the platform's own confirmation.** Every publisher polls the platform's
  processing status to a terminal-**success** state before returning; an accepted upload is never
  reported as published, and a post reaches `published` only when *every* target has a confirmed
  remote id (otherwise `partially_published`).
- **One target, one job, one attempt counter.** Each destination is its own `post_targets` row and
  its own `publishing_jobs` row, dispatched separately to Celery, so a failure writes only to that
  target and can never cancel another.
- **Retries are bounded and backed off.** A classified *retryable* failure (rate limit, 5xx,
  timeout) schedules the next attempt at `base × 2^(attempt-1)` seconds, capped, honouring the
  platform's `Retry-After`; once `max_attempts` (5) is reached the job is **dead-lettered**.
- **Duplicate publishes are refused.** The idempotency key is a SHA-256 fingerprint of the
  workspace + the video's **content checksum** + the destination account + the exact text, so
  pressing Publish twice (or re-uploading the same file) cannot post the same video twice. A
  re-delivered Celery message is a no-op once the job is terminal.
- **Validation happens before a byte moves.** The publisher refuses an unacceptable container,
  size, duration, resolution, or caption, and a destination that fails is recorded `skipped` with
  its reason without affecting the others.
- **Every attempt is logged, and nothing secret is.** `publishing_logs` keeps the classified error
  and the platform's raw response with any credential-shaped key redacted.

### Success criteria

- [x] A composed post can be published to several platforms at once; each destination gets one
      independent job, and a failure on one leaves the others `published` (`partially_published`).
- [x] A target publishes only once the platform reports a terminal-success status (verified per
      adapter against an injected `httpx` transport).
- [x] Retryable failures back off (honouring `Retry-After`) and dead-letter at `max_attempts`; a
      permanent failure is not retried; a duplicate publish is refused.
- [x] Tenant isolation: another workspace's post/job id answers `404`, never `403`; a viewer can
      read but not publish.
- [x] `/compose` is a real composer (video picker, per-platform captions, destination toggles,
      live per-target progress) replacing the placeholder.
- [x] No new table and no migration — 19 tables, migration head `5347775d077d` unchanged.
- [x] Full suite: **234 passed** (5 + 52 + 49 + 54 + 40 + 34), 0 failed.
- [x] `npm run typecheck` / `lint` / `build` clean (**17 routes**; `/compose` now 9.44 kB).

### Not included in Phase 7 (by design)

Scheduling a future publish (Phase 8) — Phase 7's `create_post` + `publish_everywhere` split is
deliberately shaped so the scheduler reuses the same dispatch path. Analytics on published
results (Phase 10), notifications delivery channels beyond the in-app row (Phase 11), and
server-side transcoding to platform-specific formats. A real end-to-end publish against each live
platform needs the external prerequisites in `docs/OAUTH_SETUP.md`; verified offline via injected
transports.

---

## Phase 8 — Content Scheduling

**Goal:** let a composed post be queued for a future, chosen time and published automatically.
The schedule must be durable (survive an API or worker restart), honour the time zone the user
picked, be cancellable and reschedulable before it fires, and dispatch through **exactly the
Phase 7 path** — the same per-target jobs, idempotency keys, retries, and dead-letter handling as
a manual publish — so scheduling can never bypass the reliability layer. It must fire exactly
once, and a post that comes due while the worker was down must not silently publish stale content.

### Deliverables

| # | Deliverable | Where |
| --- | --- | --- |
| 1 | Scheduling settings (`SCHEDULING_SWEEP_INTERVAL_SECONDS`, `SCHEDULING_BATCH_SIZE`, `SCHEDULING_MISSED_GRACE_SECONDS`, `SCHEDULING_DEFAULT_TIMEZONE`) | `app/core/config.py` |
| 2 | Scheduling permission codes (`schedule:read/create/cancel`) | `app/core/permissions.py` |
| 3 | Scheduler service (time zones, list/get, queue, reschedule, cancel, due-sweep) | `app/services/scheduling.py` |
| 4 | Publisher dispatch split (`record_targets` + `queue_targets`) shared by the manual and scheduled paths | `app/services/publishing.py` |
| 5 | Request/response schemas | `app/schemas/schedule.py` |
| 6 | 4 schedule routes + `_translate` error mapping | `app/api/v1/endpoints/scheduling.py` |
| 7 | Router registration (before the posts router) | `app/api/v1/router.py` |
| 8 | Celery task + Celery Beat schedule entry (`scheduling.dispatch_due`) | `app/worker.py` |
| 9 | Scheduler/API tests (19) | `app/tests/test_scheduling.py` |
| 10 | Schedule DTOs mirroring the backend schemas | `frontend/lib/types.ts` |
| 11 | `api.schedules.*` client group | `frontend/lib/api.ts` |
| 12 | `formatDateTimeInZone` (render an instant in a chosen IANA zone) | `frontend/lib/format.ts` |
| 13 | Shared schedule time picker (`datetime-local` + IANA zone) | `frontend/components/schedule/schedule-time-field.tsx` |
| 14 | Scheduling calendar (upcoming/past tabs, day grouping, reschedule, cancel) | `frontend/components/schedule/scheduling-calendar.tsx` |
| 15 | Real `/schedule` page | `app/(dashboard)/schedule/page.tsx` |
| 16 | "Schedule for later" action in the composer (shares destination validation) | `frontend/components/posts/composer.tsx` |

### Dispatch lifecycle

| Step | Where | What happens |
| --- | --- | --- |
| Queue | `POST /posts/{ws}/schedule` | Validate destinations via `record_targets` → store them `pending` (no job); write the `scheduled_posts` row (`pending`); post → `scheduled` |
| Sweep | `scheduling.dispatch_due` (Celery Beat, every 60 s) | `SELECT … FOR UPDATE SKIP LOCKED` the due `pending` rows |
| Fire | `publishing.queue_targets` | Build the same per-target jobs + idempotency keys as a manual publish; schedule → `dispatched` |
| Miss | overdue > grace | schedule → `missed`, targets cancelled, post → `draft` |
| Edit | `PATCH /posts/{ws}/schedule/{post_id}` | Move a `pending`/`queued` entry; `409` once dispatched |
| Drop | `DELETE /posts/{ws}/schedule/{post_id}` | Cancel the entry, its pending targets, and any non-terminal job |

### Reliability decisions

- **Durable queue, periodic sweep.** The schedule lives in PostgreSQL, not in a broker-held timer,
  so a broker, API, or worker restart simply means the next sweep catches up. Beat ticks every
  `SCHEDULING_SWEEP_INTERVAL_SECONDS`, which bounds how late a healthy publish can be.
- **Atomic claim, exactly-once.** `FOR UPDATE SKIP LOCKED` lets overlapping ticks (or two workers)
  take disjoint rows, and an entry is only ever reached in `pending` — so a re-run cannot
  double-publish. This is the same property the Phase 7 `run_job` terminal short-circuit gives a
  re-delivered Celery message.
- **No reliability bypass.** A due entry is dispatched through `publishing.queue_targets`, so a
  scheduled publish gets the identical per-target jobs, content-derived idempotency keys, retries
  with exponential backoff, rate-limit handling, and dead-lettering as a manual one.
- **Stale content is not published.** An entry overdue past the grace is `missed` and reverted to a
  draft for the user to re-queue; a post that became unusable between scheduling and dispatch is
  left un-dispatched rather than published broken.
- **Time zones are explicit.** `scheduled_for` is stored in **UTC**; the caller's IANA zone is
  validated with the stdlib `zoneinfo` and stored in `timezone`, so `local_time` is the wall-clock
  time the user actually picked — never inferred from the server.
- **Cancellation is total.** Cancelling also cancels the entry's pending destinations and any
  not-yet-terminal job, so nothing is left for the sweep to pick up.

### Success criteria

- [x] A composed post can be queued for a future time; the entry and its pending destinations are
      recorded and **nothing is dispatched** until it comes due.
- [x] A past time and an unknown IANA zone are rejected; a naive time is read in the chosen zone
      and stored as UTC.
- [x] The due-sweep dispatches a due entry through the Phase 7 path, never fires the same entry
      twice, and leaves future entries alone.
- [x] An overdue entry is marked `missed` (targets cancelled, post back to `draft`).
- [x] A scheduled and a manual publish derive the **same** idempotency key.
- [x] A queued entry can be rescheduled; a dispatched one answers `409`. Cancelling prevents firing.
- [x] Tenant isolation: another workspace's schedule answers `404`, never `403`; a viewer may read
      but not change the queue.
- [x] No new table and no migration — 19 tables, migration head `5347775d077d` unchanged.
- [x] Full suite: **253 passed** (5 + 52 + 49 + 54 + 40 + 34 + 19), 0 failed.
- [x] `npm run typecheck` / `lint` / `build` clean (**17 routes**; `/schedule` now 3.8 kB).

### Not included in Phase 8 (by design)

AI-suggested posting times (Phase 9) and analytics on delivered schedules (Phase 10). The sweep
reuses the Phase 7 reliability layer rather than reimplementing it; a real deployment should run
Celery Beat (or a single scheduler) alongside the workers so the sweep is always ticking.

---

## Phase 9 — AI Content Generation

**Goal:** help a user write the copy for a composed post — a caption, a hashtag set, a longer
description, a rewrite of text they already have, or content ideas — tuned to a chosen platform,
tone, and style. The model vendor must be swappable and the whole path testable **offline**, the
API key must never leave the server, and **nothing generated may ever be published automatically**:
a suggestion is stored, reviewed, edited, and reused by the user's own action. A server with no
model configured must say so, not fail.

### Deliverables

| # | Deliverable | Where |
| --- | --- | --- |
| 1 | AI settings (`OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL`, `OPENAI_HTTP_TIMEOUT_SECONDS`, `OPENAI_MAX_OUTPUT_TOKENS`, `OPENAI_TEMPERATURE`, `AI_MAX_BRIEF_CHARS`) | `app/core/config.py` |
| 2 | AI permission code (`ai:generate`, already seeded to owner/admin/editor in Phase 2) | `app/core/permissions.py` |
| 3 | Provider abstraction: `AIProvider` ABC, error taxonomy, `CompletionRequest`/`AICompletion` value objects | `app/services/ai/base.py` |
| 4 | OpenAI provider (`httpx` against the REST API, injectable transport) | `app/services/ai/openai.py` |
| 5 | Provider registry (`get_provider`, `supported_providers`) | `app/services/ai/__init__.py` |
| 6 | Prompt engineering + orchestration (capability, generate, history, accept) | `app/services/ai_content.py` |
| 7 | Request/response schemas | `app/schemas/ai.py` |
| 8 | 4 AI routes + `_translate` error mapping | `app/api/v1/endpoints/ai.py` |
| 9 | Router registration (`/ai`) | `app/api/v1/router.py` |
| 10 | Provider + API tests (28) | `app/tests/test_ai.py` |
| 11 | AI DTOs mirroring the backend schemas | `frontend/lib/types.ts` |
| 12 | `api.ai.*` client group | `frontend/lib/api.ts` |
| 13 | Styled native `Select` primitive (tone/style/platform controls) | `frontend/components/ui/select.tsx` |
| 14 | AI assistant UI (generate, edit, copy, accept, history) | `frontend/components/ai/ai-assistant.tsx` |
| 15 | Real `/assistant` page (replaces the Phase 9 placeholder) | `app/(dashboard)/assistant/page.tsx` |
| 16 | "Draft with AI" helper in the composer (fills the base caption) | `frontend/components/posts/composer.tsx` |

### Generation lifecycle

| Step | Where | What happens |
| --- | --- | --- |
| Describe | `GET /ai/{ws}/capability` | Report the provider, model, and whether a key is configured, plus the allowed kinds/tones/styles |
| Generate | `POST /ai/{ws}/generate` | Validate the brief/tone/style, build the prompt, call the provider, store a suggestion (`is_accepted=false`) |
| Review | `/assistant`, composer | The text is shown in an editable field; the user copies or replaces their caption |
| Accept | `POST /ai/{ws}/history/{id}/accept` | Flip `is_accepted` — **record only**, it publishes nothing |
| Revisit | `GET /ai/{ws}/history` | Past suggestions, newest first, optionally filtered by kind |

### Reliability & safety decisions

- **Provider abstraction, not a hard-coded call.** `AIProvider` mirrors the Phase 6/7 pattern — an
  `ABC` with a class-level `transport` hook — so the vendor is swappable and every test drives a
  `httpx.MockTransport` with no network and no new dependency (`httpx` is already required).
- **Capability gap, never a crash.** With no `OPENAI_API_KEY` the capability reports
  `configured: false`, generation answers **503**, and the UI explains the state. This is the same
  rule the OAuth and publishing providers follow for a missing credential.
- **Nothing is published automatically.** Generation only writes an `ai_generated_content` row.
  Accepting a suggestion flips a flag; it creates no post, no target, and no publishing job — the
  Phase 7 composer remains the only path to a publish, satisfying the Phase 9 brief exactly.
- **Prompts are engineering, not vendor calls.** The system prompt, per-kind instructions, and
  per-platform guidance live in `ai_content.py`; the provider only transports a message pair. A new
  vendor cannot change what the assistant is asked to produce.
- **The key never leaves the server.** It is read from settings inside the provider, is never
  echoed into a response, a log, or an exception message, and the browser only ever sees a boolean
  `configured` flag.
- **Centralised error mapping.** A foreign `post_id` and an unknown `content_id` answer **404**; a
  bad request, an unknown tone/style, a rewrite without a draft, and a model policy refusal answer
  **422**; an unconfigured model and a transient upstream failure answer **503**; any other
  provider rejection answers **502**.

### Success criteria

- [x] With no key the capability reports `configured: false`, generation answers `503`, and **no
      row is written**.
- [x] With a key, all five kinds generate and are stored; **`posts` and `publishing_jobs` stay
      empty** (nothing is published).
- [x] The outbound request carries the configured model and the key as a `Bearer` credential;
      tone/style/platform shape the prompt; rewrite uses the supplied draft.
- [x] `422` for an unknown tone/style, an empty or over-long brief, a rewrite without a draft, and
      a content-policy refusal; `503` for `429`/connect-error/`500`; `502` for a provider `401`.
- [x] Accepting marks `is_accepted` and publishes nothing; history filters by kind.
- [x] RBAC/tenant isolation: another workspace's `post_id` answers `404` (never `403`), a viewer is
      refused, an editor succeeds, an unauthenticated call is `401`.
- [x] No new table and no migration — 19 tables, migration head `5347775d077d` unchanged.
- [x] Full suite: **281 passed** (5 + 52 + 49 + 54 + 40 + 34 + 19 + 28), 0 failed.
- [x] `npm run typecheck` / `lint` / `build` clean (17 routes; `/assistant` now 8.1 kB).

### Not included in Phase 9 (by design)

- **Best-time recommendation.** A credible "best time to post" needs this workspace's own delivery
  and engagement history, which arrives with the Phase 10 analytics. Emitting a time from no data
  would be a fabricated recommendation, so it is deferred rather than faked.
- **Automated posting of generated content.** Foreclosed by design — generation is user-initiated
  and a suggestion is only ever stored and accepted, never dispatched.
- **Background AI jobs.** Every generation is a synchronous, user-triggered request; there is no
  Celery task, so there is no queue to monitor and no per-workspace usage accounting yet.
- **Image/thumbnail generation and multi-turn chat.** Out of the Phase 9 scope in the master spec.

---

## Phase 10 — Analytics Dashboard

**Goal:** show how published videos are performing, using **only** the numbers each platform's
**official API** actually returns — published post count, views, likes, comments, shares,
engagement, audience metrics, account-level performance, platform comparisons, and a historical
performance chart. The single hard rule from the brief is that a metric the platform does **not**
provide must be shown as **unavailable**, never as `0`: a real zero and a missing value are
different facts, and the system must neither invent nor estimate either. Metric retrieval times
are recorded so a delayed read cannot misdate a measurement.

### Deliverables

| # | Deliverable | Where |
| --- | --- | --- |
| 1 | `PostMetric` + `AccountMetric` append-only snapshot models (`int \| None` metrics, `retrieved_at`) | `app/models/analytics.py` |
| 2 | Measurement tables + `analytics:read`/`analytics:refresh` grants | `alembic/versions/f241d12e080b_add_analytics_metrics.py` |
| 3 | Analytics settings (`ANALYTICS_HTTP_TIMEOUT_SECONDS`, `ANALYTICS_DEFAULT_WINDOW_DAYS`, `ANALYTICS_MAX_HISTORY_POINTS`, `ANALYTICS_MAX_REFRESH_TARGETS`) | `app/core/config.py` |
| 4 | Permission codes (`analytics:read`, `analytics:refresh`) | `app/core/permissions.py` |
| 5 | Metrics provider `ABC` + `PostMetrics`/`AccountMetrics` value objects + error taxonomy + `as_int` | `app/services/metrics/base.py` |
| 6 | Facebook + Instagram (Meta Graph Insights) metrics providers | `app/services/metrics/facebook.py`, `app/services/metrics/instagram.py` |
| 7 | YouTube (Data API v3) metrics provider | `app/services/metrics/youtube.py` |
| 8 | TikTok (Display API) metrics provider | `app/services/metrics/tiktok.py` |
| 9 | Provider registry (`get_metrics_provider`, `supported_metrics_platforms`) | `app/services/metrics/__init__.py` |
| 10 | Read + aggregate + refresh service (overview, platform/account/target performance, history, refresh) | `app/services/analytics.py` |
| 11 | Request/response schemas (`int \| None` metrics + `available` counters) | `app/schemas/analytics.py` |
| 12 | 7 analytics routes + `_translate` error mapping | `app/api/v1/endpoints/analytics.py` |
| 13 | Router registration (`/analytics`) | `app/api/v1/router.py` |
| 14 | Celery task `analytics.refresh_workspace` | `app/worker.py` |
| 15 | Service/API tests (36) | `app/tests/test_analytics.py` |
| 16 | Analytics DTOs mirroring the backend schemas | `frontend/lib/types.ts` |
| 17 | `api.analytics.*` client group | `frontend/lib/api.ts` |
| 18 | Dashboard (overview cards, comparison, SVG history chart, audience, recent targets) | `frontend/components/analytics/analytics-dashboard.tsx` |
| 19 | Real `/analytics` page | `app/(dashboard)/analytics/page.tsx` |

### Metric availability (per platform, from the official APIs)

| Platform | Post metrics read | Audience metrics read |
| --- | --- | --- |
| Instagram | views (plays), likes, comments, shares, reach, impressions, saved | followers, follows, media count |
| Facebook | views (post video plays), `post_reactions` (≈ likes), comments, shares, reach, impressions | followers, follows, page likes/post count |
| YouTube | views, likes, comments, (shares N/A) | subscribers, (following N/A), video count |
| TikTok | views, likes, comments, shares, reach, impressions | follower count, following, video count |

> A metric with **no public API** is left `NULL` (unavailable) — it is never estimated. The
> capability endpoint reports `metrics_available: false` for any platform with no provider at all.

### Read / refresh lifecycle

| Step | Where | What happens |
| --- | --- | --- |
| Describe | `GET …/capability` | Platform list, per-platform `metrics_available`, connected-account counts, and window ceilings |
| Headline | `GET …/overview` | Published post/target counts, summed latest metrics, summed audience, `last_refreshed_at` |
| Compare | `GET …/platforms` | One row per platform with a `metrics_available` flag and summed latest metrics |
| Accounts | `GET …/accounts` | Per-account audience + the performance of its targets |
| Targets | `GET …/targets` | Each published destination with its latest snapshot (or `null`); filter + paginate |
| Chart | `GET …/history` | A daily series over a window, filtered by platform; oldest day first |
| Refresh | `POST …/refresh` | The **only** network path — reads each platform, appends a snapshot, returns tallies |

### Correctness decisions

- **Unavailable is not zero, end to end.** Every metric is `int | None` from the provider through
  the ORM, the schema, and the DTO. A platform that does not return a figure leaves the column
  `NULL`, an aggregate over all-unavailable values is `None`, and the UI renders a muted **N/A** —
  never `0`. `available` on each metric set counts how many fields were actually provided.
- **Providers are swappable and offline-testable.** `MetricsProvider` mirrors the Phase 6/7/9
  pattern — an `ABC` with a class-level `transport` hook — so every test drives an
  `httpx.MockTransport` with no network and no new dependency (`httpx` is already required).
  `as_int` treats an absent/empty/non-numeric value as `None` and a genuine `0` as `0`.
- **Immutable snapshots, timestamped.** A refresh **appends** a `post_metrics`/`account_metrics`
  row; it never overwrites history. `retrieved_at` records when the platform answered, so the
  chart plots measurements at the time they were taken, not when the worker happened to run.
- **Reading never calls a platform.** Every `GET` reads stored snapshots only, so opening the
  dashboard is free; `POST …/refresh` is the single route that reaches out, behind
  `analytics:refresh` (a write-shaped action held by the roles that may publish).
- **One platform's failure never freezes the dashboard.** A refresh reads each destination
  independently: a failure is collected in `errors` and the sweep continues, and a platform with
  no provider is counted as **skipped**, not failed.
- **Reading is separated from refreshing and both are honest.** A target that has never been
  refreshed is still listed, with `snapshot: null` — the UI shows "Not read", not `0`.
- **Capability gap, not a crash.** A platform with no public metrics API reports
  `metrics_available: false` and a refresh against it is skipped; a provider that cannot read the
  metric raises `MetricsUnavailableError`, translated to **`503`** rather than a stack trace.
- **Tenant isolation.** The workspace always comes from the resolved context (never request
  input), so another tenant's numbers and another workspace's target/account ids are **`404`,
  never `403`**.
- **No chart library.** The `/analytics` history chart is a dependency-free SVG sparkline
  (gridlines, axis labels, per-point tooltips) that draws a **gap** for a missing day and an
  explanatory empty state when a whole series is unavailable.

### Success criteria

- [x] Published post/target counts, views, likes, comments, shares, engagement, audience,
      account-level performance, platform comparisons, and a historical chart are all served from
      the 7 endpoints.
- [x] Every metric is nullable; a platform that does not report a figure yields `null` and the UI
      shows **N/A** (never `0`), while a genuine `0` is stored and shown as `0`.
- [x] `retrieved_at` is recorded per snapshot; the history series keeps only each day's newest
      snapshot per target, so several refreshes a day do not multiply a target's numbers.
- [x] `POST …/refresh` appends snapshots and returns tallies; a failure on one platform is
      reported in `errors` and the others still update.
- [x] Reading endpoints never contact a platform; only `refresh` does, behind `analytics:refresh`.
- [x] Tenant isolation: a foreign workspace/target/account answers `404`, never `403`; a viewer may
      read analytics but cannot trigger a refresh.
- [x] New tables and migration: **2 tables** (`post_metrics`, `account_metrics`), **21 total**;
      migration head `f241d12e080b`; `alembic check` → `No new upgrade operations detected`.
- [x] Full suite: **319 passed** (5 + 52 + 49 + 54 + 40 + 34 + 19 + 28 + 36), 0 failed.
- [x] `npm run typecheck` / `lint` / `build` clean (**17 routes**; `/analytics` now 9.08 kB).

### Not included in Phase 10 (by design)

- **Private / non-public analytics.** Only figures each platform's public API exposes are read; a
  metric behind a private endpoint (or one a platform does not publish at all) is reported as
  unavailable rather than approximated, exactly as the brief requires.
- **Scheduled background refresh.** A Celery task (`analytics.refresh_workspace`) exists, but no
  Beat entry drives it yet — a refresh is user-triggered from the dashboard. An automatic cadence
  belongs with the notifications/background work of a later phase.
- **"Best time to post".** The Phase 9 note deferred it here because it needs this delivery +
  engagement history; the history endpoint now supplies the data, but a recommendation surface is
  left to a later phase rather than shipped without a model the user can trust.
- **Exporting reports and real-time streaming.** Out of the Phase 10 scope in the master spec.

---

## Phase 11 — Notifications

**Goal:** tell a user what happened without them watching a screen — a publish that succeeded, a
publish that failed, a scheduled post about to fire, and a connected account whose token is about to
expire — as an **in-app** record they can read, mark read, and dismiss, plus an **optional email
copy** delivered by background processing. The in-app row is the source of truth and is **always**
written; preferences only decide whether a *copy* is also emailed, so turning one off can never hide
something from the bell. Delivery must be **idempotent** — a notice is sent **once**, even if the
sweep runs twice or two workers overlap.

### Deliverables

| # | Deliverable | Where |
| --- | --- | --- |
| 1 | Delivery core: record/list/count/mark-read/mark-all/delete, preferences, the three sub-sweeps + `sweep()` | `app/services/notifications.py` |
| 2 | `Notification.emailed_at` column + partial `ix_notifications_pending_email` index | `app/models/notification.py` |
| 3 | `NotificationPreference` model (one row per user, four `NOT NULL DEFAULT true` email opt-outs) | `app/models/notification.py` |
| 4 | Request/response schemas (`is_read` derived from `read_at`) | `app/schemas/notification.py` |
| 5 | 7 routes under `/api/v1/notifications` (list, unread-count, read, read-all, dismiss, get/put preferences) | `app/api/v1/endpoints/notifications.py` |
| 6 | Notifications settings (`NOTIFICATIONS_SWEEP_INTERVAL_SECONDS`, `…_EMAIL_BATCH_SIZE`, `…_REMINDER_LEAD_SECONDS`, `…_TOKEN_WARNING_DAYS`) | `app/core/config.py` |
| 7 | Celery task `notifications_sweep` + Beat entry `notifications.sweep` | `app/worker.py` |
| 8 | `reminder_sent_at` on `scheduled_posts`, `token_warning_sent_at` on `social_accounts` | `app/models/scheduling.py`, `app/models/social_account.py` |
| 9 | Router registration | `app/api/v1/router.py` |
| 10 | Migration (new column, partial index, two marker columns, `notification_preferences` table) | `alembic/versions/b8e2f5a91c47_phase11_notifications.py` |
| 11 | Service + API tests (16) | `app/tests/test_notifications.py` |
| 12 | Notification DTOs mirroring the backend schemas | `frontend/lib/types.ts` |
| 13 | `api.notifications.*` client group | `frontend/lib/api.ts` |
| 14 | Top-bar **notification bell** with unread badge + inbox dropdown (mark read, dismiss, mark all, click-through) | `frontend/components/notifications/notification-bell.tsx` |
| 15 | "Notifications" tab in Settings (email opt-out switches) | `frontend/components/settings/settings-tabs.tsx` |
| 16 | Mount the bell in the top bar | `frontend/components/app-shell/topbar.tsx` |

### Notification types & delivery

| Type | Written by | In-app | Email copy | Routes to |
| --- | --- | --- | --- | --- |
| `publish_success` | `services/publishing.py` `_notify()` (Phase 7 — not duplicated here) | ✔ | opt-out (`email_publish_success`) | `/history` |
| `publish_failure` | `services/publishing.py` `_notify()` (Phase 7) | ✔ | opt-out (`email_publish_failure`) | `/history` |
| `schedule_reminder` | `send_schedule_reminders` (lead = `NOTIFICATIONS_REMINDER_LEAD_SECONDS`) | ✔ | opt-out (`email_schedule_reminder`) | `/schedule` |
| `token_expiring` | `warn_expiring_tokens` (within `NOTIFICATIONS_TOKEN_WARNING_DAYS`) | ✔ | opt-out (`email_token_expiring`) | `/accounts` |
| `subscription`, `system` | reserved | ✔ | in-app only (no email flag) | — |

### Delivery lifecycle

| Step | Where | What happens |
| --- | --- | --- |
| Record | publishing engine, reminder/token sub-sweeps | One `notifications` row addressed to the **user** (`user_id`), `workspace_id` nullable |
| Email sweep | `dispatch_emails` | Pending rows (`emailed_at IS NULL`) in batches; honour preferences; stamp `emailed_at` whether or not a message went out |
| Remind | `send_schedule_reminders` | `scheduled_posts` still `pending` due within the lead window and not yet reminded → row + `reminder_sent_at` |
| Warn | `warn_expiring_tokens` | Connected accounts with a token expiring inside the window and not yet warned → row + `token_warning_sent_at` |
| Run | `notifications.sweep` (Celery Beat, every `NOTIFICATIONS_SWEEP_INTERVAL_SECONDS`) | Runs all three sub-sweeps and returns a `SweepResult` |
| Read/serve | `GET/POST/DELETE /api/v1/notifications/**` | List (paged ≤100), unread count, mark read, mark all read, dismiss, get/update preferences |

### Reliability decisions

- **Addressed to the user, not the workspace.** The inbox is per-account, so a notice outlives the
  workspace it referred to (`notifications.workspace_id` stays nullable with `ON DELETE SET NULL`).
  Every query is scoped on the caller's own `user_id`, so a foreign id answers **`404`, never
  `403`**.
- **In-app is the source of truth; email is an opt-in copy.** The four preferences only decide
  whether a *copy* is mailed. Turning one off can never hide something from the bell.
- **Idempotent by durable markers.** `notifications.emailed_at`, `scheduled_posts.reminder_sent_at`,
  and `social_accounts.token_warning_sent_at` are stamped as the work is done, so a repeated sweep
  or two overlapping workers cannot deliver a notice twice.
- **The publishing engine already wrote the outcome rows.** Phase 7's `_notify()` records
  `publish_success` / `publish_failure`; Phase 11 **delivers** them rather than duplicating the
  creation.
- **A transport failure does not lose the record.** The in-app row is committed first; a failed
  email leaves `emailed_at` un-set so the next sweep retries, and the failure is reported in the
  `SweepResult`, not raised into the request path.
- **One sweep, three sub-sweeps.** A single Beat entry drives email dispatch, schedule reminders,
  and token warnings, so there is one cadence to reason about and one place to observe.

### Success criteria

- [x] A publish outcome (success/failure) written by the Phase 7 engine is listed by
      `GET /api/v1/notifications` and counted by `GET /api/v1/notifications/unread-count`.
- [x] Marking one read, marking all read, and dismissing remove/flag the right rows; all three
      mutations commit.
- [x] A pending row is emailed **once** by the sweep (`emailed_at` stamped); a user who has opted
      out of that type is **not** emailed but still sees the in-app row.
- [x] A scheduled post due inside the lead window produces a `schedule_reminder` **once**
      (`reminder_sent_at`); an expiring token produces a `token_expiring` **once**
      (`token_warning_sent_at`).
- [x] Preferences default to all-on and round-trip through `GET`/`PUT /notifications/preferences`.
- [x] Tenant/user isolation: another user's notification id answers `404`, never `403`; an
      unauthenticated call is `401`.
- [x] New migration `b8e2f5a91c47`; **22 tables**; `alembic check` → `No new upgrade operations
      detected`.
- [x] Full suite: **335 passed** (5 + 52 + 49 + 54 + 40 + 34 + 19 + 28 + 36 + 16), 0 failed.
- [x] `npm run typecheck` / `lint` clean.

### Not included in Phase 11 (by design)

- **Real email transport.** Delivery reuses the Phase 3 pluggable email backend (`console` in dev,
  `smtp` in production); no new transport was added — the sweep simply calls `send_email`.
- **Push / mobile / SMS channels.** The brief lists in-app and email only.
- **Per-notification digests, batching policy, or quiet hours.** Preferences are the four per-type
  email opt-outs; anything finer is a later refinement, not fabricated here.

---

## Phase 12 — Free AI Model Management & Automatic Failover

**Objective.** Make the assistant (Phase 9) free and resilient: hold several models, pick one
automatically, and keep an assigned task alive when a model is unavailable, rate-limited or timing
out — with no manual model selection and no paid billing layer. This phase replaces the original
subscription/billing phase entirely; the application stays free to use.

> Phases 9–12 are one feature seen from four sides: Phase 9 built the *provider abstraction*,
> Phase 10 the *metrics* providers, Phase 11 *delivery of outcomes*, and Phase 12 the *routing* that
> decides which model actually answers. `ai_content.py` still owns the prompt; `ai_router.py` owns
> delivery.

### Files

| # | What | Path |
| --- | --- | --- |
| 1 | The two tables: `ai_models` (registry) + `ai_task_attempts` (append-only log) | `app/models/ai_model.py` |
| 2 | Router: candidate resolution, `complete()`, health checks, performance, task history | `app/services/ai_router.py` |
| 3 | New Ollama provider (`POST /api/chat`, no key) | `app/services/ai/ollama.py` |
| 4 | OpenAI provider gains a `spec` binding (per-model endpoint/key) | `app/services/ai/openai.py` |
| 5 | Provider registry now maps both names | `app/services/ai/__init__.py` |
| 6 | `complete()` routed through the registry instead of one vendor | `app/services/ai_content.py` |
| 7 | Request/response schemas for the registry, health, performance, history | `app/schemas/ai_model.py` |
| 8 | 8 admin routes under `/api/v1/ai/{workspace_id}` | `app/api/v1/endpoints/ai_models.py` |
| 9 | Routes registered on the v1 router | `app/api/v1/router.py` |
| 10 | `ai.health_sweep` Celery task + Beat entry | `app/worker.py` |
| 11 | New settings (`AI_ROUTER_*`, `AI_HEALTH_CHECK_INTERVAL_SECONDS`, `OLLAMA_*`) | `app/core/config.py` |
| 12 | New enums (`AIModelKind`, `AIModelStatus`, `AITaskStatus`) + `model_fallback` notification type + `ai:manage` permission | `app/db/enums.py`, `app/core/permissions.py` |
| 13 | Migration: two tables, the `model_fallback` category, `ai:manage` seeded to owner/admin | `alembic/versions/c3f7a2e91d08_phase12_ai_models.py` |
| 14 | Service + API tests (27) | `app/tests/test_ai_router.py` |
| 15 | `api.aiModels.*` client group + types | `frontend/lib/api.ts`, `frontend/lib/types.ts` |
| 16 | `/ai-models` page + registry component (failover order, performance, task history) | `frontend/app/(dashboard)/ai-models/page.tsx`, `frontend/components/ai/model-registry.tsx` |
| 17 | Sidebar entry (gated on `ai:manage`) + top-bar menu link + `model_fallback` → `/ai-models` | `frontend/components/app-shell/*`, `frontend/components/notifications/notification-bell.tsx` |

### Data flow

| Step | Where | What |
| --- | --- | --- |
| Configure | `POST/PATCH/DELETE /api/v1/ai/{ws}/models` | `ai_models` rows: `priority`, `enabled`, `kind`, `supports`, `credential_env` |
| Assign | `ai_router.complete(db, request, kind, …)` | Resolve ordered candidates (enabled → serves kind → credential present) |
| Route | same | Walk the list; each model call appends one `ai_task_attempts` row |
| Fail over | same | Retry a transient failure on the same model, then move to the next |
| Record | `ai_task_attempts` | Grouped by `task_id` = the failover chain; by `model_name` = performance |
| Announce | `notifications.record` | A `model_fallback` row when a backup produced the result |
| Monitor | `ai.health_sweep` (Celery Beat) + `POST …/models/health-check` | Refresh `status` / `last_error`; never gates traffic |
| Read | `GET …/models`, `…/performance`, `…/tasks`, `…/tasks/{id}` | Registry, aggregates, and the execution trail |

### Verification (all green)

- [x] Primary model answers → no switch, one attempt, `model_fallback` not raised.
- [x] Primary unavailable / 5xx / timeout → next model finishes; `switched=True`; a
      `model_fallback` notification names the backup.
- [x] Primary rate-limited (`429`) → no retry on the same model, straight to the next.
- [x] A transient failure retries on the same model up to `AI_ROUTER_MAX_ATTEMPTS` with exponential
      backoff, then moves on.
- [x] **Content refusal is never failed over** — `AIContentRejectedError` is re-raised immediately.
- [x] Local **Ollama** takes over as the last candidate; a model with no resolvable credential is
      skipped, never attempted.
- [x] All models fail → the last model's error propagates (existing status mapping intact).
- [x] Re-running a `task_id` that already succeeded is refused → **no duplicate execution**.
- [x] Task history groups by `task_id` and names the model that answered; per-model performance
      aggregates from the attempt log.
- [x] On-demand and Beat health sweeps refresh `status`; a recovered primary resumes with no action.
- [x] **No credential is ever returned or accepted** — only `credential_env` (a variable name); the
      key is read from the process environment at call time.
- [x] `ai:manage` gates every admin route (`404`-not-`403` isolation for a foreign workspace).
- [x] New migration `c3f7a2e91d08`; **24 tables**; `alembic check` → `No new upgrade operations
      detected`.
- [x] Full suite: **363 passed** (24 of them in the new `test_ai_router.py`), 0 failed.
- [x] `npm run typecheck` / `lint` / `build` clean (17 routes; `/ai-models` 8.97 kB).

### Not included in Phase 12 (by design)

- **LiteLLM.** The brief lists it as a *recommended* router; the project already has a
  provider-registry pattern (`publishers`, `oauth`, `metrics`, `ai`), so routing is implemented
  in-house with the same shape and no new dependency. LiteLLM remains a drop-in option for a future
  deployment that wants 100+ upstreams.
- **Billing / subscriptions / payments.** Explicitly out of scope — the platform is free.
- **Model auto-discovery or an auto-registered Ollama default.** An operator registers models
  (including the local Ollama one) through the API or the `/ai-models` page. With **no** rows the
  router falls back to the single environment-configured default, exactly as before Phase 12.
- **Weighted or cost-aware routing.** Failover is a strict priority walk; richer policies are a later
  refinement, not fabricated here.

---

## Phase 13 — Security & Performance

**Objective.** Harden the running application and remove avoidable latency without changing what it
does: throttle abusive callers, add the browser security headers and the CSRF guard a
cookie-authenticated API needs, keep a durable trail of who did what, and compress responses. Every
item the plan lists as a security **review** was re-checked here — most were already in place from
earlier phases; only rate limiting, the origin guard, security headers, and the audit trail are new
code.

### Files

| # | What | Path |
| --- | --- | --- |
| 1 | Fixed-window limiter: `CounterStore` / `MemoryStore` / `RedisStore` / `RateLimiter`, `get_limiter()`, `client_identity()`, and the `rate_limit(bucket, *, limit, window_seconds=None)` dependency factory | `app/core/rate_limit.py` |
| 2 | Global `SecurityHeadersMiddleware` (CSP, nosniff, DENY, Referrer-Policy, Permissions-Policy, COOP, prod-only HSTS) + `OriginGuardMiddleware` (CSRF) | `app/core/middleware.py` |
| 3 | Append-only `audit_logs` table | `app/models/audit.py` |
| 4 | `record(...)` write path + `list_events(...)` read path + the action-code catalogue | `app/services/audit.py` |
| 5 | `AuditLogPublic` / `AuditLogListPublic` schemas | `app/schemas/audit.py` |
| 6 | `GET /api/v1/workspaces/{workspace_id}/audit` (read-only, gated by the new `audit:read`) | `app/api/v1/endpoints/audit.py` |
| 7 | Middleware wiring (CORS → SecurityHeaders → OriginGuard → GZip → app) + `GZipMiddleware(1024)` | `app/main.py` |
| 8 | New settings (`RATE_LIMIT_*`) | `app/core/config.py` |
| 9 | `AUDIT_READ = "audit:read"` permission constant | `app/core/permissions.py` |
| 10 | Rate limiting + audit events on the credential routes (the `auth` bucket) | `app/api/v1/endpoints/auth.py` |
| 11 | AI generation limited to `RATE_LIMIT_AI_PER_MINUTE` | `app/api/v1/endpoints/ai.py` |
| 12 | Audit entries on registry writes (`ai_model.created/updated/deleted`) | `app/api/v1/endpoints/ai_models.py` |
| 13 | Route registered on the v1 router | `app/api/v1/router.py` |
| 14 | `AuditLog` registered on `Base.metadata` | `app/models/__init__.py`, `app/tests/test_models.py` |
| 15 | Suite-wide `RATE_LIMIT_ENABLED=false` | `app/tests/conftest.py` |
| 16 | Migration: `audit_logs` + its three indexes + `audit:read` seeded to owner/admin | `alembic/versions/d1a4b7c93e02_phase13_audit_logs.py` |
| 17 | Security + performance tests (20) | `app/tests/test_security_hardening.py` |

### Middleware chain

Request processing, outermost first (`app/main.py`):

| Order | Middleware | Purpose |
| --- | --- | --- |
| 1 | `CORSMiddleware` | Allow-list from `CORS_ORIGINS` (already configured — reviewed) |
| 2 | `SecurityHeadersMiddleware` | `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Permissions-Policy: geolocation=(), microphone=(), camera=()`, `Cross-Origin-Opener-Policy: same-origin`, deny-by-default CSP; HSTS only in production |
| 3 | `OriginGuardMiddleware` | Refuse a cross-origin state-changing request (**403**) — the CSRF control for cookie auth |
| 4 | `GZipMiddleware` | Compress responses ≥ 1 KB |
| 5 | application | Routers |

The CSP is `default-src 'none'; frame-ancestors 'none'; base-uri 'none'`; `/docs`, `/redoc`, and
`/openapi.json` are exempt because Swagger UI ships its own scripts. HSTS
(`Strict-Transport-Security`, one year, `includeSubDomains`) is added **only** when
`ENVIRONMENT=production`.

### Rate limiting

`app/core/rate_limit.py` is an in-house fixed-window limiter. Counters are shared across worker
processes: the default `RedisStore` uses `INCR` + `EXPIRE … nx=True`, and if Redis is unreachable the
limiter degrades to an in-process `MemoryStore` (logged once) rather than failing open or closed. The
caller is keyed by `client_identity()` — the first hop of `X-Forwarded-For`, else the socket peer. An
over-limit request answers **429** with a `Retry-After` header.

| Bucket | Where | Limit |
| --- | --- | --- |
| `auth` | `/register`, `/login`, `/refresh`, `/logout`, `/password/reset`, `/password/reset/confirm` | `RATE_LIMIT_AUTH_PER_MINUTE` (10/min) |
| AI | `POST /api/v1/ai/{workspace_id}/generate` | `RATE_LIMIT_AI_PER_MINUTE` (30/min) |

### Audit trail

`audit_logs` is **append-only** — the application never updates or deletes a row.
`services/audit.record(...)` stages a row on the caller's session (so the entry lands in the **same
transaction** as the change it describes) and never raises; `list_events(...)` reads newest-first with
a clamped page size (default 50, max 200). The read route is
`GET /api/v1/workspaces/{workspace_id}/audit`, with `limit`, `before` (cursor timestamp), and `action`
(prefix) filters; there is **no** write path in the API.

| Action | Recorded by |
| --- | --- |
| `auth.registered`, `auth.login.succeeded`, `auth.login.failed`, `auth.logout`, `auth.logout_all`, `auth.password.changed`, `auth.password.reset` | `app/api/v1/endpoints/auth.py` |
| `ai_model.created`, `ai_model.updated`, `ai_model.deleted` | `app/api/v1/endpoints/ai_models.py` |

A failed login is committed **before** the `401` is raised, so the rejection is still recorded.
Sign-in events are filed under the user's **primary workspace** (their oldest active membership),
because the read route is workspace-scoped and a sign-in is not otherwise tied to a workspace; an
unknown address on a failed login records no user and no workspace.

### Security review outcomes

Most items below were already implemented in earlier phases and were re-checked here, not rewritten.

| Area | Status | Note |
| --- | --- | --- |
| SQL injection | Already in place | All access through SQLAlchemy bound parameters; no raw SQL on any request path |
| Cross-site scripting | Already in place + new | JSON-only API (no server-rendered HTML); the deny-by-default CSP is the new defence-in-depth layer |
| CSRF | **New** | `OriginGuardMiddleware` |
| CORS | Already configured | Allow-list from `CORS_ORIGINS`; reviewed |
| OAuth security | Already in place | state/PKCE, single-use authorization state, encrypted token storage (Phase 6); reviewed |
| Token encryption | Already in place | AES-256-GCM at rest (Phase 6); reviewed |
| File-upload security | Already in place | MIME allow-list, size cap, chunked upload (Phase 5); reviewed |
| Rate limiting | **New** | `app/core/rate_limit.py` |
| Secure cookies | Already in place | Refresh token is HttpOnly/SameSite, Secure in production; the config guard refuses weak values |
| Password security | Already in place | bcrypt at the OWASP work factor, minimum length, rehash-on-cost-change, reuse rejection |
| Tenant isolation | Already in place | Every tenant query scoped via `TenantMixin.scoped`; a foreign workspace answers `404`, never `403` |
| Audit logs | **New** | `audit_logs` + `audit:read` |
| Secret management | Already in place | Env-only secrets; a production guard refuses a placeholder/short `SECRET_KEY`; AI keys are named by env var and never stored |
| Dependency scanning | Performed | `npm audit`: 2 advisories (1 moderate, 1 high) in the PostCSS copy bundled inside Next.js (`node_modules/next/node_modules/postcss`) — build-time only, and the only fix is the breaking `next@16`, so it is documented as a known issue; Python deps are pinned |

### Performance review

| Area | Outcome |
| --- | --- |
| Database queries | Reviewed — `audit_logs` ships three composite indexes matching its read patterns; the Phase 2–12 models already index their hot read paths, so no new index was required |
| API response times | `GZipMiddleware` now compresses responses ≥ 1 KB |
| Caching | The rate-limiter counters live in Redis, reusing the cache the project already runs |
| JSONB GIN indexes | Considered and deliberately **not** added — no query filters on a JSONB column yet (audit `detail` and `ai_models.supports` are read, never filtered) |
| Video, workers, storage, frontend | Reviewed — already bounded/configured in Phases 5, 8, 11; no change required |

### Verification (all green)

- [x] The fixed-window store starts a new window when the old one expires and keeps buckets separate per key.
- [x] `check()` returns the `Retry-After` only once over the limit; the dependency is a no-op when disabled.
- [x] The limiter keys on the first hop of `X-Forwarded-For`; the login route returns **429** with `Retry-After` after the limit.
- [x] Every JSON response carries the security headers; HSTS is absent outside production; the docs pages are CSP-exempt.
- [x] A cross-origin state-changing request is refused (**403**) while same-origin, origin-less, and safe (GET) requests pass.
- [x] Registration, a failed login (known and unknown address), a successful login, and an AI-model creation are each recorded in the audit trail.
- [x] The trail reads newest-first and can be filtered by action prefix; an owner can read it, a member without `audit:read` gets **403**, and a non-member gets **404**.
- [x] New migration `d1a4b7c93e02`; **25 tables**; `alembic check` → `No new upgrade operations detected`.
- [x] Full suite: **383 passed** (20 of them in the new file), 0 failed, 2 warnings, exit 0.
- [x] `npm run typecheck` → exit 0; `npm run lint` → "No ESLint warnings or errors" (no frontend source changed).

### Not included in Phase 13 (by design)

- **JSONB GIN indexes.** No query filters on a JSONB column yet, so an index would only cost writes.
- **An edge/WAF rate limiter.** The in-house limiter covers the application; a provider-side limiter
  belongs to the Phase 15 deployment topology.
- **Frontend performance work.** No frontend source changed this phase; the frontend was reviewed and
  needed none.
- **The `next@16` upgrade.** The two `npm audit` advisories sit in Next.js's own bundled PostCSS and the
  only fix is a breaking major upgrade, deferred rather than taken here.

## Phase 14 — Complete Testing

**Objective.** Close the two coverage gaps the earlier phases left: the Celery worker had only ever
been hand-verified, and the frontend had no tests at all. Widen the backend suite over the worker and
Beat schedules, and stand up a real frontend test runner with a first tranche of unit tests — without
changing any application behaviour.

### Files

| # | What | Path |
| --- | --- | --- |
| 1 | Worker + Beat-schedule tests (9) — tasks driven as objects, `session_scope` patched | `backend/app/tests/test_worker.py` |
| 2 | Vitest config (jsdom, `@/*` alias, automatic JSX, setup file) | `frontend/vitest.config.ts` |
| 3 | jest-dom matcher registration | `frontend/vitest.setup.ts` |
| 4 | Formatting helper tests | `frontend/lib/format.test.ts` |
| 5 | `cn` tests (incl. Tailwind conflict resolution) | `frontend/lib/utils.test.ts` |
| 6 | `formatDetail` + `ApiError` tests | `frontend/lib/api.test.ts` |
| 7 | Upload-validation tests | `frontend/lib/media-upload.test.ts` |
| 8 | `EmptyState` render test | `frontend/components/empty-state.test.tsx` |
| 9 | `Button` render/variant/`asChild` test | `frontend/components/ui/button.test.tsx` |
| 10 | `test` / `test:watch` scripts | `frontend/package.json` |

### What is covered

- **Worker (9 tests).** Every dispatched task name resolves on the Celery app; Beat carries all three
  periodic sweeps (`scheduling.dispatch_due`, `notifications.sweep`, `ai.health_sweep`); each sweep's
  period comes from its `*_INTERVAL_SECONDS` setting; `health.ping` answers; and the scheduling,
  notification and AI sweeps each execute their real work. `publishing.publish_target` and
  `analytics.refresh_workspace` coerce a string id and flatten their result. The task body runs
  **in-process** (no broker) because the task object is called directly.
- **Frontend (47 tests).** `format*` helpers incl. `null`/non-finite branches; `cn`; `formatDetail` for
  plain/blank/single/multi/unrecognised details; `ApiError` fields; upload validation for
  empty/oversize/undeterminable/disallowed files; and two component render tests.

### Verification (all green)

- [x] `pytest app/tests/test_worker.py` (with `POSTGRES_PORT=5434`) → **9 passed**.
- [x] `npm test` (`vitest run`) → **47 passed** across 6 files.
- [x] `npm run typecheck` → exit 0; `npm run lint` → clean; `npm run build` → compiles.
- [x] Full suite: **394 passed** (383 + 9 worker + 2 observability), 0 failed, 2 warnings, exit 0.

### Not included in Phase 14 (by design)

- **A frontend E2E suite** (register → connect → upload → compose → schedule). The unit runner is in
  place; a browser-driven E2E suite is deferred as an optional follow-up rather than blocked on here.
- **100 % coverage thresholds.** The goal was to cover the untested *behaviours*, not to hit a number.

## Phase 15 — Production Deployment

**Objective.** Give the finished platform a real deployment path: production images (non-root,
multi-stage), a compose stack that exposes only nginx, TLS termination, streamed large uploads, a
finally-wired error tracker, backups, and CI.

### Files

| # | What | Path |
| --- | --- | --- |
| 1 | Two-stage backend image: builder wheels → slim runtime, non-root `appuser` (1001), HEALTHCHECK, `alembic upgrade head && gunicorn …` | `backend/Dockerfile.prod` |
| 2 | Three-stage frontend image using the `standalone` output, non-root `nextjs` (1001) | `frontend/Dockerfile.prod` |
| 3 | Deployable 7-service stack (nginx/frontend/backend/worker/beat/postgres/redis); only nginx publishes ports | `docker-compose.prod.yml` |
| 4 | nginx single entry point: TLSv1.2/1.3, `http`→`https` (ACME first), `/api/`→backend with `proxy_request_buffering off` + 600 s timeouts + `client_max_body_size 2g`, `/`→frontend | `deploy/nginx/app.conf` |
| 5 | Backup script (timestamped `.sql.gz`, retention prune, cron-ready) | `deploy/backup.sh` |
| 6 | Restore script (stops writers, typed confirmation, `ON_ERROR_STOP=1`) | `deploy/restore.sh` |
| 7 | Production env template | `.env.production.example` |
| 8 | CI: backend (Postgres/Redis services + pytest) and frontend (npm ci → typecheck → lint → test → build) | `.github/workflows/ci.yml` |
| 9 | Opt-in Sentry wiring (`configure_error_tracking()`) | `backend/app/core/observability.py` |
| 10 | `SENTRY_DSN` / `SENTRY_TRACES_SAMPLE_RATE` settings (rate validated to `[0, 1]`) | `backend/app/core/config.py` |
| 11 | Sentry init called **before** the `FastAPI` app is built | `backend/app/main.py` |
| 12 | `gunicorn==23.0.0` (production process manager) | `backend/requirements.txt` |
| 13 | Observability tests (2) | `backend/app/tests/test_observability.py` |
| 14 | Deployment runbook (containers, first deploy, TLS, change/rollback, backups, monitoring, scaling, checklist, limitations, troubleshooting) | `docs/DEPLOYMENT.md` |

### Key decisions

- **Only nginx is public.** PostgreSQL and Redis are reachable only on the compose network — never
  published to the host, unlike the development `docker-compose.yml`.
- **Migrations run in the container start command** (`alembic upgrade head && gunicorn …`), so a
  deploy is one command and cannot serve against an un-migrated schema.
- **`beat` exists here and must stay a single replica.** The local stack never had it; without Beat the
  three periodic sweeps never run, and more than one replica would double-fire every sweep.
- **Sentry is strictly opt-in and PII-free.** With no `SENTRY_DSN` nothing is imported; when enabled,
  `send_default_pii=False` is hard-coded because bodies carry credentials and video bytes.
- **`configure_error_tracking()` runs before `FastAPI(...)`** — Sentry patches the ASGI app during
  `init`, so the order matters.
- **Streamed uploads.** `proxy_request_buffering off` plus 600 s timeouts keep a resumable multi-GiB
  upload out of nginx's memory and alive past the default proxy timeout.

### Verification (all green where executable)

- [x] `pytest app/tests/test_observability.py` → **2 passed** (Sentry off without a DSN; DSN passed
      through with `send_default_pii=False`).
- [x] Production boot guards: `ENVIRONMENT=production` with a placeholder/short `SECRET_KEY` or
      `REFRESH_COOKIE_SECURE=false` refuses to start.
- [x] Sentry sample-rate validator rejects values outside `[0, 1]`.
- [~] The container/nginx/CI files are **reviewed, not executed** — Docker Desktop cannot be installed
      on this machine without admin rights. They are written against the pinned versions and documented
      as reviewed-not-executed (see the known issues in `docs/PROJECT_STATE.md`).

### Not included in Phase 15 (by design)

- **Kubernetes / orchestration beyond Compose**, autoscaling, and managed database/Redis — the
  single-host Compose topology is the deployment target.
- **Object storage.** Media stays on the `media_data` volume behind `LocalStorage`; an S3/MinIO backend
  can drop in behind the same interface when needed (a database restore does not bring videos back, and
  the runbook says so).
- **An edge/WAF rate limiter and CDN.** nginx terminates TLS and proxies; provider-side protections are
  the environment's concern.
