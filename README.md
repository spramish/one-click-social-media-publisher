# One-Click Social Media Video Publisher

Upload a video **once** and publish it to **Instagram, Facebook, YouTube, and TikTok**
from a single dashboard.

> Author: **Pramish Sharma**

---

## Status

**Phases 1–15 are implemented — the project is complete.** The frontend, backend, database, and Redis
run locally with verified health checks, the full relational schema is in place, and authentication and
authorization are complete: registration, login, logout, email verification, password
hashing, password reset, short-lived JWT access tokens, rotating `HttpOnly` refresh tokens
with theft detection, session management, protected routes, and role-based workspace
permissions. **394 backend tests and 47 frontend tests pass.**

**Phase 4** adds the professional dashboard on top of that API: a marketing landing page,
five auth pages, a responsive app shell with grouped sidebar navigation and an account menu,
ten dashboard pages, and a fully working Settings area (account, password change, active
sessions, and system health). Access tokens are held in memory only and the refresh token
stays in the `HttpOnly` cookie. Pages whose backend arrives in a later phase show honest,
phase-labelled placeholders — no mock data or fake charts. `typecheck`, `lint`, and `build`
are clean (**17 routes**).

**Phase 5** adds video upload and media management. Uploads use a **resumable, chunked
protocol** (declare → append-at-offset → resume on `409` → complete → abort), validated
server-side by **MIME type and file size before a single byte moves**. **FFmpeg/FFprobe**
inspect each stored file for duration and resolution and generate a JPEG thumbnail, and
files are served only through **signed, expiring URLs** (HMAC-SHA256, 900 s) — never raw
paths. The `/upload` page is a real drag-and-drop uploader with an in-browser preview,
per-file validation, live progress, a searchable media library, replace, and delete.

**Phase 6** adds **social account connections** through each platform's **official** OAuth 2.0
flow — Instagram and Facebook (Meta Graph v21.0), YouTube (Google + Data API v3), and TikTok
(Login Kit v2 + Content Posting API). The `state` parameter is single-use, expiring, and stored
only as a **SHA-256 digest**; **PKCE** is used where the platform supports it. Connector tokens
are sealed with **AES-256-GCM** and **never reach the browser** — the UI sees only expiry and
health metadata. Tokens refresh automatically before they lapse, an unusable grant is flagged
`needs_reconnect`, and disconnecting revokes the grant upstream where the platform allows it.
The `/accounts` page is the real connect / refresh / reconnect / disconnect interface.

**Phase 7** adds the **multi-platform publishing engine**: a `Publisher` abstraction with one
adapter per platform, and a **"Publish Everywhere"** orchestration that validates the video, the
accounts, and each platform, then creates **one independent job per destination** — so a failure
on one platform can never cancel another. A target is reported published **only when the platform
itself confirms it**; retries use exponential backoff (honouring `Retry-After`), duplicate
publishes are refused by a content-derived **idempotency key**, transient failures are retried
until a **dead-letter**, and every attempt is logged with secrets redacted. The `/compose` page is
the real composer.

**Phase 8** adds **content scheduling**: queue a composed post for a future time and let a
**Celery Beat due-sweep** publish it automatically. The schedule is **durable** (it lives in
PostgreSQL, so it survives a restart) and each entry is claimed **atomically**
(`SELECT … FOR UPDATE SKIP LOCKED`) so it fires **exactly once**. A due entry is dispatched through
the **same** Phase 7 path — identical per-target jobs, idempotency keys, retries, and dead-letter
handling — so scheduling cannot bypass the reliability layer. Times are stored in **UTC** with the
caller's IANA zone, so the UI shows the local time the user actually picked. Queued posts can be
**rescheduled or cancelled** before they fire, and an entry that comes due while the worker was
down is marked `missed` rather than publishing stale content. The `/schedule` page is the real
calendar.

