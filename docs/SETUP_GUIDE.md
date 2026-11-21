# Setup Guide

Author: **Pramish Sharma**
Target OS: **Windows 10/11** — all commands below are PowerShell.

---

## 1. Prerequisites

| Tool | Version | Notes |
| --- | --- | --- |
| Node.js | 20+ (24 tested) | `node -v` |
| Python | **3.12** | `py -3.12 --version` — see the warning below |
| Docker Desktop | latest | Required for the containerised stack |
| Git | latest | Required for version control |
| FFmpeg + FFprobe | 6+ (9.0.2 tested) | Needed for video inspection and thumbnails (Phase 5). Optional: without it uploads complete but answer `503`. Not on `PATH` by default — see §4.3 |

> ⚠️ **Use Python 3.12, not 3.14.** The pinned dependency versions ship prebuilt wheels
> for 3.12; on 3.14 some packages (e.g. `pydantic-core`) fall back to a Rust source build
> and may fail. This machine has both `py -3.14` (default) and `py -3.12`.

> 🔑 **Connecting social accounts** (Phase 6) additionally needs a **developer application** on each
> platform you want to enable. That is external onboarding, not a local install — see §4.4 and
> [`OAUTH_SETUP.md`](OAUTH_SETUP.md). The app runs and every test passes without it.

---

## 2. Environment file

```powershell
# from the project root
Copy-Item .env.example .env
```

Then edit `.env` and set a strong `SECRET_KEY`. Generate one with:

```powershell
[Convert]::ToBase64String((1..48 | ForEach-Object { Get-Random -Max 256 }))
```

---

## 3. Option A — Run everything with Docker (recommended)

```powershell
docker compose up --build
```

| Service | URL |
| --- | --- |
| Frontend | http://localhost:3000 |
| API docs | http://localhost:8000/docs |
| Health | http://localhost:8000/api/v1/health |
| PostgreSQL | `localhost:5432` |
| Redis | `localhost:6379` |

Stop / reset:

```powershell
docker compose down        # stop, keep data volumes
docker compose down -v     # stop and DELETE the database volume (destructive)
```

---

## 4. Option B — Run natively (no Docker) ← **currently used on this machine**

Docker Desktop is not installed here and Chocolatey cannot install packages without an
elevated shell, so the stack runs natively. `.env` has already been created with
`localhost` hosts for `POSTGRES_HOST` / `REDIS_HOST` / `DATABASE_URL` / `REDIS_URL`.

### 4.0 Local services (user-local, no admin required)

Both live outside the repository, under `%USERPROFILE%\.ocp\`, and are **not** registered
as Windows services — restart them after a reboot.

**PostgreSQL 16.16** (data dir `%USERPROFILE%\.ocp\pgdata`, user `publisher`, trust auth,
database `publisher_db`, port 5432):

```powershell
$pg   = 'C:\Program Files\PostgreSQL\16\bin'
$data = "$env:USERPROFILE\.ocp\pgdata"

