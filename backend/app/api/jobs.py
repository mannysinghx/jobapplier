"""Sources, boards, jobs, applications, packets, handoffs, pipeline triggers."""
import re

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit, discovery, imports, pipeline, policy
from ..connectors.base import ConnectorError
from ..connectors.dice import validate_params as validate_dice_params
from ..connectors.registry import SourceNotPermitted
from ..ingestion import email_alerts
from ..connectors.registry import review_is_current
from ..db import get_db
from ..models import Application, Document, HandoffTask, Job, Source, SourceBoard, StandardAnswer, SubmissionAttempt, User
from ..prep import answers as ans
from ..prep.packet import render_cover_letter_text
from ..security.auth import actor, current_user, require_admin
from ..security.crypto import EncryptedFileStore
from ..statemachine import InvalidTransition, transition
from ..submission.base import ADAPTERS
from .profile import get_profile

router = APIRouter()


# ------------------------------------------------------------------ sources
def source_out(s: Source) -> dict:
    return {"key": s.key, "name": s.name, "method": s.method, "terms_url": s.terms_url, "date_checked": s.date_checked,
            "review_current": review_is_current(s), "rate_limit_per_minute": s.rate_limit_per_minute,
            "read_permitted": s.read_permitted, "submit_permitted": s.submit_permitted,
            "read_basis": s.registry_entry.get("read_basis"), "submit_basis": s.registry_entry.get("submit_basis"),
            "notes": s.registry_entry.get("notes"), "enabled": s.enabled, "auto_submit_opt_in": s.auto_submit_opt_in,
            "daily_submit_limit": s.daily_submit_limit, "verified_adapter": bool(ADAPTERS.get(s.key) and ADAPTERS[s.key].verified),
            "implemented": s.key in discovery.CONNECTORS}


