"""Ingestion orchestration: store (encrypted) -> scan -> isolated parse -> facts with provenance -> conflicts."""
import csv
import io
import re
import zipfile

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..models import Document, Fact, FactConflict, Profile
from ..security.crypto import EncryptedFileStore
from . import files
from .extract import ExtractedFact, extract_facts, normalize_point

LINKEDIN_MAX_BYTES = 50 * 1024 * 1024


def _norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _fact_signature(kind: str, data: dict) -> str:
    keys = {"role": ("title", "employer", "start"), "skill": ("name",), "education": ("degree", "institution"),
            "certification": ("name",), "contact": ("field", "value")}.get(kind, ("text",))
    return kind + "|" + "|".join(_norm(str(data.get(k, ""))) for k in keys)


def _existing_signatures(db: Session, profile_id: int) -> set[str]:
    rows = db.execute(select(Fact.kind, Fact.data).where(Fact.profile_id == profile_id, Fact.status != "REJECTED"))
    return {_fact_signature(k, d) for k, d in rows}


def _save_fact(db: Session, profile_id: int, ef: ExtractedFact, origin: str, doc_id: int | None,
               sigs: set[str], parent_id: int | None = None) -> Fact | None:
    sig = _fact_signature(ef.kind, ef.data)
    if sig in sigs and ef.kind != "achievement":
        return None
    sigs.add(sig)
    f = Fact(profile_id=profile_id, kind=ef.kind, data=ef.data, origin=origin, document_id=doc_id,
             char_start=ef.start, char_end=ef.end, snippet=(ef.snippet or "")[:1000], parent_id=parent_id)
    db.add(f)
    db.flush()
    for child in ef.children:
        _save_fact(db, profile_id, child, origin, doc_id, sigs, parent_id=f.id)
    return f


def ingest_resume(db: Session, profile: Profile, filename: str, data: bytes, actor: str) -> Document:
    kind, mime = files.validate(filename, data)
    files.av_scan(data)
    store = EncryptedFileStore()
    rel, digest = store.put(data, "resume")
    existing = db.execute(select(Document).where(Document.profile_id == profile.id, Document.sha256 == digest)).scalar()
    if existing:
        return existing
    version = (db.execute(select(func.max(Document.version)).where(
        Document.profile_id == profile.id, Document.kind == "resume")).scalar() or 0) + 1
    doc = Document(profile_id=profile.id, kind="resume", filename=filename[:255], mime=mime, sha256=digest,
                   store_path=rel, size_bytes=len(data), version=version)
    db.add(doc)
    db.flush()
    audit.record(db, actor, "document.ingested", "document", doc.id, {"sha256": digest, "version": version, "kind": kind})
    try:
        text = files.parse_isolated(kind, data)
    except files.IntakeError as e:
        doc.parse_status, doc.parse_error = "failed", str(e)[:500]
        audit.record(db, actor, "document.parse_failed", "document", doc.id, {"error": str(e)[:200]})
        db.commit()
        return doc
    doc.text_path, _ = store.put(text.encode(), "text")
    doc.parse_status = "parsed"
    sigs = _existing_signatures(db, profile.id)
    created = 0
    for ef in extract_facts(text):
        if _save_fact(db, profile.id, ef, "resume", doc.id, sigs):
            created += 1
    db.flush()
    _detect_contact_conflicts(db, profile)
    audit.record(db, actor, "facts.extracted", "document", doc.id, {"facts_created": created})
    db.commit()
    return doc


def document_text(doc: Document) -> str:
    if not doc.text_path:
        return ""
    return EncryptedFileStore().get(doc.text_path).decode()


