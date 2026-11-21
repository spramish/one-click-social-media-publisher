# Project State

Author: **Pramish Sharma**
Last updated: Phase 15 — Production Deployment ✅ COMPLETE (2026-11-22)
This file is the single source of truth for project continuity. Update it after every phase.

---

## Current phase

**Phase 15 — Production Deployment (complete).** All 15 phases of the master plan are done: the platform
is feature-complete, tested, and has a real deployment path. Nothing is outstanding.

## Completed features

**Phase 15 — Production Deployment**

- **Production images** — `backend/Dockerfile.prod` is a two-stage build: wheels are compiled once in a
  `builder` stage that carries `build-essential` + `libpq-dev`, and the final `python:3.12-slim` stage
  ships only the virtualenv plus the *runtime* libraries (`ffmpeg`, `libpq5`, `curl`). It runs as the
  non-root `appuser` (uid 1001), declares a `/api/v1/health` `HEALTHCHECK`, and its start command is
  `alembic upgrade head && gunicorn …` — migrations are applied by the container itself, and
  `gunicorn` supervises `WEB_CONCURRENCY` uvicorn workers so one wedged worker cannot take the API down.
  `frontend/Dockerfile.prod` is a three-stage build (`deps` → `builder` → `runner`) that exploits the
  `output: "standalone"` bundle the project already emits, so the runtime layer contains the server and
  its production dependencies only — no toolchain, no `devDependencies`, no source.
  `gunicorn==23.0.0` was added to `requirements.txt` for this.
- **`docker-compose.prod.yml`** — the deployable stack. Unlike the local file it bind-mounts **no source**
  (the image is the unit of deployment), runs no dev server, and — the point of the whole file —
  **publishes no host ports for PostgreSQL or Redis**. Only `nginx` is reachable from outside. It also
  adds the `beat` container the local stack never had: without Celery Beat the three periodic sweeps
  (scheduled publishing, notification delivery, AI health checks) silently never run. `worker` and `beat`
  share the backend image; `worker` scales with `--scale worker=N` while `beat` must stay a single replica
  or every sweep fires twice.
- **nginx reverse proxy** (`deploy/nginx/app.conf`) — the single public entry point. Terminates TLS
  (TLSv1.2/1.3, session cache), redirects `http` → `https`, answers the Let's Encrypt HTTP-01 challenge
  before the redirect, and routes `/api/` → backend, `/` → frontend. It streams request bodies
  (`proxy_request_buffering off`) with 600 s read/send timeouts so a resumable multi-gigabyte upload is
  not buffered in nginx's memory and does not time out, and raises `client_max_body_size` to 2 GiB to
  match `MEDIA_MAX_UPLOAD_BYTES`. It propagates `X-Forwarded-For`/`-Proto` so the app's rate limiter keys
  on the real client address. The application's own security headers are left un-duplicated.
- **Error tracking (Sentry), finally wired** — `sentry-sdk[fastapi]` had been installed since Phase 1 but
  never initialised; nothing was ever reported. `app/core/observability.py` now exposes
  `configure_error_tracking()`, called in `main.py` **before** the `FastAPI` instance is built (Sentry
  patches the ASGI app during `init`, so ordering matters). It is strictly opt-in: with no `SENTRY_DSN`
  it returns `False` and imports nothing, so development and the 394-test suite stay completely offline.
  `send_default_pii=False` is hard-coded — request bodies carry credentials, refresh tokens and video
  bytes and must never leave the process. `SENTRY_DSN` and `SENTRY_TRACES_SAMPLE_RATE` (validated to
  `[0, 1]`) were added to `config.py`.
- **Backups and recovery** — `deploy/backup.sh` dumps the database through the running stack to a
  timestamped `.sql.gz`, prunes past a retention window, and is cron-ready; `deploy/restore.sh` stops the
  three writing services, requires the database name to be typed as confirmation, restores, and brings
  the writers back. The guide documents the media volume as a *separate* artefact (a database restore does
  not bring videos back) and gives a repeated recovery drill.
- **CI pipeline** — `.github/workflows/ci.yml` runs two independent jobs on every push and PR. Backend:
  PostgreSQL 16 and Redis 7 as service containers, Python 3.12, `pip install -r requirements-dev.txt`,
  then `pytest`. Frontend: Node 22, `npm ci`, then typecheck → lint → `npm test` → build. Both jobs mirror
  the exact commands verified locally, so a green run and a green local run mean the same thing.
- **Deployment documentation** — `docs/DEPLOYMENT.md` is the full runbook: container map, first deploy,
  TLS, the three variables that make boot fail on purpose, change deploys, rollback (application and
  schema), backups/cron/off-host copies, a recovery drill, monitoring, scaling, a pre-launch security
  checklist, a **known-limitations section**, and a symptom→cause troubleshooting table.
  `.env.production.example` lists only what differs from `.env.example`, and `.gitignore` was taught to
  keep that file while ignoring real `.env` values and `deploy/certs/`.

**Phase 14 — Complete Testing**

- **The two hand-verified gaps closed.** Earlier phases checked the Celery worker by eye (`celery inspect`
  output) rather than in the suite. `app/tests/test_worker.py` now drives the tasks themselves: every
  dispatched task name is registered on the app, the beat schedule carries all three periodic sweeps with
  the intervals that come from settings, `ping` answers `pong`, and each DB-backed task is fed the test's
  own rolled-back session (patching `session_scope`) so its delegation, its id coercion, and its
  JSON-serialisable return shape are asserted. Calling a Celery task object directly runs it in-process,
  so the suite stays broker-free.
- **The frontend had no tests at all** — this was the largest real gap in the project. Vitest + jsdom +
  Testing Library is now configured (`vitest.config.ts` shares the `@/*` alias and the automatic JSX
  runtime; `vitest.setup.ts` registers the jest-dom matchers) with `npm test` / `npm run test:watch`
  scripts. **47 tests across 6 files** cover the pure logic the UI depends on — `format.ts`
  (byte/duration/date/timezone/relative formatting including every null and non-finite branch),
  `media-upload.ts` (MIME resolution, the accept attribute, and each `validateFile` rejection path),
  `api.ts` (`formatDetail` for plain, blank, single-error, multi-error and unrecognised bodies, plus
  `ApiError`), `utils.ts` (`cn` including Tailwind conflict resolution) — and render two components
  (`EmptyState`, `Button`) through Testing Library, asserting roles, forwarded props, variant classes and
  the `asChild` slot. All of it is typechecked and linted by the existing `typecheck` / `lint` scripts.

**Phase 13 — Security & Performance**

- **Rate limiting, per route, in-house** — `app/core/rate_limit.py` is a fixed-window counter applied as a
  FastAPI dependency (`rate_limit(bucket, limit=…, window_seconds=…)`). Counters live in Redis (`INCR` +
  a one-time `EXPIRE`) so the limit is shared across worker processes; if Redis is unreachable the limiter
  **degrades to an in-process counter** and keeps enforcing a per-instance limit rather than failing open
  (no protection) or failing closed (the API stops working because a cache is down). The bucket is keyed on
  the caller's address — the first hop of `X-Forwarded-For`, else the socket peer — and an over-limit call
  answers **`429` with `Retry-After`**.
  - The six credential routes (`/register`, `/login`, `/refresh`, `/logout`, `/password/reset`,
    `/password/reset/confirm`) share the **`auth`** bucket: `RATE_LIMIT_AUTH_PER_MINUTE` (default **10**)
    per address, so `login` alone cannot be brute-forced and the guess budget is shared. `POST
    /ai/{workspace_id}/generate` has its own **`ai-generate`** bucket (`RATE_LIMIT_AI_PER_MINUTE`,
    default **30**) because every generation costs money or quota.