@router.get("/sources")
def list_sources(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return [source_out(s) for s in db.execute(select(Source).order_by(Source.key)).scalars()]


class SourcePatch(BaseModel):
    enabled: bool | None = None
    auto_submit_opt_in: bool | None = None
    daily_submit_limit: int | None = Field(default=None, ge=0, le=50)


@router.patch("/sources/{key}")
def patch_source(key: str, body: SourcePatch, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    s = db.get(Source, key)
    if s is None:
        raise HTTPException(404)
    if body.enabled:
        if not s.read_permitted:
            raise HTTPException(409, "registry does not permit reading this source")
        if key not in discovery.CONNECTORS:
            raise HTTPException(409, "no connector implemented for this source")
        if not review_is_current(s):
            raise HTTPException(409, "permission review is out of date; re-check terms and update config/sources.yaml")
    if body.auto_submit_opt_in:
        if not s.submit_permitted:
            raise HTTPException(409, "registry does not permit automated submission for this source")
        if not (ADAPTERS.get(key) and ADAPTERS[key].verified):
            raise HTTPException(409, "no verified submission integration exists for this source")
    changes = body.model_dump(exclude_none=True)
    for k, v in changes.items():
        setattr(s, k, v)
    audit.record(db, actor(user), "source.updated", "source", key, changes)
    db.commit()
    return source_out(s)


class BoardIn(BaseModel):
    source_key: str
    board_token: str | None = Field(default=None, max_length=120)  # optional for saved searches (auto-named)
    employer_name: str | None = Field(default=None, max_length=200)
    params: dict | None = None  # saved search for search-based sources (Dice)


def board_out(b: SourceBoard) -> dict:
    return {"id": b.id, "source_key": b.source_key, "board_token": b.board_token, "employer_name": b.employer_name,
            "params": b.params or {},
            "enabled": b.enabled, "consecutive_failures": b.consecutive_failures, "next_attempt_at": b.next_attempt_at,
            "last_success_at": b.last_success_at}


@router.get("/boards")
def list_boards(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return [board_out(b) for b in db.execute(select(SourceBoard).order_by(SourceBoard.id)).scalars()]


@router.post("/boards")
def add_board(body: BoardIn, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    if db.get(Source, body.source_key) is None or body.source_key not in discovery.CONNECTORS:
        raise HTTPException(422, "unknown or unimplemented source")
    params: dict = {}
    token = body.board_token
    if not discovery.CONNECTORS[body.source_key].complete_listing:  # search-based: needs a saved search
        try:
            params = validate_dice_params(body.params or {})
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        token = token or re.sub(r"[^A-Za-z0-9_.-]+", "-", f"{params['keyword']}-{params.get('location', 'any')}")[:100].strip("-")
    elif body.params:
        raise HTTPException(422, "this source follows employer boards; params are not accepted")
    if not token or not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", token):
        raise HTTPException(422, "board token may contain only letters, digits, '.', '_' and '-'")
    if db.execute(select(SourceBoard).where(SourceBoard.source_key == body.source_key,
                                            SourceBoard.board_token == token)).scalar():
        raise HTTPException(409, "board already added")
    b = SourceBoard(source_key=body.source_key, board_token=token, employer_name=body.employer_name, params=params)
    db.add(b)
    db.flush()
    audit.record(db, actor(user), "board.added", "source_board", b.id, {"source": b.source_key, "board": b.board_token})
    db.commit()
    return board_out(b)


class BoardPatch(BaseModel):
    enabled: bool


@router.patch("/boards/{bid}")
def patch_board(bid: int, body: BoardPatch, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    b = db.get(SourceBoard, bid)
    if b is None:
        raise HTTPException(404)
    b.enabled = body.enabled
    if body.enabled:
        b.consecutive_failures, b.next_attempt_at = 0, None
    audit.record(db, actor(user), "board.updated", "source_board", b.id, {"enabled": body.enabled})
    db.commit()
    return board_out(b)


@router.delete("/boards/{bid}")
def delete_board(bid: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    b = db.get(SourceBoard, bid)
    if b:
        audit.record(db, actor(user), "board.removed", "source_board", b.id, {"source": b.source_key, "board": b.board_token})
        db.delete(b)
        db.commit()
    return Response(status_code=204)


@router.get("/health/connectors")
def health(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return discovery.connector_health(db)


# ------------------------------------------------------------------ pipeline triggers
@router.post("/pipeline/poll")
def poll_now(db: Session = Depends(get_db), user: User = Depends(require_admin)):
    runs = discovery.poll_all(db)
    audit.record(db, actor(user), "pipeline.poll_triggered", details={"runs": len(runs)})
    db.commit()
    return [{"source": r.source_key, "board": r.board_token, "status": r.status, "seen": r.jobs_seen, "new": r.jobs_new,
             "expired": r.jobs_expired, "error": r.error} for r in runs]


@router.post("/pipeline/match")
def match_now(db: Session = Depends(get_db), user: User = Depends(require_admin)):
    return pipeline.match_jobs(db, get_profile(db), actor(user))


# ------------------------------------------------------------------ jobs & applications
def job_out(j: Job) -> dict:
    return {"id": j.id, "source": j.source_key, "board": j.board_token, "external_id": j.external_id, "employer": j.employer,
            "title": j.title, "location": j.location, "work_arrangement": j.work_arrangement,
            "employment_type": j.employment_type, "salary_min": j.salary_min, "salary_max": j.salary_max,
            "salary_currency": j.salary_currency, "posted_at": j.posted_at, "first_seen_at": j.first_seen_at,
            "last_seen_at": j.last_seen_at, "expired_at": j.expired_at, "expires_at": j.expires_at, "duplicate_of_id": j.duplicate_of_id,
            "canonical_url": j.canonical_url, "apply_url": j.apply_url, "permission": j.permission,
            "meta": j.meta or {}, "site": (j.meta or {}).get("site") or j.source_key}


def app_summary(a: Application) -> dict:
    return {"id": a.id, "state": a.state, "score": a.score, "job": job_out(a.job), "updated_at": a.updated_at,
            "unmet": (a.match or {}).get("unmet", [])[:5], "exclusions": (a.match or {}).get("exclusions", [])}


@router.get("/applications")
def list_applications(state: str | None = None, min_score: float | None = None, limit: int = 200,
                      db: Session = Depends(get_db), user: User = Depends(current_user)):
    p = get_profile(db)
    q = select(Application).where(Application.profile_id == p.id)
    if state:
        q = q.where(Application.state.in_(state.split(",")))
    if min_score is not None:
        q = q.where(Application.score >= min_score)
    q = q.order_by(Application.score.desc().nullslast(), Application.id.desc()).limit(min(limit, 1000))
    return [app_summary(a) for a in db.execute(q).scalars()]


def _app(db: Session, app_id: int) -> Application:
    p = get_profile(db)
    a = db.get(Application, app_id)
    if a is None or a.profile_id != p.id:
        raise HTTPException(404)
    return a


@router.get("/applications/{app_id}")
def application_detail(app_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    a = _app(db, app_id)
    p = get_profile(db)
    packet = policy.latest_packet(db, a.id)
    std = db.execute(select(StandardAnswer).where(StandardAnswer.profile_id == p.id)).scalars().all()
    resolved = ans.resolve(packet.answers, p, std) if packet else []
    for r in resolved:  # sensitive values are masked in the API. The user sees them in the Answers screen.
        if r.get("sensitive") and r.get("answer"):
            r["answer"] = "•••••• (approved sensitive answer)"
    decision = policy.evaluate(db, a)
    return {
        **app_summary(a), "match": a.match, "description": a.job.description, "requirements": a.job.requirements,
        "packet": None if packet is None else {
            "id": packet.id, "version": packet.version, "approved_at": packet.approved_at,
            "resume_lines": packet.resume_lines, "cover_letter": packet.cover_letter, "answers": resolved,
            "unsupported_count": packet.unsupported_count, "generation": packet.generation or {}},
        "handoffs": [{"id": h.id, "reasons": h.reasons, "status": h.status, "created_at": h.created_at}
                     for h in db.execute(select(HandoffTask).where(HandoffTask.application_id == a.id)).scalars()],
        "attempts": [{"id": s.id, "adapter": s.adapter, "status": s.status, "confirmation_id": s.confirmation_id,
                      "confirmation_url": s.confirmation_url, "error": s.error, "started_at": s.started_at,
                      "finished_at": s.finished_at}
                     for s in db.execute(select(SubmissionAttempt).where(SubmissionAttempt.application_id == a.id)).scalars()],
        "auto_submit_policy": {"allowed": decision.allowed, "reasons": decision.reasons, "checks": decision.checks},
        "prior_applications": policy.prior_applications(db, a.profile_id, a.job),
        "source_attribution": (db.get(Source, a.job.source_key).registry_entry or {}).get("display_notice") if db.get(Source, a.job.source_key) else None,
        "notes": a.notes,
    }


def _wrap(fn):  # noqa: ANN001
    try:
        return fn()
    except (ValueError, InvalidTransition) as e:
        raise HTTPException(409, str(e)) from e


@router.post("/applications/{app_id}/prepare")
def prepare(app_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    a = _app(db, app_id)
    pk = _wrap(lambda: pipeline.prepare(db, a, actor(user)))
    return {"packet_id": pk.id, "state": a.state, "unsupported_count": pk.unsupported_count}


class AnswerMap(BaseModel):
    answer_id: int | None


@router.post("/applications/{app_id}/answers/{index}")
def map_answer(app_id: int, index: int, body: AnswerMap, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    a = _app(db, app_id)
    pk = _wrap(lambda: pipeline.set_packet_answer(db, a, index, body.answer_id, actor(user)))
    return {"unsupported_count": pk.unsupported_count}


class ApproveIn(BaseModel):
    answer_on_site: bool = False


@router.post("/applications/{app_id}/approve")
def approve(app_id: int, body: ApproveIn, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    a = _app(db, app_id)
    _wrap(lambda: pipeline.approve(db, a, actor(user), body.answer_on_site))
    return {"state": a.state}


@router.post("/applications/{app_id}/submit")
def submit(app_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    """Runs the full deterministic gate. If anything fails, a handoff task is created instead."""
    a = _app(db, app_id)
    return pipeline.try_submit(db, a, actor(user))


class ManualSubmission(BaseModel):
    confirmation_id: str | None = Field(default=None, max_length=200)
    confirmation_url: str | None = Field(default=None, max_length=600)


@router.post("/applications/{app_id}/manual-submission")
def manual_submission(app_id: int, body: ManualSubmission, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    a = _app(db, app_id)
    att = _wrap(lambda: pipeline.record_manual_submission(db, a, actor(user), body.confirmation_id, body.confirmation_url))
    return {"state": a.state, "attempt_id": att.id}


class StateIn(BaseModel):
    reason: str = Field(default="", max_length=300)


def _simple_transition(app_id: int, dst: str, body: StateIn, db: Session, user: User) -> dict:
    a = _app(db, app_id)
    _wrap(lambda: transition(db, a, dst, actor(user), body.reason))
    if dst == "CONFIRMED":
        att = db.execute(select(SubmissionAttempt).where(SubmissionAttempt.application_id == a.id)).scalar()
        if att and att.status == "SUBMITTED":
            att.status = "CONFIRMED"
    db.commit()
    return {"state": a.state}


@router.post("/applications/{app_id}/reject")
def reject(app_id: int, body: StateIn, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    return _simple_transition(app_id, "REJECTED_BY_USER", body, db, user)


@router.post("/applications/{app_id}/restore")
def restore(app_id: int, body: StateIn, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    return _simple_transition(app_id, "DISCOVERED", body, db, user)


@router.post("/applications/{app_id}/retry")
def retry(app_id: int, body: StateIn, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    """FAILED -> APPROVED. Safe: the idempotency key still blocks any attempt whose outcome is UNKNOWN or SUBMITTED."""
    return _simple_transition(app_id, "APPROVED", body, db, user)


@router.post("/applications/{app_id}/reopen")
def reopen(app_id: int, body: StateIn, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    """FAILED/APPROVED -> NEEDS_REVIEW, so the packet can be re-prepared."""
    return _simple_transition(app_id, "NEEDS_REVIEW", body, db, user)


@router.post("/applications/{app_id}/confirm")
def confirm(app_id: int, body: StateIn, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    return _simple_transition(app_id, "CONFIRMED", body, db, user)


@router.post("/applications/{app_id}/follow-up")
def follow_up(app_id: int, body: StateIn, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    return _simple_transition(app_id, "FOLLOW_UP", body, db, user)


@router.get("/applications/{app_id}/resume.docx")
def resume_docx(app_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    a = _app(db, app_id)
    packet = policy.latest_packet(db, a.id)
    if not packet or not packet.resume_document_id:
        raise HTTPException(404)
    doc = db.get(Document, packet.resume_document_id)
    return Response(EncryptedFileStore().get(doc.store_path), media_type=doc.mime,
                    headers={"Content-Disposition": f'attachment; filename="resume_{a.id}.docx"'})


@router.get("/applications/{app_id}/cover-letter.txt")
def cover_letter(app_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    a = _app(db, app_id)
    packet = policy.latest_packet(db, a.id)
    if not packet:
        raise HTTPException(404)
    return Response(render_cover_letter_text(packet.cover_letter), media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="cover_letter_{a.id}.txt"'})


@router.get("/handoffs")
def list_handoffs(status: str = "OPEN", db: Session = Depends(get_db), user: User = Depends(current_user)):
    p = get_profile(db)
    q = (select(HandoffTask).join(Application, Application.id == HandoffTask.application_id)
         .where(Application.profile_id == p.id, HandoffTask.status == status).order_by(HandoffTask.id.desc()))
    out = []
    for h in db.execute(q).scalars():
        a = db.get(Application, h.application_id)
        out.append({"id": h.id, "application_id": a.id, "state": a.state, "reasons": h.reasons, "created_at": h.created_at,
                    "employer": a.job.employer, "title": a.job.title, "apply_url": a.job.apply_url})
    return out


@router.post("/handoffs/{hid}/dismiss")
def dismiss_handoff(hid: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    h = db.get(HandoffTask, hid)
    if h is None:
        raise HTTPException(404)
    h.status = "DISMISSED"
    audit.record(db, actor(user), "handoff.dismissed", "application", h.application_id)
    db.commit()
    return {"id": h.id, "status": h.status}


# ------------------------------------------------------------------ user imports (no scraping)
@router.post("/imports/alert-emails")
async def import_alert_emails(file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(require_admin)):
    """Upload job-alert emails (.eml, .mbox, or .zip of .eml) exported from your own mailbox."""
    data = await file.read(email_alerts.MAX_BYTES + 1)
    if len(data) > email_alerts.MAX_BYTES:
        raise HTTPException(413, "file too large (max 50 MB)")
    try:
        res = imports.import_alert_emails(db, file.filename or "alerts.eml", data, actor(user))
    except (email_alerts.AlertImportError, SourceNotPermitted) as e:
        raise HTTPException(422, str(e)) from e
    res["matched"] = pipeline.match_jobs(db, get_profile(db), actor(user))
    return res


class ManualJobIn(BaseModel):
    url: str = Field(min_length=9, max_length=600)
    title: str = Field(min_length=1, max_length=300)
    employer: str = Field(min_length=1, max_length=200)
    location: str | None = Field(default=None, max_length=200)
    salary: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=60_000)


@router.post("/jobs/manual")
def add_manual_job(body: ManualJobIn, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    """Add a listing you are viewing on any site (LinkedIn, Indeed, ZipRecruiter, Dice, Ladders, ...)."""
    try:
        job, suggestion = imports.add_manual_job(db, body.url, body.title, body.employer, actor(user),
                                                 body.location, body.description, body.salary)
    except (ValueError, SourceNotPermitted) as e:
        raise HTTPException(422, str(e)) from e
    pipeline.match_jobs(db, get_profile(db), actor(user))
    app = db.execute(select(Application).where(Application.job_id == job.id)).scalar()
    return {"job": job_out(job), "application_id": app.id if app else None, "suggested_board": suggestion}


class DescriptionIn(BaseModel):
    description: str = Field(min_length=20, max_length=60_000)


@router.put("/jobs/{job_id}/description")
def set_description(job_id: int, body: DescriptionIn, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404)
    try:
        imports.set_description(db, job, body.description, actor(user))
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    pipeline.match_jobs(db, get_profile(db), actor(user))
    return job_out(job)


@router.post("/jobs/{job_id}/fetch-details")
def fetch_details(job_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    """User-initiated: fetch one Dice job's full description via Dice's official MCP server."""
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404)
    try:
        imports.fetch_dice_details(db, job, actor(user))
    except (ValueError, SourceNotPermitted) as e:
        raise HTTPException(409, str(e)) from e
    except ConnectorError as e:
        raise HTTPException(502, f"Dice: {e}") from e
    pipeline.match_jobs(db, get_profile(db), actor(user))
    return job_out(job)