# ------------------------------------------------------------------ LinkedIn export (user-downloaded ZIP)
def _read_csv(zf: zipfile.ZipFile, basename: str) -> list[dict]:
    for info in zf.infolist():
        if info.filename.rsplit("/", 1)[-1].lower() == basename.lower():
            if info.file_size > 10 * 1024 * 1024:
                raise files.IntakeError(f"{basename} too large")
            raw = zf.read(info).decode("utf-8-sig", errors="replace")
            # LinkedIn sometimes prefixes CSVs with "Notes:" lines before the header row
            lines = raw.splitlines()
            start = next((i for i, ln in enumerate(lines) if "," in ln and not ln.lower().startswith("notes")), 0)
            return list(csv.DictReader(io.StringIO("\n".join(lines[start:]))))
    return []


def ingest_linkedin_export(db: Session, profile: Profile, filename: str, data: bytes, actor: str) -> dict:
    if len(data) > LINKEDIN_MAX_BYTES or not data.startswith(b"PK\x03\x04"):
        raise files.IntakeError("expected a LinkedIn data export .zip")
    files.av_scan(data)
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise files.IntakeError("invalid zip") from e
    infos = zf.infolist()
    if len(infos) > 500 or sum(i.file_size for i in infos) > 200 * 1024 * 1024:
        raise files.IntakeError("archive too large")
    store = EncryptedFileStore()
    rel, digest = store.put(data, "linkedin")
    doc = Document(profile_id=profile.id, kind="linkedin_export", filename=filename[:255], mime="application/zip",
                   sha256=digest, store_path=rel, size_bytes=len(data), parse_status="parsed")
    dup = db.execute(select(Document).where(Document.profile_id == profile.id, Document.sha256 == digest)).scalar()
    if dup:
        return {"document_id": dup.id, "facts_created": 0, "conflicts": 0, "duplicate": True}
    db.add(doc)
    db.flush()
    sigs = _existing_signatures(db, profile.id)
    created = 0
    for row in _read_csv(zf, "Positions.csv"):
        ef = ExtractedFact("role", {
            "title": (row.get("Title") or "").strip(), "employer": (row.get("Company Name") or "").strip(),
            "start": normalize_point(row.get("Started On") or ""), "end": normalize_point(row.get("Finished On") or "") or "present",
            "location": (row.get("Location") or "").strip(),
        }, None, None, f"Positions.csv: {row.get('Title', '')} @ {row.get('Company Name', '')}")
        desc = (row.get("Description") or "").strip()
        if desc:
            ef.children = [ExtractedFact("achievement", {"text": d.strip(" •-")}, None, None, "Positions.csv description")
                           for d in re.split(r"\n+|•", desc) if len(d.strip(" •-")) > 3]
        if _save_fact(db, profile.id, ef, "linkedin_export", doc.id, sigs):
            created += 1
    for row in _read_csv(zf, "Skills.csv"):
        name = (row.get("Name") or "").strip()
        if name and _save_fact(db, profile.id, ExtractedFact("skill", {"name": name}, None, None, "Skills.csv"),
                               "linkedin_export", doc.id, sigs):
            created += 1
    for row in _read_csv(zf, "Education.csv"):
        ef = ExtractedFact("education", {"degree": (row.get("Degree Name") or "").strip(),
                                         "institution": (row.get("School Name") or "").strip(),
                                         "year": ((row.get("End Date") or "").strip()[-4:] or None)},
                           None, None, "Education.csv")
        if _save_fact(db, profile.id, ef, "linkedin_export", doc.id, sigs):
            created += 1
    for row in _read_csv(zf, "Certifications.csv"):
        name = (row.get("Name") or "").strip()
        if name and _save_fact(db, profile.id, ExtractedFact("certification", {"name": name, "authority": row.get("Authority")},
                                                             None, None, "Certifications.csv"), "linkedin_export", doc.id, sigs):
            created += 1
    db.flush()
    conflicts = detect_role_conflicts(db, profile)
    audit.record(db, actor, "linkedin_export.ingested", "document", doc.id, {"facts_created": created, "conflicts": conflicts})
    db.commit()
    return {"document_id": doc.id, "facts_created": created, "conflicts": conflicts, "duplicate": False}


