from datetime import timedelta

import httpx
import pytest
import respx
from sqlalchemy import select

from app import controls, discovery
from app.connectors.ats import AshbyConnector, GreenhouseConnector, LeverConnector
from app.connectors.base import sanitize_text
from app.db import utcnow
from app.models import ConnectorRun, Job, Source, SourceBoard

GH_BOARD = "https://boards-api.greenhouse.io/v1/boards/examplecorp"


def gh_payload(ids=(1, 2)):
    jobs = [{"id": 1, "title": "Senior Backend Engineer", "location": {"name": "Remote - US"}, "company_name": "Examplecorp",
             "pay_input_ranges": [{"min_cents": 15000000, "max_cents": 19000000, "currency_type": "USD"}],
             "content": "&lt;p&gt;We use Python and PostgreSQL.&lt;/p&gt;&lt;h3&gt;Requirements&lt;/h3&gt;&lt;ul&gt;&lt;li&gt;5+ years Python&lt;/li&gt;&lt;/ul&gt;"
                        "&lt;script&gt;alert(1)&lt;/script&gt;",
             "absolute_url": "https://boards.greenhouse.io/examplecorp/jobs/1", "updated_at": utcnow().isoformat() + "Z"},
            {"id": 2, "title": "Data Engineer", "location": {"name": "Austin, TX (Hybrid)"}, "content": "Airflow, dbt", "company_name": "Examplecorp",
             "absolute_url": "https://boards.greenhouse.io/examplecorp/jobs/2", "updated_at": utcnow().isoformat() + "Z"}]
    return {"jobs": [j for j in jobs if j["id"] in ids]}


def enable(db, key="greenhouse", board="examplecorp"):
    s = db.get(Source, key)
    s.enabled = True
    b = SourceBoard(source_key=key, board_token=board, employer_name="Examplecorp")
    db.add(b)
    db.commit()
    return s, b


@respx.mock
def test_greenhouse_normalization_and_sanitization():
    respx.get(f"{GH_BOARD}/jobs").mock(return_value=httpx.Response(200, json=gh_payload()))
    jobs = GreenhouseConnector(httpx.Client()).fetch("examplecorp")
    j = jobs[0]
    assert j.employer == "Examplecorp" and j.work_arrangement == "remote"
    assert "<" not in j.description and "alert" not in j.description
    assert j.requirements == ["5+ years Python"]
    assert (j.salary_min, j.salary_max, j.salary_currency) == (150000, 190000, "USD")
    assert jobs[1].work_arrangement == "hybrid" and jobs[1].salary_max is None


@respx.mock
def test_lever_and_ashby_normalization():
    respx.get("https://api.lever.co/v0/postings/samplesoft").mock(return_value=httpx.Response(200, json=[{
        "id": "abc", "text": "Platform Engineer", "categories": {"location": "Austin", "commitment": "Full-time"},
        "descriptionPlain": "Kubernetes", "lists": [{"text": "Requirements", "content": "<li>Go</li>"}],
        "hostedUrl": "https://jobs.lever.co/samplesoft/abc", "applyUrl": "https://jobs.lever.co/samplesoft/abc/apply",
        "createdAt": 1700000000000, "workplaceType": "onsite", "salaryRange": {"min": 100000, "max": 150000, "currency": "USD"}}]))
    lj = LeverConnector(httpx.Client()).fetch("samplesoft")[0]
    assert (lj.work_arrangement, lj.employment_type, lj.salary_max) == ("onsite", "full_time", 150000)
    respx.get("https://api.ashbyhq.com/posting-api/job-board/demo").mock(return_value=httpx.Response(200, json={"jobs": [{
        "id": "u1", "title": "SRE", "location": "Remote", "isRemote": True, "employmentType": "FullTime",
        "descriptionPlain": "Terraform", "jobUrl": "https://jobs.ashbyhq.com/demo/u1", "applyUrl": "https://jobs.ashbyhq.com/demo/u1/application",
        "publishedAt": "2026-09-01T00:00:00Z",
        "compensation": {"summaryComponents": [{"compensationType": "Salary", "interval": "1 HOUR", "minValue": 60, "maxValue": 80, "currencyCode": "USD"}]}},
        {"id": "u2", "title": "Hidden", "isListed": False}]}))
    aj = AshbyConnector(httpx.Client()).fetch("demo")
    assert len(aj) == 1 and aj[0].work_arrangement == "remote" and aj[0].salary_max == 80 * 2080  # hourly annualized


def test_invalid_board_token_rejected():
    with pytest.raises(ValueError):
        GreenhouseConnector(httpx.Client()).fetch("../../admin")