**Phase 9** adds **AI content generation**. A small `AIProvider` abstraction talks to a model
vendor over its **REST API** (`httpx`, no SDK), so the vendor is **swappable** and every test
runs offline against an injected transport. The assistant drafts a **caption**, a **hashtag
set**, a **video description**, a **platform-specific rewrite**, or **content ideas** — shaped
by an optional tone, style, and target platform — and **nothing is ever published
automatically**: a generation is only stored (`is_accepted = false`) and publishing still
requires the explicit Phase 7 composer action. The API key stays **server-side** and is never
sent to the browser, logged, or echoed in an error. The `/assistant` page is the real
review-and-accept interface, and the composer gains a **Draft with AI** helper that fills the
base caption from a short brief.

**Phase 10** adds the **analytics dashboard**. Metrics are read from each platform's **own API**
through a provider abstraction (`app/services/metrics/`) that mirrors the publishing and AI
layers — swappable, and testable offline against an injected `httpx` transport. Two append-only
snapshot tables (`post_metrics`, `account_metrics`) store **immutable measurements** with a
`retrieved_at` timestamp, and every metric column is **nullable on purpose**: `NULL` means the
platform *did not report* that metric, while `0` is a genuine zero, so the UI shows **N/A — never
zero** for a metric a platform does not expose. The dashboard covers published post count, views,
likes, comments, shares, engagement, audience/account metrics, **platform comparisons**, and a
**historical performance chart** (a hand-built SVG sparkline — no chart dependency). Reading is
separate from refreshing, and only `POST …/refresh` talks to the platforms. The `/analytics`
page is the real dashboard.

**Phase 11** adds **notifications**. A publish that succeeded or failed (written by the Phase 7
engine), a scheduled post about to fire, and a connected account whose token is about to expire each
become an **in-app** record, delivered alongside an **optional email copy** by a **Celery Beat
sweep**. The inbox is addressed to the **account**, not the workspace, so a notice outlives the
workspace it referred to, and a foreign id answers `404`. The in-app row is the **source of truth**
and is always written — the four per-user email switches only decide whether a *copy* is mailed, so
turning one off can never hide something from the bell. Delivery is **idempotent** through durable
markers (`notifications.emailed_at`, `scheduled_posts.reminder_sent_at`,
`social_accounts.token_warning_sent_at`), so a repeated sweep or two workers cannot send a notice
twice. The top bar gains a **notification bell** (unread badge, inbox dropdown, mark read, dismiss,
mark all read, click-through) and Settings gains a **Notifications** tab for the email preferences.

**Phase 12** makes the assistant **free and self-healing**. An instance-wide **AI-model registry**
holds an ordered list of models — OpenAI-compatible providers (OpenAI, Groq, Together, OpenRouter,
a local proxy) *and* a locally hosted **Ollama** model — and a router walks that list by `priority`
and **fails over automatically** when a model is unavailable, rate-limited, times out, or returns a
5xx. The **same request is re-sent unchanged** to each model (the brief and output format are never
altered), a **content refusal is never failed over** (that would be a policy bypass), and failure is
reported only after **every** compatible model has failed. Every attempt is written to an
append-only `ai_task_attempts` log — the usage log, execution trail, task history, and performance
sample at once — and a **duplicate `task_id` is refused**, so a redelivered job never generates
twice. A backup that finishes the work raises a **`model_fallback` notification** naming the model
that stepped in. Keys are **never stored**: a model names the *environment variable* holding its
key. The `/ai-models` page manages the failover order, shows per-model performance, and expands each
routed task into its full attempt chain. **No billing — the platform stays free.**

**Phase 13** hardens the running API and trims latency. A new in-house **rate limiter**
(fixed-window, counters shared across workers in **Redis**, with an in-process fallback) throttles the
credential routes (10/min) and AI generation (30/min), answering **429** with a `Retry-After`. Every
response now carries the browser **security headers** — a deny-by-default CSP (the API docs pages are
exempt), `nosniff`, `frame-ancestors 'none'`, `Referrer-Policy`, `Permissions-Policy`,
`Cross-Origin-Opener-Policy`, and HSTS in production — and a new **origin guard** refuses a
cross-origin state-changing request (**CSRF**, `403`) while leaving origin-less callers (CLIs, the test
suite) untouched. A new append-only **`audit_logs`** trail records security-relevant actions (sign-ins,
password changes, registry edits) in the **same transaction** as the change and is readable through
`GET /api/v1/workspaces/{ws}/audit` behind the `audit:read` permission. Responses of 1 KB or more are
**GZip**-compressed, and the database gains its 25th table. **383 backend tests pass.**

