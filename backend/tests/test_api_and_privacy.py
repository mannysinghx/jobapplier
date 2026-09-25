import io
import zipfile

import httpx
import pytest
import respx
from sqlalchemy import text

from app import audit, privacy
from app.models import AuditEvent, Document, Fact, Profile
from app.security.crypto import EncryptedFileStore

from .conftest import make_docx


def test_auth_required_and_csrf_enforced(client, admin):
    assert client.get("/api/profile").status_code == 401
    # wrong TOTP
    r = client.post("/api/auth/login", json={"username": "admin", "password": admin["password"], "totp": "000000"})
    assert r.status_code == 401
    # missing TOTP for admin
    r = client.post("/api/auth/login", json={"username": "admin", "password": admin["password"]})
    assert r.status_code == 401
    r = client.post("/api/auth/login", json={"username": "admin", "password": admin["password"], "totp": admin["totp"].now()})
    assert r.status_code == 200
    assert "httponly" in r.headers["set-cookie"].lower() and "samesite=strict" in r.headers["set-cookie"].lower()
    # POST without CSRF header is refused
    assert client.post("/api/controls/pause", json={}).status_code == 403
    assert client.post("/api/controls/pause", json={}, headers={"x-csrf-token": "wrong"}).status_code == 403
    assert client.post("/api/controls/pause", json={}, headers={"x-csrf-token": r.json()["csrf_token"]}).status_code == 200


def test_login_lockout(client, admin):
    for _ in range(5):
        client.post("/api/auth/login", json={"username": "admin", "password": "nope", "totp": "1"})
    r = client.post("/api/auth/login", json={"username": "admin", "password": admin["password"], "totp": admin["totp"].now()})
    assert r.status_code == 429


def test_viewer_cannot_write(client, db):
    from app.security.auth import create_user

    create_user(db, "viewer", "viewer password 123", "viewer", with_totp=False)
    r = client.post("/api/auth/login", json={"username": "viewer", "password": "viewer password 123"})
    assert r.status_code == 200
    h = {"x-csrf-token": r.json()["csrf_token"]}
    assert client.post("/api/controls/pause", json={}, headers=h).status_code == 403
    assert client.get("/api/controls").status_code == 200


@respx.mock
def test_end_to_end_flow(authed, db, tmp_path):
    c = authed
    r = c.put("/api/profile", json={"first_name": "Alex", "last_name": "Synthetic", "email": "alex.synthetic@example.com",
                                    "phone": "555-010-0199", "location": "Austin, TX",
                                    "linkedin_url": "https://www.linkedin.com/in/alex-synthetic-test"})
    assert r.status_code == 200, r.text
    assert c.put("/api/profile", json={"first_name": "A", "last_name": "B", "email": "a@example.com",
                                       "linkedin_url": "https://evil.example/in/x"}).status_code == 422
    # consent + import from folder
    (tmp_path / "resume.docx").write_bytes(make_docx())
    assert c.post("/api/profile/folder-consent", json={"folder": str(tmp_path), "consent": False}).status_code == 422
    assert c.post("/api/profile/folder-consent", json={"folder": str(tmp_path), "consent": True}).status_code == 200
    assert [f["name"] for f in c.get("/api/folder/files").json()] == ["resume.docx"]
    doc = c.post("/api/documents/import-from-folder", json={"name": "resume.docx"}).json()
    assert doc["parse_status"] == "parsed"
    facts = c.get("/api/facts").json()
    ids = [f["id"] for f in facts if f["kind"] != "contact" and not f["has_open_conflict"]]
    out = c.post("/api/facts/decide", json={"ids": ids, "decision": "APPROVED"}).json()
    assert out["updated"]
    # preferences
    r = c.put("/api/preferences", json={"titles": ["Senior Software Engineer"], "keywords": ["python"],
                                        "seniority": ["senior"], "work_arrangements": ["remote"], "min_score": 50,
                                        "salary_floor": 100000})
    assert r.status_code == 200, r.text
    # standard answers (sensitive = masked in listings)
    c.put("/api/answers/work_authorization", json={"answer": "Yes", "approved": True})
    c.put("/api/answers/sponsorship", json={"answer": "No", "approved": True})
    listing = c.get("/api/answers").json()["answers"]
    assert all(a["answer"] == "••••••" for a in listing if a["sensitive"])
    # sources: submission cannot be enabled for any registry source
    assert c.patch("/api/sources/greenhouse", json={"auto_submit_opt_in": True}).status_code == 409
    assert c.patch("/api/sources/linkedin", json={"enabled": True}).status_code == 409
    assert c.patch("/api/sources/greenhouse", json={"enabled": True}).status_code == 200
    assert c.post("/api/boards", json={"source_key": "greenhouse", "board_token": "../etc"}).status_code == 422
    assert c.post("/api/boards", json={"source_key": "greenhouse", "board_token": "examplecorp"}).status_code == 200
    respx.get("https://boards-api.greenhouse.io/v1/boards/examplecorp/jobs").mock(return_value=httpx.Response(200, json={"jobs": [
        {"id": 7, "title": "Senior Software Engineer", "location": {"name": "Remote"},
         "content": "Python, PostgreSQL, Kubernetes. 3+ years.", "absolute_url": "https://boards.greenhouse.io/examplecorp/jobs/7"}]}))
    runs = c.post("/api/pipeline/poll").json()
    assert runs[0]["status"] == "ok" and runs[0]["new"] == 1
    c.post("/api/pipeline/match")
    apps = c.get("/api/applications").json()
    assert apps[0]["state"] == "MATCHED", apps[0]
    aid = apps[0]["id"]
    r = c.post(f"/api/applications/{aid}/prepare").json()
    assert r["state"] == "PREPARED", r
    detail = c.get(f"/api/applications/{aid}").json()
    assert all(a["answer"] != "Yes" for a in detail["packet"]["answers"] if a["sensitive"])  # masked
    assert c.get(f"/api/applications/{aid}/resume.docx").content.startswith(b"PK")
    assert "Dear Hiring Team" in c.get(f"/api/applications/{aid}/cover-letter.txt").text
    assert c.post(f"/api/applications/{aid}/approve", json={}).json()["state"] == "APPROVED"
    r = c.post(f"/api/applications/{aid}/submit").json()
    assert r["submitted"] is False and any("not permit automated submission" in x for x in r["reasons"])
    handoffs = c.get("/api/handoffs").json()
    assert handoffs and handoffs[0]["apply_url"].startswith("https://")
    r = c.post(f"/api/applications/{aid}/manual-submission", json={"confirmation_id": "GH-7"})
    assert r.json()["state"] == "CONFIRMED"
    assert c.get("/api/handoffs").json() == []
    assert c.get("/api/audit/verify").json()["ok"] is True
    # export
    z = zipfile.ZipFile(io.BytesIO(c.get("/api/privacy/export").content))
    assert {"profile.json", "facts.json", "applications.json", "submissions.json"} <= set(z.namelist())
    assert any(n.startswith("documents/") for n in z.namelist())
    # delete
    paths = [d.store_path for d in db.query(Document).all()]
    assert c.post("/api/privacy/delete", json={"confirm": "nope"}).status_code == 422
    assert c.post("/api/privacy/delete", json={"confirm": "DELETE MY DATA"}).status_code == 200
    db.expire_all()
    assert db.query(Profile).count() == 0 and db.query(Fact).count() == 0 and db.query(Document).count() == 0
    root = EncryptedFileStore().root
    assert not any((root / p).exists() for p in paths)
    assert db.query(AuditEvent).filter_by(action="privacy.deleted").count() == 1


