"""Test fixtures. SYNTHETIC DATA ONLY: no real resumes, no network (respx blocks it), no real submissions."""
import io
import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="ja_test_"))
os.environ.update({
    "JA_ENV": "test",
    "JA_DATA_DIR": str(_TMP),
    "JA_DATABASE_URL": f"sqlite:///{_TMP / 'test.sqlite3'}",
    "JA_KILL_SWITCH_FILE": str(_TMP / "KILL_SWITCH"),
    "JA_MFA_REQUIRED": "true",
})
from cryptography.fernet import Fernet  # noqa: E402

os.environ["JA_ENCRYPTION_KEY"] = Fernet.generate_key().decode()

import pyotp  # noqa: E402
import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app import audit  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.connectors.registry import sync_registry  # noqa: E402
from app.db import Base, SessionLocal, make_engine, set_engine  # noqa: E402
from app.matching import DEFAULT_WEIGHTS  # noqa: E402
from app.models import Fact, Preferences, Profile  # noqa: E402
from app.submission import base as sub_base  # noqa: E402

SYNTH_RESUME_LINES = [
    "Alex Synthetic",
    "alex.synthetic@example.com | (555) 010-0199 | linkedin.com/in/alex-synthetic-test",
    "",
    "SUMMARY",
    "Backend engineer focused on data platforms.",
    "",
    "EXPERIENCE",
    "Senior Software Engineer, Examplecorp",
    "Jan 2020 – Present",
    "• Built Python and PostgreSQL services handling 2M requests per day",
    "• Led migration of batch jobs to Kubernetes",
    "• Mentored 4 engineers",
    "Software Engineer | Samplesoft",
    "Jun 2016 - Dec 2019",
    "• Developed REST APIs in Django",
    "• Wrote Terraform modules for AWS",
    "",
    "EDUCATION",
    "B.S. Computer Science",
    "State University of Nowhere, 2016",
    "",
    "SKILLS",
    "Python, PostgreSQL, Kubernetes, Django, AWS, Terraform, Docker",
    "",
    "CERTIFICATIONS",
    "AWS Certified Solutions Architect – Associate",
]


def make_docx(lines: list[str] = SYNTH_RESUME_LINES) -> bytes:
    import docx

    d = docx.Document()
    for ln in lines:
        d.add_paragraph(ln)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def make_pdf(lines: list[str]) -> bytes:
    """Minimal single-page PDF with one text line per entry (synthetic test fixture)."""
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").encode("latin-1", "replace").decode("latin-1")

    ops = ["BT", "/F1 10 Tf", "12 TL", "50 780 Td"] + [f"({esc(ln)}) Tj T*" for ln in lines] + ["ET"]
    stream = "\n".join(ops).encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + o + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


@pytest.fixture()
def db():
    """A fresh database per test. Set JA_TEST_PG_URL to run the suite against a DISPOSABLE PostgreSQL database
    (all tables in it are dropped per test, so never point it at a real database)."""
    pg = os.environ.get("JA_TEST_PG_URL")
    url = pg or f"sqlite:///{_TMP / f'db_{os.urandom(4).hex()}.sqlite3'}"
    eng = make_engine(url)
    if pg:
        Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    with eng.begin() as conn:
        audit.install_audit_guards(conn)
    set_engine(eng)
    s = SessionLocal()
    sync_registry(s)
    yield s
    s.close()
    eng.dispose()


@pytest.fixture(autouse=True)
def _clean_global_state():
    ks = get_settings().kill_switch_file
    if ks.exists():
        ks.unlink()
    sub_base.ADAPTERS.clear()
    from app.discovery import RateLimiter

    RateLimiter._last.clear()
    from app.security import auth

    auth._failures.clear()
    yield
    if ks.exists():
        ks.unlink()
    sub_base.ADAPTERS.clear()


@pytest.fixture()
def profile(db):
    p = Profile(first_name="Alex", last_name="Synthetic", email="alex.synthetic@example.com", phone="555-010-0199",
                location="Austin, TX", linkedin_url="https://www.linkedin.com/in/alex-synthetic-test")
    db.add(p)
    db.flush()
    db.add(Preferences(profile_id=p.id, titles=["Senior Software Engineer", "Backend Engineer"],
                       keywords=["python", "postgresql"], seniority=["senior", "staff"], geographies=["Austin", "Texas"],
                       work_arrangements=["remote", "hybrid"], salary_floor=120000, industries=["data"],
                       excluded_employers=["Badco"], excluded_terms=["clearance required"], min_score=60,
                       daily_application_limit=5, weights=dict(DEFAULT_WEIGHTS)))
    db.commit()
    return p


def approved_facts(db, profile) -> list[Fact]:
    """A small approved fact profile, built directly (bypasses parsing)."""
    role = Fact(profile_id=profile.id, kind="role", origin="user", status="APPROVED",
                data={"title": "Senior Software Engineer", "employer": "Examplecorp", "start": "2018-01", "end": "present"})
    db.add(role)
    db.flush()
    facts = [role,
             Fact(profile_id=profile.id, kind="achievement", origin="user", status="APPROVED", parent_id=role.id,
                  data={"text": "Built Python and PostgreSQL services handling 2M requests per day"}),
             Fact(profile_id=profile.id, kind="achievement", origin="user", status="APPROVED", parent_id=role.id,
                  data={"text": "Led migration of batch jobs to Kubernetes"})]
    for s in ("Python", "PostgreSQL", "Kubernetes", "AWS"):
        facts.append(Fact(profile_id=profile.id, kind="skill", origin="user", status="APPROVED", data={"name": s}))
    facts.append(Fact(profile_id=profile.id, kind="skill", origin="user", status="PENDING", data={"name": "Rust"}))
    facts.append(Fact(profile_id=profile.id, kind="achievement", origin="user", status="PENDING", parent_id=role.id,
                      data={"text": "UNAPPROVED CLAIM: won a Turing Award"}))
    db.add_all(facts[1:])
    db.commit()
    return facts


@pytest.fixture()
def client(db):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c


@pytest.fixture()
def admin(db):
    from app.security.auth import create_user

    u, uri = create_user(db, "admin", "correct horse battery staple", "admin", with_totp=True)
    return {"username": "admin", "password": "correct horse battery staple", "totp": pyotp.TOTP(u.totp_secret)}


@pytest.fixture()
def authed(client, admin):
    r = client.post("/api/auth/login", json={"username": admin["username"], "password": admin["password"],
                                              "totp": admin["totp"].now()})
    assert r.status_code == 200, r.text
    client.headers["x-csrf-token"] = r.json()["csrf_token"]
    return client


__all__ = ["make_docx", "make_pdf", "approved_facts", "text"]