& "$pg\pg_ctl.exe" -D $data -l "$env:USERPROFILE\.ocp\postgres.log" start   # start
& "$pg\pg_ctl.exe" -D $data status                                          # status
& "$pg\pg_ctl.exe" -D $data stop                                            # stop
& "$pg\psql.exe"   -h 127.0.0.1 -p 5432 -U publisher -d publisher_db        # connect
```

Re-create from scratch only if the cluster is lost:

```powershell
& "$pg\initdb.exe" -D $data -U publisher --auth-local=trust --auth-host=trust -E UTF8
& "$pg\pg_ctl.exe" -D $data -l "$env:USERPROFILE\.ocp\postgres.log" start
& "$pg\createdb.exe" -h 127.0.0.1 -U publisher publisher_db
```

> The Chocolatey-managed `C:\Program Files\PostgreSQL\16\data` is empty and **not
> writable without admin**, so it is intentionally unused.

> ⚠️ **On this machine the project cluster answers on port `5434`, not 5432.** The Windows
> service `postgresql-x64-16` holds 5432 with `scram-sha-256` auth, so connecting there fails
> with `password authentication failed for user "publisher"`. Because the composed
> `DATABASE_URL` default already contains `:5432`, setting `POSTGRES_PORT` alone does **not**
> move the app — start the API with the full URL, and set `POSTGRES_PORT` for pytest (its
> `conftest.py` builds the test URL from `POSTGRES_HOST`/`POSTGRES_PORT`):
>
> ```powershell
> # API
> $env:DATABASE_URL='postgresql+psycopg://publisher:publisher_dev_password@127.0.0.1:5434/publisher_db'
> python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
> # tests
> $env:POSTGRES_PORT='5434'; pytest
> ```

**Redis 8.10.3** (portable build, `%USERPROFILE%\.ocp\redis`, port 6379):

```powershell
$redis = "$env:USERPROFILE\.ocp\redis\Redis-8.10.3-Windows-x64-cygwin"
& "$redis\redis-server.exe" --port 6379 --bind 127.0.0.1 --appendonly no
& "$redis\redis-cli.exe" -h 127.0.0.1 -p 6379 ping    # -> PONG
```

### 4.1 Backend

```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
```

Adjust `.env` for native mode:

```dotenv
POSTGRES_HOST=localhost
REDIS_HOST=localhost
DATABASE_URL=postgresql+psycopg://publisher:publisher_dev_password@localhost:5432/publisher_db
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/1
CELERY_RESULT_BACKEND=redis://localhost:6379/2
# Media (Phase 5) — leave MEDIA_ROOT unset to use backend/var/media
MEDIA_FFMPEG_BINARY=C:\Users\PIRATE\AppData\Local\ffmpeg\ffmpeg-9.0.2-essentials_build\bin\ffmpeg.exe
MEDIA_FFPROBE_BINARY=C:\Users\PIRATE\AppData\Local\ffmpeg\ffmpeg-9.0.2-essentials_build\bin\ffprobe.exe
```

Run the API:

```powershell
uvicorn app.main:app --reload --port 8000
```

Run a Celery worker (separate terminal, venv activated):

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
celery -A app.worker.celery_app worker --loglevel=info --pool=solo
```

> On Windows use `--pool=solo` (or install `eventlet`) — the default prefork pool is not
> fully supported.

