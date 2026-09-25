from datetime import date

from sqlalchemy import select

from app import pipeline
from app.matching import score_job
from app.models import Application, Fact, Preferences, StandardAnswer
from app.prep import answers as ans
from app.prep.packet import build_cover_letter, build_resume_lines, verify_provenance

from .conftest import approved_facts
from .helpers import make_job


def prefs(db, profile) -> Preferences:
    return db.execute(select(Preferences).where(Preferences.profile_id == profile.id)).scalar()


def test_score_is_explainable_and_evidence_backed(db, profile):
    facts = approved_facts(db, profile)
    job = make_job(db)
    m = score_job(job, prefs(db, profile), facts, today=date(2026, 9, 1))
    assert set(m.components) == {"title", "skills", "seniority", "location", "domain", "compensation"}
    assert m.score > 60 and not m.exclusions
    skill_ev = {e["detail"]: e["fact_ids"] for e in m.evidence if e["criterion"] == "skill"}
    assert set(skill_ev) >= {"python", "postgresql", "kubernetes"}
    approved_ids = {f.id for f in facts if f.status == "APPROVED"}
    assert all(i in approved_ids for e in m.evidence for i in e["fact_ids"])


def test_unapproved_facts_do_not_count(db, profile):
    facts = approved_facts(db, profile)
    job = make_job(db, description="Must know Rust. 3+ years.")
    m = score_job(job, prefs(db, profile), facts)
    assert "rust" in m.required_skills and "rust" not in m.matched_skill_fact_ids  # Rust fact is PENDING
    assert any("rust" in u for u in m.unmet)


def test_missing_experience_lowers_score(db, profile):
    facts = approved_facts(db, profile)
    p = prefs(db, profile)
    low = score_job(make_job(db, ext="a", description="Python. 20+ years required."), p, facts, today=date(2026, 9, 1))
    ok = score_job(make_job(db, ext="b", description="Python. 3+ years required."), p, facts, today=date(2026, 9, 1))
    assert low.components["seniority"]["points"] < ok.components["seniority"]["points"]
    assert any("20+ years" in u for u in low.unmet)


def test_no_facts_means_no_skill_credit(db, profile):
    m = score_job(make_job(db), prefs(db, profile), [])
    assert m.components["skills"]["points"] == 0


def test_hard_exclusions_override_score(db, profile):
    facts = approved_facts(db, profile)
    p = prefs(db, profile)
    cases = {
        "employer": make_job(db, ext="e1", employer="Badco Industries"),
        "term": make_job(db, ext="e2", description="Python. Clearance required."),
        "salary": make_job(db, ext="e3", salary_max=90000),
        "arrangement": make_job(db, ext="e4", work_arrangement="onsite", location="Austin, TX"),
    }
    for name, job in cases.items():
        m = score_job(job, p, facts)
        assert m.exclusions and m.score == 0, name


def test_resume_and_letter_cite_only_approved_facts(db, profile):
    facts = approved_facts(db, profile)
    job = make_job(db)
    m = score_job(job, prefs(db, profile), facts)
    approved_ids = {f.id for f in facts if f.status == "APPROVED"}
    lines = build_resume_lines(profile, facts, m.required_skills)
    letter, flags = build_cover_letter(profile, job, facts, m.matched_skill_fact_ids)
    assert verify_provenance(lines, approved_ids) == [] and verify_provenance(letter, approved_ids) == []
    full = " ".join(u["text"] for u in lines + letter)
    assert "Turing" not in full and "Rust" not in full  # pending facts never appear
    assert not flags
    # every fact-backed unit's text is built from the cited facts' values
    by_id = {f.id: f for f in facts}
    for u in lines:
        if u["section"] == "achievement":
            assert u["text"] == by_id[u["fact_ids"][0]].data["text"]


def test_verify_provenance_catches_unsupported_units():
    assert verify_provenance([{"text": "I have a PhD"}], set())
    assert verify_provenance([{"text": "x", "fact_ids": [99]}], {1})


def test_sensitive_questions_are_never_inferred(db, profile):
    # Even if a resume fact mentions citizenship, the work authorization answer must come from an explicit answer.
    db.add(Fact(profile_id=profile.id, kind="summary", origin="user", status="APPROVED", data={"text": "US citizen, no sponsorship needed"}))
    db.commit()
    qs = [{"label": "Are you legally authorized to work in the US?", "required": True},
          {"label": "Do you require visa sponsorship?", "required": True},
          {"label": "Have you ever been convicted of a felony?", "required": True},
          {"label": "Gender", "required": False},
          {"label": "I certify that the information provided is true", "required": True},
          {"label": "What is your favourite colour?", "required": True},
          {"label": "Email", "required": True}]
    out = ans.draft_answers(qs, profile, [])
    status = {o["key"]: o["status"] for o in out}
    assert status["work_authorization"] == "SENSITIVE_MISSING"
    assert status["sponsorship"] == "SENSITIVE_MISSING"
    assert status["criminal_history"] == "SENSITIVE_MISSING"
    assert status["gender"] == "SENSITIVE_MISSING"
    assert status["attestation"] == "ATTESTATION"
    assert status["unknown"] == "NEEDS_REVIEW"
    assert status["email"] == "SUPPORTED"
    assert all(ans.is_blocking(o) for o in out if o["key"] != "email")
    # unapproved standard answer still does not count
    sa = StandardAnswer(profile_id=profile.id, question_key="work_authorization", answer="Yes", sensitive=True, approved=False)
    db.add(sa)
    db.commit()
    out = ans.draft_answers(qs[:1], profile, [sa])
    assert out[0]["status"] == "SENSITIVE_MISSING"
    sa.approved = True
    out = ans.draft_answers(qs[:1], profile, [sa])
    assert out[0]["status"] == "USER_APPROVED" and out[0]["source"] == {"answer_id": sa.id}


def test_packet_stores_references_not_sensitive_values(db, profile):
    approved_facts(db, profile)
    db.add(StandardAnswer(profile_id=profile.id, question_key="work_authorization", answer="SECRET-AUTH-VALUE", sensitive=True, approved=True))
    db.commit()
    make_job(db)
    pipeline.match_jobs(db, profile)
    app = db.query(Application).one()
    packet = pipeline.prepare(db, app, "test")
    assert "SECRET-AUTH-VALUE" not in str(packet.answers)
    assert app.state == "NEEDS_REVIEW"  # sponsorship still unanswered


def test_prepare_flags_unsupported_and_approve_requires_resolution(db, profile):
    approved_facts(db, profile)
    make_job(db)
    pipeline.match_jobs(db, profile)
    app = db.query(Application).one()
    pipeline.prepare(db, app, "test")
    assert app.state == "NEEDS_REVIEW"
    import pytest

    with pytest.raises(ValueError, match="resolve open items"):
        pipeline.approve(db, app, "test")
    pipeline.approve(db, app, "test", answer_on_site=True)
    assert app.state == "APPROVED"
    packet = pipeline.policy.latest_packet(db, app.id)
    assert {a["status"] for a in packet.answers if a["key"] in ("work_authorization", "sponsorship")} == {"ON_SITE"}


def test_different_role_type_halves_title_fit(db, profile):
    p = prefs(db, profile)
    p.titles = ["Software Engineer"]
    ic = score_job(make_job(db, ext="ic", title="Senior Software Engineer"), p, [])
    mgr = score_job(make_job(db, ext="mgr", title="Software Engineering Manager"), p, [])
    assert mgr.components["title"]["points"] == ic.components["title"]["points"] / 2
