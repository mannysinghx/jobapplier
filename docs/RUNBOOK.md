# Incident & kill-switch runbook

## Stop everything now
Any one of these halts polling **and** submission. They are checked before every board poll and immediately before any submission call.

1. **UI**: Controls & Privacy → **Pause** (enter a reason).
2. **CLI**: `docker compose exec api python -m app.cli pause "reason"` (local: `backend/.venv/bin/python -m app.cli pause "reason"`).
3. **Kill-switch file (works even if the API or DB is down)**:
   `docker compose exec api touch /data/KILL_SWITCH` (local: `touch data/KILL_SWITCH`).
4. Last resort: `docker compose stop worker beat`. Nothing polls or submits without the worker.

Resume: remove the file (`rm /data/KILL_SWITCH`) **and** click Resume (or `app.cli resume`). The UI warns if the file is still present.

## Suspected duplicate or unwanted submission
1. Pause (above).
2. Applications → filter SUBMITTED/CONFIRMED/FAILED. Check each attempt's status, confirmation and time.
3. An `UNKNOWN` attempt means the outcome is uncertain. Check the employer's site/email before doing anything. The system will not retry it.
4. Withdraw directly with the employer if needed. Record it in `docs/OPS_LOG.md`.

## Source terms changed
1. Set `read_permitted`/`submit_permitted: false` in `config/sources.yaml` and restart the API (`docker compose restart api`). This force-disables user toggles and writes an audit event.
2. Update `docs/SOURCE_REGISTRY.md`.

## Suspected credential / key compromise
- **Session**: delete all rows in `sessions` (`DELETE FROM sessions;`) to log everyone out, then change the password (re-create the user).
- **TOTP**: re-create the admin user to get a new secret.
- **Encryption key**: generate a new key, decrypt with the old key and re-encrypt with the new one (a rotation script is on the roadmap). Until then, assume data encrypted under the old key is exposed if the key leaked. Export, delete, then re-import with the new key.

## Audit integrity
`GET /api/audit/verify` (or `app.cli verify-audit`). `ok: false` names the first tampered row. Treat that as a security incident: snapshot the DB before investigating.

## Health signals
- UI → Sources → Connector health; Prometheus `ja_boards_failing`, `ja_connector_errors_recent`, `ja_paused`, `ja_handoffs_open`.
- Boards back off exponentially (2^n min, max 24h). After 3 permanent errors (404/401/403) a board is auto-disabled and audited.