**Phase 14** completes the test suite. It closes the two gaps the earlier phases left: the **Celery
worker** (tasks were only ever hand-verified) now has **9 tests** that drive each task in-process —
covering the task registry, all three **Beat** sweeps, their configured intervals, and result
coercion — and the **frontend** gains a real **Vitest** runner with **47 tests** over the formatting
helpers, the API client, upload validation, and two component render tests. `npm test` joins
`typecheck` / `lint` / `build` in the pipeline.

**Phase 15** gives the platform a real deployment path. `Dockerfile.prod` ×2 build **non-root,
multi-stage** images (the backend runs `alembic upgrade head && gunicorn` on start and supervises
uvicorn workers; the frontend ships the Next.js `standalone` output), and `docker-compose.prod.yml`
runs seven services of which **only nginx is public** — Postgres and Redis are never published to the
host, and a `beat` container drives the periodic sweeps. nginx terminates **TLS**, redirects HTTP →
HTTPS (answering the ACME challenge first), and streams large resumable uploads
(`proxy_request_buffering off`, 2 GiB body cap, 600 s timeouts). **Sentry** — installed since Phase 1
but never wired — is now opt-in through `configure_error_tracking()` with `send_default_pii=False`, the
config still refuses to boot in production on a weak `SECRET_KEY` or an insecure refresh cookie, and
`deploy/backup.sh` / `deploy/restore.sh` plus a GitHub Actions CI workflow round out the story. The
full runbook is in [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md). **394 backend tests and 47 frontend
tests pass.**

See [`docs/PROJECT_STATE.md`](docs/PROJECT_STATE.md) for the current state and exact
next step.

---

## Architecture

| Layer              | Technology                                             |
| ------------------ | ------------------------------------------------------ |
| Frontend           | Next.js (App Router), React, TypeScript, Tailwind, shadcn/ui |
| Backend            | Python, FastAPI, Pydantic, SQLAlchemy, Alembic         |
| Database           | PostgreSQL 16                                          |
| Background jobs    | Celery + Redis                                         |
| Video processing   | FFmpeg / FFprobe (Phase 5 — implemented)               |
| Social accounts    | OAuth 2.0 — Instagram, Facebook, YouTube, TikTok (Phase 6 — implemented) |
| Publishing         | Per-platform adapters + "Publish Everywhere" (Phase 7 — implemented) |
| Scheduling         | Durable schedule queue + Celery Beat due-sweep (Phase 8 — implemented) |
| AI content         | Swappable `AIProvider` over a model vendor's REST API (Phase 9 — implemented) |
| Analytics          | Per-platform metrics providers + append-only snapshots, hand-built SVG charts (Phase 10 — implemented) |
| Notifications      | User-addressed in-app inbox + optional email copy delivered by a Celery Beat sweep (Phase 11 — implemented) |
| AI models          | Instance-wide registry + automatic failover across OpenAI-compatible providers and local Ollama (Phase 12 — implemented) |
| Security           | Fixed-window rate limiting + browser security headers + CSRF origin guard + append-only audit trail (Phase 13 — implemented) |
| Testing            | Pytest (backend, incl. worker/Beat) + Vitest, Testing Library, jsdom (frontend) (Phase 14 — implemented) |
| Media storage      | Local filesystem now; S3/MinIO behind the same interface later |
| Observability      | Structured JSON logs + opt-in Sentry error tracking (`send_default_pii=False`) (Phase 15 — implemented) |
| Deployment         | Multi-stage non-root images + Compose production stack + nginx TLS proxy + GitHub Actions CI (Phase 15 — implemented) |
| Containers         | Docker, Docker Compose                                 |

Full details: [`docs/PROJECT_ARCHITECTURE.md`](docs/PROJECT_ARCHITECTURE.md).

---

## Repository layout