@respx.mock
def test_poll_dedupe_and_expiry(db, profile):
    src, board = enable(db)
    route = respx.get(f"{GH_BOARD}/jobs").mock(return_value=httpx.Response(200, json=gh_payload()))
    run = discovery.poll_board(db, src, board, GreenhouseConnector(httpx.Client()))
    assert (run.status, run.jobs_new) == ("ok", 2)
    # second poll: nothing new, no duplicates created
    run = discovery.poll_board(db, src, board, GreenhouseConnector(httpx.Client()))
    assert run.jobs_new == 0 and db.query(Job).count() == 2
    # job 2 disappears -> expired
    route.mock(return_value=httpx.Response(200, json=gh_payload(ids=(1,))))
    run = discovery.poll_board(db, src, board, GreenhouseConnector(httpx.Client()))
    assert run.jobs_expired == 1
    assert db.execute(select(Job).where(Job.external_id == "2")).scalar().expired_at is not None


def test_cross_source_duplicate(db):
    lever = db.get(Source, "lever")
    gh = db.get(Source, "greenhouse")
    b1 = SourceBoard(source_key="greenhouse", board_token="x", employer_name="Examplecorp")
    b2 = SourceBoard(source_key="lever", board_token="y", employer_name="Examplecorp Inc.")
    db.add_all([b1, b2])
    db.flush()
    from app.connectors.base import NormalizedJob

    nj = NormalizedJob(external_id="1", employer="Examplecorp", title="Sr. Backend Engineer", description="d", location="Remote")
    discovery.upsert_jobs(db, gh, b1, [nj], utcnow())
    nj2 = NormalizedJob(external_id="zz", employer="Examplecorp Inc.", title="Senior Backend Engineer", description="d", location="Remote")
    discovery.upsert_jobs(db, lever, b2, [nj2], utcnow())
    dup = db.execute(select(Job).where(Job.source_key == "lever")).scalar()
    assert dup.duplicate_of_id is not None


@respx.mock
def test_rate_limit_backoff(db):
    src, board = enable(db)
    respx.get(f"{GH_BOARD}/jobs").mock(return_value=httpx.Response(429, headers={"Retry-After": "600"}))
    now = utcnow()
    run = discovery.poll_board(db, src, board, GreenhouseConnector(httpx.Client()), now)
    assert run.status == "error" and board.consecutive_failures == 1
    assert board.next_attempt_at >= now + timedelta(minutes=10)
    # poll_all skips the board until next_attempt_at
    assert discovery.poll_all(db, httpx.Client(), now) == []


def test_disabled_or_unreviewed_source_is_not_polled(db):
    src, board = enable(db)
    src.enabled = False
    db.commit()
    run = discovery.poll_board(db, src, board, GreenhouseConnector(httpx.Client()))
    assert run.status == "skipped" and "disabled" in run.error
    src.enabled, src.date_checked = True, "2020-01-01"
    db.commit()
    run = discovery.poll_board(db, src, board, GreenhouseConnector(httpx.Client()))
    assert run.status == "skipped" and "re-check" in run.error


def test_linkedin_cannot_be_enabled(db):
    li = db.get(Source, "linkedin")
    assert li.read_permitted is False and li.submit_permitted is False


def test_pause_stops_polling(db):
    enable(db)
    controls.set_paused(db, True, "test", "incident")
    assert discovery.poll_all(db, httpx.Client()) == []
    controls.set_paused(db, False, "test")
    from app.config import get_settings

    get_settings().kill_switch_file.write_text("stop")
    assert discovery.poll_all(db, httpx.Client()) == []
    assert db.query(ConnectorRun).count() == 0


def test_sanitize_strips_bidi_and_controls():
    s = sanitize_text("Hello‮World\x00<b>x</b>")
    assert s == "HelloWorld x"


def test_application_deadline_expires_listing(db):
    from app.connectors.base import NormalizedJob

    src, board = enable(db)
    now = utcnow()
    nj = NormalizedJob(external_id="d1", employer="E", title="T", description="d", expires_at=now - timedelta(hours=1))
    new, expired = discovery.upsert_jobs(db, src, board, [nj], now)
    assert (new, expired) == (1, 1)
    assert db.execute(select(Job).where(Job.external_id == "d1")).scalar().expired_at is not None


def test_old_but_still_listed_job_is_not_expired(db):
    from app.connectors.base import NormalizedJob

    src, board = enable(db)
    now = utcnow()
    nj = NormalizedJob(external_id="old", employer="E", title="T", description="d", posted_at=now - timedelta(days=400))
    assert discovery.upsert_jobs(db, src, board, [nj], now) == (1, 0)
    assert db.execute(select(Job).where(Job.external_id == "old")).scalar().expired_at is None


def test_annualize():
    from app.connectors.base import annualize

    assert annualize(100000, "1 YEAR") == 100000
    assert annualize(50, "per-hour-wage") == 104000
    assert annualize(10000, "per-month-salary") == 120000
    assert annualize(86, None) is None  # ambiguous: never used for a salary-floor exclusion
    assert annualize(150000, None) == 150000