- **Security headers on every response** — `SecurityHeadersMiddleware` sets `X-Content-Type-Options:
  nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, a `Permissions-Policy` that switches
  off geolocation/microphone/camera, `Cross-Origin-Opener-Policy: same-origin`, and a **deny-by-default
  CSP** (`default-src 'none'; frame-ancestors 'none'; base-uri 'none'`). The API returns only JSON, so the
  locked-down policy costs nothing; `HSTS` is sent **only in production**, and `/docs`, `/redoc`,
  `/openapi.json` are exempt from the CSP because Swagger UI ships its own scripts.
- **CSRF covered for the cookie-authenticated path** — `OriginGuardMiddleware` refuses a state-changing
  request (`POST`/`PUT`/`PATCH`/`DELETE`) whose `Origin` matches neither a configured CORS origin nor the
  request's own `Host`, answering **`403 "Cross-origin request refused"`**. A cross-site form POST — which
  a browser *does* send the refresh cookie with — is therefore blocked, while requests with no `Origin`
  (CLIs, mobile apps, the test suite) are unaffected. Bearer-token calls are inherently CSRF-safe, so the
  same guard protects them at no cost.
- **An append-only audit trail** — `audit_logs` records *who* did *what* to *which* object, from *where*.
  `app/services/audit.py`'s `record()` only **stages** a row on the caller's session, so an entry lands in
  the same transaction as the change it describes (they either both commit or neither does); a rejected
  action (a failed login) commits before the error is raised. The table is never updated or deleted, and
  `user_id`/`workspace_id` are `ON DELETE SET NULL` — the trail outlives the account or workspace it names.
  `action` is a plain dotted string (`auth.login.succeeded`, `ai_model.created`, …) so a new auditable
  event needs no migration.
  - **Recorded:** `auth.registered`, `auth.login.succeeded`, `auth.login.failed`, `auth.logout`,
    `auth.logout_all`, `auth.password.changed`, `auth.password.reset`, and `ai_model.created` /
    `ai_model.updated` / `ai_model.deleted`. Sign-ins are filed under the account's **primary workspace**
    (its oldest active membership) so a workspace-scoped trail can answer "who signed in"; a failed login
    for an unknown address names no user and no workspace.
  - **Read:** `GET /api/v1/workspaces/{workspace_id}/audit` (newest first, `limit` ≤ 200, a `before`
    cursor, and an `action` prefix filter), gated by the new **`audit:read`** permission (owner/admin).
    There is **no write route** — the API consumer can never author an entry.
- **A new table, a new migration, a new permission** — `audit_logs` (+ three composite indexes) via
  migration `d1a4b7c93e02`; `audit:read` seeded to the owner/admin system roles. **25 tables**, head
  `d1a4b7c93e02`.
- **The rest of the review found the work already done** — SQL injection (SQLAlchemy Core/ORM with bound
  parameters; no raw string SQL on any request path), OAuth security, AES-256-GCM token encryption,
  file-upload allow-listing, secure cookies, bcrypt password policy, tenant isolation (`404`, never
  `403`), and secret management (environment-only, with a production startup guard) were all already in
  place from earlier phases and were **reviewed, not rewritten**.
- **Dependency scan** — `npm audit` reports **2 advisories** (1 moderate, 1 high) in the PostCSS copy
  bundled inside Next.js; it is a build-time-only dependency and the only fix is the breaking `next@16`
  upgrade, so it is logged as a known issue. Python dependencies stay pinned in `requirements*.txt`.
- **Optimizations** — `GZipMiddleware` (≥ 1 KB) now compresses responses; the audit read patterns are
  covered by the three `audit_logs` composite indexes; the rate limiter reuses Redis. No new index was
  needed for the earlier phases (their hot paths were already indexed), and **no GIN index was added** —
  deliberate, because no query filters on a JSONB column yet. Video processing, background workers,
  storage and the frontend were reviewed; they were already bounded in Phases 5/8/11, so nothing changed.
- **20 new backend tests** (`app/tests/test_security_hardening.py`) — **383 passed** across 18 test
  files, 0 failed. The suite sets `RATE_LIMIT_ENABLED=false` so ordinary tests may authenticate freely;
  the limiter's own tests turn it back on over a fresh in-process store.
- `npm run typecheck`, `npm run lint`, and `npm run build` all pass; **17 routes** (no frontend source
  changed this phase).

**Phase 12 — Free AI Model Management & Automatic Failover**

- **A registry, not a hard-coded vendor** — `ai_models` is the ordered list the router walks. `priority`
  ascending is the failover order (ties break on `created_at`), `enabled` takes a model out of service
  without deleting it, `supports` (nullable) restricts a model to some content kinds, and
  `credential_env` names the **environment variable** holding the key. **The key itself is never
  stored** — only the variable name — so a database dump cannot leak a credential.
- **One router, one place for the rules** — `app/services/ai_router.py` owns delivery (which model
  writes the copy); `app/services/ai_content.py` still owns the prompt. `complete()` re-sends the
  **identical** `CompletionRequest` to every model, so failing over never alters the instructions or the
  required output format.
- **Only a model-local failure switches models** — a timeout, rate limit, 5xx, rejected key or malformed
  response moves to the next model. A **content refusal** is re-raised immediately (a policy bypass is
  not resilience). Transient failures are retried on the same model up to `AI_ROUTER_MAX_ATTEMPTS` with
  exponential backoff; a rate limit skips the retry.
- **Two transports** — `openai_compatible` (the existing OpenAI chat-completions provider, pointed at any
  OpenAI-compatible gateway via that model's own `base_url` + key) and `ollama` (a new provider calling
  the local `/api/chat`, no key needed).
- **Every attempt is recorded** — one append-only `ai_task_attempts` row per model call is the usage log,
  execution trail, task history and performance sample at once, keyed by a shared `task_id`. A
  `task_id` that already succeeded is refused, so a redelivered job never generates twice.
- **A switch is announced** — a backup finishing the work raises a `model_fallback` notification naming
  the model that stepped in. Failure is reported only after every compatible model has failed (the last
  error propagates, so the existing status mapping holds).
- **Non-blocking availability monitoring** — `check_health`/`health_sweep` probe models and record
  `status`/`consecutive_failures`/`last_error` for operators; the router still walks the whole list every
  time, which is exactly how a recovered primary resumes with no action. Registered on Celery Beat
  (`ai.health_sweep`, interval `AI_HEALTH_CHECK_INTERVAL_SECONDS`, default 600s).
- **`/api/v1/ai/{workspace_id}` — 8 administration routes** — `GET/POST /models`,
  `PATCH/DELETE /models/{id}`, `POST /models/health-check`, `GET /models/performance`, `GET /tasks`,
  `GET /tasks/{id}`. Gated by `ai:manage` (owner/admin); the workspace in the path authorizes the
  caller but does not partition the instance-wide registry. **No route ever returns or accepts a
  credential**; `credential_ready` is a live boolean.
- **Two new tables + a migration** — `ai_models` and `ai_task_attempts`, plus the `model_fallback`
  notification category and `ai:manage` seeded to owner/admin; **24 tables**, migration head
  `c3f7a2e91d08`.
- **`/ai-models` page** — a failover-order list (move up/down, enable/disable, remove, add), a
  performance table, and a task-history list that expands into the full attempt chain (which models were
  tried, latency, error). Added to the sidebar (gated on `ai:manage`) and reachable from the
  `model_fallback` notification.
- **24 new backend tests** (`app/tests/test_ai_router.py`) — the suite stood at **363 passed**.
- `npm run typecheck`, `npm run lint`, and `npm run build` all pass; **17 routes** (`/ai-models` 8.97 kB).

**Phase 11 — Notifications**

- **Addressed to the user, not the workspace** — `notifications` predates this phase; the bell and
  inbox are per-account, so a notice outlives the workspace it referred to (`workspace_id` stays
  nullable with `ON DELETE SET NULL`). Membership is re-checked by scoping every query on the
  caller's own `user_id`, so a foreign id answers **`404`, never `403`**.
- **In-app is the source of truth; email is an opt-in copy** — the row is always written. The four
  preferences only decide whether a *copy* is also mailed, so turning one off can never hide
  something from the bell.
- **The publishing engine already wrote the outcome rows** — `services/publishing.py`'s `_notify()`
  records `publish_success` / `publish_failure`. Phase 11 does not duplicate that; it delivers them.
- **Idempotent background delivery** — new durable markers `notifications.emailed_at`,
  `scheduled_posts.reminder_sent_at`, and `social_accounts.token_warning_sent_at` guarantee each
  notice is delivered **once**, even if the sweep runs twice or two workers overlap.
- **One sweep, three sub-sweeps** — `notifications.sweep` runs `dispatch_emails` (pending rows with
  `emailed_at IS NULL`, honouring preferences; a transport failure leaves the marker unset so the
  next tick retries without blocking the batch), `send_schedule_reminders` (a `pending` schedule
  inside the lead window, addressed to the post's author), and `warn_expiring_tokens` (a connected
  account expiring within the window, addressed to whoever connected it, falling back to the
  workspace owner). Each is a plain query over persisted state, so a worker restart catches up on
  its next tick. Registered on Celery Beat (`notifications.sweep`, interval
  `NOTIFICATIONS_SWEEP_INTERVAL_SECONDS`, default 60s).
- **7 endpoints** under `/api/v1/notifications` — authenticated but **not** workspace-scoped:
  `GET ""` (a page plus `unread_count`), `GET /unread-count`, `POST /read-all`, `POST /{id}/read`,
  `DELETE /{id}`, `GET /preferences`, `PUT /preferences`. `is_read` is derived from `read_at`
  (one source of truth). Reads never contact a platform or a mail server — SMTP latency and
  failure stay off the request path.
- **Two new tables/columns + a migration** — `notification_preferences` (one row per user, created
  lazily with all four flags defaulting to `true`) plus `notifications.emailed_at` (with a partial
  index `ix_notifications_pending_email`), `scheduled_posts.reminder_sent_at`, and
  `social_accounts.token_warning_sent_at`; **22 tables**, migration head `b8e2f5a91c47`.
- **`NotificationBell` in the top bar** — an unread badge (polled every 60s), a dropdown inbox that
  loads the newest 20, per-item mark-read + dismiss, a "mark all read" action, and a click-through
  to the relevant page (publish outcomes → `/history`, reminders → `/schedule`, expiring tokens →
  `/accounts`).
- **A "Notifications" tab in Settings** — the four email opt-outs as switches, saved optimistically
  and reverted on failure.
- **16 new backend tests** (`app/tests/test_notifications.py`) — **335 total**.
- `npm run typecheck`, `npm run lint` pass. **No new dependency** — the email transport and Celery
  Beat schedule were already in place.

**Phase 10 — Analytics Dashboard**

- **Provider abstraction** (`app/services/metrics/`) — a `MetricsProvider` ABC owning the shared
  HTTP machinery and an error taxonomy (`MetricsTransientError`, `MetricsRateLimitError`,
  `MetricsAuthError`, `MetricsUnavailableError`, each carrying a `retryable` marker), with a
  class-level `transport` hook so every test drives an injected `httpx.MockTransport` with **no
  network call**. One implementation per platform (`facebook.py`, `instagram.py`, `youtube.py`,
  `tiktok.py`) plus a registry (`get_metrics_provider`, `supported_metrics_platforms`) — the same
  shape as `services/publishers/`, `services/oauth/`, and `services/ai/`. **No new dependency**
  (`httpx` was already required).
- **Unavailable is not zero** — the brief's single hard rule, preserved end to end. Every metric is
  `int | None` from the provider through the ORM, schema, and DTO. A platform that does not return a
  figure leaves the column `NULL`; an aggregate over all-unavailable values is `None`; the UI
  renders a muted **N/A** — never `0`. `as_int` treats an absent/empty/non-numeric value as `None`
  and a genuine `0` as `0`.
- **Immutable, timestamped snapshots** — `post_metrics` and `account_metrics` are append-only; a
  refresh **inserts** a row and never overwrites history. `retrieved_at` records when the platform
  answered, so the chart plots measurements when taken, not when the worker ran.
- **Reading is separated from refreshing** — `overview`, `platform_performance`,
  `account_performance`, `target_performance`, and `history` are pure reads (no network); `refresh`
  is the only function that touches a platform. Opening `/analytics` never calls a platform.
- **One platform's failure never freezes the dashboard** — a refresh reads each destination
  independently; a failure is collected in `errors` and the sweep continues, and a platform with no
  provider is counted **skipped**, not failed.
- **7 endpoints** under `/api/v1/analytics/{workspace_id}` — `GET …/capability`, `GET …/overview`,
  `GET …/platforms`, `GET …/accounts`, `GET …/targets`, `GET …/history`, and `POST …/refresh`. The
  `GET`s require `analytics:read` (every role); `refresh` requires `analytics:refresh`
  (owner/admin/editor). Centralised `_translate` maps `MetricsUnavailableError` → `503`,
  `MetricsAuthError` → `502`, a rate-limit/transient error → `503`, another metrics error → `502`,
  and an analytics-domain error → `400`.
- **Tenant isolation** — the workspace always comes from the resolved context (never request
  input), so another tenant's numbers and another workspace's target/account ids answer **`404`,
  never `403`**.
- **Two new tables + a migration** — `post_metrics` and `account_metrics` with `analytics:read` /
  `analytics:refresh` seeded additively; **21 tables**, migration head `f241d12e080b`.
- **`/analytics` page** — the placeholder is replaced by the real `AnalyticsDashboard`: overview
  cards, a platform-comparison card (hand-built bars + a full metric table), a history card with
  metric/platform/range pickers and a **dependency-free SVG sparkline** (gridlines, axis labels,
  per-point tooltips, and a **gap** for a missing day), an audience table, and a recent-targets
  table. A `MetricValue` renders **N/A** for a `null` metric.
- **36 new backend tests** (`app/tests/test_analytics.py`) — **319 total**.
- `npm run typecheck`, `npm run lint`, and `npm run build` all pass; **17 routes**
  (`/analytics` grew from a placeholder to 9.08 kB).

**Phase 9 — AI Content Generation**

- **Provider abstraction** (`app/services/ai/`) — an `AIProvider` ABC owning the shared HTTP
  machinery and an error taxonomy (`AINotConfiguredError`, `AITransientError`,
  `AIRateLimitError`, `AIProviderError`, `AIContentRejectedError`), with a class-level `transport`
  hook so every test drives an injected `httpx.MockTransport` with **no network call**. One
  implementation (`openai.py`, `POST …/chat/completions`) plus a registry (`get_provider` /
  `supported_providers`) — the same shape as `services/publishers/` and `services/oauth/`.
  **No new dependency** (`httpx` was already required).
- **Prompt engineering lives in the service** (`app/services/ai_content.py`) — a system prompt,
  per-kind instructions, and per-platform guidance build the request; the provider only transports
  a message pair, so the vendor is swappable without changing what is produced.
- **Five kinds of copy** — `caption`, `hashtags`, `description`, `rewrite` (needs the source text),
  and `suggestion` (content ideas), shaped by an optional tone, style, and target platform.
- **Nothing is ever published automatically.** `generate` only writes an `ai_generated_content`
  row (`is_accepted = false`); `accept` flips that flag and nothing else. No `Post`, `PostTarget`,
  or `PublishingJob` is created — publishing still requires the explicit Phase 7 composer action.
- **Capability gap, not a crash.** With no `OPENAI_API_KEY` the capability reports
  `configured: false` and generation answers `503`; the UI explains the state up front. The key is
  read only inside the provider and never appears in a response, a log, or an exception message.
- **4 endpoints** under `/api/v1/ai/{workspace_id}` — `GET …/capability`, `POST …/generate`
  (`201`), `GET …/history` (kind filter + pagination), `POST …/history/{content_id}/accept`.
  Centralised `_translate` maps a foreign `post_id`/`content_id` to `404`, a bad request or policy
  refusal to `422`, an unconfigured model or a transient failure to `503`, and any other provider
  rejection to `502`.
- **RBAC + tenant isolation** — the existing `ai:generate` code (seeded in Phase 2 to
  owner/admin/editor) guards every route; a `post_id` is re-resolved inside the workspace, so
  another tenant's post answers **`404`, never `403`**.
- **`/assistant` page** — the placeholder is replaced by the real `AIAssistant`: a kind selector, a
  brief field (with a draft field only for `rewrite`), tone/style/platform pickers fed by the live
  capability, an editable result with copy + accept, and a suggestion history. A small styled
  native `Select` primitive (`components/ui/select.tsx`) was added for the pickers. The composer
  gained a **Draft with AI** panel that fills the base caption from a short brief.
- **No new table and no migration** — `ai_generated_content` already existed from Phase 2;
  **19 tables**, migration head remains `5347775d077d`.
- **28 new backend tests** (`app/tests/test_ai.py`) — **281 total**.
- `npm run typecheck`, `npm run lint`, and `npm run build` all pass; **17 routes**
  (`/assistant` grew from a placeholder to 8.1 kB).

**Phase 8 — Content Scheduling**

- **Scheduler service** (`app/services/scheduling.py`) on the existing `scheduled_posts`
  model — a schedule is the *when*, the post is the *what*, and a unique constraint keeps
  exactly one schedule row per post. `schedule_post` validates the destinations through the
  Phase 7 publishing layer and records them as **pending targets with no job**; `reschedule`
  and `cancel` move/drop an entry only while it has not fired; `list_schedules` splits the
  calendar into an `upcoming` queue and `past` history. Every read resolves through
  `TenantMixin.scoped`, so another tenant's post id answers **`404`, never `403`**.
- **A durable queue, not a countdown.** The schedule lives in PostgreSQL, so it survives an
  API or worker restart — and a **Celery Beat** periodic task (`scheduling.dispatch_due`,
  every `SCHEDULING_SWEEP_INTERVAL_SECONDS`) sweeps for entries that have come due instead of
  relying on a broker-held timer that a restart would lose.
- **Claimed atomically, fires exactly once.** The sweep claims due rows with a single
  `SELECT … FOR UPDATE SKIP LOCKED`, so two workers (or two overlapping ticks) take disjoint
  rows; an entry is only ever reached in `pending`, so a re-run or a second worker cannot
  publish the same post twice.
- **Reuses the Phase 7 reliability layer.** A due entry is handed to
  `publishing.queue_targets`, which builds the **very same per-target jobs, idempotency keys,
  retries, backoff, and dead-letter handling** as a manual "Publish Everywhere" — scheduling
  cannot bypass it. `publishing.publish_everywhere` was split into `record_targets`
  (validate + persist pending targets) and `queue_targets` (create + dispatch jobs) so both
  the manual and the scheduled path share one implementation.
- **Missed-job handling.** An entry overdue by more than `SCHEDULING_MISSED_GRACE_SECONDS`
  (the worker was down, the machine slept) is marked `MISSED`, its pending targets cancelled,
  and the post reverted to `DRAFT` so the user simply re-queues it — stale content is never
  published hours late. A post that became unusable between scheduling and dispatch is left
  un-dispatched (`MISSED`) for the operator rather than publishing something broken.
- **Explicit time zones.** `scheduled_for` is always persisted in **UTC**; the caller's IANA
  zone is validated with the stdlib `zoneinfo` (no new dependency) and stored alongside it in
  `timezone`, so the API can render `local_time` — the exact wall-clock time the user picked —
  without guessing.
- **Cancellation is total.** Cancelling a schedule also cancels its pending destinations and
  any not-yet-terminal job, so nothing is left behind for the sweep to pick up. A cancelled
  post can never publish.
- **4 endpoints** under `/api/v1/posts/{workspace_id}` — `GET …/schedule` (`schedule:read`,
  `upcoming`/`past`/`all` window + status filter), `POST …/schedule` (`schedule:create`,
  `201`), `PATCH …/schedule/{post_id}` (`schedule:create`, reschedule), `DELETE
  …/schedule/{post_id}` (`schedule:cancel`). The router is registered **before** the posts
  router so `/posts/{id}/schedule` is not swallowed by `/posts/{id}/{post_id}`.
- **RBAC + tenant isolation** — new codes `schedule:read`, `schedule:create`,
  `schedule:cancel`; every route resolves the workspace through `require_permission`, and
  another tenant's schedule is invisible and unreachable (`404`).
- **`/schedule` page** — the placeholder is replaced by the real `SchedulingCalendar`: an
  upcoming/past tab, entries grouped by day (each rendered in the entry's own zone),
  inline reschedule (a shared `ScheduleTimeField` with a `datetime-local` input + IANA zone),
  and cancel, with status badges and empty states. The composer gained a **"Schedule for
  later"** action that shares its destination validation with publish-now.
- **No new table and no migration** — the `scheduled_posts` row already existed from Phase 2;
  **19 tables**, migration head remains `5347775d077d`.
- **19 new backend tests** (`app/tests/test_scheduling.py`) — **253 total**.
- `npm run typecheck`, `npm run lint`, and `npm run build` all pass; **17 routes**
  (`/schedule` grew from a placeholder to 3.8 kB).

**Phase 7 — Multi-Platform Publishing Engine**

- **Publisher abstraction** (`app/services/publishers/`) — a `Publisher` ABC that owns the
  shared machinery (validation, caption assembly, HTTP error classification, status polling)
  plus a registry (`get_publisher` / `supported_platforms`). Four adapters implement it:
  `instagram.py`, `facebook.py`, `youtube.py`, `tiktok.py` — Instagram and Facebook share a
  `MetaPublisher` base (`meta.py`). The orchestration service never names a platform.
- **Per-platform capability + validation** — each publisher declares a `PublisherCapability`
  (max size, max duration, minimum resolution, caption ceiling, title rules, allowed MIME
  types) and refuses a bad request *before a byte moves* via `validate_media` and
  `validate_caption`; platform-specific captions/titles are built by `build_caption` /
  `build_title`.
- **Classified error taxonomy** — base `PublishError` with a `retryable` class flag splitting
  `PublishTransientError` / `PublishRateLimitError` (carries `Retry-After`),
  `PublishAuthError`, `PublishPermanentError`, `MediaValidationError`, and
  `CaptionValidationError`. A transport fault (timeout, DNS, connection) becomes a transient
  error rather than an `httpx` escape.
- **Platform-confirmed success** — `publish()` drives the platform's **own** processing status
  to a terminal-success state via `Publisher._poll()` before returning a `PublishResult`; an
  HTTP 200 that merely *accepted* the upload is never reported as published.
- **"Publish Everywhere" orchestration** (`app/services/publishing.py`) — the flow is two
  steps so a draft can be composed and previewed first (`create_post`) and dispatched later
  (`publish_everywhere`, reusable by Phase 8's scheduler). Validation runs across the user, the
  video, every selected account, and every selected platform; each destination then gets **one
  independent `post_targets` row and one `publishing_jobs` row**, dispatched to Celery
  individually — so one platform failing can never cancel another. A destination that fails
  validation is recorded `skipped` with its reason and does not affect the rest.
- **Reliability layer** — retries with exponential backoff (`base × 2^(attempt-1)`, capped at
  `PUBLISHING_RETRY_MAX_SECONDS`) honouring the platform's `Retry-After`; a content-derived
  **idempotency key** (workspace + SHA-256 video checksum + account + exact text) blocks
  duplicate publishing; per-call **request timeouts** (`PUBLISHING_HTTP_TIMEOUT_SECONDS`);
  error **classification**; **dead-letter** (`JobStatus.DEAD_LETTER`) once `max_attempts` is
  reached; and **job monitoring** through an append-only `publishing_logs` trail that keeps the
  classified error and the platform's **redacted** raw response per attempt. A re-delivered
  Celery message cannot publish twice (`run_job` short-circuits on a terminal status).
- **Post status aggregation** — a post is `published` only when *every* target holds a
  platform-confirmed publish; anything less is `partially_published` (the visible proof that a
  failure was isolated). Each confirmed or failed target also writes an in-app `Notification`.
- **Celery task** `publishing.publish_target` (`app/worker.py`) — executes one job through
  `session_scope()` and never re-raises: the outcome is recorded on the job row and, when
  retryable, the next attempt is re-dispatched with a countdown.
- **Endpoints** under `/api/v1/posts` — 6 routes: `capabilities`, `jobs/{job_id}`,
  list, get, create (`201`, `post:create`), and publish-everywhere (`202`, `post:publish`);
  reads are guarded by `post:read`. The publish route only *dispatches* — it returns `202` with
  every target `queued`, and a target turns `published` only once the worker records the
  platform's terminal status, which the client reads back from the per-target job.
- **RBAC + tenant isolation** — new codes `post:read`, `post:create`, `post:publish` (plus
  `post:update` / `post:delete` declared for later phases); every workspace route resolves
  through `require_permission`, and another tenant's post/job id answers **`404`, never `403`**.
- **`/compose` page** — the phase-labelled placeholder is replaced by the real
  `PublisherComposer`: pick a READY video, write a title + base caption, tick connected accounts
  as destinations with per-platform caption/title overrides (validated client-side against each
  platform's advertised limits), submit compose → publish, then poll per-target job progress
  until every destination reaches a terminal state.
- **No new table and no migration** — the publishing domain tables (`posts`, `post_targets`,
  `publishing_jobs`, `publishing_logs`) and the `post:*` permission rows already existed from
  Phases 1–2; **19 tables**, migration head remains `5347775d077d`.
- **34 new backend tests** (16 adapter-level in `test_publishers.py`, 18 orchestration/API-level
  in `test_publishing.py`) — **234 total**.
- `npm run typecheck`, `npm run lint`, and `npm run build` all pass; **17 routes**
  (`/compose` grew from a 135 B placeholder to 9.44 kB).

**Phase 6 — Social Media Account Connections (OAuth)**

- **OAuth 2.0 authorization-code flow for all four platforms** under
  `/api/v1/accounts` — Instagram and Facebook share a Meta (Facebook Login + Graph
  v21.0) provider base; YouTube uses Google OAuth 2.0 + YouTube Data API v3; TikTok uses
  Login Kit v2 + Content Posting API. Official APIs only.
- **Signed, single-use `state` + PKCE** — a 256-bit `state` bound to the workspace, stored
  only as its **SHA-256 digest** in `oauth_authorization_states`; single-use (`consumed_at`)
  with a 600 s TTL. PKCE `S256` is mandatory on TikTok, used on Google, and the verifier is
  stored **encrypted**. A state minted for one platform is refused by another.
- **Encrypted token storage** (`app/core/crypto.py`) — AES-256-GCM with a key derived via
  HKDF-SHA256 (from `OAUTH_ENCRYPTION_KEY`, else `SECRET_KEY`); ciphertext format
  `v1.<base64url(nonce‖ciphertext‖tag)>.` Tokens never leave the server.
- **Provider abstraction** — an `OAuthProvider` ABC (`services/oauth/base.py`) with
  `FacebookProvider`, `InstagramProvider`, `YouTubeProvider`, `TikTokProvider`, a registry
  (`get_provider` / `supported_platforms`), and an injectable `httpx` transport so tests run
  without network access.
- **Per-platform token lifecycle** — refresh for YouTube/TikTok; Meta's
  `fb_exchange_token` long-lived-token re-exchange where the platform has **no refresh
  token**; `ensure_fresh_tokens` refreshes a token `OAUTH_REFRESH_MARGIN_SECONDS` before it
  expires.
- **Account-info retrieval** — Facebook Pages (one account per Page, each with its own Page
  access token), Instagram professional accounts linked to a Page, YouTube channels, TikTok
  profiles.
- **Disconnect with upstream revocation** — Google `/revoke` and TikTok `/v2/oauth/revoke`,
  Meta `DELETE /me/permissions`; the credential is destroyed locally and the account is
  soft-deleted (`status=revoked`) so historical post targets keep their reference.
  `revoked_upstream` reports whether the platform confirmed it.
- **Expired-token handling** — an account whose token cannot be refreshed (no refresh token,
  or an undecryptable credential) is flagged `needs_reconnect` and answers **`409`**.
- **Centralised error translation** (`_translate`) — `503` not-configured, `404` unknown
  account, `409` unusable grant, `400` bad state/authorization, `502` platform failure.
- **RBAC + tenant isolation** — new codes `account:read`, `account:connect`,
  `account:disconnect`; every workspace route resolves through `require_permission`, and
  another tenant's account id answers `404`, never `403`.
- **`/accounts` page** — per-platform connect cards (with a "not configured" state), a
  connected-account list showing status, granted scopes, token expiry, and health, plus
  Refresh / Reconnect / Disconnect controls; the OAuth callback result is surfaced as a toast
  and the query string is scrubbed.
- One new table (`oauth_authorization_states`) — **19 tables total**; migration
  `5347775d077d`. `alembic check` reports no drift.
- **40 new backend tests** (20 provider-level in `test_oauth.py`, 20 API-level in
  `test_accounts.py`) — **200 total**.
- `npm run typecheck`, `npm run lint`, and `npm run build` all pass; **17 routes**
  (`/accounts` grew from a placeholder to 8.76 kB).

**Phase 5 — Video Upload and Media Management**

- **Resumable upload protocol** (`/api/v1/media/{workspace_id}/uploads`) — five stages:
  `POST` declares filename/MIME/size and validates them *before any byte moves*; `PUT ?offset=`
  appends a chunk and refuses an out-of-order chunk with `409` + `X-Upload-Expected-Offset`;
  `GET` returns `received_bytes`/`offset` so a dropped connection resumes; `POST …/complete`
  verifies the byte count, hashes, FFprobes, and adopts the file; `DELETE …` aborts.
  Sessions are JSON sidecars under `.uploads/` with a `MEDIA_UPLOAD_SESSION_TTL_HOURS=6` TTL.
- **Server-side validation** — allowed MIME types (`MEDIA_ALLOWED_MIME_TYPES`) and a maximum size
  (`MEDIA_MAX_UPLOAD_BYTES`, 2 GiB) are enforced at declare time and again per chunk; filenames
  are rejected if they contain path separators. A renamed non-video is caught by FFprobe at
  completion (`422`).
- **FFmpeg / FFprobe integration** (`app/services/video.py`) — duration, width, height, and
  codecs are read from the stored bytes, and a JPEG thumbnail (max width 640, seeking 1 s in) is
  generated. FFmpeg is a **soft dependency**: a missing binary answers `503`, and a failed
  thumbnail never fails an upload.
- **Signed, expiring URLs** (`app/services/storage.py`) — HMAC-SHA256 over
  `media_id`+variant+expiry keyed by `SECRET_KEY`; the file-serving route needs no session
  because the signature *is* the authorization; a tampered signature answers `403`. TTL 900 s.
  Raw storage keys and filesystem paths never leave the server.
- **Storage abstraction** — `LocalStorage` behind a narrow interface (`original`/`thumbnail`
  variants, `{workspace_id}/{media_id}/source{ext}` keys built server-side), injected via
  `get_storage()` so an S3/MinIO backend can replace it without touching routes or services.
- **Media library** — list (status filter, debounced filename search, `limit`/`offset`),
  get, per-workspace stats (count / bytes / ready / failed), replace-in-place (keeps the media
  id), and soft delete (frees bytes, `409` while a live post still references the file).
- **RBAC + tenant isolation** — new codes `media:read`, `media:upload`, `media:delete`
  (owner/admin full, editor media access, viewer read); every route resolves the workspace
  through `require_permission`, and another tenant's id answers `404`.
- **`/upload` page** — real drag-and-drop uploader with in-browser preview, client-side
  pre-validation, chunked progress with a byte counter, resumable retry, a searchable library
  grid, a preview dialog, replace, and delete. Includes an FFmpeg-missing warning banner that
  names the exact env vars to set.
- **54 new backend tests** (160 total) across `test_media.py`, `test_storage.py`, `test_video.py`.
- `npm run typecheck`, `npm run lint`, and `npm run build` all pass; **17 routes** build
  (`/upload` grew from a placeholder to 10.9 kB).

**Phase 4 — Professional Frontend Dashboard**

- **Landing page** (`/`) — hero, feature grid, platform coverage, auth-aware CTAs.
- **Auth pages** — `/register`, `/login`, `/forgot-password`, `/reset-password`, `/verify-email`,
  all wired to the real Phase 3 API.
- **Dashboard shell** — fixed sidebar with grouped navigation, mobile slide-over, sticky topbar
  with workspace switcher, theme toggle, and account menu (sign out / sign out everywhere).
- **Ten dashboard pages** — `/dashboard`, `/accounts`, `/upload`, `/compose`, `/schedule`,
  `/history`, `/analytics`, `/assistant`, `/settings`, `/billing`.
- **`/dashboard`** renders real data: active workspace (role, slug, created), live member count
  and member table from `GET /api/v1/workspaces/{id}/members`, and a getting-started checklist.
- **`/settings`** is fully functional against Phase 3: account details + resend verification
  email, change password (revokes other sessions), active-session list with per-session revoke
  and "sign out everywhere", and a system-health panel (API / PostgreSQL / Redis probes).
- **Token handling** — access token in memory only (never `localStorage`), refresh token in the
  `HttpOnly` cookie, shared `lib/api.ts` client with `credentials: "include"`, automatic
  401 → refresh → retry-once, and a de-duplicated single-flight refresh.
- **Route guard** — `RequireAuth` (client-side UX only; every API route re-authorises server-side).
- **Design system** — 18 UI primitives, dark-first theme with an anti-flash inline script,
  loading skeletons, empty states, toasts, inline form validation, and focus-visible rings.
- **Honest placeholders** — pages for later-phase modules list their planned capabilities and
  phase number. No mock data, fake charts, or simulated integrations anywhere.
- Zero backend changes: Phase 4 consumes the Phase 3 contract as-is.
- `npm run typecheck`, `npm run lint`, and `npm run build` all pass; **17 routes** build.

**Phase 3 — Authentication and Authorization**

- Registration, login, logout, and logout-all endpoints under `/api/v1/auth`.
- Password hashing with **bcrypt** (work factor 12); long inputs pre-hashed to respect bcrypt's 72-byte cap.
- Short-lived HS256 **access tokens** (`sub`/`sid`/`jti`/`typ`/`iss`/`aud`), validated against a live session row.
- Opaque 256-bit **refresh tokens** delivered in an `HttpOnly` cookie scoped to `/api/v1/auth`; only the SHA-256 digest is stored.
- **Refresh-token rotation** with theft detection — replaying a rotated token revokes its whole `family_id`.
- **Email verification** and **password reset** via single-use, expiring `verification_tokens`.
- Password change (keeps the calling session, revokes the rest) and password-reset confirm.
- **Session management**: list active sessions, revoke one, revoke all.
- **Protected routes** via `get_current_session` / `get_current_user`; optional auth via `get_optional_user`.
- **Role-based access** (`require_permission` over `roles.permissions` codes) and **workspace permissions** (workspace/member endpoints).
- **Tenant isolation** in routes: `get_workspace_context` uses `TenantMixin.scoped` and returns 404 (not 403) for non-members.
- Two new tables (`auth_sessions`, `verification_tokens`) — **18 tables total**; migration `14ae69daee8c`.
- Pluggable email delivery (`console` for dev, `smtp` for real), with a production guard against logging links.
- 49 new tests (106 total) covering security primitives and the full auth/authorization flows.

**Phase 2 — Database Architecture**

- 16 SQLAlchemy 2.0 models on `Base.metadata`, typed with `Mapped` / `mapped_column`.
- Reusable mixins: `UUIDPrimaryKeyMixin`, `TimestampMixin`, `SoftDeleteMixin`, `TenantMixin`.
- 14 domain enums stored as `VARCHAR(32)` + named `CHECK` constraints (not native `ENUM`).
- Deterministic constraint naming convention for stable Alembic autogenerate.
- Alembic migration `0d24e6e89e59_create_core_tables` — 16 tables, FKs, CHECKs, 9 composite indexes.
- Alembic migration `70ed893bdb70_seed_system_roles_and_plans` — 4 roles + 4 plans, idempotent.
- Transaction management: `session_scope()` context manager + `get_db()` FastAPI dependency.
- Tenant isolation: schema-level `NOT NULL workspace_id` + `TenantMixin.scoped()` query helper.
- ER diagram documented in `docs/DATABASE_SCHEMA.md`.
- 52 new tests (57 total) covering constraints, referential actions, tenant isolation, and seed data.

**Phase 1 — Project Foundation**

- Repository layout (`backend/`, `frontend/`, `docs/`).
- FastAPI backend with structured JSON logging, typed settings, CORS, and lifespan hooks.
- Versioned API router (`/api/v1`) with health, database, and Redis probes.
- SQLAlchemy engine/session and a declarative `Base` ready for Phase 2 models.
- Alembic configured to read the DB URL from application settings.
- Celery application wired to Redis (worker boots; `health.ping` task available).
- Next.js (App Router) + TypeScript + Tailwind + shadcn/ui shell with a dashboard page.
- Frontend `SystemHealth` component that probes backend, database, and Redis.
- Docker Compose stack: `postgres`, `redis`, `backend`, `worker`, `frontend`.
- Documentation set (README + `docs/*`).

## Files created

**Phase 15 — Production Deployment**

*Root* — `docker-compose.prod.yml` (the seven-container production stack: nginx, frontend, backend,
worker, beat, postgres, redis — only nginx publishes ports); `.env.production.example` (the production
overrides, kept by `.gitignore`); `.github/workflows/ci.yml` (backend + frontend jobs on every push/PR)

*Backend* — `Dockerfile.prod` (two-stage, non-root, gunicorn + `alembic upgrade head`)

*Backend (`app/core/`)* — `observability.py` (`configure_error_tracking()` — opt-in Sentry init; a no-op
without `SENTRY_DSN`, `send_default_pii=False`)

*Backend (tests)* — `app/tests/test_observability.py` (2 tests)

*Frontend* — `Dockerfile.prod` (three-stage `deps` → `builder` → `runner` on the standalone bundle)

*Deploy* — `nginx/app.conf` (TLS, HTTP→HTTPS, `/api/` → backend with streamed 2 GiB bodies, `/` →
frontend); `backup.sh`, `restore.sh`

*Docs* — `docs/DEPLOYMENT.md`

**Phase 14 — Complete Testing**

*Backend (tests)* — `app/tests/test_worker.py` (9 tests: task registration, the three beat schedules and
their settings-driven intervals, `ping`, and each DB-backed task's delegation + id coercion + return shape)

*Frontend (tooling)* — `vitest.config.ts` (`@/*` alias, automatic JSX, jsdom, jest-dom setup),
`vitest.setup.ts`

*Frontend (tests)* — `lib/format.test.ts`, `lib/media-upload.test.ts`, `lib/api.test.ts`,
`lib/utils.test.ts`, `components/empty-state.test.tsx`, `components/ui/button.test.tsx` (47 tests)

**Phase 13 — Security & Performance**

*Backend (`app/core/`)* — `rate_limit.py` (the in-house fixed-window limiter: `RateLimiter`, the Redis
`_RedisStore` and the in-process `MemoryStore` fallback, `rate_limit()` — the dependency factory —
`client_identity()`, and the `429` + `Retry-After` response); `middleware.py`
(`SecurityHeadersMiddleware` + `OriginGuardMiddleware`)

*Backend (`app/models/`)* — `audit.py` (`AuditLog`: append-only, `user_id`/`workspace_id` FK
`ON DELETE SET NULL`, `detail` JSONB, three composite indexes)

*Backend (`app/schemas/`)* — `audit.py` (`AuditLogPublic` + `from_model`, `AuditLogListPublic`)

*Backend (`app/services/`)* — `audit.py` (the write path `record()` — staged, never committed — the
read path `list_events()`, and the dotted `action` catalogue)

*Backend (`app/api/v1/endpoints/`)* — `audit.py` (the read-only `GET /{workspace_id}/audit`)

*Backend (migrations)* — `alembic/versions/d1a4b7c93e02_phase13_audit_logs.py`

*Backend (tests)* — `app/tests/test_security_hardening.py` (20 rate-limit / security-header / CSRF /
audit-trail tests)

**Phase 11 — Notifications**

*Backend (`app/services/`)* — `notifications.py` (the delivery core: `record`, `list_notifications`,
`count_unread`, `mark_read`, `mark_all_read`, `delete`, `get_preferences`, `update_preferences`, the
three sub-sweeps `dispatch_emails` / `send_schedule_reminders` / `warn_expiring_tokens`, `sweep` +
`SweepResult`, and the `NotificationError` taxonomy)

*Backend (`app/schemas/`)* — `notification.py` (`NotificationPublic` + `from_model`,
`NotificationListPublic`, `UnreadCountPublic`, `MarkAllReadResult`, `NotificationPreferencePublic` +
`from_model`, `NotificationPreferenceUpdate`)

*Backend (`app/api/v1/endpoints/`)* — `notifications.py` (7 user-scoped routes)

*Backend (migrations)* — `alembic/versions/b8e2f5a91c47_phase11_notifications.py`

*Backend (tests)* — `app/tests/test_notifications.py` (16 inbox/service/sweep tests)

*Frontend (`components/notifications/`)* — `notification-bell.tsx` (the top-bar bell + inbox
dropdown)

**Phase 10 — Analytics Dashboard**

*Backend (`app/services/metrics/`)* — `__init__.py` (the registry: `get_metrics_provider`,
`supported_metrics_platforms`), `base.py` (the `MetricsProvider` ABC with the shared HTTP
machinery, the `MetricsError` taxonomy, the `PostMetrics`/`AccountMetrics` value objects, and
`as_int`), `facebook.py` + `instagram.py` (Meta Graph Insights), `youtube.py` (Data API v3),
`tiktok.py` (Display API)

*Backend (`app/services/`)* — `analytics.py` (performance reads, aggregation, and the refresh sweep)

*Backend (`app/models/`)* — `analytics.py` (`PostMetric`, `AccountMetric` — append-only snapshots)

*Backend (`app/schemas/`)* — `analytics.py` (`MetricSetPublic`, `AudienceTotalsPublic`,
`MetricSnapshotPublic`, `OverviewPublic`, `PlatformPerformancePublic`, `AccountPerformancePublic`,
`TargetPerformancePublic`, `HistoryPointPublic`, `PlatformCapabilityPublic`,
`AnalyticsCapabilityPublic`, `RefreshResultPublic`)

*Backend (`app/api/v1/endpoints/`)* — `analytics.py` (7 routes + `_translate` error mapping)

*Backend (migrations)* — `alembic/versions/f241d12e080b_add_analytics_metrics.py`

*Backend (tests)* — `app/tests/test_analytics.py` (36 provider/service/API-level tests)

*Frontend (`components/analytics/`)* — `analytics-dashboard.tsx` (the `/analytics` implementation,
including the dependency-free SVG `Sparkline`)

**Phase 9 — AI Content Generation**

*Backend (`app/services/ai/`)* — `__init__.py` (the registry: `get_provider`,
`supported_providers`), `base.py` (the `AIProvider` ABC with the shared HTTP machinery, the
`AIError` taxonomy, and the class-level `transport` hook), `openai.py` (the OpenAI
`chat/completions` provider)

*Backend (`app/services/`)* — `ai_content.py` (prompt engineering + the generation, history,
and accept orchestration)

*Backend (`app/schemas/`)* — `ai.py` (`AICapabilityPublic`, `AIContentPublic`,
`AIGenerateRequest`)

*Backend (`app/api/v1/endpoints/`)* — `ai.py` (4 routes + `_translate` error mapping)

*Backend (tests)* — `app/tests/test_ai.py` (28 provider/service/API-level tests)

*Frontend (`components/ui/`)* — `select.tsx` (a small styled native `<select>` primitive for
the AI and schedule pickers)

*Frontend (`components/ai/`)* — `ai-assistant.tsx` (the `/assistant` implementation)

**Phase 8 — Content Scheduling**

*Backend (`app/services/`)* — `scheduling.py` (the scheduler: `normalize_timezone`,
`to_utc_instant`, `local_time`, `get_schedule`, `list_schedules`, `schedule_post`,
`reschedule`, `cancel`, `sweep_due` + `SweepResult`, `_mark_missed`, and the
`SchedulingError` taxonomy)

*Backend (`app/schemas/`)* — `schedule.py` (`SchedulePublic` + `from_schedule`,
`ScheduleCreateRequest`, `ScheduleUpdateRequest`)

*Backend (`app/api/v1/endpoints/`)* — `scheduling.py` (4 routes + `_translate` error mapping)

*Backend (tests)* — `app/tests/test_scheduling.py` (19 scheduler/API-level tests)

*Frontend (`components/schedule/`)* — `schedule-time-field.tsx` (shared `datetime-local` +
IANA zone picker), `scheduling-calendar.tsx` (the `/schedule` calendar)

**Phase 7 — Multi-Platform Publishing Engine**

*Backend (`app/services/publishers/`)* — `base.py` (`Publisher` ABC, `PublisherCapability`,
`MediaAsset`, `PublishRequest`, `PublishResult`, the `PublishError` taxonomy, HTTP
classification + status polling), `__init__.py` (registry: `get_publisher`,
`supported_platforms`), `meta.py` (shared Meta Graph publishing base), `facebook.py`,
`instagram.py`, `youtube.py`, `tiktok.py`

*Backend (`app/services/`)* — `publishing.py` (the "Publish Everywhere" orchestration:
`capabilities`, `create_post`, `publish_everywhere`, `run_job`, `get_post`, `list_posts`,
`get_job`, `enqueue_job`, plus the domain errors)

*Backend (`app/schemas/`)* — `post.py` (`PublisherCapabilityPublic`, `PublishingLogPublic`,
`PublishingJobPublic`, `PublishingJobDetail`, `PostTargetPublic`, `PostPublic`,
`TargetSelectionRequest`, `PostCreateRequest`, `PublishEverywhereRequest`)

*Backend (`app/api/v1/endpoints/`)* — `posts.py` (6 routes + `_translate` error mapping)

*Backend (tests)* — `app/tests/test_publishers.py` (16 adapter-level),
`app/tests/test_publishing.py` (18 orchestration/API-level)

*Frontend (`components/posts/`)* — `composer.tsx`

**Phase 6 — Social Media Account Connections (OAuth)**

*Backend (`app/core/`)* — `crypto.py` (AES-256-GCM envelope encryption, HKDF-SHA256 key
derivation, `v1.` ciphertext format)

*Backend (`app/services/`)* — `accounts.py` (connect/refresh/disconnect orchestration,
`ensure_fresh_tokens`)

*Backend (`app/services/oauth/`)* — `__init__.py` (registry: `get_provider`,
`supported_platforms`), `base.py` (`OAuthProvider` ABC, shared authorization/callback helpers),
`meta.py` (Meta Graph base), `facebook.py`, `instagram.py`, `youtube.py`, `tiktok.py`

*Backend (`app/schemas/`)* — `account.py` (`SocialAccountPublic`, `PlatformCapability`,
`AccountCapabilitiesPublic`, `AuthorizationStartPublic`, `AccountDisconnectResult`)

*Backend (`app/api/v1/endpoints/`)* — `accounts.py` (7 routes + `_translate` error mapping)

*Backend (migrations)* — `alembic/versions/5347775d077d_add_oauth_authorization_states.py`

*Backend (tests)* — `app/tests/test_oauth.py` (20 provider-level), `app/tests/test_accounts.py`
(20 API-level)

*Frontend (`components/accounts/`)* — `accounts-manager.tsx`

*Docs* — `docs/OAUTH_SETUP.md`

**Phase 5 — Video Upload and Media Management**

*Backend (`app/services/`)* — `media.py` (validation, resumable sessions, finalise, list,
delete), `storage.py` (`LocalStorage`, safe keys, signed URLs), `video.py` (FFprobe inspect +
FFmpeg thumbnail)

*Backend (`app/schemas/`)* — `media.py`

*Backend (`app/api/v1/endpoints/`)* — `media.py`

*Backend (tests)* — `app/tests/test_media.py` (27), `app/tests/test_storage.py` (21),
`app/tests/test_video.py` (6)

*Frontend (`lib/`)* — `media-upload.ts` (the client side of the resumable protocol)

*Frontend (`components/media/`)* — `upload-panel.tsx`, `media-library.tsx`

*Runtime (git-ignored)* — `backend/var/media/` (uploaded files + generated thumbnails)

**Phase 4 — Professional Frontend Dashboard**

*Frontend (pages)* — `app/page.tsx` (landing, rewritten), `app/(auth)/login/page.tsx`,
`app/(auth)/register/page.tsx`, `app/(auth)/forgot-password/page.tsx`,
`app/(auth)/reset-password/page.tsx`, `app/(auth)/verify-email/page.tsx`,
`app/(dashboard)/layout.tsx`, `app/(dashboard)/dashboard/page.tsx`,
`app/(dashboard)/accounts/page.tsx`, `app/(dashboard)/upload/page.tsx`,
`app/(dashboard)/compose/page.tsx`, `app/(dashboard)/schedule/page.tsx`,
`app/(dashboard)/history/page.tsx`, `app/(dashboard)/analytics/page.tsx`,
`app/(dashboard)/assistant/page.tsx`, `app/(dashboard)/settings/page.tsx`,
`app/(dashboard)/billing/page.tsx`

*Frontend (`lib/`)* — `api.ts` (rewritten), `types.ts`, `config.ts`, `auth-context.tsx`,
`theme-context.tsx`, `format.ts`, `platforms.ts`

*Frontend (`components/ui/`)* — `input.tsx`, `label.tsx`, `textarea.tsx`, `separator.tsx`,
`skeleton.tsx`, `alert.tsx`, `avatar.tsx`, `table.tsx`, `tabs.tsx`, `dropdown-menu.tsx`,
`progress.tsx`, `switch.tsx`, `toaster.tsx`, `spinner.tsx`, `field.tsx`

*Frontend (`components/app-shell/`)* — `dashboard-shell.tsx`, `sidebar.tsx`, `topbar.tsx`, `nav.ts`

*Frontend (`components/auth/`)* — `auth-layout.tsx`, `marketing-aside.tsx`, `password-input.tsx`,
`login-form.tsx`, `register-form.tsx`, `forgot-password-form.tsx`, `reset-password-form.tsx`,
`verify-email-panel.tsx`

*Frontend (`components/`)* — `brand.tsx`, `page-header.tsx`, `empty-state.tsx`,
`module-placeholder.tsx`, `platform-icon.tsx`, `require-auth.tsx`, `landing-actions.tsx`,
`settings/settings-tabs.tsx`

*Frontend (`hooks/`)* — `use-toast.ts`

**Phase 3 — Authentication and Authorization**

*Backend (`app/core/`)* — `security.py`, `permissions.py`
*Backend (`app/models/`)* — `auth.py` (`AuthSession`, `VerificationToken`)
*Backend (`app/schemas/`)* — `__init__.py`, `auth.py`, `workspace.py`
*Backend (`app/services/`)* — `__init__.py`, `auth.py`, `email.py`, `workspaces.py`
*Backend (`app/api/`)* — `cookies.py`, `deps.py`
*Backend (`app/api/v1/endpoints/`)* — `auth.py`, `workspaces.py`
*Backend (migrations)* — `alembic/versions/14ae69daee8c_add_auth_sessions_and_verification_.py`
*Backend (tests)* — `app/tests/test_auth.py`, `app/tests/test_security.py`

**Phase 2 — Database Architecture**

*Backend (`app/db/`)* — `enums.py`, `types.py`, `mixins.py`
*Backend (`app/models/`)* — `__init__.py`, `user.py`, `workspace.py`, `social_account.py`,
`media.py`, `post.py`, `publishing.py`, `scheduling.py`, `ai_content.py`,
`notification.py`, `billing.py`
*Backend (migrations)* — `alembic/versions/0d24e6e89e59_create_core_tables.py`,
`alembic/versions/70ed893bdb70_seed_system_roles_and_plans.py`
*Backend (tests)* — `app/tests/conftest.py`, `app/tests/test_models.py`,
`app/tests/test_db_integration.py`

**Phase 1 — Project Foundation**

**Root:** `.gitignore`, `.env.example`, `docker-compose.yml`, `README.md`

> Verification note: `next` upgraded `15.1.3` → `15.5.29` and `eslint-config-next`
> `15.1.3` → `15.5.29` in `frontend/package.json` during Phase 1 verification.

**Backend:** `Dockerfile`, `.dockerignore`, `requirements.txt`, `requirements-dev.txt`,
`pytest.ini`, `alembic.ini`, `alembic/env.py`, `alembic/script.py.mako`,
`alembic/versions/.gitkeep`, `app/__init__.py`, `app/main.py`, `app/worker.py`,
`app/core/{__init__,config,logging}.py`, `app/db/{__init__,base,session}.py`,
`app/api/__init__.py`, `app/api/v1/{__init__,router}.py`,
`app/api/v1/endpoints/{__init__,health}.py`, `app/tests/{__init__,test_health}.py`

**Frontend:** `package.json`, `tsconfig.json`, `next.config.mjs`, `postcss.config.mjs`,
`tailwind.config.ts`, `components.json`, `.eslintrc.json`, `.dockerignore`, `Dockerfile`,
`.env.local.example`, `app/{layout,page}.tsx`, `app/globals.css`,
`lib/{api,utils}.ts`, `components/system-health.tsx`,
`components/ui/{button,card,badge}.tsx`

**Docs:** `docs/PROJECT_ARCHITECTURE.md`, `docs/DATABASE_SCHEMA.md`,
`docs/API_DOCUMENTATION.md`, `docs/DEVELOPMENT_PROGRESS.md`,
`docs/SETUP_GUIDE.md`, `docs/PROJECT_STATE.md`

## Files modified

**Phase 15 — Production Deployment**

- `app/main.py` — calls `configure_error_tracking()` before the `FastAPI` instance is created.
- `app/core/config.py` — the observability block (`sentry_dsn`, `sentry_traces_sample_rate`) plus the
  `[0, 1]` validator on the sample rate.
- `backend/requirements.txt` — added `gunicorn==23.0.0` (the production process manager).
- `.gitignore` — `!.env.production.example` (it matched the `.env.*` rule and would have been ignored),
  and ignores for `deploy/certs/` and `backups/`.
- `.env.example` — the "Observability (Phase 15)" block.
- `docs/PROJECT_STATE.md`, `docs/DEVELOPMENT_PROGRESS.md`, `docs/SETUP_GUIDE.md`, `README.md`.

**Phase 14 — Complete Testing**

- `frontend/package.json` — `test` (`vitest run`) and `test:watch` (`vitest`) scripts, plus the Vitest /
  jsdom / Testing Library devDependencies.
- `docs/API_DOCUMENTATION.md` — documented the Phase 13 `GET /api/v1/workspaces/{workspace_id}/audit`
  endpoint (query params, `audit:read` gate, newest-first ordering, the `AuditLogListPublic` shape) and the
  Phase 13 hardening entry in Conventions; the "Planned endpoints" note no longer claims later phases add no
  API surface.
- `docs/PROJECT_STATE.md`, `docs/DEVELOPMENT_PROGRESS.md`, `docs/SETUP_GUIDE.md`.

> Phase 15 adds **no** new Python dependency beyond `gunicorn` (Sentry was already installed and is now
> used). The frontend test tooling is a devDependency only — `next build` is unaffected.

**Phase 13 — Security & Performance**

- `app/core/config.py` — the Phase 13 security block (`rate_limit_enabled`,
  `rate_limit_window_seconds`, `rate_limit_auth_per_minute`, `rate_limit_ai_per_minute`).
- `app/core/permissions.py` — the new `AUDIT_READ` (`audit:read`) code.
- `app/main.py` — wired `GZipMiddleware` (≥ 1 KB), `OriginGuardMiddleware`, and
  `SecurityHeadersMiddleware`; the documented order is CORS → SecurityHeaders → OriginGuard → GZip → app.
- `app/models/__init__.py` — registered `AuditLog`.
- `app/api/v1/router.py` — registered the audit router under `/workspaces`.
- `app/api/v1/endpoints/auth.py` — the shared `auth` rate-limit dependency on the six credential routes,
  plus the audit calls (`auth.registered`, `auth.login.succeeded`, `auth.login.failed`, `auth.logout`,
  `auth.logout_all`, `auth.password.changed`, `auth.password.reset`) and the `_primary_workspace_id`
  helper that files a sign-in under the account's oldest active membership.
- `app/api/v1/endpoints/ai.py` — the `ai-generate` rate-limit dependency on `POST /{workspace_id}/generate`.
- `app/tests/conftest.py` — sets `RATE_LIMIT_ENABLED=false` for the suite (the limiter's own tests turn it
  back on over a fresh in-process store).
- `app/tests/test_models.py` — `AuditLog` registered and `"audit_logs"` added to the expected-tables set.
- `.env.example` — the "Security hardening (Phase 13)" block.
- `docs/PROJECT_STATE.md`, `docs/DATABASE_SCHEMA.md`, `docs/DEVELOPMENT_PROGRESS.md`,
  `docs/SETUP_GUIDE.md`, `docs/PROJECT_ARCHITECTURE.md`, `README.md`.

> No new dependency was added in Phase 13 — the limiter reuses `redis` (already required) and the
> middleware ships with Starlette. One new table (`audit_logs`) ships with migration `d1a4b7c93e02`,
> taking the count to **25 tables**; the same migration seeds `audit:read` to the owner/admin roles.

**Phase 11 — Notifications**

- `app/core/config.py` — the notifications settings block (`notifications_sweep_interval_seconds`,
  `notifications_email_batch_size`, `notifications_reminder_lead_seconds`,
  `notifications_token_warning_days`).
- `app/models/notification.py` — `Notification.emailed_at` + the `ix_notifications_pending_email`
  partial index, and the new `NotificationPreference` model.
- `app/models/scheduling.py` — `ScheduledPost.reminder_sent_at`.
- `app/models/social_account.py` — `SocialAccount.token_warning_sent_at`.
- `app/models/__init__.py` — registered `NotificationPreference`.
- `app/api/v1/router.py` — registered the notifications router at `/notifications`.
- `app/worker.py` — the `notifications.sweep` Celery task + its Beat schedule entry.
- `app/tests/test_models.py` — the expected-tables set and the model list include
  `notification_preferences`.
- `frontend/lib/types.ts` — the Phase 11 DTO block (`NotificationType`, `Notification`,
  `NotificationList`, `NotificationPreference`), field-for-field with
  `app/schemas/notification.py`.
- `frontend/lib/api.ts` — added the `api.notifications.*` group (list, unreadCount, markRead,
  markAllRead, dismiss, preferences, updatePreferences).
- `frontend/components/app-shell/topbar.tsx` — mounted the `NotificationBell`.
- `frontend/components/settings/settings-tabs.tsx` — the "Notifications" tab (email opt-outs).

**Phase 10 — Analytics Dashboard**

- `app/core/config.py` — the analytics settings block (`analytics_http_timeout_seconds`,
  `analytics_default_window_days`, `analytics_max_history_points`,
  `analytics_max_refresh_targets`).
- `app/core/permissions.py` — the `ANALYTICS_READ` / `ANALYTICS_REFRESH` codes.
- `app/api/v1/router.py` — registered the analytics router at `/analytics`.
- `app/worker.py` — the `analytics.refresh_workspace` Celery task.
- `app/models/__init__.py` — registered `PostMetric` / `AccountMetric`.
- `frontend/lib/types.ts` — the Phase 10 DTO block (`MetricKey`, `MetricSet`, `AudienceTotals`,
  `MetricSnapshot`, `AnalyticsOverview`, `PlatformPerformance`, `AccountPerformance`,
  `TargetPerformance`, `HistoryPoint`, `AnalyticsPlatformCapability`, `AnalyticsCapability`,
  `RefreshResult`), field-for-field with `app/schemas/analytics.py`.
- `frontend/lib/api.ts` — added the `api.analytics.*` group (capability, overview, platforms,
  accounts, targets, history, refresh).
- `frontend/app/(dashboard)/analytics/page.tsx` — the placeholder replaced by the real
  `AnalyticsDashboard`.
- `.env.example`, `backend/.env` — documented/registered the analytics settings.
- `docs/PROJECT_STATE.md`, `docs/DEVELOPMENT_PROGRESS.md`, `docs/API_DOCUMENTATION.md`,
  `docs/PROJECT_ARCHITECTURE.md`, `docs/DATABASE_SCHEMA.md`, `docs/SETUP_GUIDE.md`, `README.md`.

> No new dependency was added in Phase 10 — the metrics providers reuse `httpx` (already required).
> Two new tables (`post_metrics`, `account_metrics`) ship with migration `f241d12e080b`, taking the
> count to **21 tables**; the same migration seeds the `analytics:read` / `analytics:refresh` codes
> additively.

**Phase 9 — AI Content Generation**

- `app/core/config.py` — the AI settings block (`openai_api_key`, `openai_base_url`,
  `openai_model`, `openai_http_timeout_seconds`, `openai_max_output_tokens`,
  `openai_temperature`, `ai_max_brief_chars`) + the temperature range validator.
- `app/api/v1/router.py` — registered the AI router at `/ai`.
- `frontend/lib/types.ts` — the Phase 9 DTO block (`AIContentKind`, `AICapability`,
  `AIContent`, `AIGeneratePayload`), field-for-field with `app/schemas/ai.py`.
- `frontend/lib/api.ts` — added the `api.ai.*` group (capability, generate, history, accept).
- `frontend/components/posts/composer.tsx` — added the "Draft with AI" panel that fills the
  base caption from a short brief (gated on `ai:generate`).
- `frontend/app/(dashboard)/assistant/page.tsx` — the placeholder replaced by the real
  `AIAssistant`.
- `.env.example`, `backend/.env` — documented/registered the AI settings.
- `docs/PROJECT_STATE.md`, `docs/DEVELOPMENT_PROGRESS.md`, `docs/API_DOCUMENTATION.md`,
  `docs/PROJECT_ARCHITECTURE.md`, `docs/SETUP_GUIDE.md`, `README.md`.

> No new dependency was added in Phase 9. The provider reuses `httpx` (already required) and
> the prompt/response handling is stdlib `json`; no new database table or migration was needed
> — `ai_generated_content` already existed from Phase 2, so the migration head stays
> `5347775d077d` and the count stays at **19 tables**.

**Phase 8 — Content Scheduling**

- `app/core/config.py` — the scheduling settings block
  (`scheduling_sweep_interval_seconds`, `scheduling_batch_size`,
  `scheduling_missed_grace_seconds`, `scheduling_default_timezone`).
- `app/core/permissions.py` — added `SCHEDULE_READ`, `SCHEDULE_CREATE`, `SCHEDULE_CANCEL`.
- `app/api/v1/router.py` — registered the scheduling router at `/posts` **before** the posts
  router (so `/posts/{workspace_id}/schedule` is not matched by `/posts/{workspace_id}/{post_id}`).
- `app/services/publishing.py` — split the dispatch path into `record_targets` (validate +
  persist pending targets, no jobs) and `queue_targets` (create + dispatch jobs), so
  `publish_everywhere` and the scheduler share one implementation.
- `app/worker.py` — added the `scheduling.dispatch_due` Celery task and its Celery Beat
  schedule entry.
- `frontend/lib/types.ts` — the Phase 8 DTO block (`ScheduleStatus`, `ScheduleWindow`,
  `Schedule`, `ScheduleCreatePayload`, `ScheduleUpdatePayload`), field-for-field with
  `app/schemas/schedule.py`.
- `frontend/lib/api.ts` — added the `api.schedules.*` group (list, create, reschedule, cancel).
- `frontend/lib/format.ts` — added `formatDateTimeInZone(value, timeZone)` to render an
  instant in the schedule's own zone.
- `frontend/components/posts/composer.tsx` — added the "Schedule for later" path (shares
  destination validation with publish-now via `collectTargets()`) and the schedule time panel.
- `frontend/app/(dashboard)/schedule/page.tsx` — the placeholder replaced by the real
  `SchedulingCalendar`.
- `docs/PROJECT_STATE.md`, `docs/DEVELOPMENT_PROGRESS.md`, `docs/API_DOCUMENTATION.md`,
  `docs/PROJECT_ARCHITECTURE.md`, `docs/SETUP_GUIDE.md`, `README.md`.

> No new dependency was added in Phase 8. Time zones use the Python **stdlib** `zoneinfo`
> (the IANA database already ships with the pinned Python), and no new database table or
> migration was needed — `scheduled_posts` already existed from Phase 2.

**Phase 7 — Multi-Platform Publishing Engine**

- `app/core/config.py` — the publishing engine settings block
  (`publishing_http_timeout_seconds`, `publishing_media_url_ttl_seconds`,
  `publishing_poll_interval_seconds`, `publishing_poll_max_attempts`,
  `publishing_retry_base_seconds`, `publishing_retry_max_seconds`, `publishing_max_attempts`,
  `publishing_default_privacy`).
- `app/core/permissions.py` — added `POST_READ`, `POST_CREATE`, `POST_UPDATE`, `POST_DELETE`,
  `POST_PUBLISH`.
- `app/api/v1/router.py` — registered the posts router at `/posts`.
- `app/worker.py` — added the `publishing.publish_target` Celery task.
- `frontend/lib/types.ts` — the Phase 7 DTO block (`PostStatus`, `PostTargetStatus`,
  `JobStatus`, `LogLevel`, `PublisherCapability`, `PublishingLog`, `PublishingJob`,
  `PublishingJobDetail`, `PostTarget`, `Post`, `PostCreatePayload`, `TargetSelectionPayload`,
  `PublishEverywherePayload`), field-for-field with `app/schemas/post.py`.
- `frontend/lib/api.ts` — added the `api.posts.*` group (capabilities, list, get, create,
  publish, job).
- `frontend/app/(dashboard)/compose/page.tsx` — the placeholder replaced by the real
  `PublisherComposer`.
- `docs/PROJECT_STATE.md`, `docs/DEVELOPMENT_PROGRESS.md`, `docs/API_DOCUMENTATION.md`,
  `docs/PROJECT_ARCHITECTURE.md`, `docs/SETUP_GUIDE.md`, `README.md`.

> No new dependency was added in Phase 7. The publishers reuse `httpx` (promoted to
> `requirements.txt` in Phase 6) and the Phase 6 credential vault, and no new database
> table or migration was needed.

**Phase 6 — Social Media Account Connections (OAuth)**

- `app/core/config.py` — the OAuth settings block (`backend_public_url`, `oauth_success_redirect`,
  `oauth_failure_redirect`, `oauth_state_ttl_seconds`, `oauth_http_timeout_seconds`,
  `oauth_refresh_margin_seconds`, `oauth_encryption_key`, `meta_graph_version`,
  `facebook_client_id/secret`, `instagram_client_id/secret`, `youtube_client_id/secret`,
  `tiktok_client_key/secret`).
- `app/core/permissions.py` — added `ACCOUNT_READ`, `ACCOUNT_CONNECT`, `ACCOUNT_DISCONNECT`.
- `app/api/v1/router.py` — registered the accounts router at `/accounts`.
- `app/models/social_account.py` — added the `OAuthAuthorizationState` model (state digest,
  encrypted PKCE verifier, TTL, consumption).
- `app/models/__init__.py` — exports `OAuthAuthorizationState`.
- `app/tests/test_models.py` — expects **19** tables (added `oauth_authorization_states`).
- `app/tests/conftest.py` — OAuth provider transport/environment overrides for the API client.
- `frontend/lib/types.ts` — the connected-account DTO block (`SocialAccount`,
  `SocialAccountStatus`, `PlatformCapability`, `AccountCapabilities`, `AuthorizationStart`,
  `AccountDisconnectResult`), field-for-field with `app/schemas/account.py`.
- `frontend/lib/api.ts` — added the `api.accounts.*` group (capabilities, list, get, authorize,
  refresh, disconnect).
- `frontend/app/(dashboard)/accounts/page.tsx` — the placeholder replaced by the real
  `AccountsManager`.
- `frontend/app/(dashboard)/dashboard/page.tsx` — getting-started checklist now links
  "Connect a social account" and "Upload your first video".
- `.env.example`, `backend/.env` — documented/registered the OAuth client credentials, redirect
  URLs, and encryption key.
- `docs/PROJECT_STATE.md`, `docs/DEVELOPMENT_PROGRESS.md`, `docs/API_DOCUMENTATION.md`,
  `docs/PROJECT_ARCHITECTURE.md`, `docs/SETUP_GUIDE.md`, `docs/DATABASE_SCHEMA.md`, `README.md`.

> Dependency change in Phase 6: **`cryptography==44.0.0`** was added to
> `requirements.txt` for AES-256-GCM + HKDF, and **`httpx==0.28.1`** was promoted from
> `requirements-dev.txt` to `requirements.txt` — the OAuth providers import it at runtime, so a
> production install would otherwise fail to start.

**Phase 5 — Video Upload and Media Management**

- `app/core/config.py` — the `MEDIA_*` settings block (`media_root`, `media_max_upload_bytes`,
  `media_allowed_mime_types`, `media_chunk_size_bytes`, `media_upload_session_ttl_hours`,
  `media_signed_url_ttl_seconds`, `media_thumbnail_max_width`, `media_ffmpeg_binary`,
  `media_ffprobe_binary`, `media_ffmpeg_timeout_seconds`) + validators and the
  `media_allowed_mime_types_list` / `media_root_path` properties.
- `app/core/permissions.py` — added `MEDIA_READ`, `MEDIA_UPLOAD`, `MEDIA_DELETE`.
- `app/api/v1/router.py` — registered the media router.
- `app/tests/conftest.py` — the API-client fixture now overrides `get_storage` with a temporary
  media root, so tests never touch the developer's real uploads.
- `frontend/lib/types.ts` — the media DTO block (`MediaFile`, `MediaUploadRequest`,
  `UploadSession`, `MediaStats`, `MediaCapabilities`), field-for-field with the backend schemas.
- `frontend/lib/api.ts` — the shared `request()` helper now sends a `Blob` body (for chunk
  `PUT`s) and reports cancellation; added the `api.media.*` group.
- `frontend/lib/format.ts` — `formatBytes()` and `formatDuration()`.
- `frontend/app/(dashboard)/upload/page.tsx` — the placeholder replaced by the real media UI.
- `.env.example`, `backend/.env` — documented/registered the two FFmpeg binary paths.
- `docs/PROJECT_STATE.md`, `docs/DEVELOPMENT_PROGRESS.md`, `docs/API_DOCUMENTATION.md`,
  `docs/PROJECT_ARCHITECTURE.md`, `docs/SETUP_GUIDE.md`, `README.md`.

> No new third-party package was added in Phase 5. FFmpeg is an external binary (see the
> Known-issues note), not a Python or npm dependency.

**Phase 4 — Professional Frontend Dashboard**

- `app/layout.tsx` — provider stack (`ThemeProvider` → `AuthProvider` → `Toaster`), the inline
  anti-flash theme script, and the `"%s · One-Click Publisher"` metadata template.
- `app/globals.css` — added `popover`, `success`, `warning`, `info`, and `sidebar` token groups
  (light + dark), a global `:focus-visible` ring, and a `prefers-reduced-motion` utility.
- `tailwind.config.ts` — added the matching colours, `fontFamily.sans` via `--font-sans`, and the
  `fade-in` / `slide-in-right` keyframes.
- `components/system-health.tsx` — imports `HealthStatus` from `lib/types` (single source).
- `docs/PROJECT_STATE.md`, `docs/DEVELOPMENT_PROGRESS.md`, `docs/PROJECT_ARCHITECTURE.md`,
  `docs/SETUP_GUIDE.md`, `README.md`.

> No backend file was touched in Phase 4 — the frontend consumes the Phase 3 contract unchanged.

**Phase 3 — Authentication and Authorization**

- `app/main.py` — registered the `/auth` and `/workspaces` routers via `app/api/v1/router.py`.
- `app/api/v1/router.py` — added the auth and workspaces routers.
- `app/models/__init__.py` — registers `AuthSession` and `VerificationToken`.
- `app/db/enums.py` — added the `VerificationPurpose` enum (15 enums total).
- `app/core/config.py` — auth/token/cookie/email settings + production guards.
- `app/tests/test_models.py` — expects 18 tables (added the two auth tables).
- `app/tests/conftest.py` — added the `api_client` fixture (TestClient + `get_db` override).
- `backend/requirements.txt` — added `email-validator`, `PyJWT`, `bcrypt`.
- `docs/DATABASE_SCHEMA.md`, `docs/API_DOCUMENTATION.md`, `docs/DEVELOPMENT_PROGRESS.md`,
  `docs/PROJECT_ARCHITECTURE.md`, `docs/SETUP_GUIDE.md`, `README.md`.

**Phase 2 — Database Architecture**

- `app/db/base.py` — added the `MetaData` naming convention (ix/uq/ck/fk/pk).
- `app/db/session.py` — added `session_scope()`; `get_db()` now rolls back on error.
- `alembic/env.py` — imports `app.models` so autogenerate sees every table.
- `docs/DATABASE_SCHEMA.md` — rewritten from "target design" to the implemented schema + ER diagram.
- `docs/PROJECT_STATE.md`, `docs/DEVELOPMENT_PROGRESS.md`, `docs/PROJECT_ARCHITECTURE.md`.

**Phase 1 — Project Foundation**

None — that was the initial scaffold.

## Dependencies installed

- **Backend (requirements.txt):** fastapi, uvicorn[standard], pydantic, pydantic-settings,
  SQLAlchemy, alembic, psycopg[binary], redis, celery, python-multipart, PyJWT,
  sentry-sdk[fastapi]. Dev extras (requirements-dev.txt): pytest, pytest-asyncio.
  - **Added in Phase 3:** `bcrypt==4.2.1` (password hashing, used directly — not via
    passlib), `email-validator==2.2.0` (backs `pydantic.EmailStr`). `PyJWT==2.10.1` was
    already pinned in Phase 1 and is now used for access tokens.
  - **Added in Phase 6:** `cryptography==44.0.0` (AES-256-GCM + HKDF for token encryption).
    `httpx==0.28.1` moved from the dev extras to `requirements.txt` (runtime HTTP client for
    the OAuth providers).
  - **Phase 9 added no dependency** — the AI provider is a plain `httpx` client (already
    required) and needs no vendor SDK.
  - **Added in Phase 15:** `gunicorn==23.0.0` — the production process manager that supervises
    `WEB_CONCURRENCY` uvicorn workers behind nginx.
  - **Pinned in Phase 15 (bug fix):** `kombu==5.6.2`. Celery 5.4.0 was pinned but **kombu was not**,
    so a fresh install resolved kombu 5.7.0, which requires `redis-py >= 6.1.0` while `redis==5.2.1`
    is pinned — the worker then refused to start with `VersionMismatch: Redis transport requires
    redis-py versions 6.1.0 or later`. Pinning kombu 5.6.2 restores a consistent set (the same break
    would have hit the Phase 15 Docker image build).
- **Frontend (package.json):** next, react, react-dom, typescript, tailwindcss,
  tailwindcss-animate, postcss, autoprefixer, eslint, eslint-config-next,
  class-variance-authority, clsx, tailwind-merge, lucide-react, @radix-ui/react-slot.
  - **Added in Phase 14 (dev):** `vitest`, `jsdom`, `@testing-library/react`,
    `@testing-library/jest-dom`, `@testing-library/user-event` — the frontend test runner. No
    runtime dependency was added.

## Verification performed

Environment: Windows, Node v24.21.0, npm 11.19.0, Python **3.12.10** (venv), Docker/Git absent.

| Check | Command | Result |
| --- | --- | --- |
| Backend deps install | `py -3.12 -m venv .venv` + `pip install -r requirements-dev.txt` | ✅ exit 0 |
| Backend tests | `.\.venv\Scripts\python.exe -m pytest -v` | ✅ **5 passed** (13.7s) |
| Backend boots | `uvicorn app.main:app --port 8000` | ✅ startup complete + structured JSON logs |
| `GET /api/v1/health` | live request | ✅ `{"status":"ok", ..., "version":"0.1.0"}` |
| `GET /api/v1/health/db` | live request | ✅ `{"status":"ok","database":"reachable"}` |
| `GET /api/v1/health/redis` | live request | ✅ `{"status":"ok","redis":"reachable"}` |
| `redis-cli ping` / Python `redis.ping()` | native Redis 8.10.3 | ✅ `True` / `PONG` |
| Frontend deps install | `npm install` | ✅ exit 0 |
| Frontend build | `npm run build` | ✅ compiled + lint + types clean, 4 static routes |
| Frontend runs | `npm run dev` + `GET /` | ✅ HTTP 200, dashboard + health component rendered |

### Phase 2 verification (same native environment)

| Check | Command | Result |
| --- | --- | --- |
| Mappers configure | `configure_mappers()` + `len(Base.metadata.tables)` | ✅ `tables: 16` |
| Migration generated | `alembic revision --autogenerate -m "create core tables"` | ✅ `0d24e6e89e59` (16 `create_table`, 27 `create_index`) |
| Migration applied | `alembic upgrade head` | ✅ `Running upgrade -> 0d24e6e89e59` then `-> 70ed893bdb70` |
| Tables live in PostgreSQL | `inspect(engine).get_table_names()` | ✅ **17** = 16 app tables + `alembic_version` |
| Models ↔ schema in sync | `alembic check` | ✅ `No new upgrade operations detected` |
| Seed rows present | `select name from roles` / `select code, price_cents from plans` | ✅ `admin, editor, owner, viewer` · `free/0, starter/1900, pro/4900, business/9900` |
| Seed permission counts | `select name, length(permissions)` | ✅ owner 23, admin 21, editor 17, viewer 5 |
| Full test suite | `.\.venv\Scripts\python.exe -m pytest` | ✅ **57 passed**, 0 failed (5.8s) |

### Phase 3 verification (same native environment)

| Check | Command | Result |
| --- | --- | --- |
| Migration applied | `alembic upgrade head` | ✅ `14ae69daee8c` applied |
| Revision current | `alembic current` | ✅ `14ae69daee8c (head)` |
| Models ↔ schema in sync | `alembic check` | ✅ `No new upgrade operations detected` |
| Auth endpoints live | uvicorn on `127.0.0.1:8010` + HTTP requests | ✅ health ok; all 17 documented auth/workspace paths behaved as specified |
| Register (live) | `POST /api/v1/auth/register` | ✅ 201; email normalised, `is_verified=false`, no token in body |
| Refresh cookie | `Set-Cookie` header | ✅ `ocp_refresh_token=...; HttpOnly; Path=/api/v1/auth; SameSite=lax; Max-Age=604799` |
| Refresh / logout | `POST /auth/refresh`, `/auth/logout` | ✅ token rotated; `me` → 401 after logout |
| Full test suite | `.\.venv\Scripts\python.exe -m pytest` | ✅ **106 passed**, 0 failed |

> Port 8010 was used for the live smoke test because a stale backend process already
> occupied 8000.

### Phase 4 verification (same native environment)

| Check | Command | Result |
| --- | --- | --- |
| Types | `npm run typecheck` | ✅ exit 0 (`tsc --noEmit`) |
| Lint | `npm run lint` | ✅ `No ESLint warnings or errors` |
| Production build | `npm run build` | ✅ compiled, **17 routes** generated |
| Pages render | `npm run dev` + `GET` each route | ✅ HTTP 200 for `/`, `/login`, `/register`, `/forgot-password`, `/reset-password`, `/verify-email`, `/dashboard`, `/accounts`, `/settings`, `/billing` |
| Server-rendered content | HTML body assertions | ✅ landing/auth/placeholder pages contain their headings; no build or runtime errors in the dev log |
| Auth API contract | live uvicorn on `127.0.0.1:8001` + HTTP | ✅ register → bearer token (`expires_in=1799`), `GET /workspaces` shape matches `Workspace`, members shape matches `WorkspaceMember` (`status=active`, `role=owner`), cookie-only `POST /auth/refresh` returns a new token, `GET /auth/sessions` returns `current=true`, duplicate email → **409**, wrong password → **401** |
| Type ↔ schema parity | `app/schemas/auth.py`, `app/schemas/workspace.py` vs `frontend/lib/types.ts` | ✅ every field name and optionality matches (`UserPublic`, `AuthResponse`, `SessionPublic`, `WorkspacePublic`, `WorkspaceMemberPublic`, `MessageResponse`) |

> `/dashboard` intentionally does **not** contain its heading in the server-rendered HTML: the
> `RequireAuth` guard renders a "Checking your session…" state until the client exchanges the
> refresh cookie for a session. This is expected, not a render failure.
>
> Port 8001 was used for the Phase 4 contract check because the pre-existing process on port
> 8000 was started **before** Phase 3 and therefore only serves the health routes. It must be
> restarted to expose `/api/v1/auth/*` and `/api/v1/workspaces` on the default port.

### Phase 5 verification (same native environment)

| Check | Command / probe | Result |
| --- | --- | --- |
| Full backend suite | `.\.venv\Scripts\python.exe -m pytest` (with `POSTGRES_PORT=5433`) | ✅ **160 passed**, 0 failed |
| Types | `npm run typecheck` | ✅ exit 0 (`tsc --noEmit`) |
| Lint | `npm run lint` | ✅ `No ESLint warnings or errors` |
| Production build | `npm run build` | ✅ compiled, **17 routes** (`/upload` 10.9 kB / 124 kB) |
| Capabilities (live) | `GET /api/v1/media/{ws}/capabilities` | ✅ `ffmpeg_available: true`, `max_upload_bytes: 2147483648`, `chunk_size_bytes: 5242880`, 5 allowed MIME types, `signed_url_ttl_seconds: 900`, `thumbnail_max_width: 640` |
| Declare → chunk → complete (live) | `POST /uploads` → `PUT ?offset=` → `POST …/complete` | ✅ `201`/`200`/`201`; stored size matched the declared size |
| Inspection (live, real FFmpeg) | `complete` on a 2 s 320×240 clip | ✅ `duration_seconds: 2.0`, `width: 320`, `height: 240`, `status: ready`, SHA-256 recorded, JPEG thumbnail generated |
| **Multi-chunk + resume (live)** | 15,161,031-byte 5 s 1280×720 clip | ✅ `chunks expected: 3`; out-of-order chunk → **`409`** with `X-Upload-Expected-Offset: 0`; resume probe `offset: 0`; 3 chunks accepted; `complete: 201`, `duration_seconds: 5.0`, `1280×720` |
| Signed URLs (live, anonymous) | `GET …/files/original?expires=&signature=` | ✅ `200` video `video/mp4`, `200` thumbnail `image/jpeg` with no session |
| Tampered signature | one hex char changed | ✅ **`403`** |
| Oversized declare | `size_bytes = 3 GiB` | ✅ **`413`** — "This file is 3221225472 bytes; the limit is 2147483648 bytes." |
| Unsupported MIME | `application/zip` | ✅ **`415`** |
| Abort a finished session | `DELETE /uploads/{id}` | ✅ **`404` upload session not found** |
| Stats + soft delete (live) | `GET /media/{ws}/stats` → `DELETE` → `stats` → `GET` | ✅ count/bytes dropped to 0; the deleted file re-fetches as **`404`** |
| Type ↔ schema parity | `app/schemas/media.py` vs `frontend/lib/types.ts` | ✅ every field name and optionality matches (`MediaFile`, `MediaUploadRequest`, `UploadSession`, `MediaStats`, `MediaCapabilities`) |

> The two live media smoke scripts were throwaway verification scaffolding run from
> `%TEMP%`; they are **not** part of the repository.

### Phase 6 verification (same native environment)

| Check | Command / probe | Result |
| --- | --- | --- |
| Migration applied | `alembic upgrade head` | ✅ `5347775d077d` applied |
| Revision current | `alembic current` | ✅ `5347775d077d (head)` |
| Models ↔ schema in sync | `alembic check` | ✅ `No new upgrade operations detected` |
| Full backend suite | `.\.venv\Scripts\python.exe -m pytest` (with `POSTGRES_PORT=5433`) | ✅ **200 passed**, 0 failed (200 total = 160 + 40 new) |
| Types | `npm run typecheck` | ✅ exit 0 (`tsc --noEmit`) |
| Lint | `npm run lint` | ✅ `No ESLint warnings or errors` |
| Production build | `npm run build` | ✅ compiled in 8.0 s, **17 routes** (`/accounts` 8.76 kB / 121 kB) |
| Capabilities | `test_capabilities_lists_every_supported_platform`, `test_capabilities_reports_configured_and_counts` | ✅ every platform listed with a `configured` flag and a `connected_count` |
| Authorize | `test_authorize_requires_authentication`, `test_authorize_unconfigured_platform_returns_503`, `test_authorize_returns_a_state_bearing_consent_url` | ✅ **`401`** unauthenticated, **`503`** unconfigured, otherwise a consent URL carrying `state` |
| Callback state binding | `test_callback_rejects_an_unknown_state`, `test_callback_state_is_single_use`, `test_state_started_for_one_platform_is_rejected_on_another`, `test_callback_reports_a_platform_refusal` | ✅ unknown / replayed / foreign-platform state rejected; a platform refusal surfaces as an error redirect |
| Cross-workspace account | `test_listing_and_get_are_scoped_to_the_workspace` | ✅ **`404`** (never `403`) |
| RBAC | `test_viewer_can_read_but_not_connect` | ✅ viewer reads; connecting without `account:connect` fails |
| Refresh / reconnect | `test_refresh_rotates_the_stored_credential`, `test_refresh_without_a_stored_credential_needs_reconnect`, `test_expired_token_without_a_refresh_token_flags_reconnect` | ✅ the stored credential is rotated, and an unrefreshable grant is flagged `needs_reconnect` |
| Disconnect / revocation | `test_disconnect_revokes_upstream_and_destroys_the_vault`, `test_disconnect_still_removes_locally_when_revocation_fails` | ✅ upstream revocation attempted; the credential is destroyed and the account soft-deleted even if revocation fails |
| Token encryption | `test_token_cipher_round_trip_and_tamper_detection`, `test_token_cipher_rejects_a_value_from_a_different_key` | ✅ AES-256-GCM round-trip; tampered ciphertext and a foreign key both raise |
| No token leakage | `test_connect_response_and_listing_never_expose_tokens` | ✅ responses expose only `token_expires_at` / `token_expires_in_seconds` / `needs_reconnect` — never a token value |
| Type ↔ schema parity | `app/schemas/account.py` vs `frontend/lib/types.ts` | ✅ every field name and optionality matches (`SocialAccount`, `PlatformCapability`, `AccountCapabilities`, `AuthorizationStart`, `AccountDisconnectResult`) |

> Provider network calls are exercised through an injected `httpx` transport, so the 40 new
> tests run offline and deterministically. `test_oauth.py` covers the provider layer
> (authorization/callback URL construction, PKCE, scope handling, error-envelope translation);
> `test_accounts.py` covers the API layer listed above.

### Phase 7 verification (same native environment)

| Check | Command / probe | Result |
| --- | --- | --- |
| Full backend suite | `.\.venv\Scripts\python.exe -m pytest` (with `POSTGRES_PORT=5433`) | ✅ **234 passed**, 0 failed (234 total = 200 + 34 new) |
| Types | `npm run typecheck` | ✅ exit 0 (`tsc --noEmit`) |
| Lint | `npm run lint` | ✅ `No ESLint warnings or errors` |
| Production build | `npm run build` | ✅ compiled, **17 routes** (`/compose` 9.44 kB / 127 kB, was a 135 B placeholder) |
| Capabilities | `test_capabilities_endpoint_lists_every_platform`, `test_every_capability_matches_its_publisher_and_is_usable` | ✅ every platform's limits reported from the publishers (single source of truth) |
| Registry | `test_registry_exposes_every_supported_platform` | ✅ all four platforms resolve to their publisher |
| Per-platform dispatch | `test_compose_then_publish_everywhere_creates_one_job_per_target` | ✅ compose then publish creates one independent job per destination |
| Partial-failure isolation | `test_one_platforms_failure_never_cancels_another`, `test_permanent_failure_does_not_retry` | ✅ a failing platform leaves the others `published`; the post is `partially_published` |
| Platform-confirmed success | `test_run_job_confirms_success_and_notifies`, `test_facebook_reports_success_only_after_the_video_is_ready`, `test_youtube_uploads_then_polls_until_processed`, `test_tiktok_init_upload_then_status` | ✅ a target publishes only once the platform reports a terminal-success status |
| Idempotency / duplicate guard | `test_republishing_the_same_video_to_the_same_account_is_refused`, `test_idempotency_key_prefers_the_content_checksum`, `test_run_job_is_idempotent_once_terminal` | ✅ a second publish is refused; a re-delivered job never publishes twice |
| Retry + backoff | `test_retryable_failure_backs_off_then_dead_letters`, `test_backoff_grows_exponentially_and_is_capped`, `test_rate_limit_backoff_honours_retry_after` | ✅ a transient failure sets `next_retry_at` and re-dispatches; backoff doubles and is capped; `Retry-After` is honoured |
| Dead letter | `test_retryable_failure_backs_off_then_dead_letters` | ✅ once `max_attempts` is reached the job is `dead_letter` and the target `failed` |
| Auth / classification | `test_tiktok_classifies_error_codes`, `test_facebook_failure_inside_a_200_envelope_is_permanent` | ✅ a `401`/`403` is `PublishAuthError` (not retried); a platform-level failure inside a `200` is permanent |
| Validation before dispatch | `test_validation_rejects_an_oversized_video_before_uploading`, `test_validation_rejects_unsupported_container_and_resolution`, `test_unpublishable_destination_is_skipped_without_blocking_others` | ✅ a bad request is refused before a byte moves; an invalid destination is `skipped` |
| No publishable target | `test_publish_everywhere_with_nothing_publishable_is_a_409`, `test_publish_without_targets_is_rejected`, `test_composing_with_a_processing_video_is_a_409` | ✅ nothing dispatched → `409`; no destinations → `422`; a non-ready video → `409` |
| Cross-workspace post / job | `test_another_workspaces_post_and_job_are_404` | ✅ **`404`** (never `403`) |
| RBAC | `test_viewer_can_read_but_not_publish`, `test_endpoints_require_authentication` | ✅ viewer reads but cannot publish; unauthenticated callers get `401` |
| Type ↔ schema parity | `app/schemas/post.py` vs `frontend/lib/types.ts` | ✅ every field name and optionality matches (`PublisherCapability`, `PublishingJob`, `PublishingJobDetail`, `PostTarget`, `Post`) |

> Publisher network calls are exercised through an injected `httpx` transport and Celery
> dispatch is replaced by a recorder, so the 34 new tests run offline and deterministically.
> `test_publishers.py` covers the adapter layer (capability, validation, caption building,
> error classification, and the platform-confirmed-status contract);
> `test_publishing.py` covers the orchestration and API layer listed above.

### Phase 8 verification (same native environment)

| Check | Command / probe | Result |
| --- | --- | --- |
| Full backend suite | `.\.venv\Scripts\python.exe -m pytest` (with `POSTGRES_PORT=5433`) | ✅ **253 passed**, 0 failed (253 total = 234 + 19 new) |
| Types | `npm run typecheck` | ✅ exit 0 (`tsc --noEmit`) |
| Lint | `npm run lint` | ✅ `No ESLint warnings or errors` |
| Production build | `npm run build` | ✅ compiled in 4.5 s, **17 routes** (`/schedule` 3.8 kB / 126 kB, was a placeholder) |
| Queue it | `test_schedule_records_entry_and_pending_targets_without_dispatching` | ✅ `POST …/schedule` records the entry + pending targets and creates **no** job (`201`) |
| Time zone | `test_normalize_timezone_defaults_and_validates`, `test_to_utc_instant_reads_naive_values_in_the_chosen_zone` | ✅ a valid IANA zone is stored; a naive value is read in the chosen zone and persisted as UTC; an unknown zone and a past time are rejected |
| Due sweep | `test_sweep_dispatches_a_due_entry_through_the_publishing_path` | ✅ a due entry goes through the Phase 7 `queue_targets` path — the post's targets become live jobs |
| Fires exactly once | `test_sweep_never_fires_the_same_entry_twice`, `test_sweep_leaves_future_entries_alone` | ✅ a second sweep cannot re-dispatch a claimed entry; a future entry is untouched |
| Missed window | `test_sweep_marks_an_overdue_entry_missed` | ✅ an entry overdue past the grace is `MISSED`, targets cancelled, and the post reverts to `DRAFT` |
| Shared reliability | `test_a_scheduled_publish_and_a_manual_publish_share_the_idempotency_key` | ✅ a scheduled publish and a manual one derive the **same** idempotency key |
| Cancel | `test_cancelling_a_schedule_prevents_it_from_firing` | ✅ a cancelled entry's pending targets are cancelled, so the sweep never publishes it |
| Reschedule | `test_reschedule_moves_a_pending_entry_to_a_new_time`, `test_rescheduling_a_dispatched_entry_is_a_conflict` | ✅ a PENDING/QUEUED entry moves; an already-dispatched one answers `409` |
| Queue vs history | `test_listing_separates_the_upcoming_queue_from_history` | ✅ `window=upcoming` returns the queue, `past` the fired/cancelled/missed history |
| Cross-workspace | `test_another_workspaces_schedule_is_invisible_and_unreachable` | ✅ **`404`** (never `403`) |
| RBAC | `test_viewer_may_read_the_queue_but_not_change_it`, `test_schedule_endpoints_require_authentication` | ✅ a viewer reads but cannot schedule; unauthenticated callers get `401` |
| Beat registration | `test_the_due_sweep_is_on_the_celery_beat_schedule` | ✅ `scheduling.dispatch_due` is scheduled on the Celery Beat timetable |
| Type ↔ schema parity | `app/schemas/schedule.py` vs `frontend/lib/types.ts` | ✅ every field name and optionality matches (`Schedule`, `ScheduleStatus`, `ScheduleWindow`, `ScheduleCreatePayload`, `ScheduleUpdatePayload`) |

> The scheduler tests run through the real FastAPI app against the migrated
> `publisher_test` database with Celery dispatch replaced by a recorder, so the 19 new tests
> run offline and deterministically. The due-sweep is driven directly
> (`scheduling.sweep_due(db, now=…)`) rather than by waiting on a Beat tick.

### Phase 9 verification (same native environment)

| Check | Command / probe | Result |
| --- | --- | --- |
| Full backend suite | `.\.venv\Scripts\python.exe -m pytest` (with `POSTGRES_PORT=5433`) | ✅ **281 passed**, 2 warnings, 0 failed (46.1 s) |
| Types | `npm run typecheck` | ✅ exit 0 (`tsc --noEmit`) |
| Lint | `npm run lint` | ✅ `No ESLint warnings or errors` |
| Production build | `npm run build` | ✅ compiled in 10.7 s, **17 routes** (`/assistant` 8.1 kB, was a placeholder; `/compose` 7.8 kB) |
| Provider abstraction | `test_registry_resolves_supported_providers`, `test_unknown_provider_is_rejected` | ✅ the registry resolves the configured provider and rejects an unknown name |
| Offline by construction | every test in `test_ai.py` | ✅ the model call is driven through an injected `httpx.MockTransport` — **no network call**, so the suite is deterministic |
| Capability gap | `test_capability_reports_unconfigured_without_a_key`, `test_generate_answers_503_when_unconfigured` | ✅ with no key, the capability reports `configured: false` and generate answers **`503`** (a config state, not a crash) |
| Five kinds | `test_every_kind_builds_a_prompt` | ✅ caption / hashtags / description / rewrite / suggestion each build a request |
| Rewrite needs a source | `test_rewrite_requires_a_draft` | ✅ `rewrite` without a draft is rejected `422` |
| Persisted, not published | `test_generate_persists_an_unaccepted_row_and_publishes_nothing` | ✅ a generation writes `ai_generated_content` with `is_accepted = false` and creates **no** `Post`/`PostTarget`/`PublishingJob` (`201`) |
| Accept flips a flag only | `test_accept_marks_the_row_accepted` | ✅ `accept` sets `is_accepted = true` and changes nothing else |
| History + filter + paging | `test_history_filters_by_kind_and_paginates` | ✅ kind filter and `limit`/`offset` behave |
| Provider errors translated | `test_http_failures_map_to_the_documented_statuses` | ✅ `401/403/429`, a `5xx`, and a malformed body map to the documented `502`/`503`; a policy refusal maps to `422` |
| Cross-workspace | `test_another_workspaces_content_is_invisible_and_unreachable` | ✅ a foreign `content_id`/`post_id` answers **`404`, never `403`** |
| RBAC | `test_viewer_cannot_generate`, `test_ai_endpoints_require_authentication` | ✅ a viewer is refused and unauthenticated callers get `401` |
| Type ↔ schema parity | `app/schemas/ai.py` vs `frontend/lib/types.ts` | ✅ every field name and optionality matches (`AICapability`, `AIContent`, `AIGeneratePayload`) |

> The AI tests run through the real FastAPI app against the migrated `publisher_test` database
> with the model call served by an injected `httpx` transport, so the 28 new tests run offline
> and deterministically. No API key, and no network, is ever required by the suite.

### Phase 15 verification (same native environment)

| Check | Command / probe | Result |
| --- | --- | --- |
| Observability suite | `.\.venv\Scripts\python.exe -m pytest app/tests/test_observability.py` | ✅ **2 passed** |
| Docker is absent | `docker --version` | ⛔ not installed (non-elevated Chocolatey; admin required) — the images, compose file and nginx config are **not** executed here, they are written against the pinned versions and reviewed by inspection |
| Boot guard (production) | `Settings(environment="production", secret_key="change-me-…")` | ✅ raises at construction — the app refuses to boot with a placeholder or short `SECRET_KEY`, and with `REFRESH_COOKIE_SECURE=false` |
| Sentry off by default | `test_error_tracking_is_off_without_a_dsn` | ✅ `configure_error_tracking()` returns `False` and imports nothing when `SENTRY_DSN` is unset |
| Sentry passthrough | `test_error_tracking_passes_the_configured_dsn_through` | ✅ the DSN + `traces_sample_rate` reach `sentry_sdk.init` with `send_default_pii=False` |
| Sample-rate guard | `_validate_sentry_sample_rate` | ✅ an out-of-range rate (`1.5`, `-0.1`) is refused |
| Ordering | `app/main.py` | ✅ `configure_error_tracking()` is called **before** the `FastAPI(...)` instance is built |

> Phase 15 is the one phase whose artefacts **cannot be executed on this machine** — Docker Desktop
> needs admin rights that are unavailable here (see Known issue 1). The code paths that *can* run
> natively (the Sentry wiring and the production config guards) are covered by real tests; the
> container/nginx/CI files are verified by careful review against their pinned versions and are
> documented as such rather than claimed as executed.

### Phase 14 verification (same native environment)

| Check | Command / probe | Result |
| --- | --- | --- |
| Worker suite | `.\.venv\Scripts\python.exe -m pytest app/tests/test_worker.py` (with `POSTGRES_PORT=5434`) | ✅ **9 passed** |
| Frontend suite | `npm test` (`vitest run`) | ✅ **47 passed** across 6 files |
| Types | `npm run typecheck` | ✅ exit 0 (`tsc --noEmit`) |
| Lint | `npm run lint` | ✅ `No ESLint warnings or errors` |
| Production build | `npm run build` | ✅ compiles, unchanged route set |
| Full backend suite | `.\.venv\Scripts\python.exe -m pytest` (with `POSTGRES_PORT=5434`) | ✅ **394 passed**, 0 failed, 2 warnings, exit 0 (9:45) |
| Task registry | `test_every_dispatched_task_is_registered` | ✅ the six dispatched task names all resolve on the Celery app |
| Beat schedule | `test_the_beat_schedule_carries_all_three_periodic_sweeps` | ✅ `scheduling.dispatch_due`, `notifications.sweep`, `ai.health_sweep` are all on Beat |
| Intervals | `test_the_sweep_intervals_come_from_settings` | ✅ each entry's period matches its `*_INTERVAL_SECONDS` setting |
| Ping | `test_ping_reports_the_worker_is_wired` | ✅ `health.ping` answers through the worker |
| Scheduling sweep | `test_dispatch_due_runs_the_scheduling_sweep` | ✅ `scheduling.dispatch_due` runs the sweep for real |
| Notification sweep | `test_notifications_sweep_runs_every_sub_sweep` | ✅ `notifications.sweep` reports every sub-sweep |
| AI sweep | `test_ai_health_sweep_probes_the_registry` | ✅ `ai.health_sweep` probes the model registry |
| Task coercion | `test_publish_target_coerces_the_job_id`, `test_refresh_workspace_coerces_the_id_and_flattens_the_result` | ✅ a string job/workspace id is coerced and the result flattened as documented |
| Frontend helpers | `lib/format.test.ts`, `lib/utils.test.ts`, `lib/api.test.ts`, `lib/media-upload.test.ts` | ✅ formatting (incl. `null` / non-finite branches), `cn` conflict resolution, `formatDetail` + `ApiError`, and upload validation all covered |
| Frontend components | `components/empty-state.test.tsx`, `components/ui/button.test.tsx` | ✅ `EmptyState` renders its title/description and a conditional action; `Button` renders natively and honours variant/size/disabled/`asChild` |

> The worker tests drive each task **object directly**, so the task body runs in-process with no broker;
> `session_scope` is patched to the test's rolled-back session. The frontend tests run under `jsdom`
> through Vitest. Neither needs a broker, a network, or SMTP.

### Phase 13 verification (same native environment)

| Check | Command / probe | Result |
| --- | --- | --- |
| New suite | `.\.venv\Scripts\python.exe -m pytest app/tests/test_security_hardening.py` (with `POSTGRES_PORT=5434`) | ✅ **20 passed** (20 dots, exit 0) |
| Full backend suite | `.\.venv\Scripts\python.exe -m pytest` (with `POSTGRES_PORT=5434`) | ✅ **383 passed**, 0 failed, 2 warnings, exit 0 |
| Collection | `pytest --collect-only -q` | ✅ **383 tests** across 18 files (`test_security_hardening.py: 20`) |
| Migration applied | `alembic upgrade head` (dev DB) | ✅ `f241d12e080b → b8e2f5a91c47 → c3f7a2e91d08 → d1a4b7c93e02` |
| Models ↔ schema in sync | `alembic check` | ✅ `No new upgrade operations detected` |
| Tables live in PostgreSQL | `information_schema.tables` (dev DB) | ✅ **25** app tables; `audit_logs` carries `ix_audit_logs_action_created` / `_user_created` / `_workspace_created` |
| Permission seeded | `select name from roles where permissions::text like '%audit:read%'` | ✅ exactly `admin` + `owner` hold `audit:read` (owner 27 codes, admin 25) |
| Types | `npm run typecheck` | ✅ exit 0 (`tsc --noEmit`) |
| Lint | `npm run lint` | ✅ `No ESLint warnings or errors` |
| Production build | `npm run build` | ✅ **17 routes** (no frontend source changed) |
| Fixed window | `test_memory_store_starts_a_new_window_once_the_old_one_expires` | ✅ the counter restarts at 1 once the window elapses |
| Over-limit | `test_limiter_check_returns_retry_after_only_once_over_the_limit`, `test_auth_endpoint_returns_429_with_retry_after` | ✅ the Nth+1 call answers **`429`** and carries `Retry-After` |
| Address keying | `test_limiter_key_is_the_first_hop_of_x_forwarded_for` | ✅ the first `X-Forwarded-For` hop is the key, not the socket peer |
| Disabled | `test_rate_limit_dependency_is_a_no_op_when_disabled` | ✅ the limiter is a no-op when `RATE_LIMIT_ENABLED=false`, as the suite sets it |
| Security headers | `test_json_responses_carry_security_headers` | ✅ `nosniff` / `DENY` / `no-referrer` / `Permissions-Policy` / CSP present; no HSTS outside production |
| Docs exemption | `test_docs_pages_are_exempt_from_the_csp` | ✅ `/docs` carries no CSP (Swagger UI keeps its own scripts) |
| CSRF guard | `test_cross_origin_state_change_is_refused`, `test_same_origin_state_change_is_allowed`, `test_originless_state_change_is_allowed`, `test_safe_methods_are_not_origin_checked` | ✅ a cross-origin `POST` → **`403`**; same-origin, origin-less, and `GET` pass |
| Audit write | `test_registration_is_recorded_in_the_audit_trail`, `test_successful_login_is_recorded_in_the_audit_trail`, `test_a_registry_change_is_recorded_in_the_audit_trail` | ✅ the change and its entry land in one transaction |
| Audit of a refusal | `test_failed_login_is_recorded_in_the_audit_trail`, `test_failed_login_for_an_unknown_address_names_no_account` | ✅ a known address records user + workspace; an unknown one records neither |
| Audit read | `test_an_owner_can_read_the_audit_trail_newest_first`, `test_the_audit_trail_can_be_filtered_by_action_prefix` | ✅ newest first; `auth.login.` returns only sign-ins |
| Audit RBAC | `test_a_member_without_audit_read_is_forbidden`, `test_the_audit_trail_is_hidden_from_non_members` | ✅ an editor gets **`403`**; a non-member gets **`404`** |
| Table registry | `test_expected_tables_are_registered` (`app/tests/test_models.py`) | ✅ `audit_logs` registered and migrated |

### Phase 11 verification (same native environment)

| Check | Command / probe | Result |
| --- | --- | --- |
| Notification suite | `.\.venv\Scripts\python.exe -m pytest app/tests/test_notifications.py` (with `POSTGRES_PORT=5434`) | ✅ **16 passed** |
| Full backend suite | `.\.venv\Scripts\python.exe -m pytest` | ✅ **335 passed**, 0 failed (16 new) |
| Types | `npm run typecheck` | ✅ exit 0 (`tsc --noEmit`) |
| Lint | `npm run lint` | ✅ `No ESLint warnings or errors` |
| Inbox CRUD | `test_recorded_notification_appears_and_marks_read`, `test_unread_only_filter`, `test_mark_all_read_reports_the_count`, `test_dismiss_removes_notification` | ✅ list / filter / mark-read / mark-all / dismiss all work |
| Isolation | `test_one_user_cannot_see_anothers_notification` | ✅ a foreign id answers **`404`**; the owner still sees it |
| Auth | `test_notifications_require_authentication` | ✅ unauthenticated callers get `401` |
| Preferences | `test_preferences_default_to_opted_in_and_persist` | ✅ all four default `true`; a change persists and leaves the others alone |
| Email once | `test_sweep_emails_publish_outcomes_once` | ✅ both outcomes mailed, then idempotent on a second tick |
| Opt-out | `test_disabled_preference_keeps_notification_but_sends_no_email` | ✅ no email, but the in-app row survives |
| Reminders | `test_sweep_reminds_upcoming_schedule_once`, `test_sweep_ignores_schedules_beyond_the_lead_window` | ✅ one reminder inside the window, none outside, never duplicated |
| Token warnings | `test_sweep_warns_about_expiring_tokens_once`, `test_sweep_ignores_far_off_token_expiry` | ✅ one warning inside the window, none for a far-off expiry |
| Whole sweep | `test_full_sweep_reports_every_sub_sweep` | ✅ `{emails_sent: 1, reminders_sent: 1, token_warnings_sent: 1}` |
| Model registry | `test_expected_tables_are_registered` | ✅ `notification_preferences` registered |
| Type ↔ schema parity | `app/schemas/notification.py` vs `frontend/lib/types.ts` | ✅ every field name and optionality matches |

> The notification tests run through the real FastAPI app against the migrated `publisher_test`
> database with the email transport monkeypatched, so the 16 new tests run offline and
> deterministically. No SMTP access, and no network, is ever required.

### Phase 10 verification (same native environment)

| Check | Command / probe | Result |
| --- | --- | --- |
| Full backend suite | `.\.venv\Scripts\python.exe -m pytest` (with `POSTGRES_PORT=5433`) | ✅ **319 passed**, 0 failed (36 new) |
| Types | `npm run typecheck` | ✅ exit 0 (`tsc --noEmit`) |
| Lint | `npm run lint` | ✅ `No ESLint warnings or errors` |
| Production build | `npm run build` | ✅ **17 routes** (`/analytics` 9.08 kB, was a placeholder) |
| Provider registry | `test_registry_exposes_every_supported_platform` | ✅ the registry resolves all four platforms |
| Unavailable ≠ zero | `test_as_int_keeps_absence_and_zero_distinct` | ✅ an absent/empty/non-numeric value is `None`; a real `0` is `0` |
| Platform reads | `test_facebook_reads_video_metrics_and_leaves_reach_unavailable`, `test_instagram_reads_likes_and_comments_only`, `test_youtube_reads_video_statistics`, `test_tiktok_reads_video_metrics` | ✅ each provider returns only what its API exposes and leaves the rest `None` |
| Absence survives | `test_youtube_hidden_counts_stay_unavailable` | ✅ a hidden count stays `null`, never `0` |
| Error taxonomy | `test_facebook_error_inside_a_200_is_a_metrics_error`, `test_facebook_401_means_the_account_must_be_reconnected`, `test_a_rate_limit_carries_the_platforms_retry_after`, `test_a_server_error_is_transient`, `test_a_transport_fault_is_transient`, `test_a_non_json_body_is_transient` | ✅ a `200`-envelope error, `401`, `429` (+ `Retry-After`), `5xx`, a timeout, and a non-JSON body each map to the taxonomy |
| Capability | `test_capability_reports_every_platform_and_which_can_be_read` | ✅ each platform reports `metrics_available` + its connected-account count |
| Empty before refresh | `test_overview_is_empty_and_unavailable_before_any_refresh` | ✅ counts are `0` and every metric is `null` before a refresh |
| Reads are pure | `test_reading_never_touches_a_platform` | ✅ opening the dashboard never calls a platform |
| Aggregation keeps absence | `test_overview_sums_the_latest_snapshot_and_keeps_absence` | ✅ the latest snapshot per target is summed; an absent metric stays `null` |
| Real zero vs absence | `test_engagement_is_zero_when_the_platform_reported_a_real_zero` | ✅ a reported `0` is kept and shown as `0` |
| Comparison / audience | `test_platform_comparison_lists_only_platforms_with_content`, `test_account_performance_includes_audience` | ✅ per-platform and per-account breakdowns are correct |
| Targets | `test_targets_list_published_destinations_with_null_metrics_when_unrefreshed`, `test_targets_can_be_filtered_by_platform` | ✅ an unrefreshed destination is listed with `snapshot: null`; filtering works |
| History | `test_history_keeps_only_the_latest_snapshot_of_each_day` | ✅ several snapshots in one day do not multiply a target's numbers |
| Refresh | `test_refresh_reads_every_destination_and_stores_snapshots` | ✅ each destination is read and a snapshot appended |
| One failure ≠ all | `test_one_platforms_failure_does_not_stop_the_others` | ✅ a failure is reported in `errors` and the others update |
| Cross-workspace | `test_another_workspaces_metrics_are_invisible_and_unreachable` | ✅ **`404`** (never `403`) |
| RBAC | `test_a_viewer_may_read_but_not_refresh`, `test_an_editor_may_refresh`, `test_analytics_endpoints_require_authentication` | ✅ a viewer reads but cannot refresh; an editor refreshes; unauthenticated callers get `401` |
| Worker task | `test_the_refresh_task_is_registered` | ✅ `analytics.refresh_workspace` is registered on the Celery app |
| Type ↔ schema parity | `app/schemas/analytics.py` vs `frontend/lib/types.ts` | ✅ every field name and optionality matches |

> The analytics tests run through the real FastAPI app against the migrated `publisher_test`
> database with each platform call served by an injected `httpx` transport, so the 36 new tests run
> offline and deterministically. No platform access, and no network, is ever required.

## Tests passed

**Phase 15**

- `app/tests/test_observability.py` — **2** tests: with no `SENTRY_DSN` `configure_error_tracking()`
  returns `False` and imports nothing, so the suite stays offline; and with a DSN the configured value
  and `traces_sample_rate` reach `sentry_sdk.init` with `send_default_pii=False` hard-coded.
- Total: backend suite unchanged at **394 tests** (383 + 9 worker + 2 observability); the frontend suite
  is **47 tests** under Vitest. Phase 15 adds no tests beyond the 2 above — the deployment artefacts
  (images, compose, nginx, CI) are not executable on this machine (Docker needs admin rights).

**Phase 14**

- `app/tests/test_worker.py` — **9** tests driving each Celery task **object directly** (the body runs
  in-process, no broker) with `session_scope` patched to the test's rolled-back session: every
  dispatched task name is registered; Beat carries all three periodic sweeps; each sweep's period comes
  from its `*_INTERVAL_SECONDS` setting; `health.ping` answers; the scheduling, notification and AI
  sweeps each run their real work; and `publishing.publish_target` / `analytics.refresh_workspace`
  coerce a string id and flatten their result.
- Frontend (Vitest 2.1.8 + jsdom + Testing Library) — **47** tests across 6 files: `lib/format.test.ts`
  (byte/duration/date/relative formatting incl. `null` and non-finite branches), `lib/utils.test.ts`
  (`cn` incl. Tailwind conflict resolution), `lib/api.test.ts` (`formatDetail` for plain/blank/single/
  multi/unrecognised details, and `ApiError` status/detail/name/message), `lib/media-upload.test.ts`
  (`acceptedLabel`, `acceptAttribute`, `resolveMimeType`, `validateFile` for empty/oversize/
  undeterminable/disallowed files), `components/empty-state.test.tsx`, and
  `components/ui/button.test.tsx` (native render + variant/size/disabled/`asChild`).
- Total: backend **394 passed**, 0 failed (383 + 9 worker + 2 observability), 2 warnings, exit 0.

**Phase 13**

- `app/tests/test_security_hardening.py` — **20** tests through the real FastAPI app: the in-process
  store starts a fresh window once the old one expires; `check()` reports `Retry-After` only once the
  limit is passed; the limiter keys on the first `X-Forwarded-For` hop; it is a no-op when disabled;
  `/auth/login` answers **`429` + `Retry-After`** past the limit; every JSON response carries the
  hardening headers (no HSTS outside production); `/docs` is CSP-exempt; a cross-origin state change is
  refused **`403`** while same-origin / origin-less / safe-method requests pass; and the audit trail
  records registration, a failed login (naming the account when the address is known, naming nothing
  when it is not), a successful login, and a registry change — then reads back newest-first, filtered by
  action prefix, allowed for an owner, **`403`** for a member without `audit:read`, and **`404`** for a
  non-member.
- Total: **383 passed**, 0 failed (`test_health` 5 · `test_models` 35 · `test_db_integration` 26 ·
  `test_auth` 32 · `test_accounts` 20 · `test_oauth` 19 · `test_media` 27 · `test_storage` 21 ·
  `test_video` 6 · `test_publishing` 18 · `test_publishers` 16 · `test_scheduling` 19 · `test_analytics`
  36 · `test_ai` 28 · `test_ai_router` 24 · `test_notifications` 16 · `test_security` 15 ·
  `test_security_hardening` 20).

**Phase 11**

- `app/tests/test_notifications.py` — **16** inbox/service/sweep tests through the real FastAPI app
  against the migrated `publisher_test` database, with the email transport monkeypatched: the
  endpoints require authentication, an empty inbox reports zero, a recorded notification appears,
  marks read, filters by unread, marks all read, is dismissed, and is invisible to another user
  (`404`), preferences default to opted-in and persist, the sweep emails both publish outcomes once
  and is idempotent, a disabled preference sends no email but keeps the in-app row, upcoming
  schedules are reminded once (and far-off ones ignored), expiring tokens are warned about once
  (and far-off ones ignored), the full sweep reports every sub-sweep, and the model registry
  includes `notification_preferences`.
- Total: **335 passed** (5 + 52 + 49 + 54 + 40 + 34 + 19 + 28 + 36 + 16), 0 failed.

**Phase 10**

- `app/tests/test_analytics.py` — **36** provider/service/API tests through the real FastAPI app
  against the migrated `publisher_test` database, with each platform call served by an injected
  `httpx` transport: the registry resolves every platform, `as_int` keeps an absent value and a
  genuine `0` distinct, each provider reads only the metrics its API exposes and leaves the rest
  `None`, a hidden count stays unavailable, a `200`-envelope error / `401` / `429` (+ `Retry-After`)
  / `5xx` / a timeout / a non-JSON body map to the taxonomy, the capability reports which platforms
  can be read, the dashboard is empty and unavailable before a refresh, reads never touch a
  platform, the latest snapshot per target is summed with absence preserved, a reported `0` is
  kept, the platform comparison and per-account audience are correct, targets list unrefreshed
  destinations with `null`, history keeps only each day's newest snapshot, a refresh reads every
  destination, one platform's failure does not stop the others, another workspace's metrics are a
  `404`, a viewer may read but not refresh, an editor may refresh, the endpoints require
  authentication, and the Celery task is registered.
- Total: **319 passed** (5 + 52 + 49 + 54 + 40 + 34 + 19 + 28 + 36), 0 failed.

**Phase 9**

- `app/tests/test_ai.py` — **28** provider/service/API tests through the real FastAPI app
  against the migrated `publisher_test` database, with the chat-completion call served by an
  injected `httpx` transport: the registry resolves the provider and rejects an unknown one, an
  unconfigured model reports `configured: false` and answers `503`, each of the five kinds
  builds its prompt (with `rewrite` requiring a draft), tone/style/platform are threaded into
  the request, the key never appears in a response, a generation persists an unaccepted row and
  publishes nothing, accept flips only `is_accepted`, history filters by kind and paginates, the
  HTTP/rate-limit/5xx/malformed-body/policy-refusal failures map to the documented `502`/`503`/
  `422`, another workspace's content is a `404`, a viewer cannot generate, the endpoints require
  authentication, and the `AIProvider` HTTP classification and JSON extraction behave.
- Total: **281 passed** (5 + 52 + 49 + 54 + 40 + 34 + 19 + 28), 0 failed.

**Phase 8**

- `app/tests/test_scheduling.py` — **19** scheduler/API tests through the real FastAPI app
  against the migrated `publisher_test` database, with Celery dispatch replaced by a recorder:
  queueing a post records its entry and pending targets without dispatching, a past time and an
  unknown time zone are rejected, scheduling an already-scheduled post is a `409`, the due-sweep
  dispatches a due entry through the publishing path (and never twice, and never a future one),
  an overdue entry is marked `missed`, a scheduled and a manual publish derive the same
  idempotency key, cancelling prevents firing, rescheduling moves a pending entry but is a `409`
  once dispatched, the queue and history are listed separately, another workspace's schedule is a
  `404`, a viewer may read but not change the queue, the endpoints require authentication, and
  `normalize_timezone` / `to_utc_instant` behave, with the sweep registered on the Celery Beat
  timetable.
- Total: **253 passed** (5 + 52 + 49 + 54 + 40 + 34 + 19), 0 failed.

**Phase 7**

- `app/tests/test_publishers.py` — **16** adapter-level tests driving each publisher against an
  injected `httpx` transport: the registry resolves every platform, every capability matches its
  publisher, Facebook reports success only after the video is `ready` (and is transient when it
  never becomes ready), a failure inside a `200` envelope is permanent, a `429` carries the
  platform's `Retry-After`, Instagram runs container → publish → permalink (and a failed
  permalink does not fail an already-published reel), YouTube requires a title and polls until
  processed, TikTok performs init-upload → status and classifies error codes, and validation
  rejects an oversized video or an unsupported container/resolution before uploading.
- `app/tests/test_publishing.py` — **18** orchestration/API tests through the real FastAPI app
  against the migrated `publisher_test` database, with Celery dispatch replaced by a recorder:
  capabilities listing, compose → publish creating one job per target, an unpublishable
  destination skipped without blocking the others, `409` when nothing is publishable, `422` with
  no targets, `409` for a processing video, a refused duplicate publish, a confirmed success that
  notifies, retry-then-dead-letter, a rate-limit backoff honouring `Retry-After`, a permanent
  failure that is not retried, one platform's failure never cancelling another, job idempotency
  once terminal, cross-workspace `404`s, viewer read-but-not-publish, auth requirements, and the
  exponential-but-capped backoff and content-checksum idempotency key.
- Total: **234 passed** (5 + 52 + 49 + 54 + 40 + 34), 0 failed.

**Phase 6**

- `app/tests/test_oauth.py` — **20** provider-level tests: authorization-URL construction for all
  four platforms, scope/redirect-URI encoding, PKCE `S256` challenge generation and verification,
  TikTok's comma-separated scopes and `ok` error envelope, Google's `access_type=offline` +
  `prompt=consent`, the Meta long-lived-token exchange, token/userinfo parsing, revocation calls,
  and the unconfigured-provider guard. Network is stubbed with an injected `httpx` transport.
- `app/tests/test_accounts.py` — **20** API-level tests through the real FastAPI app: capabilities
  listing/counts, authorize authentication + `503` when unconfigured + consent URL, callback
  creating an account with encrypted credentials, token non-exposure, workspace scoping
  (cross-workspace `404`), unknown/replayed/foreign-platform state rejection, a platform refusal
  surfaced as an error redirect, viewer read-but-not-connect, refresh rotating the stored
  credential, `needs_reconnect` when no credential can be refreshed, disconnect with and without
  successful upstream revocation, and the AES-256-GCM round-trip / tamper / foreign-key checks.
- Total: **200 passed** (5 + 52 + 49 + 54 + 40), 0 failed.

**Phase 5**

- `app/tests/test_media.py` — **27** integration tests through the real FastAPI app: the full
  declare → chunk → complete path, out-of-order chunk (`409` + expected-offset header), resume
  probe, incomplete-finalise `409`, oversized declare `413`, unsupported MIME `415`, expired
  session `410`, abort `404`, listing/filtering/search/stats, delete-in-use `409`, replacement
  keeping the media id, a viewer being able to read but not upload, and cross-workspace `404`s.
- `app/tests/test_storage.py` — **21** unit tests: key construction, unsafe-key rejection
  (`..`, absolute, backslash), root-escape prevention, append/adopt/delete semantics, and
  signed-URL mint/verify (valid, expired, tampered, wrong variant).
- `app/tests/test_video.py` — **6** tests: binary discovery, `pipeline_available`, FFprobe JSON
  parsing, non-video rejection, and thumbnail generation behaviour.
- Total: **160 passed** (5 + 52 + 49 + 54), 0 failed.

**Phase 4**

- No automated test framework was added to the frontend in this phase (the master prompt puts
  the frontend test suite in Phase 14). Phase 4 was verified by `tsc --noEmit`, ESLint,
  `next build` (17 routes), an HTTP smoke test of every route, and a live end-to-end check of
  the auth/workspace contract against a real uvicorn process.
- Backend suite unchanged and re-runnable: `.\.venv\Scripts\python.exe -m pytest` → 106 tests.
  Phase 4 added no backend code, so the Phase 3 result stands.

**Phase 3**

- `app/tests/test_auth.py` — 32 integration tests driving the real FastAPI app (TestClient):
  registration + personal-workspace provisioning + email normalisation, no secrets in the
  body (refresh in the cookie only), duplicate email → 409, short password → 422, verification
  email sent, login success / wrong password 401 / indistinguishable unknown email, `/me`
  401 vs 200, refresh rotation, reuse → whole family revoked, logout (immediate) and
  logout-all, session listing with `current` flag, cross-user session revoke → 404, password
  change revokes other sessions, reset request (no enumeration) + confirm + single-use, email
  verification, and workspace RBAC/tenant isolation (non-member → 404, missing permission →
  403, owner removes member, members listing scoped).
- `app/tests/test_security.py` — 15 unit tests: password hash/verify round-trip, corrupt-hash
  rejection, long-password pre-hashing, access-token claim round-trip, expiry, tampering,
  wrong issuer/audience, and opaque-token generation/digest.

**Phase 2**

- `app/tests/test_models.py` — 28 tests (26 in Phase 2; +2 in Phase 3 for `auth_sessions`
  and `verification_tokens`): table registration, UUID PKs, timezone-aware timestamps,
  tenant `workspace_id` FKs, unique/CHECK constraint declarations, soft-delete support,
  1:1 OAuth mapping, and enum membership.
- `app/tests/test_db_integration.py` — 26 tests against live PostgreSQL: unique violations
  (email, slug, membership, social account, idempotency key, OAuth 1:1), CHECK violations
  (negative size/duration/price/attempts, unknown enum value), referential actions
  (`RESTRICT` on `owner_id`/`role_id`, `CASCADE` workspace teardown, `SET NULL` on audit
  FKs), `TenantMixin.scoped` isolation and soft-delete filtering, and seeded roles/plans.

**Phase 1**

- `app/tests/test_health.py`: root metadata, liveness, DB probe shape, Redis probe shape,
  OpenAPI schema — **5/5 passed**.

## Tests failed

None. Final full backend suite: **394 passed**, 0 failed, 2 warnings, exit 0. (Earlier runs in this
session showed `test_worker.py` connection errors because the local **PostgreSQL** process was being
killed by the shell that launched it; once PostgreSQL was started detached, the suite passed
**394/394**.)

## Known issues / blockers

1. ⛔ **Docker is not installed, and it cannot be installed without admin rights.**
   Chocolatey runs non-elevated here, so the install aborts with
   `Access to the path 'C:\ProgramData\chocolatey\lib-bad' is denied`, and the
   `redis-64` package resolves to `memurai-developer` which also requires admin.
   Phase 1 is therefore verified in **native mode** rather than via `docker compose up`.
   Installing Docker Desktop later requires admin + WSL2 + a reboot.
2. ⛔ **Git is not installed.** The Git repository has not been initialised (the spec
   requires version control).
3. ⚠️ **Python 3.14 is the default `py` interpreter but has no prebuilt wheels for the
   pinned dependency versions** (pydantic-core attempted a source build). The backend
   venv **must** be created with `py -3.12`.
4. ℹ️ **PostgreSQL and Redis run natively (no Docker) from user-local installs:**
   - **PostgreSQL 16.16** cluster at `%USERPROFILE%\.ocp\pgdata`, initialised with the
     already-installed Chocolatey PG16 binaries (`initdb -U publisher --auth-host=trust`)
     and started with `pg_ctl`. Database `publisher_db` created.
     The choco-managed `C:\Program Files\PostgreSQL\16\data` is **empty and not
     writable without admin**, so it is deliberately not used.
   - ⚠️ **On this machine the project cluster answers on `127.0.0.1:5434`, not 5432.** The
     Windows service `postgresql-x64-16` holds 5432 with `scram-sha-256` auth, so connecting
     there fails with `password authentication failed for user "publisher"`. Because the
     composed `database_url` default already contains `:5432`, setting `POSTGRES_PORT` alone
     does **not** redirect the app — start uvicorn with the full URL instead, and set
     `POSTGRES_PORT` for the test suite (its `conftest.py` composes the test URL from
     `POSTGRES_HOST`/`POSTGRES_PORT`):
     ```powershell
     # API
     $env:DATABASE_URL='postgresql+psycopg://publisher:publisher_dev_password@127.0.0.1:5434/publisher_db'
     python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
     # tests
     $env:POSTGRES_PORT='5434'; pytest
     ```
   - **Redis 8.10.3** portable build at `%USERPROFILE%\.ocp\redis`, started on
     `127.0.0.1:6379`.
   - A second database **`publisher_test`** exists for the test suite. It is created
     automatically by `app/tests/conftest.py`, rebuilt once per session
     (`DROP SCHEMA public CASCADE` → `alembic upgrade head`), and every test runs inside
     a rolled-back transaction. It is **never** the development database.
   - Neither is registered as a Windows service, so **both must be restarted after a
     reboot** — commands are in `docs/SETUP_GUIDE.md`.
5. ✅ **Resolved during verification:** `next@15.1.3` had a security advisory
   (CVE-2025-66478); upgraded to the patched **15.5.29**. The **Phase 13 re-check** of `npm audit`
   leaves **2 advisories — 1 moderate, 1 high — in the PostCSS copy bundled inside Next.js**
   (`node_modules/next/node_modules/postcss`). It is **build-time-only** (it never ships to the
   browser and never runs at request time), it is not a production dependency, and the only fix —
   `npm audit fix --force` — would upgrade to the breaking `next@16`, so the finding is
   **deliberately accepted and documented** rather than "fixed" by breaking the build. Python
   dependencies stay pinned in `requirements*.txt`.
6. ✅ **Resolved:** a stale backend process started before Phase 3 was bound to port 8000 and
   served only `/api/v1/health*`, so frontend sign-in returned `404`. It was stopped and
   replaced with a fresh `uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`; port 8000
   now exposes **13 auth routes + 4 workspace routes** and register/refresh succeed against it.
   If the symptom reappears (e.g. a manually started old process), check `GET /openapi.json`
   and restart the backend — no code change is needed.
7. ℹ️ **FFmpeg is an external binary, not a package dependency.** A portable essentials build
   (9.0.2) is installed at
   `C:\Users\PIRATE\AppData\Local\ffmpeg\ffmpeg-9.0.2-essentials_build\bin\` and is **not on
   `PATH`**, so `backend/.env` points `MEDIA_FFMPEG_BINARY` / `MEDIA_FFPROBE_BINARY` at the two
   executables. Without them the upload still works but completion answers **`503`** and no
   thumbnail is generated — `GET …/capabilities` reports `ffmpeg_available: false`, and the
   `/upload` page shows a warning banner. Nothing breaks: FFmpeg is a soft dependency by design.
8. ℹ️ **The AI assistant needs `OPENAI_API_KEY` to generate.** With no key the capability
   reports `configured: false`, `POST …/generate` answers **`503`**, and the `/assistant` page
   (and the composer's "Draft with AI" panel) shows a warning telling you to set the key.
   Nothing else breaks — the key is server-side only and is never sent to the browser, logged,
   or echoed in an error. The suite never needs a key: the model call is stubbed with an
   injected `httpx` transport.
9. ℹ️ **Analytics needs no extra credentials — but a platform may not expose a metric.** Metrics
   are read through the existing Phase 6 connections, so a connected account is all a refresh
   needs. A platform whose public API does not return a figure reports `metrics_available: false`
   (or leaves the metric `NULL`) and the dashboard shows **N/A** — never `0`. A revoked/expired
   grant surfaces in the refresh result's `errors`, not as a crash. The suite never needs platform
   access: every provider is driven through an injected `httpx` transport.

10. ⚠️ **The Phase 15 deployment artefacts are not executed on this machine.** Because Docker Desktop is
   absent (item 1), `Dockerfile.prod` ×2, `docker-compose.prod.yml`, `deploy/nginx/app.conf`, the
   backup/restore scripts and `.github/workflows/ci.yml` are written against their pinned versions and
   reviewed by inspection, but `docker compose -f docker-compose.prod.yml up` has never run here. The
   production **config guards** and the **Sentry wiring** *are* covered by native tests
   (`test_observability.py`, and the `ENVIRONMENT=production` boot refusals). Everything else in the
   Phase 15 runbook should be treated as reviewed-not-executed until a machine with Docker is available.

## Current architecture

See `docs/PROJECT_ARCHITECTURE.md`.

## Pending tasks

- ✅ **All 15 phases complete.** Phase 11 — Notifications · Phase 12 — Free AI Model Management &
  Automatic Failover · Phase 13 — Security & Performance · Phase 14 — Complete Testing · Phase 15 —
  Production Deployment. There is **no remaining phase**; the master plan is fully delivered.
  Outstanding items below are environment/optional, not plan phases.
- ℹ️ **SaaS billing is out of scope** — Phase 12 records the platform as free (its "not included"
  section), so **no billing or subscription routes were built**; the Phase 2 `plans` / `subscriptions`
  tables have no API surface.
- Phase 10's `analytics.refresh_workspace` task exists but is not on the Beat schedule yet; a
  refresh is user-triggered from the dashboard. Phase 11 added the notification sweep on Beat, so an
  automatic analytics refresh (and its token-expiration alerting, which Phase 11 now delivers) can
  be scheduled here later.
- Install **Git** and initialise the repository. ⛔ External blocker — Git is not on this machine.
- (Optional) Install Docker Desktop **with admin rights** to validate the
  `docker compose up --build` path (including the `worker` and `frontend` containers).
- Phase 5 keeps media on local disk behind `LocalStorage`; an S3/MinIO implementation can be
  dropped in behind the same interface (no route or service change) when object storage is
  needed. Phase 7's publishers reuse the Phase 6 encrypted-credential vault, so no new secret
  storage was needed.
- Phase 7 assumed live platform APIs but was verified offline through an injected `httpx`
  transport; a real end-to-end publish against each platform needs the external prerequisites in
  `docs/OAUTH_SETUP.md` (developer apps, app review, publishing audits, account eligibility).
- Phase 8's due-sweep is driven by Celery Beat; on a healthy worker a scheduled post fires within
  `SCHEDULING_SWEEP_INTERVAL_SECONDS` of its time. A real deployment should run Beat (or a single
  scheduler) alongside the workers so the sweep is always ticking.
- Phase 13 **considered and deliberately declined** a GIN index on the JSONB columns (`audit_logs.detail`,
  `ai_models.supports`): no query filters on them yet, so an index would only cost writes. Add one if a
  query starts filtering on a JSONB column.

## Exact next development step

**All 15 phases of the master plan are complete.** There is no next phase to start. What remains is
environment setup and optional hardening, none of which is required for the codebase to be finished:

1. On a machine **with admin rights**, install **Git** and initialise the repository, then install
   **Docker Desktop** and run `docker compose -f docker-compose.prod.yml up --build` to exercise the
   Phase 15 artefacts for real (see `docs/DEPLOYMENT.md`). The pinned `docker-compose.yml` still runs
   the local stack for development.
2. Real-world integration, once external prerequisites exist: a live publish against each platform
   (developer apps, app review, publishing audits — `docs/OAUTH_SETUP.md`) and a live AI generation
   (`OPENAI_API_KEY`). Both are already implemented and tested offline through injected transports.
3. Optional follow-ups recorded in Pending tasks: put `analytics.refresh_workspace` on the Beat
   schedule, add a GIN index if a JSONB column starts being queried, and land the frontend E2E suite
   the phase notes deferred beyond the unit tests shipped in Phase 14.
4. Keep the documentation current (Rule 6) whenever any of the above is done.
