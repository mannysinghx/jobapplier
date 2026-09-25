"""Application pipeline: match -> prepare -> (review/approve) -> submit or hand off."""
import hashlib
import json

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from . import audit, controls, policy
from .connectors.base import sanitize_short
from .db import utcnow
from .matching import score_job
from .models import (
    Application,
    Document,
    Fact,
    FactConflict,
    HandoffTask,
    Job,
    Packet,
    Preferences,
    Profile,
    Source,
    StandardAnswer,
    SubmissionAttempt,
)
from .llm import cover_letter as llm_cl
from .llm.ollama import LLMUnavailable, OllamaClient
from .llm.settings import get_llm_config
from .prep import answers as ans
from .prep import packet as pk
from .security.crypto import EncryptedFileStore
from .statemachine import RESCORABLE, USER_OWNED, transition
from .submission.base import ADAPTERS, SubmissionPayload


def idempotency_key(profile_id: int, job: Job) -> str:
    return hashlib.sha256(f"{profile_id}|{job.source_key}|{job.external_id}".encode()).hexdigest()


def _facts(db: Session, profile_id: int) -> list[Fact]:
    return db.execute(select(Fact).where(Fact.profile_id == profile_id)).scalars().all()


# ------------------------------------------------------------------ matching
def match_jobs(db: Session, profile: Profile, actor: str = "worker") -> dict:
    prefs = db.execute(select(Preferences).where(Preferences.profile_id == profile.id)).scalar()
    if prefs is None:
        return {"matched": 0, "reason": "no preferences"}
    facts = _facts(db, profile.id)
    counts = {"new": 0, "matched": 0, "blocked": 0, "expired": 0, "duplicate": 0}
    for job in db.execute(select(Job)).scalars():
        app = db.execute(select(Application).where(Application.profile_id == profile.id, Application.job_id == job.id)).scalar()
        if app is None:
            if job.expired_at is not None:
                continue  # never create applications for already-expired listings
            app = Application(profile_id=profile.id, job_id=job.id, idempotency_key=idempotency_key(profile.id, job))
            db.add(app)
            db.flush()
            audit.record(db, actor, "application.discovered", "application", app.id, {"job_id": job.id})
            counts["new"] += 1
        if app.state not in RESCORABLE:
            continue
        m = score_job(job, prefs, facts)
        app.score, app.match = m.score, m.as_dict()
        if job.duplicate_of_id is not None:
            transition(db, app, "DUPLICATE", actor, f"duplicate of job {job.duplicate_of_id}")
            counts["duplicate"] += 1
        elif job.expired_at is not None:
            transition(db, app, "EXPIRED", actor, "listing expired")
            counts["expired"] += 1
        elif m.exclusions:
            if app.state != "BLOCKED_BY_POLICY":
                transition(db, app, "BLOCKED_BY_POLICY", actor, "hard exclusion", exclusions=m.exclusions[:5])
            counts["blocked"] += 1
        elif m.score >= prefs.min_score:
            if app.state == "BLOCKED_BY_POLICY":
                transition(db, app, "DISCOVERED", actor, "exclusion cleared")
            if app.state != "MATCHED":
                transition(db, app, "MATCHED", actor, "score meets threshold", score=m.score)
            counts["matched"] += 1
        else:
            if app.state in ("MATCHED", "BLOCKED_BY_POLICY"):
                transition(db, app, "DISCOVERED", actor, "below threshold", score=m.score)
    db.commit()
    return counts


