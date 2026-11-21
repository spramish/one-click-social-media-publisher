# Deployment Guide

Author: **Pramish Sharma**
Target: **single Linux host running Docker Compose** (any VPS with 2 vCPU / 4 GB RAM and 40 GB disk is enough to start)
Related: [`SETUP_GUIDE.md`](SETUP_GUIDE.md) (local development), [`OAUTH_SETUP.md`](OAUTH_SETUP.md) (platform apps)

---

## 1. What gets deployed

| Container | Image | Public? | Purpose |
| --- | --- | --- | --- |
| `ocp_nginx` | `nginx:1.27-alpine` | **yes** (80, 443) | TLS termination, reverse proxy, upload streaming |
| `ocp_frontend` | `ocp-frontend:latest` | no | Next.js server (`output: "standalone"`) |
| `ocp_backend` | `ocp-backend:latest` | no | FastAPI under gunicorn + uvicorn workers; runs `alembic upgrade head` on start |
| `ocp_worker` | `ocp-backend:latest` | no | Celery worker (publishing, analytics, AI health, notifications) |
| `ocp_beat` | `ocp-backend:latest` | no | Celery Beat — the three periodic sweeps |
| `ocp_postgres` | `postgres:16-alpine` | no | Primary database |
| `ocp_redis` | `redis:7-alpine` | no | Cache, rate-limit counters, Celery broker (1) and result backend (2) |

Only nginx publishes host ports. PostgreSQL and Redis are reachable **only** on the
compose network — they are never exposed to the internet, which is the single most
important property of this configuration.

---

## 2. First deploy

### 2.1 Host preparation

```bash
# Docker Engine + compose plugin
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker "$USER"   # log out and back in

# Firewall: only SSH and the web ports
sudo ufw allow 22,80,443/tcp && sudo ufw enable
```

### 2.2 Get the code and the environment

```bash
git clone <your-repo-url> /opt/ocp && cd /opt/ocp
cp .env.example .env
```

Now apply every override from [`.env.production.example`](../.env.production.example)
to `.env`. The three that **must** change or the stack will not boot:

| Variable | Why boot fails without it |
| --- | --- |
| `SECRET_KEY` | `config.py` refuses `ENVIRONMENT=production` with the placeholder or a value under 32 chars |
| `REFRESH_COOKIE_SECURE` | must be `true` in production (guarded) |
| `POSTGRES_PASSWORD` | `docker-compose.prod.yml` refuses to start without it |

Set `ENVIRONMENT=production`, `DEBUG=false`, `CORS_ORIGINS` and
`BACKEND_PUBLIC_URL` to your real domains, `NEXT_PUBLIC_API_URL` to the public API
URL, and `EMAIL_BACKEND=smtp` with real relay credentials — `console` refuses to
run in production, so verification and reset mail would not be delivered.

Generate the two secrets:

```bash
openssl rand -base64 48   # SECRET_KEY
openssl rand -base64 48   # OAUTH_ENCRYPTION_KEY — keeps tokens readable across a SECRET_KEY rotation
```

### 2.3 TLS certificates

nginx reads `deploy/certs/fullchain.pem` and `deploy/certs/privkey.pem`. Either:

**Let's Encrypt (recommended).** Answer the HTTP-01 challenge through the port-80
server block, which proxies `/.well-known/acme-challenge/` to `/var/www/certbot`:

```bash
sudo apt install certbot
sudo certbot certonly --standalone -d api.example.com -d www.example.com
sudo mkdir -p deploy/certs
sudo cp /etc/letsencrypt/live/<domain>/fullchain.pem deploy/certs/
sudo cp /etc/letsencrypt/live/<domain>/privkey.pem   deploy/certs/
sudo chmod 644 deploy/certs/*.pem
```

Renewal runs on a timer; after each renewal copy the files again and
`docker compose -f docker-compose.prod.yml exec nginx nginx -s reload`.

**Self-signed**, for a smoke test only — browsers will warn:

```bash
mkdir -p deploy/certs
openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
  -keyout deploy/certs/privkey.pem -out deploy/certs/fullchain.pem -subj "/CN=localhost"
```

### 2.4 Start the stack

```bash
docker compose -f docker-compose.prod.yml --env-file .env up -d --build

# Watch the API apply migrations, then come up healthy
docker compose -f docker-compose.prod.yml logs -f backend
```

Verify:

```bash
curl -fsS https://api.example.com/api/v1/health
curl -fsS https://api.example.com/api/v1/health/db
docker compose -f docker-compose.prod.yml exec backend alembic current
```

`alembic current` must print the same revision as `alembic heads` — if it does not,
the migration step failed and the container log says why.

---

## 3. Deploying a change

```bash
cd /opt/ocp
git pull

# Take a backup first — a migration is the only step that cannot be undone by
# redeploying the previous image.
./deploy/backup.sh

docker compose -f docker-compose.prod.yml --env-file .env up -d --build
docker compose -f docker-compose.prod.yml logs -f backend
```

`backend` re-runs `alembic upgrade head` on every start. Alembic is idempotent, so
a restart with no new revision is a no-op.

Downtime is limited to the seconds each container takes to recreate. For a change
that touches only the API, rebuild that service alone:

```bash
docker compose -f docker-compose.prod.yml up -d --build --no-deps backend
```

---

## 4. Rollback

**Application only (no schema change in the release).** Re-deploy the previous
commit — images are rebuilt from source, so this is a normal deploy of the old SHA:

```bash
git log --oneline -n 5
git checkout <previous-good-sha>
docker compose -f docker-compose.prod.yml --env-file .env up -d --build
```

**Schema change.** Alembic downgrades are supported per revision, but a downgrade
that drops a column discards its data. Choose deliberately:

```bash
# Inspect what would be undone
docker compose -f docker-compose.prod.yml exec backend alembic history

# Step back one revision
docker compose -f docker-compose.prod.yml exec backend alembic downgrade -1
```

If the downgrade is not safe, restore instead:

```bash
./deploy/restore.sh backups/publisher_db_<stamp>.sql.gz
```

The restore script stops `backend`, `worker` and `beat` so nothing writes while the
schema is replaced, requires you to type the database name to confirm, and brings
the writers back up afterwards.

---

## 5. Backups and recovery

```bash
# Manual
./deploy/backup.sh                       # → backups/publisher_db_<UTC>.sql.gz

# Nightly at 03:15, keeping 14 days
crontab -e
15 3 * * * cd /opt/ocp && ./deploy/backup.sh >> /var/log/ocp-backup.log 2>&1
```

**Off-host copies are mandatory.** A dump that only exists on the same disk is not
a backup. Sync the directory somewhere else, e.g.
`rclone sync backups/ remote:ocp-backups`.

Uploaded media lives in the `media_data` volume, not in the database, so a database
restore does not bring videos back:

```bash
docker run --rm -v one-click-publisher_media_data:/media -v "$PWD/backups:/out" alpine \
  tar czf /out/media_<stamp>.tar.gz -C /media .
```

### Recovery drill

Practise this before you need it, on a throwaway host:

1. `docker compose -f docker-compose.prod.yml up -d postgres redis`
2. `./deploy/restore.sh backups/<newest>.sql.gz`
3. `docker compose -f docker-compose.prod.yml up -d`
4. `curl -fsS https://api.example.com/api/v1/health/db`
5. Sign in and confirm a workspace, a media item, and an audit entry are present.

---

## 6. Monitoring and error tracking

**Health endpoints** — wire these into any uptime monitor:

| Endpoint | Meaning |
| --- | --- |
| `GET /api/v1/health` | process is up (no dependencies checked) |
| `GET /api/v1/health/db` | PostgreSQL reachable |
| `GET /api/v1/health/redis` | Redis reachable |

The probes always answer `200` and report the failure in the body, so a monitor
must inspect `status`, not just the HTTP code.

**Logs** are single-line JSON on stdout from all three backend processes:

```bash
docker compose -f docker-compose.prod.yml logs -f --tail=200 backend worker beat
```

Every record carries `timestamp`, `level`, `logger`, `message`, plus whatever the
call site attached (for example `environment`). Ship them with the Docker logging
driver (`--log-driver=json-file --log-opt max-size=50m`) or a collector.

**Error tracking (Sentry)** is opt-in. Set `SENTRY_DSN` in `.env` and restart
`backend`. Nothing is sent when it is empty, which is why development and the test
suite stay offline. `SENTRY_TRACES_SAMPLE_RATE` samples performance traces
(errors are always captured); `send_default_pii` is hard-coded to `false`, so
request bodies — credentials, refresh tokens, video bytes — are never attached.

Container health is visible directly:

```bash
docker compose -f docker-compose.prod.yml ps        # "healthy" / "starting" / "unhealthy"
```