Run **Celery Beat** too if you want scheduled posts to fire and notification email to be delivered
(a third terminal, venv activated). Beat emits the `scheduling.dispatch_due` sweep every
`SCHEDULING_SWEEP_INTERVAL_SECONDS` (60 s by default) and the `notifications.sweep` sweep every
`NOTIFICATIONS_SWEEP_INTERVAL_SECONDS` (60 s by default); without Beat, a queued schedule stays
`pending` and email copies stay undelivered until the next sweep runs:

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
celery -A app.worker.celery_app beat --loglevel=info
```

> On Windows run the worker with `--pool=solo`; Beat is a lightweight scheduler and needs no pool.
> The schedule itself lives in PostgreSQL, so a Beat restart loses nothing — the next tick catches
> up on any entry that came due while it was down (subject to `SCHEDULING_MISSED_GRACE_SECONDS`).

The scheduling knobs — `SCHEDULING_SWEEP_INTERVAL_SECONDS` (default `60`, the worst-case lateness
of a healthy publish), `SCHEDULING_BATCH_SIZE` (`100`), `SCHEDULING_MISSED_GRACE_SECONDS`
(`3600`), and `SCHEDULING_DEFAULT_TIMEZONE` (`UTC`) — all have sensible defaults and are read from
the environment like any other setting (see `backend/app/core/config.py`); set them only to change
the sweep cadence, the batch size, or the grace window.

### 4.2 Frontend

```powershell
cd frontend
Copy-Item .env.local.example .env.local
npm install
npm run dev
```

Open http://localhost:3000.

`frontend/.env.local` must point at the backend **as the browser reaches it**:

```dotenv
NEXT_PUBLIC_API_URL=http://localhost:8000
```

#### Routes

| Route | Auth required | Notes |
| --- | --- | --- |
| `/` | No | Marketing landing page |
| `/register` | No | Creates the account **and** its first workspace |
| `/login` | No | Honours `?next=<path>` after sign-in |
| `/forgot-password` | No | Sends a reset link (printed to the backend console in dev) |
| `/reset-password?token=…` | No | Sets a new password, then redirects to `/login` |
| `/verify-email?token=…` | No | Verifies on load |
| `/dashboard` | Yes | Real workspace + member data from the API |
| `/upload` | Yes | Real drag-and-drop uploader + media library (Phase 5) |
| `/accounts` | Yes | Real connect / refresh / disconnect UI for Instagram, Facebook, YouTube, TikTok (Phase 6) |
| `/compose` | Yes | Real composer — pick a video, per-platform captions, then **Publish Everywhere** or **Schedule for later** (Phase 7–8) |
| `/schedule` | Yes | Real scheduling calendar — upcoming/past queue, reschedule, cancel (Phase 8) |
| `/assistant` | Yes | Real AI assistant — draft captions, hashtags, descriptions, rewrites, ideas (Phase 9) |
| `/analytics` | Yes | Real analytics dashboard — overview, platform comparison, SVG history chart, audience (Phase 10) |
| `/history`, `/billing` | Yes | Phase-labelled placeholders until their backend lands |
| `/settings` | Yes | Account, password, sessions, **notifications**, and system health — fully functional |

> If **sign-in fails with 404** while the backend "is running", check `GET /openapi.json` on
> port 8000. A backend process started before Phase 3 serves only the health routes; stop it
> and start a fresh one with `uvicorn app.main:app --reload --port 8000`.

### 4.3 FFmpeg (video inspection & thumbnails)

FFmpeg is **optional but recommended**. With it, every upload is inspected for duration and
resolution and gets a JPEG thumbnail. Without it, uploads still complete — the media row is
written and served — but `POST …/complete` answers **`503`**, no thumbnail is produced, and
`GET …/capabilities` reports `ffmpeg_available: false`. It is a soft dependency by design.

This machine uses a portable "essentials" build (no admin, not on `PATH`):

```powershell
$ff = "$env:LOCALAPPDATA\ffmpeg\ffmpeg-9.0.2-essentials_build\bin"
& "$ff\ffmpeg.exe"  -version    # confirm both binaries run
& "$ff\ffprobe.exe" -version
```

Point the backend at them via `backend/.env` (see §4.1):

```dotenv
MEDIA_FFMPEG_BINARY=C:\Users\PIRATE\AppData\Local\ffmpeg\ffmpeg-9.0.2-essentials_build\bin\ffmpeg.exe
MEDIA_FFPROBE_BINARY=C:\Users\PIRATE\AppData\Local\ffmpeg\ffmpeg-9.0.2-essentials_build\bin\ffprobe.exe
```

If the binaries are on `PATH`, the defaults (`ffmpeg` / `ffprobe`) already work and the two
variables can be left unset. The rest of the media settings — `MEDIA_ROOT`,
`MEDIA_MAX_UPLOAD_BYTES`, `MEDIA_ALLOWED_MIME_TYPES`, `MEDIA_CHUNK_SIZE_BYTES`,
`MEDIA_UPLOAD_SESSION_TTL_HOURS`, `MEDIA_SIGNED_URL_TTL_SECONDS`, `MEDIA_THUMBNAIL_MAX_WIDTH`,
`MEDIA_FFMPEG_TIMEOUT_SECONDS` — all have sensible defaults and are documented in
[`../.env.example`](../.env.example).

> The upload endpoint is a multi-step protocol (declare, then one `PUT` per chunk at an explicit
> `offset`, then `complete`), so driving it from `curl` by hand is tedious. Use the dashboard
> (`/upload`) or a scripted client. Each `PUT` sends the raw chunk as the request body
> (`--data-binary @part`); no multipart form is involved.

### 4.4 Social account connections (OAuth)

Connecting Instagram, Facebook, YouTube, or TikTok needs a **developer application** on each
platform and the client credentials in `backend/.env`. The `/accounts` page reads
`GET /api/v1/accounts/{ws}/capabilities` first, so a platform whose credentials are absent shows
as **"not configured"** instead of failing when someone clicks Connect.

Minimum `.env` for the platforms you want to enable:

```dotenv
# Public origin the browser reaches THIS API on — used to build the redirect URI.
BACKEND_PUBLIC_URL=http://localhost:8000
# Where the backend bounces the browser after the callback.
OAUTH_SUCCESS_REDIRECT=http://localhost:3000/accounts
OAUTH_FAILURE_REDIRECT=http://localhost:3000/accounts
# Optional: a dedicated secret for token encryption. Falls back to SECRET_KEY.
# Generate a strong value the same way as SECRET_KEY (see §2).
OAUTH_ENCRYPTION_KEY=