# ------------------------------------------------------------------ preparation
def _maybe_llm_letter(db: Session, app: Application, facts: list[Fact], template_letter: list[dict],
                      matched_skill_ids: dict, client=None) -> tuple[list[dict], dict]:  # noqa: ANN001
    """Replace the template letter body with verified local-LLM sentences when enabled. Any failure -> template."""
    cfg = get_llm_config(db)
    if not cfg["enabled"]:
        return template_letter, {"generator": "template"}
    title, employer, _ = pk.safe_job_fields(app.job)
    try:
        client = client or OllamaClient()
        body, info = llm_cl.draft(client, cfg["model"], title, employer, sorted(matched_skill_ids), facts)
    except LLMUnavailable as e:
        return template_letter, {"generator": "template", "fallback_reason": str(e)[:300]}
    if len(body) < llm_cl.MIN_SENTENCES:
        return template_letter, {**info, "generator": "template",
                                 "fallback_reason": f"only {len(body)} sentence(s) passed verification"}
    # Keep the template's greeting, opening line (job fields only) and closing/signature. Swap the body.
    head = [u for u in template_letter[:2]]
    tail = [u for u in template_letter if u.get("template") and u["text"].startswith(("Thank you", "Sincerely"))] + \
           [template_letter[-1]]
    return head + body + tail, info


def prepare(db: Session, app: Application, actor: str, questions: list[dict] | None = None, llm_client=None) -> Packet:  # noqa: ANN001
    if app.state not in ("MATCHED", "PREPARED", "NEEDS_REVIEW"):
        raise ValueError(f"cannot prepare from state {app.state}")
    profile = db.get(Profile, app.profile_id)
    facts = _facts(db, profile.id)
    approved_ids = {f.id for f in facts if f.status == "APPROVED"}
    std = db.execute(select(StandardAnswer).where(StandardAnswer.profile_id == profile.id)).scalars().all()
    match = app.match or {}
    matched_skill_ids = {k: v for k, v in (match.get("matched_skill_fact_ids") or {}).items()}

    resume_lines = pk.build_resume_lines(profile, facts, list(matched_skill_ids))
    letter, flags = pk.build_cover_letter(profile, app.job, facts, matched_skill_ids)
    letter, letter_info = _maybe_llm_letter(db, app, facts, letter, matched_skill_ids, llm_client)
    problems = pk.verify_provenance(resume_lines, approved_ids) + pk.verify_provenance(letter, approved_ids)
    if problems:  # construction bug guard: never persist unverifiable material
        audit.record(db, actor, "packet.provenance_failed", "application", app.id, {"problems": problems[:5]})
        db.commit()
        raise RuntimeError("provenance verification failed: " + "; ".join(problems[:3]))

    drafted = ans.draft_answers(questions or ans.DEFAULT_QUESTIONS, profile, std)
    open_conflicts = db.execute(select(FactConflict.id).where(FactConflict.profile_id == profile.id,
                                                              FactConflict.status == "OPEN")).first() is not None
    resume_bytes = pk.render_docx(resume_lines)
    rel, digest = EncryptedFileStore().put(resume_bytes, "generated")
    doc = db.execute(select(Document).where(Document.profile_id == profile.id, Document.sha256 == digest)).scalar()
    if doc is None:
        doc = Document(profile_id=profile.id, kind="generated_resume", filename=f"resume_app{app.id}.docx",
                       mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                       sha256=digest, store_path=rel, size_bytes=len(resume_bytes), parse_status="parsed")
        db.add(doc)
        db.flush()
    prev = policy.latest_packet(db, app.id)
    blocking = [a for a in drafted if ans.is_blocking(a)]
    review_notes = list(flags)
    if open_conflicts:
        review_notes.append("open resume/LinkedIn/profile conflicts must be resolved")
    if not approved_ids:
        review_notes.append("no approved facts; approve your fact profile first")
    packet = Packet(application_id=app.id, version=(prev.version + 1) if prev else 1, resume_document_id=doc.id,
                    resume_lines=resume_lines, cover_letter=letter,
                    answers=drafted + [{"question": n, "key": "review_note", "required": True, "sensitive": False,
                                        "status": "NEEDS_REVIEW", "source": None, "note": n} for n in review_notes],
                    unsupported_count=len(blocking) + len(review_notes), generation={"cover_letter": letter_info})
    db.add(packet)
    db.flush()
    audit.record(db, actor, "packet.generated", "application", app.id,
                 {"packet_id": packet.id, "version": packet.version, "resume_sha256": digest,
                  "unsupported": packet.unsupported_count, "letter_generator": letter_info.get("generator"),
                  "letter_dropped": len(letter_info.get("dropped", []))})
    if app.state != "PREPARED":
        transition(db, app, "PREPARED", actor, "packet generated")
    if packet.unsupported_count:
        transition(db, app, "NEEDS_REVIEW", actor, "unsupported answers or review notes",
                   keys=sorted({a["key"] for a in blocking})[:10])
    db.commit()
    return packet


