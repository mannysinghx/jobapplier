"""Profile, folder consent, documents, facts, conflicts, standard answers, preferences."""
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, EmailStr, Field, HttpUrl
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..config import get_settings
from ..db import get_db, utcnow
from ..ingestion import files, service
from ..matching import DEFAULT_WEIGHTS, SENIORITY_LEVELS
from ..models import Document, Fact, FactConflict, Preferences, Profile, StandardAnswer, User
from ..prep.answers import SENSITIVE_KEYS
from ..security.auth import actor, current_user, require_admin

router = APIRouter()


def get_profile(db: Session) -> Profile:
    p = db.execute(select(Profile).order_by(Profile.id).limit(1)).scalar()
    if p is None:
        raise HTTPException(404, "create your profile first")
    return p


# ------------------------------------------------------------------ profile
class ProfileIn(BaseModel):
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=40)
    location: str | None = Field(default=None, max_length=200)
    timezone: str = Field(default="UTC", max_length=64)
    linkedin_url: HttpUrl | None = None
    portfolio_links: list[HttpUrl] = Field(default_factory=list, max_length=10)


def profile_out(p: Profile) -> dict:
    return {"id": p.id, "first_name": p.first_name, "last_name": p.last_name, "email": p.email, "phone": p.phone,
            "location": p.location, "timezone": p.timezone, "linkedin_url": p.linkedin_url,
            "portfolio_links": p.portfolio_links, "resume_folder": p.resume_folder, "folder_consent_at": p.folder_consent_at}


@router.get("/profile")
def read_profile(db: Session = Depends(get_db), user: User = Depends(current_user)):
    p = db.execute(select(Profile).order_by(Profile.id).limit(1)).scalar()
    return profile_out(p) if p else None


