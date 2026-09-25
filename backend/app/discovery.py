"""Polling, normalization, deduplication, expiry and connector health."""
import hashlib
import re
import time
from collections.abc import Callable
from datetime import datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import audit, controls
from .config import get_settings
from .connectors.ats import CONNECTORS
from .connectors.base import Connector, ConnectorError, NormalizedJob
from .connectors.registry import SourceNotPermitted, assert_can_read
from .db import utcnow
from .models import Application, ConnectorRun, Job, Source, SourceBoard
from .statemachine import USER_OWNED, transition

MAX_BACKOFF_MINUTES = 24 * 60


def _norm(s: str | None) -> str:
    s = (s or "").lower()
    s = re.sub(r"\b(sr|snr)\b\.?", "senior", s)
    s = re.sub(r"\b(jr)\b\.?", "junior", s)
    s = re.sub(r"\b(inc|llc|ltd|corp|corporation|co|gmbh)\b\.?", "", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def dedupe_key(employer: str, title: str, location: str | None) -> str:
    return hashlib.sha256(f"{_norm(employer)}|{_norm(title)}|{_norm(location)}".encode()).hexdigest()


def content_hash(j: NormalizedJob) -> str:
    return hashlib.sha256(f"{j.title}|{j.location}|{j.description}|{j.salary_min}|{j.salary_max}".encode()).hexdigest()


class RateLimiter:
    """Minimum spacing between requests per source (process-local)."""

    _last: dict[str, float] = {}

    @classmethod
    def wait(cls, key: str, per_minute: int, sleep: Callable[[float], None] = time.sleep) -> None:
        if per_minute <= 0:
            return
        gap = 60.0 / per_minute
        now = time.monotonic()
        last = cls._last.get(key, 0.0)
        if now - last < gap:
            sleep(gap - (now - last))
        cls._last[key] = time.monotonic()


def make_client() -> httpx.Client:
    s = get_settings()
    return httpx.Client(timeout=s.http_timeout_seconds, headers={"User-Agent": s.user_agent, "Accept": "application/json"},
                        follow_redirects=False)


def upsert_jobs(db: Session, source: Source, board: SourceBoard | None, jobs: list[NormalizedJob], now: datetime,
                complete_listing: bool = True, ttl_days: int | None = None, board_token: str | None = None) -> tuple[int, int]:
    """complete_listing: absence from `jobs` expires a job. Otherwise jobs expire ttl_days after they were last seen.
    board may be None for user imports (email alerts, manual, LinkedIn export); then board_token names the site."""
    token = board.board_token if board is not None else (board_token or "import")
    new = 0
    seen_ids = set()
    for nj in jobs:
        if not nj.external_id or not nj.title:
            continue
        seen_ids.add(nj.external_id)
        employer = (board.employer_name if board is not None else None) or nj.employer
        existing = db.execute(select(Job).where(Job.source_key == source.key, Job.external_id == nj.external_id)).scalar()
        ch = content_hash(nj)
        fields = dict(
            employer=employer[:200], title=nj.title[:300], description=nj.description, requirements=nj.requirements,
            location=(nj.location or None), work_arrangement=nj.work_arrangement, employment_type=nj.employment_type,
            salary_min=nj.salary_min, salary_max=nj.salary_max, salary_currency=nj.salary_currency,
            posted_at=nj.posted_at, expires_at=nj.expires_at, canonical_url=nj.canonical_url, apply_url=nj.apply_url,
            permission={"source": source.key, "method": source.method, "terms_url": source.terms_url,
                        "date_checked": source.date_checked, "submit_permitted": source.submit_permitted},
            dedupe_key=dedupe_key(employer, nj.title, nj.location), content_hash=ch,
        )
        if existing:
            keep_desc = (existing.meta or {}).get("details_fetched") or (existing.meta or {}).get("description_by_user")
            for k, v in fields.items():
                if k in ("description", "requirements") and keep_desc:
                    continue  # never overwrite a fetched/user-provided full description with a summary
                setattr(existing, k, v)
            existing.meta = {**(existing.meta or {}), **nj.meta}
            existing.last_seen_at = now
            if existing.expired_at is not None:  # re-listed
                existing.expired_at = None
            continue
        job = Job(source_key=source.key, board_token=token, external_id=nj.external_id,
                  first_seen_at=now, last_seen_at=now, meta=dict(nj.meta), **fields)
        # Cross-source / re-post duplicate: same employer+title+location already active
        dup = db.execute(select(Job).where(Job.dedupe_key == job.dedupe_key, Job.expired_at.is_(None),
                                           Job.duplicate_of_id.is_(None))).scalar()
        db.add(job)
        db.flush()
        if dup is not None:
            job.duplicate_of_id = dup.id
            audit.record(db, "worker", "job.duplicate_detected", "job", job.id, {"duplicate_of": dup.id})
        new += 1
    # Expiry: jobs from this board that the (complete) listing no longer contains
    expired = 0
    # Age alone never expires a listing: if the employer still publishes it, it is open (age is a matching note).
    for job in db.execute(select(Job).where(Job.source_key == source.key, Job.board_token == token,
                                            Job.expired_at.is_(None))).scalars():
        past_deadline = job.expires_at is not None and job.expires_at < now
        gone = job.external_id not in seen_ids if complete_listing else False
        stale = ttl_days is not None and job.last_seen_at < now - timedelta(days=ttl_days)
        if gone or past_deadline or stale:
            job.expired_at = now
            expired += 1
            _expire_applications(db, job)
    return new, expired


def _expire_applications(db: Session, job: Job) -> None:
    for app in db.execute(select(Application).where(Application.job_id == job.id)).scalars():
        if app.state not in USER_OWNED and app.state not in ("EXPIRED", "DUPLICATE", "FAILED"):
            transition(db, app, "EXPIRED", "worker", "listing no longer published")


def poll_board(db: Session, source: Source, board: SourceBoard, connector: Connector, now: datetime | None = None) -> ConnectorRun:
    now = now or utcnow()
    run = ConnectorRun(source_key=source.key, board_token=board.board_token)
    db.add(run)
    db.flush()
    try:
        assert_can_read(source)
        if controls.is_paused(db):
            raise SourceNotPermitted("global pause is on")
        RateLimiter.wait(source.key, source.rate_limit_per_minute)
        jobs = connector.fetch(board.board_token, board.params or None)
        run.jobs_seen = len(jobs)
        run.jobs_new, run.jobs_expired = upsert_jobs(
            db, source, board, jobs, now, complete_listing=connector.complete_listing,
            ttl_days=None if connector.complete_listing else get_settings().search_listing_ttl_days)
        run.status = "ok"
        board.consecutive_failures = 0
        board.next_attempt_at = None
        board.last_success_at = now
    except SourceNotPermitted as e:
        run.status, run.error = "skipped", str(e)[:500]
    except (ConnectorError, ValueError) as e:
        run.status, run.error = "error", str(e)[:500]
        board.consecutive_failures += 1
        backoff = min(2 ** board.consecutive_failures, MAX_BACKOFF_MINUTES)
        if isinstance(e, ConnectorError) and e.retry_after:
            backoff = max(backoff, int(e.retry_after / 60) + 1)
        board.next_attempt_at = now + timedelta(minutes=backoff)
        if isinstance(e, ConnectorError) and e.permanent and board.consecutive_failures >= 3:
            board.enabled = False
            audit.record(db, "worker", "board.auto_disabled", "source_board", board.id, {"error": str(e)[:200]})
    run.finished_at = utcnow()
    db.commit()
    return run


def poll_all(db: Session, client: httpx.Client | None = None, now: datetime | None = None) -> list[ConnectorRun]:
    now = now or utcnow()
    if controls.is_paused(db):
        return []
    client = client or make_client()
    runs = []
    boards = db.execute(select(SourceBoard).where(SourceBoard.enabled.is_(True))).scalars().all()
    for board in boards:
        if board.next_attempt_at and board.next_attempt_at > now:
            continue
        if controls.is_paused(db):  # re-check between boards: pause takes effect immediately
            break
        source = db.get(Source, board.source_key)
        cls = CONNECTORS.get(board.source_key)
        if source is None or cls is None:
            continue
        runs.append(poll_board(db, source, board, cls(client), now))
    return runs


def connector_health(db: Session) -> list[dict]:
    out = []
    for board in db.execute(select(SourceBoard)).scalars():
        last = db.execute(select(ConnectorRun).where(ConnectorRun.source_key == board.source_key,
                                                     ConnectorRun.board_token == board.board_token)
                          .order_by(ConnectorRun.id.desc()).limit(1)).scalar()
        out.append({
            "board_id": board.id, "source": board.source_key, "board": board.board_token, "enabled": board.enabled,
            "consecutive_failures": board.consecutive_failures,
            "next_attempt_at": board.next_attempt_at, "last_success_at": board.last_success_at,
            "last_status": last.status if last else None, "last_error": last.error if last else None,
            "last_run_at": last.started_at if last else None,
            "last_counts": {"seen": last.jobs_seen, "new": last.jobs_new, "expired": last.jobs_expired} if last else None,
        })
    return out