def set_packet_answer(db: Session, app: Application, index: int, answer_id: int | None, actor: str) -> Packet:
    """User maps a packet question to one of their APPROVED standard answers. Attestations cannot be pre-answered."""
    packet = policy.latest_packet(db, app.id)
    if packet is None or packet.approved_at:
        raise ValueError("no editable packet")
    entries = [dict(a) for a in packet.answers]
    e = entries[index]
    if e["status"] == "ATTESTATION" or e["key"] == "review_note":
        raise ValueError("attestations and review notes must be handled by the user on the site / by fixing the cause")
    if answer_id is None:
        e.update(status="NEEDS_REVIEW", source=None)
    else:
        sa = db.get(StandardAnswer, answer_id)
        if sa is None or sa.profile_id != app.profile_id or not sa.approved:
            raise ValueError("answer must be one of your approved standard answers")
        e.update(status="USER_APPROVED", source={"answer_id": sa.id}, sensitive=sa.sensitive or e["sensitive"])
    packet.answers = entries
    packet.unsupported_count = sum(1 for a in entries if ans.is_blocking(a))
    audit.record(db, actor, "packet.answer_set", "application", app.id, {"index": index, "answer_id": answer_id})
    db.commit()
    return packet


def approve(db: Session, app: Application, actor: str, answer_on_site: bool = False) -> Packet:
    """Approve the packet. Open items (sensitive/unsupported/attestation/review notes) either block approval, or,
    with answer_on_site=True, are marked ON_SITE: the user answers them in the employer's form. ON_SITE items
    still block auto-submit, so the application goes to handoff."""
    packet = policy.latest_packet(db, app.id)
    if packet is None:
        raise ValueError("no packet to approve")
    if app.state not in ("PREPARED", "NEEDS_REVIEW"):
        raise ValueError(f"cannot approve from {app.state}")
    facts = _facts(db, app.profile_id)
    approved_ids = {f.id for f in facts if f.status == "APPROVED"}
    problems = pk.verify_provenance(packet.resume_lines, approved_ids) + pk.verify_provenance(packet.cover_letter, approved_ids)
    if problems:
        raise ValueError("packet cites facts that are no longer approved; re-prepare: " + problems[0])
    open_items = [a for a in packet.answers if ans.is_blocking(a) and a["status"] != "ON_SITE"]
    if open_items and not answer_on_site:
        raise ValueError("resolve open items first, or approve with answer_on_site: "
                         + ", ".join(sorted({a['key'] for a in open_items})))
    if open_items:
        packet.answers = [({**a, "status": "ON_SITE"} if ans.is_blocking(a) else a) for a in packet.answers]
    packet.approved_at = utcnow()
    transition(db, app, "APPROVED", actor, "user approved packet", packet_id=packet.id, on_site_items=len(open_items))
    db.commit()
    return packet


# ------------------------------------------------------------------ submission / handoff
def open_handoff(db: Session, app: Application, reasons: list[str], actor: str) -> HandoffTask:
    task = db.execute(select(HandoffTask).where(HandoffTask.application_id == app.id, HandoffTask.status == "OPEN")).scalar()
    if task:
        task.reasons = reasons
    else:
        task = HandoffTask(application_id=app.id, reasons=reasons)
        db.add(task)
        db.flush()
        audit.record(db, actor, "handoff.created", "application", app.id, {"reasons": [r[:120] for r in reasons[:10]]})
    db.commit()
    return task