@router.put("/profile")
def upsert_profile(body: ProfileIn, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    if body.linkedin_url and "linkedin.com" not in body.linkedin_url.host:
        raise HTTPException(422, "LinkedIn URL must be a linkedin.com profile URL")
    p = db.execute(select(Profile).order_by(Profile.id).limit(1)).scalar()
    created = p is None
    if created:
        p = Profile(first_name="", last_name="", email="")
        db.add(p)
    data = body.model_dump()
    data["linkedin_url"] = str(body.linkedin_url) if body.linkedin_url else None
    data["portfolio_links"] = [str(u) for u in body.portfolio_links]
    for k, v in data.items():
        setattr(p, k, v)
    db.flush()
    if created:
        db.add(Preferences(profile_id=p.id, weights=dict(DEFAULT_WEIGHTS)))
    audit.record(db, actor(user), "profile.created" if created else "profile.updated", "profile", p.id,
                 {"fields": sorted(k for k in data if k not in ("email", "phone"))})
    db.commit()
    return profile_out(p)


class FolderConsentIn(BaseModel):
    folder: str = Field(min_length=1, max_length=1000)
    consent: bool


@router.post("/profile/folder-consent")
def folder_consent(body: FolderConsentIn, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    if not body.consent:
        raise HTTPException(422, "explicit consent is required to read the folder")
    p = get_profile(db)
    try:
        root = files.resolve_consented_folder(body.folder)
    except files.IntakeError as e:
        raise HTTPException(422, str(e)) from e
    p.resume_folder, p.folder_consent_at = str(root), utcnow()
    audit.record(db, actor(user), "folder.consent_granted", "profile", p.id, {"access": "read-only, top-level pdf/docx"})
    db.commit()
    return profile_out(p)


@router.delete("/profile/folder-consent")
def revoke_folder(db: Session = Depends(get_db), user: User = Depends(require_admin)):
    p = get_profile(db)
    p.resume_folder, p.folder_consent_at = None, None
    audit.record(db, actor(user), "folder.consent_revoked", "profile", p.id)
    db.commit()
    return profile_out(p)


# ------------------------------------------------------------------ documents
def doc_out(d: Document) -> dict:
    return {"id": d.id, "kind": d.kind, "filename": d.filename, "sha256": d.sha256, "size_bytes": d.size_bytes,
            "version": d.version, "parse_status": d.parse_status, "parse_error": d.parse_error, "created_at": d.created_at}


@router.get("/documents")
def list_documents(db: Session = Depends(get_db), user: User = Depends(current_user)):
    p = get_profile(db)
    return [doc_out(d) for d in db.execute(select(Document).where(Document.profile_id == p.id)
                                           .order_by(Document.id.desc())).scalars()]


@router.get("/folder/files")
def folder_files(db: Session = Depends(get_db), user: User = Depends(current_user)):
    p = get_profile(db)
    if not p.resume_folder or not p.folder_consent_at:
        raise HTTPException(409, "no consented folder")
    try:
        return [f.__dict__ for f in files.list_folder(p.resume_folder)]
    except files.IntakeError as e:
        raise HTTPException(422, str(e)) from e


class ImportIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)


@router.post("/documents/import-from-folder")
def import_from_folder(body: ImportIn, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    p = get_profile(db)
    if not p.resume_folder or not p.folder_consent_at:
        raise HTTPException(409, "no consented folder")
    try:
        data = files.read_from_folder(p.resume_folder, body.name)
        d = service.ingest_resume(db, p, body.name, data, actor(user))
    except files.IntakeError as e:
        raise HTTPException(422, str(e)) from e
    return doc_out(d)


async def _read_upload(f: UploadFile, limit: int) -> bytes:
    data = await f.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, "file too large")
    return data


@router.post("/documents/upload")
async def upload(file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(require_admin)):
    p = get_profile(db)
    data = await _read_upload(file, get_settings().max_upload_bytes)
    try:
        d = service.ingest_resume(db, p, file.filename or "resume", data, actor(user))
    except files.IntakeError as e:
        raise HTTPException(422, str(e)) from e
    return doc_out(d)


@router.post("/documents/linkedin-export")
async def linkedin_export(file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(require_admin)):
    p = get_profile(db)
    data = await _read_upload(file, service.LINKEDIN_MAX_BYTES)
    try:
        return service.ingest_linkedin_export(db, p, file.filename or "linkedin.zip", data, actor(user))
    except files.IntakeError as e:
        raise HTTPException(422, str(e)) from e


@router.get("/documents/{doc_id}/text")
def document_text(doc_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    p = get_profile(db)
    d = db.get(Document, doc_id)
    if d is None or d.profile_id != p.id:
        raise HTTPException(404)
    return {"id": d.id, "text": service.document_text(d)}


# ------------------------------------------------------------------ facts
def fact_out(f: Fact, conflicted: set[int]) -> dict:
    return {"id": f.id, "kind": f.kind, "data": f.data, "parent_id": f.parent_id, "status": f.status, "origin": f.origin,
            "document_id": f.document_id, "char_start": f.char_start, "char_end": f.char_end, "snippet": f.snippet,
            "approved_at": f.approved_at, "has_open_conflict": f.id in conflicted}


def _conflicted_ids(db: Session, pid: int) -> set[int]:
    out = set()
    for c in db.execute(select(FactConflict).where(FactConflict.profile_id == pid, FactConflict.status == "OPEN")).scalars():
        out.add(c.fact_a_id)
        if c.fact_b_id:
            out.add(c.fact_b_id)
    return out


@router.get("/facts")
def list_facts(db: Session = Depends(get_db), user: User = Depends(current_user)):
    p = get_profile(db)
    conflicted = _conflicted_ids(db, p.id)
    return [fact_out(f, conflicted) for f in db.execute(select(Fact).where(Fact.profile_id == p.id).order_by(Fact.id)).scalars()]


FACT_FIELDS = {"role": {"title", "employer", "start", "end", "location"}, "achievement": {"text"}, "project": {"text"},
               "summary": {"text"}, "skill": {"name"}, "certification": {"name", "authority"},
               "education": {"degree", "institution", "year"}, "contact": {"field", "value"}}


class FactEdit(BaseModel):
    data: dict


class FactCreate(BaseModel):
    kind: Literal["role", "achievement", "education", "skill", "certification", "project", "summary"]
    data: dict
    parent_id: int | None = None


def _clean_fact_data(kind: str, data: dict) -> dict:
    allowed = FACT_FIELDS[kind]
    out = {}
    for k, v in data.items():
        if k in allowed and (v is None or isinstance(v, str)):
            out[k] = (v or "").strip()[:2000] or None
    return out


@router.patch("/facts/{fact_id}")
def edit_fact(fact_id: int, body: FactEdit, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    p = get_profile(db)
    f = db.get(Fact, fact_id)
    if f is None or f.profile_id != p.id:
        raise HTTPException(404)
    if f.status != "PENDING":
        raise HTTPException(409, "only pending facts can be edited; reject and re-add instead")
    f.data = {**f.data, **_clean_fact_data(f.kind, body.data), "edited_by_user": True}
    audit.record(db, actor(user), "fact.edited", "fact", f.id, {"fields": sorted(body.data)[:10]})
    db.commit()
    return fact_out(f, _conflicted_ids(db, p.id))


@router.post("/facts")
def add_fact(body: FactCreate, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    """User-entered facts: provenance is 'user'. They are still reviewed and approved explicitly."""
    p = get_profile(db)
    if body.parent_id is not None:
        parent = db.get(Fact, body.parent_id)
        if parent is None or parent.profile_id != p.id or parent.kind != "role":
            raise HTTPException(422, "parent must be one of your role facts")
    f = Fact(profile_id=p.id, kind=body.kind, data=_clean_fact_data(body.kind, body.data), parent_id=body.parent_id,
             origin="user", snippet="entered by user")
    db.add(f)
    db.flush()
    audit.record(db, actor(user), "fact.added", "fact", f.id, {"kind": f.kind})
    db.commit()
    return fact_out(f, set())


class FactDecision(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=500)
    decision: Literal["APPROVED", "REJECTED", "PENDING"]


@router.post("/facts/decide")
def decide_facts(body: FactDecision, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    p = get_profile(db)
    conflicted = _conflicted_ids(db, p.id)
    done, skipped = [], []
    for fid in body.ids:
        f = db.get(Fact, fid)
        if f is None or f.profile_id != p.id:
            skipped.append({"id": fid, "reason": "not found"})
            continue
        if body.decision == "APPROVED" and fid in conflicted:
            skipped.append({"id": fid, "reason": "open conflict must be resolved first"})
            continue
        if body.decision == "APPROVED" and f.parent_id:
            parent = db.get(Fact, f.parent_id)
            if parent and parent.status == "REJECTED":
                skipped.append({"id": fid, "reason": "parent role is rejected"})
                continue
        f.status = body.decision
        f.approved_at = utcnow() if body.decision == "APPROVED" else None
        done.append(fid)
    audit.record(db, actor(user), f"facts.{body.decision.lower()}", "profile", p.id, {"fact_ids": done[:200]})
    db.commit()
    return {"updated": done, "skipped": skipped}


@router.get("/conflicts")
def list_conflicts(db: Session = Depends(get_db), user: User = Depends(current_user)):
    p = get_profile(db)
    return [{"id": c.id, "fact_a_id": c.fact_a_id, "fact_b_id": c.fact_b_id, "field": c.field, "description": c.description,
             "status": c.status, "resolution": c.resolution}
            for c in db.execute(select(FactConflict).where(FactConflict.profile_id == p.id).order_by(FactConflict.id)).scalars()]


class ConflictResolve(BaseModel):
    keep: Literal["a", "b", "both", "neither"]
    note: str = Field(default="", max_length=500)


@router.post("/conflicts/{cid}/resolve")
def resolve_conflict(cid: int, body: ConflictResolve, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    p = get_profile(db)
    c = db.get(FactConflict, cid)
    if c is None or c.profile_id != p.id:
        raise HTTPException(404)
    a = db.get(Fact, c.fact_a_id)
    b = db.get(Fact, c.fact_b_id) if c.fact_b_id else None
    if body.keep in ("b", "neither") and a:
        a.status = "REJECTED"
    if body.keep in ("a", "neither") and b:
        b.status = "REJECTED"
    if body.keep == "b" and b is None:
        raise HTTPException(422, "this conflict has only one fact")
    c.status, c.resolution = "RESOLVED", f"keep={body.keep}; {body.note}"[:1000]
    audit.record(db, actor(user), "conflict.resolved", "conflict", c.id, {"keep": body.keep})
    db.commit()
    return {"id": c.id, "status": c.status}


# ------------------------------------------------------------------ standard answers
class AnswerIn(BaseModel):
    answer: str = Field(min_length=1, max_length=4000)
    approved: bool = False
    sensitive: bool | None = None


def answer_out(a: StandardAnswer, reveal: bool) -> dict:
    return {"id": a.id, "question_key": a.question_key, "sensitive": a.sensitive, "approved": a.approved,
            "answer": a.answer if (reveal or not a.sensitive) else "••••••", "updated_at": a.updated_at}


@router.get("/answers")
def list_answers(reveal: bool = False, db: Session = Depends(get_db), user: User = Depends(current_user)):
    p = get_profile(db)
    reveal = reveal and user.role == "admin"
    rows = db.execute(select(StandardAnswer).where(StandardAnswer.profile_id == p.id).order_by(StandardAnswer.question_key)).scalars()
    return {"answers": [answer_out(a, reveal) for a in rows], "sensitive_keys": sorted(SENSITIVE_KEYS)}


@router.put("/answers/{key}")
def put_answer(key: str, body: AnswerIn, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    if not key.replace("_", "").isalnum() or len(key) > 80:
        raise HTTPException(422, "invalid key")
    p = get_profile(db)
    a = db.execute(select(StandardAnswer).where(StandardAnswer.profile_id == p.id, StandardAnswer.question_key == key)).scalar()
    if a is None:
        a = StandardAnswer(profile_id=p.id, question_key=key)
        db.add(a)
    a.answer = body.answer
    a.sensitive = key in SENSITIVE_KEYS or bool(body.sensitive)  # cannot downgrade a sensitive key
    a.approved = body.approved
    db.flush()
    audit.record(db, actor(user), "answer.saved", "standard_answer", a.id,
                 {"key": key, "approved": a.approved, "sensitive": a.sensitive})
    db.commit()
    return answer_out(a, False)


@router.delete("/answers/{key}")
def delete_answer(key: str, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    p = get_profile(db)
    a = db.execute(select(StandardAnswer).where(StandardAnswer.profile_id == p.id, StandardAnswer.question_key == key)).scalar()
    if a:
        audit.record(db, actor(user), "answer.deleted", "standard_answer", a.id, {"key": key})
        db.delete(a)
        db.commit()
    return Response(status_code=204)


# ------------------------------------------------------------------ preferences
class PrefsIn(BaseModel):
    titles: list[str] = Field(default_factory=list, max_length=30)
    keywords: list[str] = Field(default_factory=list, max_length=100)
    seniority: list[str] = Field(default_factory=list)
    geographies: list[str] = Field(default_factory=list, max_length=30)
    work_arrangements: list[Literal["remote", "hybrid", "onsite"]] = Field(default_factory=lambda: ["remote", "hybrid", "onsite"])
    employment_types: list[Literal["full_time", "part_time", "contract", "internship", "temporary"]] = Field(
        default_factory=lambda: ["full_time"])
    salary_floor: int | None = Field(default=None, ge=0)
    salary_currency: str = Field(default="USD", min_length=3, max_length=3)
    industries: list[str] = Field(default_factory=list, max_length=30)
    excluded_employers: list[str] = Field(default_factory=list, max_length=200)
    excluded_terms: list[str] = Field(default_factory=list, max_length=200)
    min_score: int = Field(default=70, ge=0, le=100)
    polling_minutes: int = Field(default=60, ge=15, le=24 * 60)
    daily_application_limit: int = Field(default=10, ge=0, le=100)
    weights: dict[str, int] = Field(default_factory=lambda: dict(DEFAULT_WEIGHTS))


def prefs_out(pr: Preferences) -> dict:
    return {c.name: getattr(pr, c.name) for c in pr.__table__.columns if c.name not in ("id", "profile_id")}


@router.get("/preferences")
def read_prefs(db: Session = Depends(get_db), user: User = Depends(current_user)):
    p = get_profile(db)
    pr = db.execute(select(Preferences).where(Preferences.profile_id == p.id)).scalar()
    return {**prefs_out(pr), "seniority_levels": SENIORITY_LEVELS}


@router.put("/preferences")
def write_prefs(body: PrefsIn, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    p = get_profile(db)
    bad = [s for s in body.seniority if s not in SENIORITY_LEVELS]
    if bad:
        raise HTTPException(422, f"unknown seniority levels: {bad}")
    if set(body.weights) - set(DEFAULT_WEIGHTS) or any(v < 0 or v > 100 for v in body.weights.values()):
        raise HTTPException(422, f"weights keys must be within {sorted(DEFAULT_WEIGHTS)} and 0..100")
    pr = db.execute(select(Preferences).where(Preferences.profile_id == p.id)).scalar()
    for k, v in body.model_dump().items():
        if isinstance(v, list):
            v = [x.strip()[:200] for x in v if isinstance(x, str) and x.strip()]
        setattr(pr, k, v)
    audit.record(db, actor(user), "preferences.updated", "profile", p.id)
    db.commit()
    return prefs_out(pr)
