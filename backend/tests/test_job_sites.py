"""LinkedIn / Indeed / ZipRecruiter / Dice / Ladders via PERMITTED routes only:
Dice official MCP search, the user's own alert emails, the LinkedIn data export, and manual adds.
All data here is synthetic; the Dice server is mocked; no job site is contacted."""
import io
import json
import zipfile
from datetime import timedelta
from email.message import EmailMessage

import httpx
import pytest
import respx
from sqlalchemy import select

from app import discovery, imports, pipeline, policy
from app.connectors.dice import DiceConnector, validate_params
from app.db import utcnow
from app.ingestion import email_alerts as ea
from app.models import Application, Job, Source, SourceBoard

from .conftest import approved_facts
from .helpers import make_job

GUID = "f155eb02-b705-4e91-9092-827827717675"


# ------------------------------------------------------------------ registry
@pytest.mark.parametrize("key", ["linkedin", "indeed", "ziprecruiter", "ladders"])
def test_direct_site_access_is_not_permitted(db, authed, key):
    s = db.get(Source, key)
    assert s.read_permitted is False and s.submit_permitted is False
    assert authed.patch(f"/api/sources/{key}", json={"enabled": True}).status_code == 409


@pytest.mark.parametrize("key", ["dice", "email_alert", "user_import"])
def test_permitted_routes_never_allow_submission(db, key):
    s = db.get(Source, key)
    assert s.read_permitted is True and s.submit_permitted is False


# ------------------------------------------------------------------ Dice (official MCP)
def _sse(obj) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=f"event: message\ndata: {json.dumps(obj)}\n\n")


def _dice_mock(calls: list):
    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        if body.get("method") == "initialize":
            return _sse({"jsonrpc": "2.0", "id": body["id"], "result": {"protocolVersion": "2025-06-18", "capabilities": {}}})
        if body.get("method") == "notifications/initialized":
            return httpx.Response(202)
        name = body["params"]["name"]
        if name == "search_jobs":
            return _sse({"jsonrpc": "2.0", "id": body["id"], "result": {"isError": False, "structuredContent": {"data": [
                {"guid": GUID, "title": "Backend Engineer - Python", "summary": "Python, PostgreSQL. <b>Remote</b>",
                 "postedDate": "2026-09-20T10:00:00Z", "detailsPageUrl": f"https://www.dice.com/job-detail/{GUID}?utm_source=x y",
                 "salary": "$60 - $70 per hour", "companyName": "Synthetic Staffing", "employmentType": "Contract",
                 "workplaceTypes": ["Remote"], "isRemote": True, "easyApply": True},
                {"guid": "not-a-guid", "title": "bad"}], "metadata": {}}}})
        if name == "get_job_details":
            return _sse({"jsonrpc": "2.0", "id": body["id"], "result": {"isError": False, "structuredContent": {
                "description": "Full description. Requirements:\n- Python\n- Kubernetes", "skills": [{"name": "Python"}, {"name": "Kubernetes"}]}}})
        return httpx.Response(400)
    return handler


def test_dice_params_allowlisted():
    assert validate_params({"keyword": "python", "workplace_types": ["Remote", "Mars"]}) == {
        "keyword": "python", "workplace_types": ["Remote"], "posted_date": "SEVEN"}
    for bad in ({}, {"keyword": "x" * 101}, {"keyword": "a", "posted_date": "YEAR"}, {"keyword": "a", "fields": ["*"]}):
        with pytest.raises(ValueError):
            validate_params(bad)


@respx.mock
def test_dice_search_normalizes_and_never_fetches_details(db):
    calls: list = []
    respx.post("https://mcp.dice.com/mcp").mock(side_effect=_dice_mock(calls))
    jobs = DiceConnector(httpx.Client()).fetch("python-remote", {"keyword": "python backend"})
    assert len(jobs) == 1
    j = jobs[0]
    assert (j.external_id, j.employer, j.work_arrangement, j.employment_type) == (GUID, "Synthetic Staffing", "remote", "contract")
    assert j.salary_max == 70 * 2080 and j.apply_url == f"https://www.dice.com/job-detail/{GUID}?utm_source=jobapplier&utm_medium=mcp"
    assert "<b>" not in j.description and j.meta["summary_only"] is True
    tools = [c["params"]["name"] for c in calls if c.get("method") == "tools/call"]
    assert tools == ["search_jobs"]  # details are never fetched automatically (Dice's tool guidance)
    search_args = next(c["params"]["arguments"] for c in calls if c.get("method") == "tools/call")
    assert search_args["keyword"] == "python backend" and search_args["jobs_per_page"] == 50