# Meta (Facebook + Instagram share a Facebook app)
META_GRAPH_VERSION=v21.0
FACEBOOK_CLIENT_ID=
FACEBOOK_CLIENT_SECRET=
INSTAGRAM_CLIENT_ID=
INSTAGRAM_CLIENT_SECRET=

# Google (YouTube Data API v3)
YOUTUBE_CLIENT_ID=
YOUTUBE_CLIENT_SECRET=

# TikTok (Login Kit v2)
TIKTOK_CLIENT_KEY=
TIKTOK_CLIENT_SECRET=
```

Register this exact redirect URI on each platform's developer console (it must match character
for character):

```
{BACKEND_PUBLIC_URL}/api/v1/accounts/{platform}/callback
```

> Filling in a **fake** client id/secret only makes a platform show as "configured"; the actual
> authorization will fail at the platform's consent screen. Real credentials require developer
> applications, app review, and (for Instagram/YouTube) publishing audits and account-eligibility
> rules. The full per-platform walkthrough — apps, scopes, review, and eligibility — is in
> [`OAUTH_SETUP.md`](OAUTH_SETUP.md).

---

### 4.5 AI content generation (OpenAI)

The `/assistant` page and the composer's **Draft with AI** panel draft captions, hashtags,
descriptions, platform-specific rewrites, and content suggestions. The backend talks to a
model vendor over its **REST API** (`httpx`), so there is **no vendor SDK to install** — only
an API key, which stays **server-side**. The `/assistant` page reads
`GET /api/v1/ai/{ws}/capability` first, so an unconfigured model shows as **"not configured"**
instead of failing when someone clicks Generate.

Minimum `.env` to enable generation:

```dotenv
# Server-side only — never sent to the browser, logged, or echoed in a response.
OPENAI_API_KEY=sk-...
# Optional: point at a compatible gateway/proxy instead of OpenAI directly.
OPENAI_BASE_URL=https://api.openai.com/v1
# Default model; any chat-completions model works.
OPENAI_MODEL=gpt-4o-mini
OPENAI_HTTP_TIMEOUT_SECONDS=60
OPENAI_MAX_OUTPUT_TOKENS=800
OPENAI_TEMPERATURE=0.8
AI_MAX_BRIEF_CHARS=2000
```

> With `OPENAI_API_KEY` empty, generation answers **`503`** and the UI shows a warning — nothing
> else breaks. The test suite never needs a key: the model call is stubbed with an injected
> `httpx` transport.

### 4.6 Analytics dashboard

The `/analytics` page shows how published videos perform — views, likes, comments, shares,
engagement, audience, per-platform comparisons, and a history chart. Metrics are read from each
platform's **own** API **through the existing Phase 6 connections**, so there is **nothing extra to
configure**: an account you have already connected is all a refresh needs.

One thing to understand before reading the numbers:

> A platform that does **not** report a metric leaves it blank. The dashboard shows **N/A** for
> such a figure, never `0` — a real zero and an unavailable value are different facts. Nothing is
> estimated or back-filled.

Opening `/analytics` reads only numbers already stored, so it never calls a platform. Pressing
**Refresh** (`POST /api/v1/analytics/{ws}/refresh`, requires `analytics:refresh`) is the one action
that reaches the platforms; it appends a timestamped snapshot per published target and connected
account, and reports any single failure in `errors` without stopping the rest.

All four settings have working defaults; set them only to tune a refresh (documented in
[`../.env.example`](../.env.example)):

```dotenv
ANALYTICS_HTTP_TIMEOUT_SECONDS=30   # per-call limit when reading a platform
ANALYTICS_DEFAULT_WINDOW_DAYS=30    # default history window
ANALYTICS_MAX_HISTORY_POINTS=180    # ceiling on daily points the history endpoint returns
ANALYTICS_MAX_REFRESH_TARGETS=200   # how many targets/accounts one refresh may pull
```

> There is no metrics credential to add: a platform with no public metrics API simply reports
> `metrics_available: false` and is skipped by a refresh. The test suite needs no platform access —
> every provider is driven against an injected `httpx` transport.

### 4.7 Notifications

The top-bar **bell** and the **Notifications** tab in `/settings` need **no configuration**. In-app
notifications are always recorded, and delivery of an email *copy* reuses the **Phase 3 email
backend** (`EMAIL_BACKEND`): `console` in development (the message is printed to the backend log) and
`smtp` in production. There is nothing new to install — the background sweep simply calls the
existing `send_email`.

The four opt-out switches are per-user and default to **on**; they only affect the email copy, never
the in-app record. All four settings have working defaults (documented in
[`../.env.example`](../.env.example)):

```dotenv
NOTIFICATIONS_SWEEP_INTERVAL_SECONDS=60   # how often Beat runs the delivery sweep
NOTIFICATIONS_EMAIL_BATCH_SIZE=100        # pending rows considered per sweep
NOTIFICATIONS_REMINDER_LEAD_SECONDS=3600  # how far ahead a scheduled post is reminded
NOTIFICATIONS_TOKEN_WARNING_DAYS=7        # warn when a connected token expires within N days
```

> Email copies are delivered by **Celery Beat** (`notifications.sweep`, §4.1) — without Beat the
> in-app bell still works, but no email leaves the server. Each notice is delivered **once** (durable
> `emailed_at` / `reminder_sent_at` / `token_warning_sent_at` markers), so re-running the sweep is
> safe. The test suite needs no mail server: `send_email` is monkeypatched.

### 4.8 AI model management & automatic failover

Generation (§4.5) no longer talks to a vendor directly: every call flows through a **router** that
walks an administrator-managed list of models in priority order and **fails over** to the next when
one returns an error. Two model kinds are supported — an **OpenAI-compatible** endpoint (a key,
server-side) and a **local Ollama** server (no key). Manage the list at `/ai-models` (requires the
`ai:manage` permission) — nothing there is required to *start*; an empty registry simply answers
`503` and the `/assistant` page shows **"not configured"**.

```dotenv
# --- Router behaviour (all have working defaults) ---
AI_ROUTER_MAX_ATTEMPTS=2                # retries per model before switching to the next
AI_ROUTER_BACKOFF_BASE_SECONDS=1.0      # exponential backoff base between retries
AI_ROUTER_BACKOFF_MAX_SECONDS=8.0       # backoff ceiling
AI_HEALTH_CHECK_INTERVAL_SECONDS=600    # how often Beat probes each model (ai.health_sweep)