```
One-Click Social Media Video Publisher/
├── backend/                 # FastAPI application
│   ├── app/
│   │   ├── api/             # Dependencies + cookie helpers + versioned routers
│   │   ├── core/            # Config, logging, security (hashing/JWT), crypto, permissions, rate limiting, security middleware
│   │   ├── db/              # Engine, session, declarative base, enums, mixins
│   │   ├── models/          # 25 SQLAlchemy models (incl. auth, OAuth, analytics, notification, AI-model, and audit tables)
│   │   ├── schemas/         # Pydantic request/response models
│   │   ├── services/        # Auth, workspace, email, media, storage, video, accounts, oauth/, publishing, publishers/, scheduling, ai/, ai_content, ai_router, metrics/, analytics, notifications, audit
│   │   ├── tests/           # Pytest suite (394 tests, incl. worker/Beat + observability)
│   │   ├── main.py          # FastAPI entry point
│   │   └── worker.py        # Celery application (publishing, scheduling, notifications, analytics, ai-health tasks)
│   ├── alembic/             # Database migrations
│   ├── requirements.txt
│   ├── Dockerfile           # Development image
│   └── Dockerfile.prod      # Production image (multi-stage, non-root, gunicorn)
├── frontend/                # Next.js application
│   ├── app/                 # App Router: (auth) + (dashboard) route groups, landing page
│   ├── components/          # ui/ primitives, app-shell/, auth/, settings/, notifications/, media/, accounts/, posts/, schedule/, ai/, analytics/, shared pieces
│   ├── hooks/               # Toast store
│   ├── lib/                 # API client, auth/theme context, types, formatters, media-upload
│   ├── vitest.config.ts     # Frontend test runner config (jsdom)
│   ├── package.json
│   ├── Dockerfile           # Development image
│   └── Dockerfile.prod      # Production image (standalone output, non-root)
├── deploy/                  # Phase 15: nginx config, backup/restore scripts
├── .github/workflows/       # Phase 15: CI (backend + frontend)
├── docs/                    # Project documentation
├── docker-compose.yml       # Development stack
├── docker-compose.prod.yml  # Production stack (only nginx is public)
├── .env.example
├── .env.production.example
└── README.md
```

---

## Quick start (Docker Compose)

Requires **Docker Desktop** on Windows.

```powershell
# 1. From the project root, create your environment file
Copy-Item .env.example .env

# 2. Build and start the whole stack
docker compose up --build

# 3. Open the apps
#    Frontend  -> http://localhost:3000
#    API docs  -> http://localhost:8000/docs
```

See [`docs/SETUP_GUIDE.md`](docs/SETUP_GUIDE.md) for the native (non-Docker) workflow
and troubleshooting.

### Production

The deployable stack is **`docker-compose.prod.yml`** (non-root multi-stage images, only nginx
public, TLS). Start from the production template and read the runbook:

```powershell
Copy-Item .env.production.example .env   # then fill in the real secrets
docker compose -f docker-compose.prod.yml up --build -d
```

Full instructions — first deploy, TLS, change/rollback, backups, monitoring, scaling, and
troubleshooting — are in [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).

---

## Documentation

| Document | Purpose |
| --- | --- |
| [`docs/SETUP_GUIDE.md`](docs/SETUP_GUIDE.md) | Install, run, and troubleshoot |
| [`docs/PROJECT_ARCHITECTURE.md`](docs/PROJECT_ARCHITECTURE.md) | System design |
| [`docs/DATABASE_SCHEMA.md`](docs/DATABASE_SCHEMA.md) | Data model and ER diagram |
| [`docs/API_DOCUMENTATION.md`](docs/API_DOCUMENTATION.md) | API endpoints |
| [`docs/OAUTH_SETUP.md`](docs/OAUTH_SETUP.md) | Per-platform OAuth apps, scopes, review, and eligibility |
| [`docs/DEVELOPMENT_PROGRESS.md`](docs/DEVELOPMENT_PROGRESS.md) | Phase checklist |
| [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) | Production deployment runbook (deploy, TLS, backups, rollback, monitoring) |
| [`docs/PROJECT_STATE.md`](docs/PROJECT_STATE.md) | Living project state |
