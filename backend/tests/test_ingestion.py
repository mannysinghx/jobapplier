import io
import os
import zipfile

import pytest
from sqlalchemy import select

from app.ingestion import files, service
from app.ingestion.extract import extract_facts
from app.models import Fact, FactConflict

from .conftest import SYNTH_RESUME_LINES, make_docx, make_pdf


def test_extract_facts_with_provenance():
    text = "\n".join(SYNTH_RESUME_LINES)
    facts = extract_facts(text)
    roles = [f for f in facts if f.kind == "role"]
    assert [(r.data["title"], r.data["employer"], r.data["start"], r.data["end"]) for r in roles] == [
        ("Senior Software Engineer", "Examplecorp", "2020-01", "present"),
        ("Software Engineer", "Samplesoft", "2016-06", "2019-12"),
    ]
    assert len(roles[0].children) == 3 and "Kubernetes" in roles[0].children[1].data["text"]
    skills = {f.data["name"] for f in facts if f.kind == "skill"}
    assert {"Python", "PostgreSQL", "Terraform"} <= skills
    edu = [f for f in facts if f.kind == "education"][0]
    assert edu.data["year"] == "2016" and "State University" in edu.data["institution"]
    # provenance: char spans point at the source text
    for f in facts:
        if f.start is not None and f.kind in ("skill", "contact"):
            assert text[f.start:f.end] == f.snippet


def test_docx_and_pdf_ingestion_creates_pending_facts(db, profile):
    docx_bytes = make_docx()  # generate once: DOCX embeds a timestamp, so each call yields different bytes
    doc = service.ingest_resume(db, profile, "resume.docx", docx_bytes, "test")
    assert doc.parse_status == "parsed", doc.parse_error
    facts = db.execute(select(Fact).where(Fact.document_id == doc.id)).scalars().all()
    assert facts and all(f.status == "PENDING" for f in facts)
    assert all(f.origin == "resume" and f.snippet for f in facts)
    pdf = service.ingest_resume(db, profile, "resume.pdf", make_pdf(SYNTH_RESUME_LINES[:16]), "test")
    assert pdf.parse_status == "parsed", pdf.parse_error
    assert pdf.version == 2
    # same file again -> no new version
    again = service.ingest_resume(db, profile, "resume.docx", docx_bytes, "test")
    assert again.id == doc.id


def test_stored_files_are_encrypted(db, profile):
    data = make_docx()
    doc = service.ingest_resume(db, profile, "resume.docx", data, "test")
    from app.security.crypto import EncryptedFileStore

    raw = (EncryptedFileStore().root / doc.store_path).read_bytes()
    assert b"Examplecorp" not in raw and not raw.startswith(b"PK")
    assert EncryptedFileStore().get(doc.store_path) == data


@pytest.mark.parametrize("name,data,msg", [
    ("resume.pdf", b"PK\x03\x04 not a pdf", "not a PDF"),
    ("resume.docx", b"%PDF-1.4 fake", "not a DOCX"),
    ("resume.exe", b"MZ", "only .pdf and .docx"),
    ("resume.pdf", b"", "empty"),
])
def test_validation_rejects_bad_files(name, data, msg):
    with pytest.raises(files.IntakeError, match=msg):
        files.validate(name, data)


def test_size_limit(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "max_upload_bytes", 10)
    with pytest.raises(files.IntakeError, match="size limit"):
        files.validate("a.pdf", b"%PDF-" + b"x" * 20)


def test_folder_is_read_only_and_confined(tmp_path):
    (tmp_path / "cv.pdf").write_bytes(make_pdf(["x"]))
    (tmp_path / "notes.txt").write_text("ignore")
    outside = tmp_path.parent / f"outside_{os.getpid()}.pdf"
    outside.write_bytes(make_pdf(["secret"]))
    (tmp_path / "link.pdf").symlink_to(outside)
    names = [f.name for f in files.list_folder(str(tmp_path))]
    assert names == ["cv.pdf"]
    with pytest.raises(files.IntakeError):
        files.read_from_folder(str(tmp_path), "link.pdf")
    with pytest.raises(files.IntakeError):
        files.read_from_folder(str(tmp_path), "../" + outside.name)
    outside.unlink()


def test_parser_subprocess_gets_no_secrets(monkeypatch):
    captured = {}
    import subprocess

    real_run = subprocess.run

    def spy(cmd, **kw):
        captured.update(kw)
        return real_run(cmd, **kw)

    monkeypatch.setattr(files.subprocess, "run", spy)
    files.parse_isolated("docx", make_docx())
    assert "JA_ENCRYPTION_KEY" not in captured["env"]
    assert set(captured["env"]) <= {"PATH", "LANG"}


def test_malformed_pdf_fails_safely(db, profile):
    doc = service.ingest_resume(db, profile, "bad.pdf", b"%PDF-1.4\n garbage \x00\x01", "test")
    assert doc.parse_status == "failed"


def _linkedin_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Positions.csv", "Company Name,Title,Description,Location,Started On,Finished On\n"
                                     "Examplecorp,Staff Software Engineer,,Austin,Jan 2020,\n"
                                     "Samplesoft,Software Engineer,,Austin,Jun 2016,Dec 2019\n"
                                     "Ghostco,Intern,,Remote,Jun 2015,Aug 2015\n")
        zf.writestr("Skills.csv", "Name\nPython\nGraphQL\n")
    return buf.getvalue()


def test_linkedin_export_conflicts_block_approval(db, profile, client, admin):
    service.ingest_resume(db, profile, "resume.docx", make_docx(), "test")
    res = service.ingest_linkedin_export(db, profile, "export.zip", _linkedin_zip(), "test")
    conflicts = db.execute(select(FactConflict)).scalars().all()
    fields = {c.field for c in conflicts}
    assert "title" in fields  # Senior vs Staff at Examplecorp: never silently pick the better claim
    assert "missing_on_resume" in fields  # Ghostco appears only on LinkedIn
    assert res["conflicts"] >= 2
    title_conflict = next(c for c in conflicts if c.field == "title")
    r = client.post("/api/auth/login", json={"username": "admin", "password": admin["password"], "totp": admin["totp"].now()})
    client.headers["x-csrf-token"] = r.json()["csrf_token"]
    out = client.post("/api/facts/decide", json={"ids": [title_conflict.fact_a_id], "decision": "APPROVED"}).json()
    assert out["updated"] == [] and "conflict" in out["skipped"][0]["reason"]
    client.post(f"/api/conflicts/{title_conflict.id}/resolve", json={"keep": "a"})
    out = client.post("/api/facts/decide", json={"ids": [title_conflict.fact_a_id], "decision": "APPROVED"}).json()
    assert out["updated"] == [title_conflict.fact_a_id]
    assert db.get(Fact, title_conflict.fact_b_id).status == "REJECTED"


def test_contact_conflict_flagged(db, profile):
    profile.email = "different@example.com"
    db.commit()
    service.ingest_resume(db, profile, "resume.docx", make_docx(), "test")
    assert db.execute(select(FactConflict).where(FactConflict.field == "email")).scalar() is not None