# --- Local Ollama (only needed for an `ollama` model) ---
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.1
OLLAMA_TIMEOUT_SECONDS=120
```

> An `openai_compatible` model row stores only the **name** of the env var holding its key (e.g.
> `OPENAI_API_KEY`); the key itself is read from the environment at call time and **never** stored,
> logged, or returned. Health is **advisory**: the router always walks the whole ordered list, so a
> model the sweep marked down that later recovers resumes automatically. The test suite needs no key
> and no Ollama — every provider is driven against an injected `httpx` transport.

### 4.9 Security hardening & rate limiting

Phase 13 adds a fixed-window **rate limiter** and browser **security headers**; nothing needs to be
installed. The limiter counts calls per caller address (the first hop of `X-Forwarded-For`, else the
socket peer) in **Redis**, so the limit is shared across worker processes — if Redis is unreachable it
falls back to an in-process counter (logged once) rather than failing open or closed. An over-limit
request answers **429** with a `Retry-After` header.

```dotenv
RATE_LIMIT_ENABLED=true              # master switch for the limiter
RATE_LIMIT_WINDOW_SECONDS=60         # fixed-window length
RATE_LIMIT_AUTH_PER_MINUTE=10        # credential routes: /register, /login, /refresh, /logout, password reset
RATE_LIMIT_AI_PER_MINUTE=30          # POST /api/v1/ai/{ws}/generate
```

> The test suite sets `RATE_LIMIT_ENABLED=false` for the whole run, so ordinary tests can
> authenticate repeatedly; only `app/tests/test_security_hardening.py` turns it back on. The security
> headers (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy`,
> `Cross-Origin-Opener-Policy`, a deny-by-default CSP, and HSTS in production) and the CSRF origin
> guard are applied automatically by middleware and need no configuration.