@respx.mock
def test_dice_poll_uses_ttl_not_absence_and_details_on_request(db, profile, authed):
    calls: list = []
    respx.post("https://mcp.dice.com/mcp").mock(side_effect=_dice_mock(calls))
    assert authed.patch("/api/sources/dice", json={"enabled": True}).status_code == 200
    r = authed.post("/api/boards", json={"source_key": "dice", "params": {"keyword": "python", "location": "Austin, TX"}})
    assert r.status_code == 200, r.text
    assert r.json()["board_token"] == "python-Austin-TX" and r.json()["params"]["location"] == "Austin, TX"
    assert authed.post("/api/boards", json={"source_key": "dice", "params": {"keyword": ""}}).status_code == 422
    runs = authed.post("/api/pipeline/poll").json()
    assert runs[0]["status"] == "ok" and runs[0]["new"] == 1
    job = db.execute(select(Job).where(Job.source_key == "dice")).scalar()
    # an empty search next time does NOT expire the job (search results are not a complete listing)
    board = db.execute(select(SourceBoard).where(SourceBoard.source_key == "dice")).scalar()
    discovery.upsert_jobs(db, db.get(Source, "dice"), board, [], utcnow(), complete_listing=False, ttl_days=14)
    db.refresh(job)
    assert job.expired_at is None
    discovery.upsert_jobs(db, db.get(Source, "dice"), board, [], utcnow() + timedelta(days=15), complete_listing=False, ttl_days=14)
    db.refresh(job)
    assert job.expired_at is not None
    job.expired_at = None
    db.commit()
    # details only when the user asks
    out = authed.post(f"/api/jobs/{job.id}/fetch-details").json()
    assert out["meta"]["summary_only"] is False and "Kubernetes" in out["meta"]["dice_skills"]
    assert [c["params"]["name"] for c in calls if c.get("method") == "tools/call"].count("get_job_details") == 1
    # disclosure is shown with Dice results
    app_id = db.execute(select(Application.id).where(Application.job_id == job.id)).scalar()
    assert "AI-powered search" in authed.get(f"/api/applications/{app_id}").json()["source_attribution"]


# ------------------------------------------------------------------ alert emails
def _alert(sender: str, html: str, dkim_domain: str | None = None, subject: str = "Jobs for you") -> EmailMessage:
    m = EmailMessage()
    m["From"] = sender
    m["To"] = "alex.synthetic@example.com"
    m["Subject"] = subject
    m["Date"] = "Wed, 23 Sep 2026 08:00:00 +0000"
    if dkim_domain:
        m["Authentication-Results"] = f"mx.example.com; dkim=pass header.d={dkim_domain}; spf=pass"
    m.set_content("see html")
    m.add_alternative(html, subtype="html")
    return m


LINKEDIN_HTML = """<html><body><table>
<tr><td><a href="https://www.linkedin.com/comm/jobs/view/4012345678/?trackingId=abc"><img alt="logo"></a></td>
<td><a href="https://www.linkedin.com/comm/jobs/view/4012345678/?trackingId=abc">Senior Backend Engineer</a>
<p>Examplecorp</p><p>Austin, TX (Hybrid)</p><p>$150K/yr - $180K/yr</p><p>Actively recruiting</p></td></tr>
<tr><td><a href="https://evil.example.net/jobs/view/999999999">Staff Engineer (phish)</a><p>Evil</p></td></tr>
<tr><td><a href="https://www.linkedin.com/comm/jobs/search?keywords=python">See all jobs</a></td></tr>
<script>alert(1)</script></table></body></html>"""
INDEED_HTML = """<a href="https://cts.indeed.com/v3/H4sI?url=https%3A%2F%2Fwww.indeed.com%2Frc%2Fclk%3Fjk%3Da1b2c3d4e5f60718%26from%3Dja">Python Developer</a>
<div>Samplesoft</div><div>Remote</div><div>$60 - $70 an hour</div>
<a href="https://www.indeed.com/viewjob?jk=0f0e0d0c0b0a0908">Data Engineer</a><div>Otherco</div><div>Denver, CO</div>"""
ZIP_HTML = """<a href="https://www.ziprecruiter.com/ekm/AAB1234?jid=zr-555">Platform Engineer</a><p>Zipco</p><p>Remote</p>"""
DICE_HTML = f"""<a href="https://www.dice.com/job-detail/{GUID}">Cloud Engineer</a><p>DiceCo</p><p>Remote</p>"""
LADDERS_HTML = """<a href="https://www.theladders.com/job/senior-sre-acme-new-york-ny_12345678">Senior SRE</a><p>Acme</p><p>New York, NY</p>"""