def _snapshot(db: Session, app: Application, packet: Packet) -> tuple[str, list[dict], bytes, str]:
    profile = db.get(Profile, app.profile_id)
    std = db.execute(select(StandardAnswer).where(StandardAnswer.profile_id == profile.id)).scalars().all()
    resolved = ans.resolve(packet.answers, profile, std)
    doc = db.get(Document, packet.resume_document_id) if packet.resume_document_id else None
    resume = EncryptedFileStore().get(doc.store_path) if doc else b""
    letter = pk.render_cover_letter_text(packet.cover_letter)
    snap = json.dumps({"packet_id": packet.id, "packet_version": packet.version, "resume_sha256": doc.sha256 if doc else None,
                       "cover_letter": letter, "answers": resolved, "apply_url": app.job.apply_url, "at": utcnow().isoformat()})
    return snap, resolved, resume, letter


def _claim_attempt(db: Session, app: Application, source_key: str, adapter: str, packet: Packet, snap: str) -> SubmissionAttempt | None:
    """Idempotency: one attempt row per application key. Retry is allowed only from FAILED/CHALLENGE (definitely not sent)."""
    existing = db.execute(select(SubmissionAttempt).where(SubmissionAttempt.idempotency_key == app.idempotency_key)).scalar()
    if existing is None:
        att = SubmissionAttempt(application_id=app.id, idempotency_key=app.idempotency_key, source_key=source_key,
                                adapter=adapter, packet_id=packet.id, snapshot=snap)
        db.add(att)
        try:
            db.commit()
        except Exception:  # noqa: BLE001 - unique violation = someone else holds the key
            db.rollback()
            return None
        return att
    res = db.execute(update(SubmissionAttempt).where(
        SubmissionAttempt.id == existing.id, SubmissionAttempt.status.in_(["FAILED", "CHALLENGE"])).values(
        # snapshot: pass plaintext; the EncryptedString column type encrypts Core UPDATE values too
        status="PENDING", adapter=adapter, packet_id=packet.id, snapshot=snap, started_at=utcnow(),
        error=None, finished_at=None))
    db.commit()
    if res.rowcount != 1:
        return None
    db.refresh(existing)
    return existing


def try_submit(db: Session, app: Application, actor: str = "worker") -> dict:
    decision = policy.evaluate(db, app)
    if not decision.allowed:
        if app.state == "APPROVED":
            open_handoff(db, app, decision.reasons, actor)
        audit.record(db, actor, "submission.blocked", "application", app.id, {"reasons": [r[:120] for r in decision.reasons[:10]]})
        db.commit()
        return {"submitted": False, "reasons": decision.reasons, "checks": decision.checks}
    packet = policy.latest_packet(db, app.id)
    adapter = ADAPTERS[app.job.source_key]
    snap, resolved, resume, letter = _snapshot(db, app, packet)
    att = _claim_attempt(db, app, app.job.source_key, adapter.name, packet, snap)
    if att is None:
        audit.record(db, actor, "submission.duplicate_prevented", "application", app.id)
        db.commit()
        return {"submitted": False, "reasons": ["duplicate prevented by idempotency key"]}
    if controls.is_paused(db):  # last check before the irreversible step
        att.status, att.error, att.finished_at = "FAILED", "aborted: paused", utcnow()
        db.commit()
        return {"submitted": False, "reasons": ["paused before submission"]}
    audit.record(db, actor, "submission.started", "application", app.id, {"adapter": adapter.name})
    db.commit()
    try:
        result = adapter.submit(SubmissionPayload(app.id, app.idempotency_key, app.job.apply_url, resolved, resume, letter),
                                should_abort=lambda: controls.is_paused(db))
    except Exception as e:  # noqa: BLE001 - outcome unknown: never auto-retry
        result_outcome, err = "UNKNOWN", f"{type(e).__name__}: {str(e)[:200]}"
        att.status, att.error, att.finished_at = result_outcome, err, utcnow()
        transition(db, app, "FAILED", actor, "adapter exception; outcome unknown")
        db.commit()
        open_handoff(db, app, ["submission outcome unknown; check the employer site before retrying"], actor)
        return {"submitted": False, "reasons": [err]}
    att.finished_at = utcnow()
    if result.outcome == "SUBMITTED":
        att.status, att.confirmation_id, att.confirmation_url = "SUBMITTED", result.confirmation_id, result.confirmation_url
        transition(db, app, "SUBMITTED", actor, "adapter submitted", adapter=adapter.name)
        if result.confirmation_id or result.confirmation_url:
            att.status = "CONFIRMED"
            transition(db, app, "CONFIRMED", actor, "confirmation captured")
        db.commit()
        return {"submitted": True, "confirmation_id": result.confirmation_id}
    if result.outcome == "CHALLENGE":
        att.status, att.error = "CHALLENGE", f"challenge: {result.challenge}"
        transition(db, app, "NEEDS_REVIEW", actor, f"challenge encountered: {result.challenge}")
        db.commit()
        open_handoff(db, app, [f"site presented a {result.challenge} challenge; complete it yourself"], actor)
        return {"submitted": False, "reasons": [f"challenge: {result.challenge}"]}
    att.status = "UNKNOWN" if result.outcome == "UNKNOWN" else "FAILED"
    att.error = (result.error or "failed")[:500]
    transition(db, app, "FAILED", actor, att.error[:120])
    db.commit()
    open_handoff(db, app, [f"submission failed: {att.error[:200]}"], actor)
    return {"submitted": False, "reasons": [att.error]}


