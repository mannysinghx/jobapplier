"""Deterministic auto-submit policy gate. No model output and no job text can influence it.

Inputs are DB state, config and the kill-switch file only. Every check is evaluated and reported (no short-circuit),
so the handoff task can list every reason.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import controls
from .config import get_settings
from .connectors.registry import review_is_current
from .db import utcnow
from .matching import hard_exclusions
from .models import Application, Job, Packet, Preferences, Source, SubmissionAttempt
from .prep.answers import is_blocking
from .submission.base import ADAPTERS


@dataclass
class PolicyDecision:
    allowed: bool
    reasons: list[str] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)


def latest_packet(db: Session, app_id: int) -> Packet | None:
    return db.execute(select(Packet).where(Packet.application_id == app_id).order_by(Packet.version.desc()).limit(1)).scalar()


def submissions_since(db: Session, since: datetime, source_key: str | None = None) -> int:
    q = select(func.count(SubmissionAttempt.id)).where(SubmissionAttempt.started_at >= since,
                                                       SubmissionAttempt.adapter != "manual")
    if source_key:
        q = q.where(SubmissionAttempt.source_key == source_key)
    return db.execute(q).scalar() or 0


def prior_applications(db: Session, profile_id: int, job: Job) -> list[dict]:
    """Applications already SUBMITTED/CONFIRMED/FOLLOW_UP for the same employer + title on ANY source (location
    ignored), including ones imported from the LinkedIn export (applied outside this tool)."""
    from .discovery import dedupe_key

    emp, title = dedupe_key(job.employer, "", None), dedupe_key("", job.title, None)
    out = []
    q = (select(Application, Job).join(Job, Job.id == Application.job_id)
         .where(Application.profile_id == profile_id, Application.job_id != job.id,
                Application.state.in_(["SUBMITTED", "CONFIRMED", "FOLLOW_UP"])))
    for other_app, other in db.execute(q):
        if dedupe_key(other.employer, "", None) == emp and dedupe_key("", other.title, None) == title:
            out.append({"application_id": other_app.id, "state": other_app.state, "source": other.source_key,
                        "note": other_app.notes, "updated_at": other_app.updated_at})
    return out


def already_applied(db: Session, app: Application, job: Job) -> bool:
    """Duplicate guard across the idempotency key AND the job's dedupe key (same role re-posted / other source)."""
    if db.execute(select(SubmissionAttempt.id).where(SubmissionAttempt.idempotency_key == app.idempotency_key,
                                                     SubmissionAttempt.status.in_(["PENDING", "SUBMITTED", "CONFIRMED", "UNKNOWN"]))).first():
        return True
    other = db.execute(
        select(Application.id).join(Job, Job.id == Application.job_id).where(
            Job.dedupe_key == job.dedupe_key, Application.id != app.id,
            Application.state.in_(["SUBMITTED", "CONFIRMED", "FOLLOW_UP"]))).first()
    return other is not None or bool(prior_applications(db, app.profile_id, job))


def evaluate(db: Session, app: Application, now: datetime | None = None) -> PolicyDecision:
    now = now or utcnow()
    s = get_settings()
    job: Job = app.job
    source = db.get(Source, job.source_key)
    prefs = db.execute(select(Preferences).where(Preferences.profile_id == app.profile_id)).scalar()
    packet = latest_packet(db, app.id)
    adapter = ADAPTERS.get(job.source_key)
    c: dict[str, bool] = {}
    r: list[str] = []

    def check(name: str, ok: bool, reason: str) -> None:
        c[name] = bool(ok)
        if not ok:
            r.append(reason)

    check("not_paused", not controls.is_paused(db), "global pause / kill switch is on")
    check("source_registered", source is not None, "source not in registry")
    check("source_submit_permitted", bool(source and source.submit_permitted),
          "source terms do not permit automated submission (registry submit_permitted=false)")
    check("source_review_current", bool(source and review_is_current(source)), "source permission review is out of date")
    check("adapter_verified", bool(adapter and adapter.verified), "no verified submission integration for this source")
    check("user_enabled_source", bool(source and source.enabled), "source disabled by user")
    check("user_opted_in", bool(source and source.auto_submit_opt_in), "auto-submit not enabled by user for this source")
    check("state_approved", app.state == "APPROVED", f"application state is {app.state}, not APPROVED")
    check("packet_approved", bool(packet and packet.approved_at), "latest packet not approved")
    check("listing_active", job.expired_at is None, "listing expired")
    check("listing_unique", job.duplicate_of_id is None, "listing is a duplicate")
    check("listing_fresh", job.last_seen_at >= now - timedelta(hours=s.listing_stale_hours),
          f"listing not seen in the last {s.listing_stale_hours}h")
    check("apply_url_https", bool(job.apply_url and job.apply_url.startswith("https://")), "missing/insecure apply URL")
    check("not_already_applied", not already_applied(db, app, job), "already applied (duplicate guard)")
    excl = hard_exclusions(job, prefs) if prefs else ["no preferences"]
    check("no_hard_exclusions", not excl, "hard exclusion: " + "; ".join(excl))
    check("score_meets_threshold", bool(prefs and app.score is not None and app.score >= prefs.min_score),
          f"score {app.score} below threshold {prefs.min_score if prefs else '?'}")
    blocking = [a for a in (packet.answers if packet else []) if is_blocking(a)]
    check("answers_supported", bool(packet) and not blocking,
          "unsupported/sensitive/attestation answers: " + ", ".join(sorted({a['key'] for a in blocking}))[:300])
    day_ago = now - timedelta(hours=24)
    check("global_daily_limit", bool(prefs) and submissions_since(db, day_ago) < prefs.daily_application_limit,
          "daily application limit reached")
    check("source_daily_limit", bool(source) and submissions_since(db, day_ago, job.source_key) < source.daily_submit_limit,
          "per-source daily limit reached")
    return PolicyDecision(allowed=all(c.values()), reasons=r, checks=c)
