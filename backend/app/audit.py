"""Append-only audit log with a SHA-256 hash chain.

Never put sensitive values (email, phone, answers, document text) into `details`.
`record()` strips keys that look sensitive as a second line of defence.
"""
import hashlib
import json
from datetime import datetime

from sqlalchemy import Connection, select, text
from sqlalchemy.orm import Session

from .db import utcnow
from .models import AuditEvent, MaintenanceFlag

GENESIS = "0" * 64
_SENSITIVE_KEYS = {"email", "phone", "answer", "password", "totp", "token", "secret", "text", "snippet", "ssn"}


def _scrub(details: dict) -> dict:
    out = {}
    for k, v in (details or {}).items():
        if any(s in k.lower() for s in _SENSITIVE_KEYS):
            out[k] = "[redacted]"
        elif isinstance(v, dict):
            out[k] = _scrub(v)
        else:
            out[k] = v
    return out


def _digest(prev: str, ts: datetime, actor: str, action: str, etype, eid, details: dict) -> str:
    payload = json.dumps(
        [prev, ts.isoformat(), actor, action, etype, eid, details], sort_keys=True, default=str
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def record(
    db: Session,
    actor: str,
    action: str,
    entity_type: str | None = None,
    entity_id: object = None,
    details: dict | None = None,
) -> AuditEvent:
    last = db.execute(select(AuditEvent.hash).order_by(AuditEvent.id.desc()).limit(1)).scalar()
    prev = last or GENESIS
    ts = utcnow()
    clean = _scrub(details or {})
    eid = None if entity_id is None else str(entity_id)
    ev = AuditEvent(
        ts=ts,
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=eid,
        details=clean,
        prev_hash=prev,
        hash=_digest(prev, ts, actor, action, entity_type, eid, clean),
    )
    db.add(ev)
    db.flush()
    return ev


def verify_chain(db: Session) -> tuple[bool, int | None]:
    """Returns (ok, first_bad_id). A purge leaves a gap, so the first retained row may start mid-chain."""
    prev = None
    for ev in db.execute(select(AuditEvent).order_by(AuditEvent.id)).scalars():
        if prev is not None and ev.prev_hash != prev:
            return False, ev.id
        if _digest(ev.prev_hash, ev.ts, ev.actor, ev.action, ev.entity_type, ev.entity_id, ev.details) != ev.hash:
            return False, ev.id
        prev = ev.hash
    return True, None


def install_audit_guards(conn: Connection) -> None:
    """DB-level append-only enforcement. Deletes are allowed only while maintenance flag 'audit_purge' is active."""
    if conn.dialect.name == "sqlite":
        conn.execute(text("DROP TRIGGER IF EXISTS audit_no_update"))
        conn.execute(text("DROP TRIGGER IF EXISTS audit_no_delete"))
        conn.execute(
            text(
                "CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit_events "
                "BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END"
            )
        )
        conn.execute(
            text(
                "CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit_events "
                "WHEN NOT EXISTS (SELECT 1 FROM maintenance_flags WHERE key='audit_purge' AND active=1) "
                "BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END"
            )
        )
    elif conn.dialect.name == "postgresql":
        conn.execute(
            text(
                """
                CREATE OR REPLACE FUNCTION audit_append_only() RETURNS trigger AS $$
                BEGIN
                  IF TG_OP = 'DELETE' AND EXISTS (
                      SELECT 1 FROM maintenance_flags WHERE key='audit_purge' AND active) THEN
                    RETURN OLD;
                  END IF;
                  RAISE EXCEPTION 'audit_events is append-only';
                END; $$ LANGUAGE plpgsql;
                """
            )
        )
        conn.execute(text("DROP TRIGGER IF EXISTS audit_guard ON audit_events"))
        conn.execute(
            text(
                "CREATE TRIGGER audit_guard BEFORE UPDATE OR DELETE ON audit_events "
                "FOR EACH ROW EXECUTE FUNCTION audit_append_only()"
            )
        )


def purge_older_than(db: Session, cutoff: datetime) -> int:
    """Retention purge. Opens the maintenance window, deletes, closes it, and audits the purge."""
    flag = db.get(MaintenanceFlag, "audit_purge") or MaintenanceFlag(key="audit_purge")
    flag.active = True
    db.add(flag)
    db.flush()
    try:
        n = db.query(AuditEvent).filter(AuditEvent.ts < cutoff).delete(synchronize_session=False)
    finally:
        flag.active = False
        db.flush()
    record(db, "system", "audit.purged", details={"cutoff": cutoff.isoformat(), "rows": n})
    return n