---

## 7. Scaling

| Bottleneck | Action |
| --- | --- |
| API CPU | `WEB_CONCURRENCY=8` in `.env`, restart `backend` |
| Publishing / analytics backlog | `docker compose -f docker-compose.prod.yml up -d --scale worker=3` |
| PostgreSQL | Move to a managed instance, point `DATABASE_URL` at it, drop the `postgres` service |
| Redis | Same — point `REDIS_URL`, `CELERY_BROKER_URL` and `CELERY_RESULT_BACKEND` at a managed instance |
| Disk (video) | Put `media_data` on a larger volume, or attach object storage (see §9) |

Celery Beat must stay a **single** replica: two beats duplicate every sweep.

---

## 8. Security checklist before going live

- [ ] `ENVIRONMENT=production`, `DEBUG=false`
- [ ] `SECRET_KEY`, `OAUTH_ENCRYPTION_KEY`, `POSTGRES_PASSWORD` are random, unique, and stored in a secret manager — not in git
- [ ] `REFRESH_COOKIE_SECURE=true` and the site is served over HTTPS
- [ ] `CORS_ORIGINS` lists only your real frontend origin(s)
- [ ] TLS chain valid; the HTTP server redirects to HTTPS
- [ ] Only 22/80/443 are open on the host firewall
- [ ] `RATE_LIMIT_ENABLED=true`
- [ ] Backups run nightly **and** leave the host
- [ ] The recovery drill in §5 has been run at least once
- [ ] `/docs` is reachable only if you intend it to be — front it with an allow-list if not

---

## 9. Known limitations

These are honest gaps, not oversights:

- **Media storage is filesystem-backed.** Uploads and thumbnails live in the
  `media_data` volume. There is no S3/S3-compatible backend, so this stack assumes a
  single host with a durable volume. Moving to object storage means adding a storage
  backend behind the existing `app/services/storage.py` interface; until then, do not
  scale the API across hosts without shared storage.
- **Single-host deployment.** No orchestrator, no horizontal autoscaling, no rolling
  updates. `docker compose up -d --build` recreates containers, so expect a few
  seconds of downtime.
- **TLS certificates are copied manually.** Certbot renews on a timer, but the copy
  into `deploy/certs/` and the nginx reload are manual steps.
- **The frontend's API URL is baked in at build time.** `NEXT_PUBLIC_API_URL` is a
  build argument, so changing it means rebuilding the `frontend` image.
- **No centralised log aggregation or dashboards.** Logs are JSON on stdout and are
  expected to be collected by the platform; there is no bundled Grafana/Loki stack.
- **Redis is a single instance.** It holds rate-limit counters (recoverable), the
  Celery broker (in-flight tasks are lost on a crash), and the result backend.
  Enable persistence — the provided compose file already passes `--appendonly yes`.

---

## 10. Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `backend` exits immediately with a validation error naming `SECRET_KEY` or `REFRESH_COOKIE_SECURE` | The production guards in `config.py`. Fill in the required values from §2.2. |
| `error while loading shared libraries: libpq.so.5` | The image was built from the dev `Dockerfile` instead of `Dockerfile.prod`. Use `-f docker-compose.prod.yml`. |
| `password authentication failed for user "publisher"` | `POSTGRES_PASSWORD` changed after the volume was created. Postgres keeps the original password; either restore the old value or remove the volume (destructive). |
| Uploads fail at ~1 MB with a connection reset | `client_max_body_size` in `deploy/nginx/app.conf` is below `MEDIA_MAX_UPLOAD_BYTES`. |
| Uploads fail with `502` after two minutes | nginx's `proxy_read_timeout` is too low for a large upload; the shipped config already sets 600 s for `/api/`. |
| `429` far more often than expected | The limiter keys on the **first hop** of `X-Forwarded-For`. Confirm nginx sets `X-Forwarded-For` (it does by default here) and that `RATE_LIMIT_*` values suit your traffic. |
| Scheduled posts never fire | `ocp_beat` is down, or two beat replicas are fighting. Check `docker compose -f docker-compose.prod.yml logs beat` and keep exactly one replica. |
| `alembic current` lags `alembic heads` | The migration step in the container command failed. Read the `backend` log; fix the revision, then restart. |
| Frontend calls `http://localhost:8000` in the browser | `NEXT_PUBLIC_API_URL` was wrong at **build** time. Fix `.env` and rebuild the `frontend` image. |
