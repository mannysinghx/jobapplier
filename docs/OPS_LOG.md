# Ops log

One line per action with a side effect outside the working tree: **timestamp · what · why · how to undo · verified?**

| Timestamp (local) | What | Why | Undo | Verified |
|---|---|---|---|---|
| 2026-09-24 19:25 | `git init -b main` in `/Users/manindersingh/-jobApplier` | Repo was empty and not under version control | `rm -rf .git` | yes |
| 2026-09-24 19:26 | Created `backend/.venv` (Python 3.12) and installed pip deps from PyPI | Build + test environment | `rm -rf backend/.venv` | yes (`pip list`) |
| 2026-09-24 19:43 | Tried to `initdb` a throwaway PG in the scratchpad with libpq's initdb | Test the migration on Postgres | none needed; failed before creating anything (no `postgres` binary next to that initdb) | yes |
| 2026-09-24 19:44 | Started a throwaway PostgreSQL 16 on **127.0.0.1:5499** (data dir in the session scratchpad, TCP only), created DBs `jobapplier_test`, `ja_pytest` | Verify the Alembic migration, audit triggers and the full test suite on Postgres. **Did not touch the user's existing Postgres on :5432.** | `pg_ctl -D <scratchpad>/pgtest stop` and delete the scratchpad dir | yes |
| 2026-09-24 19:45 | Two failed start attempts (Unix socket path >103 bytes; then "postmaster became multithreaded", fixed with `LC_ALL`) | Same as above | nothing to undo (server exited on its own) | yes (log read) |
| 2026-09-24 19:47 | Stopped the throwaway PostgreSQL on :5499 (`pg_ctl stop -m fast`) | Testing finished | n/a | yes (`lsof` shows nothing on :5499; :5432 still up) |
