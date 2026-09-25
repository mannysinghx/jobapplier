# Setup guide

## Option A: Docker Compose (recommended)
Requires Docker (Desktop or Engine). All ports bind to 127.0.0.1.

```bash
cp .env.example .env
cd backend && python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/python -m app.cli gen-key   # paste into JA_ENCRYPTION_KEY
# set a strong POSTGRES_PASSWORD in .env
cd .. && docker compose up -d --build
docker compose exec api python -m app.cli create-admin admin   # prompts for password; prints a TOTP URI: add it to your authenticator
```
Open http://localhost:5180 and log in with username, password and TOTP code.

| Service | Host port |
|---|---|
| Web UI (nginx) | 127.0.0.1:5180 |
| API | 127.0.0.1:8100 (`/api/docs` for OpenAPI) |
| PostgreSQL | 127.0.0.1:5442 |
| Valkey (Redis protocol) | 127.0.0.1:6389 |
| Prometheus (optional, `--profile monitoring`) | 127.0.0.1:9095 |

Optional upload scanning: `docker compose --profile av up -d` and set `JA_CLAMAV_HOST=clamav` in `.env`.

## Option C: Railway (single service)
The root `Dockerfile` builds the React UI and serves it from the FastAPI app, so the UI and API share one origin. Polling and retention run in-process (`JA_EMBEDDED_SCHEDULER=true`), which means no Redis or Celery services and **exactly one replica**.

Resources (project `jobapplier2026`, environment `production`):
- `jobapplier` service built from GitHub `mannysinghx/jobapplier` (main) with the root `Dockerfile`, health check `/api/health`
- `Postgres` (Railway template); the app reads `JA_DATABASE_URL=${{Postgres.DATABASE_URL}}`
- Volume `jobapplier-volume` mounted at `/data` (encrypted file store + `KILL_SWITCH` file)

Variables: `JA_ENV=prod`, `JA_ENCRYPTION_KEY` (**back it up** from the Railway dashboard → Variables; without it your data can't be decrypted), `JA_DATA_DIR=/data`, `JA_KILL_SWITCH_FILE=/data/KILL_SWITCH`, `JA_COOKIE_SECURE=true`, `JA_MFA_REQUIRED=true`, `JA_EMBEDDED_SCHEDULER=true`, `JA_METRICS_PUBLIC=false`, `JA_CORS_ORIGINS=["https://<your-domain>"]`.

Create the admin (you choose the password; the TOTP URI prints only in your terminal):
```bash
railway ssh --project jobapplier2026 --service jobapplier -- python -m app.cli create-admin admin
```
Kill switch on Railway: `railway ssh ... -- touch /data/KILL_SWITCH`, or the Pause button.
Limitations on Railway: the local Ollama model on your Mac is not reachable (cover letters use the template). The consented-folder import can't see your Mac's folders; use upload instead. The container runs as root because Railway mounts volumes root-owned.

## Option B: Local development (no Docker)
```bash
cd backend
python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
export JA_ENCRYPTION_KEY=$(.venv/bin/python -m app.cli gen-key)   # store it; the dev DB is useless without it
.venv/bin/python -m app.cli init-db          # SQLite at ../data/dev.sqlite3
.venv/bin/python -m app.cli create-admin admin
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8100
# other terminal
cd frontend && npm install && npm run dev    # http://localhost:5180
```
Scheduled polling needs a Redis-protocol server on 6389 plus `celery -A app.tasks worker` and `celery -A app.tasks beat`. Without them, use **Poll now** in the UI or `python -m app.cli poll`.

## First-run checklist
1. **Profile**: name, email, phone, LinkedIn URL.
2. **Resume**: grant read-only consent to a folder and import, or upload a PDF/DOCX. Optionally upload your LinkedIn data-export ZIP.
3. **Facts**: review every extracted fact, fix titles/employers, resolve conflicts, approve. Only approved facts are used.
4. **Answers**: add and approve standard answers. Sensitive ones (work authorization, sponsorship, …) are only ever taken from here.
5. **Preferences**: titles, keywords, seniority, geographies, arrangements, salary floor, exclusions, min score, daily limit.
6. **Sources**: enable Greenhouse/Lever/Ashby and add employer boards (the company slug in the job-board URL).
7. **Poll now → Applications**: review matches, prepare packets, approve, then submit via the handoff (apply link + packet) and record the submission.

## Running tests
```bash
cd backend && .venv/bin/python -m pytest -q
# optional: against a DISPOSABLE Postgres (all tables are dropped per test)
JA_TEST_PG_URL=postgresql+psycopg://user@127.0.0.1:5499/scratch_db .venv/bin/python -m pytest -q
```

## Backups
- `docker compose exec db pg_dump -U jobapplier jobapplier | gzip > backup.sql.gz`, plus the `appdata` volume (encrypted files).
- Back up **`JA_ENCRYPTION_KEY` separately** (password manager or offline). Without it, the backups cannot be decrypted. With it, anyone holding the backup can read it.
- Restore: `gunzip -c backup.sql.gz | docker compose exec -T db psql -U jobapplier jobapplier`, restore the `appdata` volume, set the same key.
