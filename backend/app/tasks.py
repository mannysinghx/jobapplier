"""Celery worker + beat. Beat ticks every 5 minutes. Each tick polls only if the user's polling cadence has elapsed."""
from datetime import timedelta

from celery import Celery
from sqlalchemy import select

from . import controls, discovery, pipeline, privacy
from .config import get_settings
from .db import SessionLocal, utcnow
from .models import Preferences, Profile, SystemControl

celery_app = Celery("jobapplier", broker=get_settings().redis_url, backend=None)
celery_app.conf.update(
    task_acks_late=True, worker_prefetch_multiplier=1, task_time_limit=30 * 60, timezone="UTC",
    beat_schedule={
        "tick": {"task": "app.tasks.tick", "schedule": 300.0},
        "retention": {"task": "app.tasks.retention", "schedule": 24 * 3600.0},
    },
)
LAST_POLL_KEY = "last_poll"


@celery_app.task(name="app.tasks.tick")
def tick() -> dict:  # also called directly by app.scheduler (embedded mode)
    db = SessionLocal()
    try:
        if controls.is_paused(db):
            return {"paused": True}
        profile = db.execute(select(Profile).order_by(Profile.id).limit(1)).scalar()
        if profile is None:
            return {"skipped": "no profile"}
        prefs = db.execute(select(Preferences).where(Preferences.profile_id == profile.id)).scalar()
        row = db.get(SystemControl, LAST_POLL_KEY)
        last = row.value.get("at") if row else None
        now = utcnow()
        if last and prefs and now - timedelta(minutes=prefs.polling_minutes) < _parse(last):
            return {"skipped": "cadence"}
        runs = discovery.poll_all(db)
        row = row or SystemControl(key=LAST_POLL_KEY, value={})
        row.value = {"at": now.isoformat()}
        db.add(row)
        db.commit()
        from . import imports

        imports.expire_stale_imports(db)
        db.commit()
        result = pipeline.auto_process(db, profile)
        return {"runs": len(runs), **result}
    finally:
        db.close()


@celery_app.task(name="app.tasks.retention")
def retention() -> dict:
    db = SessionLocal()
    try:
        return privacy.apply_retention(db)
    finally:
        db.close()


def _parse(s: str):  # noqa: ANN202
    from datetime import datetime

    return datetime.fromisoformat(s)