def test_parse_linkedin_alert_rejects_phishing_and_generic_links():
    parsed = ea.parse_message(_alert("LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>", LINKEDIN_HTML, "linkedin.com"))
    assert parsed.site == "linkedin" and len(parsed.jobs) == 1
    j = parsed.jobs[0]
    assert (j.title, j.employer, j.location) == ("Senior Backend Engineer", "Examplecorp", "Austin, TX (Hybrid)")
    assert j.work_arrangement == "hybrid" and j.salary_min == 150000 and j.salary_max == 180000
    assert j.apply_url == "https://www.linkedin.com/jobs/view/4012345678/" and j.meta["dkim"] == "pass"
    assert parsed.rejected_links == 1  # the evil.example.net link


@pytest.mark.parametrize("sender,html,expect", [
    ("Indeed <alert@indeed.com>", INDEED_HTML, [("Python Developer", "Samplesoft", "https://www.indeed.com/viewjob?jk=a1b2c3d4e5f60718"),
                                                 ("Data Engineer", "Otherco", "https://www.indeed.com/viewjob?jk=0f0e0d0c0b0a0908")]),
    ("ZipRecruiter <alerts@ziprecruiter.com>", ZIP_HTML, [("Platform Engineer", "Zipco", None)]),
    ("Dice <jobalerts@dice.com>", DICE_HTML, [("Cloud Engineer", "DiceCo", f"https://www.dice.com/job-detail/{GUID}")]),
    ("Ladders <alerts@theladders.com>", LADDERS_HTML, [("Senior SRE", "Acme", "https://www.theladders.com/job/senior-sre-acme-new-york-ny_12345678")]),
])
def test_parse_other_sites(sender, html, expect):
    parsed = ea.parse_message(_alert(sender, html))
    got = [(j.title, j.employer, j.apply_url) for j in parsed.jobs]
    assert [(t, e) for t, e, _ in got] == [(t, e) for t, e, _ in expect]
    for (_, _, url), (_, _, want) in zip(got, expect):
        assert url.startswith("https://") and (want is None or url == want)
    assert all(j.meta["dkim"] == "unknown" for j in parsed.jobs)


def test_spoofed_sender_links_must_be_on_site_domain():
    html = '<a href="https://linkedin.com.evil.example/jobs/view/4012345678">Great Job</a><p>Co</p>'
    parsed = ea.parse_message(_alert("jobs@linkedin.com", html))
    assert parsed.jobs == []


def test_mbox_skips_personal_mail_and_stores_nothing_from_it(db, profile):
    personal = EmailMessage()
    personal["From"] = "friend@example.org"
    personal["Subject"] = "Private: medical results"
    personal.set_content("Very private content https://www.linkedin.com/jobs/view/1234567890")
    alert = _alert("jobalerts-noreply@linkedin.com", LINKEDIN_HTML)
    mbox = b"".join(b"From x@y Wed Sep 23 08:00:00 2026\n" + m.as_bytes() + b"\n" for m in (personal, alert))
    res = imports.import_alert_emails(db, "Inbox.mbox", mbox, "test")
    assert (res["messages"], res["alert_messages"], res["skipped_non_alert_messages"], res["jobs_new"]) == (2, 1, 1, 1)
    all_text = json.dumps([[j.title, j.employer, j.description, j.meta] for j in db.query(Job).all()])
    assert "medical" not in all_text and "friend@example.org" not in all_text and "1234567890" not in all_text


def test_zip_of_eml_and_reimport_is_idempotent(db, profile):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.eml", _alert("alert@indeed.com", INDEED_HTML).as_bytes())
        zf.writestr("b.eml", _alert("jobalerts@dice.com", DICE_HTML).as_bytes())
    res = imports.import_alert_emails(db, "alerts.zip", buf.getvalue(), "test")
    assert res["jobs_new"] == 3 and res["by_site"] == {"indeed": 2, "dice": 1}
    again = imports.import_alert_emails(db, "alerts.zip", buf.getvalue(), "test")
    assert again["jobs_new"] == 0 and db.query(Job).count() == 3