### 4.10 Frontend tests (Vitest)

Phase 14 adds the frontend unit tests. The runner is **Vitest** on **jsdom** with **Testing Library**;
it installs with `npm install` (no extra setup) and shares the `@/*` alias the app uses.

```powershell
cd frontend
npm test            # vitest run — 47 tests across 6 files
npm run test:watch  # re-run on change while developing
```

The tests cover the formatting helpers, the API client (`formatDetail` / `ApiError`), upload
validation, and two component render tests. No backend, no network, and no browser are needed.

### 4.11 Production deployment

Phase 15 adds the deployable stack (`docker-compose.prod.yml`, `Dockerfile.prod` ×2, nginx, backups,
CI). It requires **Docker** — which this machine does not have (see the known issues in
[`PROJECT_STATE.md`](PROJECT_STATE.md)) — and needs no local setup beyond a filled-in `.env`. The
end-to-end runbook (first deploy, TLS, change/rollback, backups, monitoring, scaling, troubleshooting)
is in [`DEPLOYMENT.md`](DEPLOYMENT.md).

---

## 5. Database migrations (Alembic)

The connection string comes from application settings, so `alembic.ini` needs no editing.

```powershell
cd backend
.\.venv\Scripts\Activate.ps1

alembic upgrade head      # apply every migration (creates the 16 core tables + auth tables + seed data)
alembic current           # show the revision the database is on
alembic check             # exit non-zero if the models and the database have drifted
```

Existing revisions:

| Revision | Description |
| --- | --- |
| `0d24e6e89e59` | `create core tables` — 16 tables, foreign keys, CHECK constraints, 27 indexes |
| `70ed893bdb70` | `seed system roles and plans` |
| `14ae69daee8c` | `add auth sessions and verification tokens` — the two Phase 3 tables |
| `5347775d077d` | `add oauth authorization states` — the Phase 6 OAuth state table |
| `f241d12e080b` | `add analytics metrics` — the Phase 10 `post_metrics` / `account_metrics` snapshot tables |
| `b8e2f5a91c47` | `phase11 notifications` — notification delivery columns + the `notification_preferences` table |
| `c3f7a2e91d08` | `phase12 ai models` — the `ai_models` registry + the append-only `ai_task_attempts` log, the `model_fallback` notification category, and the `ai:manage` permission |
| `d1a4b7c93e02` | `phase13 audit logs` — the append-only `audit_logs` table + its three composite indexes, and the `audit:read` grant to the owner/admin system roles (**head**) |

After changing a model, generate the next revision:

```powershell
alembic revision --autogenerate -m "describe the change"
alembic upgrade head
```

> `upgrade head` is required before the test suite, because `app/tests/conftest.py`
> builds the test database by running the migrations.

---

## 6. Testing

### Backend