def test_audit_is_append_only(db):
    audit.record(db, "t", "a1")
    audit.record(db, "t", "a2", details={"email": "x@example.com", "note": "ok"})
    db.commit()
    ev = db.query(AuditEvent).order_by(AuditEvent.id.desc()).first()
    assert ev.details["email"] == "[redacted]"
    with pytest.raises(Exception, match="append-only"):
        db.execute(text("UPDATE audit_events SET action='x'"))
    db.rollback()
    with pytest.raises(Exception, match="append-only"):
        db.execute(text("DELETE FROM audit_events"))
    db.rollback()
    assert audit.verify_chain(db) == (True, None)


def test_audit_tamper_detected(db):
    for i in range(3):
        audit.record(db, "t", f"a{i}")
    db.commit()
    # simulate an attacker with DDL rights disabling the trigger: the hash chain must still expose the edit
    if db.bind.dialect.name == "postgresql":
        db.execute(text("DROP TRIGGER audit_guard ON audit_events"))
    else:
        db.execute(text("DROP TRIGGER audit_no_update"))
    db.execute(text("UPDATE audit_events SET action='tampered' WHERE id=2"))
    db.commit()
    ok, bad = audit.verify_chain(db)
    assert not ok and bad == 2


def test_retention_purges_through_maintenance_window(db):
    from datetime import timedelta

    from app.db import utcnow

    audit.record(db, "t", "old")
    db.commit()
    res = privacy.apply_retention(db)
    assert res["audit_events"] == 0
    # nothing is older than the cutoff yet; purge with a future cutoff removes rows through the window
    n = audit.purge_older_than(db, utcnow() + timedelta(seconds=1))
    db.commit()
    assert n >= 1
    assert db.query(AuditEvent).filter_by(action="audit.purged").count() >= 1


def test_reset_mfa_replaces_secret_and_signs_out(authed, db, admin, capsys):
    import io

    import pyotp

    from app.cli import print_totp_setup
    from app.models import User, UserSession
    from app.security.auth import reset_totp

    old_secret = db.query(User).filter_by(username="admin").one().totp_secret
    assert db.query(UserSession).count() == 1
    _, uri, secret = reset_totp(db, "admin", "cli")
    db.expire_all()
    assert secret != old_secret and db.query(User).one().totp_secret == secret
    assert db.query(UserSession).count() == 0
    assert authed.get("/api/auth/me").status_code == 401  # signed out everywhere
    assert db.query(AuditEvent).filter_by(action="user.mfa_reset").count() == 1
    # old authenticator code no longer works; new one does
    r = authed.post("/api/auth/login", json={"username": "admin", "password": admin["password"], "totp": admin["totp"].now()})
    assert r.status_code == 401
    r = authed.post("/api/auth/login", json={"username": "admin", "password": admin["password"], "totp": pyotp.TOTP(secret).now()})
    assert r.status_code == 200
    buf = io.StringIO()
    print_totp_setup(uri, buf)
    text = buf.getvalue()
    grouped = " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))
    assert grouped in text and "jobApplier:admin" in text and ("█" in text or "▀" in text)
    with pytest.raises(ValueError):
        reset_totp(db, "nobody", "cli")