# ------------------------------------------------------------------ conflicts
def _open_conflict_exists(db: Session, a: int, b: int | None, fld: str) -> bool:
    return db.execute(select(FactConflict.id).where(
        FactConflict.fact_a_id == a, FactConflict.fact_b_id == b, FactConflict.field == fld)).first() is not None


def _add_conflict(db: Session, profile_id: int, a: int, b: int | None, fld: str, desc: str) -> int:
    if _open_conflict_exists(db, a, b, fld):
        return 0
    db.add(FactConflict(profile_id=profile_id, fact_a_id=a, fact_b_id=b, field=fld, description=desc[:1000]))
    return 1


def detect_role_conflicts(db: Session, profile: Profile) -> int:
    """Resume vs LinkedIn: mismatched titles/dates for the same employer, or roles present in only one."""
    roles = db.execute(select(Fact).where(Fact.profile_id == profile.id, Fact.kind == "role",
                                          Fact.status != "REJECTED")).scalars().all()
    resume = [r for r in roles if r.origin == "resume"]
    li = [r for r in roles if r.origin == "linkedin_export"]
    if not li or not resume:
        return 0
    n = 0
    for lr in li:
        matches = [r for r in resume if _norm(r.data.get("employer")) and
                   (_norm(r.data.get("employer")) in _norm(lr.data.get("employer")) or
                    _norm(lr.data.get("employer")) in _norm(r.data.get("employer")))]
        if not matches:
            n += _add_conflict(db, profile.id, lr.id, None, "missing_on_resume",
                               f"LinkedIn lists '{lr.data.get('title')}' at '{lr.data.get('employer')}', not found on resume.")
            continue
        best = min(matches, key=lambda r: 0 if _norm(r.data.get("title")) == _norm(lr.data.get("title")) else 1)
        if _norm(best.data.get("title")) != _norm(lr.data.get("title")):
            n += _add_conflict(db, profile.id, best.id, lr.id, "title",
                               f"Title differs: resume '{best.data.get('title')}' vs LinkedIn '{lr.data.get('title')}'.")
        for fld in ("start", "end"):
            rv, lv = best.data.get(fld), lr.data.get(fld)
            if rv and lv and rv[:7] != lv[:7] and not (len(rv) == 4 and lv.startswith(rv)) and not (len(lv) == 4 and rv.startswith(lv)):
                n += _add_conflict(db, profile.id, best.id, lr.id, fld, f"{fld} date differs: resume {rv} vs LinkedIn {lv}.")
    for r in resume:
        if not any(_norm(r.data.get("employer")) and (_norm(r.data.get("employer")) in _norm(lr.data.get("employer")) or
                   _norm(lr.data.get("employer")) in _norm(r.data.get("employer"))) for lr in li):
            n += _add_conflict(db, profile.id, r.id, None, "missing_on_linkedin",
                               f"Resume lists '{r.data.get('title')}' at '{r.data.get('employer')}', not found in LinkedIn export.")
    db.flush()
    return n


def _digits(s: str | None) -> str:
    return re.sub(r"\D", "", s or "")[-10:]


def _detect_contact_conflicts(db: Session, profile: Profile) -> int:
    n = 0
    for f in db.execute(select(Fact).where(Fact.profile_id == profile.id, Fact.kind == "contact",
                                           Fact.status == "PENDING")).scalars():
        fld, val = f.data.get("field"), f.data.get("value", "")
        if fld == "email" and profile.email and val.lower() != profile.email.lower():
            n += _add_conflict(db, profile.id, f.id, None, "email", "Resume email differs from profile email.")
        elif fld == "phone" and profile.phone and _digits(val) != _digits(profile.phone):
            n += _add_conflict(db, profile.id, f.id, None, "phone", "Resume phone differs from profile phone.")
        elif fld == "linkedin_url" and profile.linkedin_url and _norm(val.split("linkedin.com")[-1]) != _norm(
                profile.linkedin_url.split("linkedin.com")[-1]):
            n += _add_conflict(db, profile.id, f.id, None, "linkedin_url", "Resume LinkedIn URL differs from profile.")
    return n
