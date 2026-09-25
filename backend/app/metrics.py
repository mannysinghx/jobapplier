"""Prometheus gauges: aggregate counts only (no personal data)."""
from prometheus_client import Gauge
from sqlalchemy import func, select

from . import controls
from .db import SessionLocal
from .models import Application, ConnectorRun, HandoffTask, Job, SourceBoard

APPS = Gauge("ja_applications", "Applications by state", ["state"])
JOBS_ACTIVE = Gauge("ja_jobs_active", "Active (non-expired, non-duplicate) jobs")
HANDOFFS_OPEN = Gauge("ja_handoffs_open", "Open handoff tasks")
BOARDS_FAILING = Gauge("ja_boards_failing", "Boards with consecutive failures")
LAST_RUN_ERRORS = Gauge("ja_connector_errors_recent", "Connector runs with status=error in the last 100 runs")
PAUSED = Gauge("ja_paused", "1 if global pause / kill switch is active")


def refresh() -> None:
    db = SessionLocal()
    try:
        APPS.clear()
        for state, n in db.execute(select(Application.state, func.count()).group_by(Application.state)):
            APPS.labels(state=state).set(n)
        JOBS_ACTIVE.set(db.execute(select(func.count(Job.id)).where(Job.expired_at.is_(None), Job.duplicate_of_id.is_(None))).scalar())
        HANDOFFS_OPEN.set(db.execute(select(func.count(HandoffTask.id)).where(HandoffTask.status == "OPEN")).scalar())
        BOARDS_FAILING.set(db.execute(select(func.count(SourceBoard.id)).where(SourceBoard.consecutive_failures > 0)).scalar())
        recent = db.execute(select(ConnectorRun.status).order_by(ConnectorRun.id.desc()).limit(100)).scalars().all()
        LAST_RUN_ERRORS.set(sum(1 for s in recent if s == "error"))
        PAUSED.set(1 if controls.is_paused(db) else 0)
    finally:
        db.close()
