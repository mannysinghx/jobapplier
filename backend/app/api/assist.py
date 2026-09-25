"""Apply Assistant: everything needed to apply BY HAND on the employer's site, in one compact payload.

The assistant never touches the job site: the user opens it in their own browser and pastes prepared values.
Sensitive values are masked and only revealed on an explicit, audited request.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit, policy
from ..db import get_db
from ..models import Application, HandoffTask, Source, StandardAnswer, User
from ..prep import answers as ans
from ..prep.packet import render_cover_letter_text
from ..security.auth import actor, current_user, require_admin
from .jobs import job_out
from .profile import get_profile

router = APIRouter()
MASK = "••••••"
SELF_APPLY_SITES = {"linkedin": "LinkedIn", "indeed": "Indeed", "ziprecruiter": "ZipRecruiter", "dice": "Dice",
                    "ladders": "Ladders"}
READY_STATES = ("APPROVED",)
NEEDS_APPROVAL_STATES = ("PREPARED", "NEEDS_REVIEW")
LABELS = {"work_authorization": "Work authorization", "sponsorship": "Visa sponsorship", "salary_expectation": "Salary expectation",
          "start_date": "Start date / notice period", "relocation": "Open to relocation", "how_heard": "How did you hear about us",
          "remote_preference": "Remote / on-site preference", "criminal_history": "Criminal history", "disability": "Disability",
          "veteran": "Veteran status", "gender": "Gender", "race_ethnicity": "Race / ethnicity", "pronouns": "Pronouns"}


def _label(key: str) -> str:
    return LABELS.get(key, key.replace("_", " ").capitalize())


def _queue(db: Session, profile_id: int) -> list[Application]:
    rows = db.execute(select(Application).where(Application.profile_id == profile_id,
                                                Application.state.in_(READY_STATES + NEEDS_APPROVAL_STATES))).scalars().all()
    ready = sorted([a for a in rows if a.state in READY_STATES], key=lambda a: -(a.score or 0))
    rest = sorted([a for a in rows if a.state not in READY_STATES], key=lambda a: -(a.score or 0))
    return [a for a in ready + rest if a.job.expired_at is None]


@router.get("/assist/queue")
def assist_queue(db: Session = Depends(get_db), user: User = Depends(current_user)):
    p = get_profile(db)
    out = []
    for a in _queue(db, p.id)[:200]:
        pk = policy.latest_packet(db, a.id)
        handoff = db.execute(select(HandoffTask.id).where(HandoffTask.application_id == a.id, HandoffTask.status == "OPEN")).first()
        out.append({"application_id": a.id, "state": a.state, "score": a.score, "title": a.job.title, "employer": a.job.employer,
                    "site": (a.job.meta or {}).get("site") or a.job.source_key, "apply_url": a.job.apply_url,
                    "ready": a.state in READY_STATES, "handoff_open": handoff is not None,
                    "open_items": sum(1 for x in (pk.answers if pk else []) if ans.is_blocking(x))})
    return out


@router.get("/applications/{app_id}/assist")
def assist(app_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    p = get_profile(db)
    a = db.get(Application, app_id)
    if a is None or a.profile_id != p.id:
        raise HTTPException(404)
    job = a.job
    site = (job.meta or {}).get("site") or job.source_key
    pk = policy.latest_packet(db, a.id)
    std = db.execute(select(StandardAnswer).where(StandardAnswer.profile_id == p.id)).scalars().all()

    fields = [
        {"key": "first_name", "label": "First name", "value": p.first_name},
        {"key": "last_name", "label": "Last name", "value": p.last_name},
        {"key": "full_name", "label": "Full name", "value": f"{p.first_name} {p.last_name}".strip()},
        {"key": "email", "label": "Email", "value": p.email},
        {"key": "phone", "label": "Phone", "value": p.phone},
        {"key": "location", "label": "Location", "value": p.location},
        {"key": "linkedin", "label": "LinkedIn", "value": p.linkedin_url},
    ] + [{"key": f"portfolio_{i}", "label": "Portfolio / website", "value": u} for i, u in enumerate(p.portfolio_links or [])]
    fields = [f for f in fields if f["value"]]

    answers, used_ids = [], set()
    if pk:
        by_id = {s.id: s for s in std}
        for i, e in enumerate(ans.resolve(pk.answers, p, std)):
            if e["key"] == "review_note":
                continue
            src = e.get("source") or {}
            aid = src.get("answer_id")
            if aid:
                used_ids.add(aid)
            sensitive = bool(e.get("sensitive")) or bool(aid and by_id.get(aid) and by_id[aid].sensitive)
            has_value = e.get("answer") is not None and "packet_file" not in src
            answers.append({"index": i, "question": e["question"], "key": e["key"], "status": e["status"],
                            "required": e["required"], "sensitive": sensitive, "note": e.get("note") or "",
                            "file": src.get("packet_file"), "answer_id": aid,
                            "value": (MASK if sensitive else e.get("answer")) if has_value else None})
    other = [{"answer_id": s.id, "key": s.question_key, "label": _label(s.question_key), "sensitive": s.sensitive,
              "value": MASK if s.sensitive else s.answer}
             for s in std if s.approved and s.id not in used_ids]
    open_items = [{"question": x["question"], "key": x["key"], "status": x["status"], "note": x.get("note", "")}
                  for x in (pk.answers if pk else []) if ans.is_blocking(x)]
    queue = [q.id for q in _queue(db, p.id)]
    nxt = queue[queue.index(a.id) + 1] if a.id in queue and queue.index(a.id) + 1 < len(queue) else (
        queue[0] if queue and queue[0] != a.id else None)
    src_row = db.get(Source, job.source_key)
    return {
        "application": {"id": a.id, "state": a.state, "score": a.score, "notes": a.notes},
        "job": job_out(job),
        "site": site,
        "self_apply_note": (f"Apply on {SELF_APPLY_SITES[site]} yourself: its terms do not allow automated applications. "
                            "Paste the prepared values below.") if site in SELF_APPLY_SITES else None,
        "source_attribution": (src_row.registry_entry or {}).get("display_notice") if src_row else None,
        "prior_applications": policy.prior_applications(db, p.id, job),
        "ready": a.state in READY_STATES,
        "can_approve": a.state in NEEDS_APPROVAL_STATES and pk is not None,
        "can_prepare": a.state == "MATCHED",
        "packet": None if pk is None else {"id": pk.id, "version": pk.version, "approved_at": pk.approved_at},
        "fields": fields,
        "answers": answers,
        "other_answers": other,
        "cover_letter_text": render_cover_letter_text(pk.cover_letter) if pk else None,
        "downloads": {"resume_docx": f"/api/applications/{a.id}/resume.docx",
                      "cover_letter_txt": f"/api/applications/{a.id}/cover-letter.txt"} if pk else None,
        "open_items": open_items,
        "next_application_id": nxt,
        "queue_position": (queue.index(a.id) + 1) if a.id in queue else None,
        "queue_length": len(queue),
    }


class RevealIn(BaseModel):
    application_id: int
    answer_id: int


@router.post("/assist/reveal")
def reveal(body: RevealIn, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    """Returns one approved answer's value for copying. Audited (key only, never the value)."""
    p = get_profile(db)
    a = db.get(Application, body.application_id)
    s = db.get(StandardAnswer, body.answer_id)
    if a is None or a.profile_id != p.id or s is None or s.profile_id != p.id:
        raise HTTPException(404)
    if not s.approved:
        raise HTTPException(409, "only approved answers can be used")
    audit.record(db, actor(user), "assist.answer_revealed", "application", a.id,
                 {"key": s.question_key, "sensitive": s.sensitive})
    db.commit()
    return {"answer_id": s.id, "key": s.question_key, "value": s.answer}