def record_manual_submission(db: Session, app: Application, actor: str, confirmation_id: str | None,
                             confirmation_url: str | None) -> SubmissionAttempt:
    """The user submitted through the employer site themselves (handoff). Records exactly what the packet contained."""
    if app.state != "APPROVED":
        raise ValueError("approve the packet before recording a submission")
    packet = policy.latest_packet(db, app.id)
    snap, *_ = _snapshot(db, app, packet)
    att = _claim_attempt(db, app, app.job.source_key, "manual", packet, snap)
    if att is None:
        raise ValueError("a submission is already recorded for this application")
    att.status = "SUBMITTED"
    att.confirmation_id = sanitize_short(confirmation_id, 200) or None
    att.confirmation_url = confirmation_url if confirmation_url and confirmation_url.startswith("https://") else None
    att.finished_at = utcnow()
    transition(db, app, "SUBMITTED", actor, "user submitted manually")
    if att.confirmation_id or att.confirmation_url:
        att.status = "CONFIRMED"
        transition(db, app, "CONFIRMED", actor, "confirmation recorded")
    for t in db.execute(select(HandoffTask).where(HandoffTask.application_id == app.id, HandoffTask.status == "OPEN")).scalars():
        t.status, t.closed_at = "DONE", utcnow()
    db.commit()
    return att


def auto_process(db: Session, profile: Profile) -> dict:
    """Worker entrypoint after polling: match, prepare matched, auto-approve only where the user opted in, then gate."""
    if controls.is_paused(db):
        return {"paused": True}
    counts = match_jobs(db, profile)
    prepared = submitted = handoffs = 0
    for app in db.execute(select(Application).where(Application.profile_id == profile.id,
                                                    Application.state == "MATCHED")).scalars().all():
        if controls.is_paused(db):
            break
        prepare(db, app, "worker")
        prepared += 1
        src = db.get(Source, app.job.source_key)
        # Auto-approval only for fully supported packets on sources where the user opted into auto-submit.
        if app.state == "PREPARED" and src and src.auto_submit_opt_in and src.submit_permitted:
            approve(db, app, "worker")
    for app in db.execute(select(Application).where(Application.profile_id == profile.id,
                                                    Application.state == "APPROVED")).scalars().all():
        if controls.is_paused(db):
            break
        src = db.get(Source, app.job.source_key)
        if src and src.auto_submit_opt_in:
            r = try_submit(db, app)
            submitted += int(r.get("submitted", False))
        else:
            handoffs += 1
            open_handoff(db, app, ["auto-submit not enabled for this source; submit via the handoff packet"], "worker")
    return {**counts, "prepared": prepared, "submitted": submitted, "handoffs": handoffs}


__all__ = ["USER_OWNED"]
