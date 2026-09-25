"""Data export, deletion and retention."""
import io
import json
import zipfile
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import audit
from .db import utcnow
from .models import (
    Application,
    AuditEvent,
    ConnectorRun,
    Document,
    Fact,
    FactConflict,
    HandoffTask,
    Job,
    Packet,
    Preferences,
    Profile,
    StandardAnswer,
    SubmissionAttempt,
    SystemControl,
)
from .security.crypto import EncryptedFileStore

RETENTION_KEY = "retention"
DEFAULT_RETENTION = {"expired_job_days": 90, "audit_days": 730, "connector_run_days": 30, "rejected_application_days": 180}


def _row(o) -> dict:  # noqa: ANN001
    return {c.name: getattr(o, c.name) for c in o.__table__.columns}


def export_zip(db: Session, profile: Profile, actor: str) -> bytes:
    store = EncryptedFileStore()
    buf = io.BytesIO()
    pid = profile.id
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        def dump(name: str, rows: list) -> None:
            zf.writestr(name, json.dumps([_row(r) for r in rows], default=str, indent=2))

        dump("profile.json", [profile])
        dump("preferences.json", db.execute(select(Preferences).where(Preferences.profile_id == pid)).scalars().all())
        dump("facts.json", db.execute(select(Fact).where(Fact.profile_id == pid)).scalars().all())
        dump("conflicts.json", db.execute(select(FactConflict).where(FactConflict.profile_id == pid)).scalars().all())
        dump("standard_answers.json", db.execute(select(StandardAnswer).where(StandardAnswer.profile_id == pid)).scalars().all())
        apps = db.execute(select(Application).where(Application.profile_id == pid)).scalars().all()
        dump("applications.json", apps)
        app_ids = [a.id for a in apps]
        dump("jobs.json", [a.job for a in apps])
        dump("packets.json", db.execute(select(Packet).where(Packet.application_id.in_(app_ids))).scalars().all() if app_ids else [])
        dump("submissions.json", db.execute(select(SubmissionAttempt).where(SubmissionAttempt.application_id.in_(app_ids))).scalars().all() if app_ids else [])
        dump("handoffs.json", db.execute(select(HandoffTask).where(HandoffTask.application_id.in_(app_ids))).scalars().all() if app_ids else [])
        dump("audit_events.json", db.execute(select(AuditEvent).order_by(AuditEvent.id)).scalars().all())
        docs = db.execute(select(Document).where(Document.profile_id == pid)).scalars().all()
        dump("documents.json", docs)
        for d in docs:
            try:
                zf.writestr(f"documents/{d.id}_{d.filename}", store.get(d.store_path))
            except FileNotFoundError:
                pass
    audit.record(db, actor, "privacy.exported", "profile", pid)
    db.commit()
    return buf.getvalue()


def delete_all(db: Session, profile: Profile, actor: str) -> dict:
    """Deletes the profile and everything derived from it, including encrypted files. Audit events are kept:
    they contain no personal values. Job listings are public data and are kept unless retention removes them."""
    store = EncryptedFileStore()
    docs = db.execute(select(Document).where(Document.profile_id == profile.id)).scalars().all()
    paths = {d.store_path for d in docs} | {d.text_path for d in docs if d.text_path}
    counts = {"documents": len(docs),
              "facts": db.query(Fact).filter(Fact.profile_id == profile.id).count(),
              "applications": db.query(Application).filter(Application.profile_id == profile.id).count()}
    pid = profile.id
    db.delete(profile)  # ON DELETE CASCADE covers facts, docs, answers, prefs, applications, packets, attempts, handoffs
    db.flush()
    for p in paths:
        store.delete(p)
    audit.record(db, actor, "privacy.deleted", "profile", pid, counts)
    db.commit()
    return counts


def get_retention(db: Session) -> dict:
    row = db.get(SystemControl, RETENTION_KEY)
    return {**DEFAULT_RETENTION, **(row.value if row else {})}


def set_retention(db: Session, values: dict, actor: str) -> dict:
    clean = {k: max(1, int(v)) for k, v in values.items() if k in DEFAULT_RETENTION}
    row = db.get(SystemControl, RETENTION_KEY) or SystemControl(key=RETENTION_KEY, value={})
    row.value = {**get_retention(db), **clean}
    db.add(row)
    audit.record(db, actor, "retention.updated", details=clean)
    db.commit()
    return row.value


def apply_retention(db: Session) -> dict:
    r = get_retention(db)
    now = utcnow()
    applied_job_ids = select(Application.job_id).where(Application.state.in_(["SUBMITTED", "CONFIRMED", "FOLLOW_UP", "FAILED"]))
    jobs = db.query(Job).filter(Job.expired_at.isnot(None), Job.expired_at < now - timedelta(days=r["expired_job_days"]),
                                Job.id.notin_(applied_job_ids))
    n_jobs = jobs.delete(synchronize_session=False)
    n_runs = db.query(ConnectorRun).filter(ConnectorRun.started_at < now - timedelta(days=r["connector_run_days"])).delete(
        synchronize_session=False)
    n_apps = db.query(Application).filter(Application.state.in_(["REJECTED_BY_USER", "EXPIRED", "DUPLICATE"]),
                                          Application.updated_at < now - timedelta(days=r["rejected_application_days"])).delete(
        synchronize_session=False)
    n_audit = audit.purge_older_than(db, now - timedelta(days=r["audit_days"]))
    db.commit()
    return {"jobs": n_jobs, "connector_runs": n_runs, "applications": n_apps, "audit_events": n_audit}
