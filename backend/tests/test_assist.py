"""Apply Assistant: prepared values for applying by hand; sensitive values masked and revealed only with an audit."""
from app import pipeline
from app.models import Application, AuditEvent, StandardAnswer

from .conftest import approved_facts
from .helpers import make_job


def _setup(db, profile):
    approved_facts(db, profile)
    for k, v, sens in (("work_authorization", "SECRET-YES", True), ("sponsorship", "SECRET-NO", True),
                       ("salary_expectation", "$170k", False), ("start_date", "2 weeks", False)):
        db.add(StandardAnswer(profile_id=profile.id, question_key=k, answer=v, sensitive=sens, approved=True))
    db.add(StandardAnswer(profile_id=profile.id, question_key="relocation", answer="UNAPPROVED", approved=False))
    db.commit()
    j1 = make_job(db, ext="a1", title="Senior Backend Engineer")
    j2 = make_job(db, ext="a2", title="Senior Software Engineer", apply_url="https://www.linkedin.com/jobs/view/4000000009/")
    j2.meta = {"site": "linkedin"}
    db.commit()
    pipeline.match_jobs(db, profile)
    apps = {a.job_id: a for a in db.query(Application).all()}
    a1, a2 = apps[j1.id], apps[j2.id]
    pipeline.prepare(db, a1, "t")
    pipeline.approve(db, a1, "t")
    pipeline.prepare(db, a2, "t")
    return a1, a2


def test_assist_payload_masks_sensitive_and_lists_everything(db, profile, authed):
    a1, a2 = _setup(db, profile)
    d = authed.get(f"/api/applications/{a1.id}/assist").json()
    text = str(d)
    assert "SECRET-YES" not in text and "SECRET-NO" not in text and "UNAPPROVED" not in text
    assert {f["key"] for f in d["fields"]} >= {"first_name", "last_name", "email", "phone", "linkedin"}
    wa = next(x for x in d["answers"] if x["key"] == "work_authorization")
    assert wa["sensitive"] and wa["value"] == "••••••" and wa["answer_id"]
    assert {o["key"] for o in d["other_answers"]} == {"salary_expectation", "start_date"}
    assert next(o for o in d["other_answers"] if o["key"] == "salary_expectation")["value"] == "$170k"
    assert "Dear Hiring Team" in d["cover_letter_text"] and d["downloads"]["resume_docx"].endswith("/resume.docx")
    assert d["ready"] is True and d["self_apply_note"] is None
    assert d["next_application_id"] == a2.id and d["queue_length"] == 2


def test_linkedin_job_gets_self_apply_note_and_needs_approval(db, profile, authed):
    a1, a2 = _setup(db, profile)
    d = authed.get(f"/api/applications/{a2.id}/assist").json()
    assert d["site"] == "linkedin" and "LinkedIn yourself" in d["self_apply_note"]
    assert d["ready"] is False and d["can_approve"] is True


def test_queue_orders_ready_first(db, profile, authed):
    a1, a2 = _setup(db, profile)
    q = authed.get("/api/assist/queue").json()
    assert [x["application_id"] for x in q] == [a1.id, a2.id] and q[0]["ready"] and not q[1]["ready"]


def test_reveal_is_audited_and_limited_to_approved(db, profile, authed):
    a1, _ = _setup(db, profile)
    wa = db.query(StandardAnswer).filter_by(question_key="work_authorization").one()
    r = authed.post("/api/assist/reveal", json={"application_id": a1.id, "answer_id": wa.id})
    assert r.json()["value"] == "SECRET-YES"
    ev = db.query(AuditEvent).filter_by(action="assist.answer_revealed").one()
    assert ev.details == {"key": "work_authorization", "sensitive": True} and "SECRET" not in str(ev.details)
    un = db.query(StandardAnswer).filter_by(question_key="relocation").one()
    assert authed.post("/api/assist/reveal", json={"application_id": a1.id, "answer_id": un.id}).status_code == 409
    assert authed.post("/api/assist/reveal", json={"application_id": a1.id, "answer_id": 99999}).status_code == 404


def test_viewer_cannot_reveal(db, profile, client):
    from app.security.auth import create_user

    a1, _ = _setup(db, profile)
    create_user(db, "viewer", "viewer password 123", "viewer", with_totp=False)
    r = client.post("/api/auth/login", json={"username": "viewer", "password": "viewer password 123"})
    h = {"x-csrf-token": r.json()["csrf_token"]}
    wa = db.query(StandardAnswer).filter_by(question_key="work_authorization").one()
    assert client.post("/api/assist/reveal", json={"application_id": a1.id, "answer_id": wa.id}, headers=h).status_code == 403