PostgreSQL must be running — the database and auth tests execute real SQL.

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
$env:POSTGRES_PORT='5434'    # this machine — see §4.0
pytest                       # 394 tests
pytest app/tests/test_models.py -v          # unit only, no database needed
```

Expected: **394 passed**, 0 failed, 2 warnings, exit 0.

> If you see many `Socket is not connected` errors all at once, the local **PostgreSQL** server has
> died — restart it (see §4.0) and re-run. Start it **detached** (e.g. via a scheduled task / `Win32_Process.Create`)
> so it is not killed when the shell that launched it exits.

| File | Scope | Needs PostgreSQL |
| --- | --- | --- |
| `app/tests/test_health.py` | 5 tests — config + health probe shapes | No (asserts shape either way) |
| `app/tests/test_models.py` | 35 tests — model registration, columns, constraints, enums | No |
| `app/tests/test_security.py` | 15 tests — password hashing, JWT claims, opaque tokens | No |
| `app/tests/test_storage.py` | 21 tests — storage keys, root-escape guard, signed URLs | No |
| `app/tests/test_video.py` | 6 tests — FFmpeg discovery, FFprobe parsing, thumbnails | No (needs FFmpeg; skips without it) |
| `app/tests/test_db_integration.py` | 26 tests — constraints, cascades, tenant isolation, seed data | **Yes** |
| `app/tests/test_auth.py` | 32 tests — full auth + authorization flows through the real app | **Yes** |
| `app/tests/test_media.py` | 27 tests — resumable upload, validation, signed serving, RBAC/tenant isolation | **Yes** (needs FFmpeg for the inspection steps) |
| `app/tests/test_oauth.py` | 19 tests — per-platform authorization URLs, PKCE, scopes, token exchange/refresh/revoke (network stubbed) | No |
| `app/tests/test_accounts.py` | 20 tests — OAuth connect/callback, state single-use, token encryption, refresh, disconnect, RBAC/tenant isolation | **Yes** |
| `app/tests/test_publishers.py` | 16 tests — per-platform publishers against an injected `httpx` transport (capability, validation, caption build, error classification, platform-confirmed status) | No |
| `app/tests/test_publishing.py` | 18 tests — compose/publish orchestration, retries/dead-letter, idempotency, partial-failure isolation, RBAC/tenant isolation (Celery dispatch recorded, not run) | **Yes** |
| `app/tests/test_scheduling.py` | 19 tests — queue/reschedule/cancel, the due-sweep (fires once, misses when overdue), time zones, idempotency-key parity, RBAC/tenant isolation | **Yes** |
| `app/tests/test_ai.py` | 28 tests — provider abstraction + the generate/history/accept endpoints against an injected `httpx` transport (capability gap, prompt shaping, error mapping, RBAC/tenant isolation) | **Yes** |
| `app/tests/test_analytics.py` | 36 tests — per-platform metrics providers (network stubbed) and the read/refresh endpoints; nullable metrics (`NULL` ≠ `0`), append-only snapshots, RBAC/tenant isolation | **Yes** |
| `app/tests/test_notifications.py` | 16 tests — in-app inbox, unread count, mark read/all/dismiss, preferences, and the idempotent email/reminder/token sweeps (`send_email` monkeypatched) | **Yes** |
| `app/tests/test_ai_router.py` | 24 tests — model registry CRUD, the priority-ordered failover walk, retry/backoff, rate-limit and content-refusal rules, duplicate-task refusal, health sweep, per-model performance, and the 8 `/ai/models` + `/ai/tasks` endpoints (network stubbed) | **Yes** |
| `app/tests/test_security_hardening.py` | 20 tests — the fixed-window rate limiter (`Retry-After`, `X-Forwarded-For` keying, disabled no-op), the security headers + CSP/HSTS rules, the CSRF origin guard, and the append-only audit trail (write paths + the read endpoint's RBAC/tenant isolation) | **Yes** |
| `app/tests/test_worker.py` | 9 tests — the Celery task registry, all three Beat sweeps + their configured intervals, and the worker tasks driven in-process (`session_scope` patched) | **Yes** |
| `app/tests/test_observability.py` | 2 tests — `configure_error_tracking()` is off without a `SENTRY_DSN` and passes a configured DSN + sample rate through with `send_default_pii=False` | No |

The suite creates and uses its own database, **`publisher_test`**, separate from
`publisher_db`. `conftest.py` drops and rebuilds its `public` schema from the
migrations once per session, then rolls back a transaction after each test — so
your development data is never touched and tests never see each other's rows.

> Override the test database name with `$env:TEST_POSTGRES_DB = "publisher_test_2"` if
> you need to run two suites concurrently.

### Frontend

```powershell
cd frontend
npm run typecheck
npm run lint
npm test          # vitest run — 47 tests
npm run build
```

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `docker : not recognized` | Docker Desktop not installed | Install Docker Desktop and restart the terminal |
| `git : not recognized` | Git not installed | Install Git for Windows |
| `pydantic-core` builds from source / fails | Python 3.14 selected | Recreate venv with `py -3.12 -m venv .venv` |
| `Activate.ps1 cannot be loaded` (execution policy) | PowerShell script policy | `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` |
| Frontend shows all badges red | Backend not running or wrong URL | Start the backend; check `NEXT_PUBLIC_API_URL` in `frontend/.env.local` |
| Sign-in returns 404 (`{"detail":"Not Found"}`) | A backend started before Phase 3 is still bound to :8000, so it serves only the health routes | Stop that process and restart: `uvicorn app.main:app --reload --port 8000`. Confirm with `GET /openapi.json` |
| Sign-in silently fails / redirects back to `/login` | Refresh cookie not sent | The cookie is `HttpOnly` and scoped to `/api/v1/auth`; make sure the browser reaches the API on the **same host** you configured, and that `CORS_ORIGINS` includes the frontend origin |
| `/health/db` → `unreachable` | PostgreSQL not running / wrong host | Start the DB; inside Docker use host `postgres`, natively use `localhost` |
| `/health/redis` → `unreachable` | Redis not running / wrong host | Start Redis; same host rule as above |
| `password authentication failed for user "publisher"` | The app is reaching the `scram-sha-256` service on 5432 instead of the project cluster on 5434 | Start the API with the full `DATABASE_URL` (`…@127.0.0.1:5434/publisher_db`) and run pytest with `POSTGRES_PORT=5434` — see §4.0 |
| Upload finishes but `complete` → **503**, no thumbnail | FFmpeg/FFprobe not found | Set `MEDIA_FFMPEG_BINARY` / `MEDIA_FFPROBE_BINARY` (or put FFmpeg on `PATH`) — see §4.3 |
| A media preview/thumbnail stops loading after a while | The signed URL expired (default TTL 900 s) | Reload the page — every read mints fresh URLs; `urls_expire_at` reports when they lapse |
| `/accounts` shows a platform as **"not configured"** | No client credentials for that platform in `backend/.env` | Fill in the `*_CLIENT_ID` / `*_CLIENT_SECRET` values (§4.4) and restart the backend |
| Connecting an account returns **503** | The server has no client credentials for that platform | Same as above — `503` is an operator problem, not a caller error |
| Connecting an account returns **409** (`needs_reconnect`) | The stored grant has no usable refresh token | Disconnect and reconnect the account from `/accounts` |
| The OAuth callback redirects with `?error=…` | State rejected, or the platform refused the authorization | Check the platform's developer console (redirect URI, scopes, app review); the exact reason is in the `error` query parameter and the backend log |
| A queued post never publishes at its time | **Celery Beat is not running**, so the `scheduling.dispatch_due` sweep never ticks | Start Beat (`celery -A app.worker.celery_app beat --loglevel=info`) alongside the worker — the entry stays `pending` until a sweep reaches it |
| A schedule shows **`missed`** | The entry came due more than `SCHEDULING_MISSED_GRACE_SECONDS` after its time (Beat/worker was down, or the machine slept) | Re-queue the post from `/schedule`; raise `SCHEDULING_MISSED_GRACE_SECONDS` if the worker is routinely offline |
| Scheduling returns **`422`** with "not a recognised IANA time zone" | The `timezone` value is not an IANA name | Send an IANA zone like `Europe/London` (not a UTC offset or an abbreviation like `BST`) |
| The bell shows a notice but **no email arrives** | Celery Beat is not running, so `notifications.sweep` never dispatches copies — or the recipient opted this type out — or `EMAIL_BACKEND=console` merely prints the message | Start Beat (§4.1); check the Settings → Notifications tab; set `EMAIL_BACKEND=smtp` for real delivery (§4.7) |
| Port already in use | Another process on 3000/8000/5432/6379 | Stop the conflicting process or change the mapped port |