def test_import_via_api_matches_and_is_handoff_only(db, profile, authed):
    approved_facts(db, profile)
    data = _alert("jobalerts-noreply@linkedin.com", LINKEDIN_HTML, "linkedin.com").as_bytes()
    r = authed.post("/api/imports/alert-emails", files={"file": ("alert.eml", data, "message/rfc822")})
    assert r.status_code == 200, r.text
    assert r.json()["jobs_new"] == 1
    app = db.query(Application).one()
    d = policy.evaluate(db, app)
    assert d.checks["source_submit_permitted"] is False


def test_stale_imports_expire(db):
    imports.import_alert_emails(db, "a.eml", _alert("alert@indeed.com", INDEED_HTML).as_bytes(), "test")
    assert imports.expire_stale_imports(db, utcnow() + timedelta(days=31)) == 2


# ------------------------------------------------------------------ manual add + description + ATS suggestion
def test_manual_add_and_ats_suggestion(db, profile, authed):
    r = authed.post("/api/jobs/manual", json={"url": "https://job-boards.greenhouse.io/examplecorp/jobs/123",
                                              "title": "Backend Engineer", "employer": "Examplecorp", "location": "Remote"})
    assert r.status_code == 200, r.text
    assert r.json()["suggested_board"] == {"source_key": "greenhouse", "board_token": "examplecorp"}
    r = authed.post("/api/jobs/manual", json={"url": "https://www.linkedin.com/jobs/view/4099999999/",
                                              "title": "Staff Engineer", "employer": "Linkco"})
    job = r.json()["job"]
    assert job["site"] == "linkedin" and job["meta"]["summary_only"] is True and r.json()["suggested_board"] is None
    assert authed.post("/api/jobs/manual", json={"url": "http://insecure.example/x", "title": "t", "employer": "e"}).status_code == 422
    out = authed.put(f"/api/jobs/{job['id']}/description", json={"description": "Requirements:\n- Python\n- PostgreSQL experience"}).json()
    assert out["meta"]["description_by_user"] is True
    # descriptions can only be hand-set on imported jobs
    gh = make_job(db, ext="gh1")
    assert authed.put(f"/api/jobs/{gh.id}/description", json={"description": "x" * 30}).status_code == 409


# ------------------------------------------------------------------ LinkedIn export: saved jobs + past applications
def _linkedin_export() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Jobs/Saved Jobs.csv", "Saved Date,Job Url,Job Title,Company Name\n"
                                           "9/1/26,https://www.linkedin.com/jobs/view/4000000001/,Data Engineer,Dataco\n")
        zf.writestr("Jobs/Job Applications.csv",
                    "Application Date,Contact Email,Contact Phone Number,Company Name,Job Title,Job Url,Resume Name,Question And Answers\n"
                    "8/15/26,recruiter@examplecorp.example,555-000-1111,Examplecorp,Senior Backend Engineer,"
                    "https://www.linkedin.com/jobs/view/4000000002/,resume.pdf,Q:Salary? A:SECRET-ANSWER\n")
    return buf.getvalue()


def test_linkedin_export_jobs_feed_duplicate_guard(db, profile):
    approved_facts(db, profile)
    res = imports.import_linkedin_jobs(db, profile.id, _linkedin_export(), "test")
    assert res == {"saved_jobs": 1, "past_applications": 1}
    ext = db.query(Application).filter_by(state="SUBMITTED").one()
    assert "outside jobApplier" in ext.notes
    stored = json.dumps([[j.title, j.employer, j.description, j.meta] for j in db.query(Job).all()]) + (ext.notes or "")
    assert "recruiter@" not in stored and "SECRET-ANSWER" not in stored and "555-000-1111" not in stored
    # the same role now found on Greenhouse (different location) is flagged as already applied
    gh = make_job(db, ext="gh-dup", employer="Examplecorp", title="Senior Backend Engineer", location="Austin, TX")
    pipeline.match_jobs(db, profile)
    app = db.query(Application).filter_by(job_id=gh.id).one()
    assert policy.prior_applications(db, profile.id, gh)
    pipeline.prepare(db, app, "t")
    pipeline.approve(db, app, "t", answer_on_site=True)
    assert policy.evaluate(db, app).checks["not_already_applied"] is False
