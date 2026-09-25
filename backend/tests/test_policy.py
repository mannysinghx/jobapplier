"""Auto-submit gate: every condition must hold; any single failure blocks and hands off."""
from datetime import timedelta

import pytest

from app import controls, pipeline, policy
from app.config import get_settings
from app.models import HandoffTask, Preferences, Source, SubmissionAttempt
from app.submission.base import SubmitResult

from .conftest import approved_facts
from .helpers import FakeAdapter, approved_app, make_job, permit_auto_submit


@pytest.fixture()
def ready(db, profile):
    approved_facts(db, profile)
    job = make_job(db)
    adapter = FakeAdapter()
    permit_auto_submit(db, adapter)
    app = approved_app(db, profile, job)
    return app, adapter


def test_default_configuration_never_auto_submits(db, profile):
    """Out of the box: no source is submit_permitted and no adapter exists, so everything is a handoff."""
    approved_facts(db, profile)
    job = make_job(db)
    app = approved_app(db, profile, job)
    d = policy.evaluate(db, app)
    assert not d.allowed
    assert not d.checks["source_submit_permitted"] and not d.checks["adapter_verified"] and not d.checks["user_opted_in"]
    r = pipeline.try_submit(db, app)
    assert r["submitted"] is False
    assert db.query(HandoffTask).filter_by(application_id=app.id, status="OPEN").count() == 1
    assert db.query(SubmissionAttempt).count() == 0


def test_all_conditions_pass_then_submits_once(db, ready):
    app, adapter = ready
    d = policy.evaluate(db, app)
    assert d.allowed, d.reasons
    assert pipeline.try_submit(db, app)["submitted"] is True
    assert app.state == "CONFIRMED" and adapter.calls == 1
    att = db.query(SubmissionAttempt).one()
    assert att.confirmation_id == "CONF-1" and att.snapshot and "answers" in att.snapshot
    from sqlalchemy import text

    raw = db.execute(text("SELECT snapshot FROM submission_attempts")).scalar()
    assert "answers" not in raw and not raw.startswith("{")  # ciphertext at rest
    # idempotency / duplicate guard: a second attempt never reaches the adapter
    assert pipeline.try_submit(db, app)["submitted"] is False
    assert adapter.calls == 1


@pytest.mark.parametrize("mutate,check", [
    (lambda db, app: controls.set_paused(db, True, "t"), "not_paused"),
    (lambda db, app: get_settings().kill_switch_file.write_text("x"), "not_paused"),
    (lambda db, app: setattr(db.get(Source, "greenhouse"), "submit_permitted", False), "source_submit_permitted"),
    (lambda db, app: setattr(db.get(Source, "greenhouse"), "auto_submit_opt_in", False), "user_opted_in"),
    (lambda db, app: setattr(db.get(Source, "greenhouse"), "enabled", False), "user_enabled_source"),
    (lambda db, app: setattr(db.get(Source, "greenhouse"), "date_checked", "2019-01-01"), "source_review_current"),
    (lambda db, app: setattr(app.job, "expired_at", app.job.last_seen_at), "listing_active"),
    (lambda db, app: setattr(app.job, "duplicate_of_id", app.job.id), "listing_unique"),
    (lambda db, app: setattr(app.job, "last_seen_at", app.job.last_seen_at - timedelta(days=5)), "listing_fresh"),
    (lambda db, app: setattr(app.job, "apply_url", "http://insecure.example/apply"), "apply_url_https"),
    (lambda db, app: setattr(app.job, "employer", "Badco"), "no_hard_exclusions"),
    (lambda db, app: setattr(app, "score", 10.0), "score_meets_threshold"),
    (lambda db, app: setattr(db.query(Preferences).one(), "daily_application_limit", 0), "global_daily_limit"),
    (lambda db, app: setattr(db.get(Source, "greenhouse"), "daily_submit_limit", 0), "source_daily_limit"),
])
def test_each_failed_condition_blocks(db, ready, mutate, check):
    app, adapter = ready
    mutate(db, app)
    db.commit()
    d = policy.evaluate(db, app)
    assert not d.allowed and d.checks[check] is False, d.checks
    r = pipeline.try_submit(db, app)
    assert r["submitted"] is False and adapter.calls == 0


def test_unsupported_answer_blocks(db, ready):
    app, adapter = ready
    packet = policy.latest_packet(db, app.id)
    packet.answers = packet.answers + [{"question": "Salary history?", "key": "salary_history", "required": True,
                                        "sensitive": True, "status": "ON_SITE", "source": None, "note": ""}]
    db.commit()
    d = policy.evaluate(db, app)
    assert not d.checks["answers_supported"]
    assert pipeline.try_submit(db, app)["submitted"] is False and adapter.calls == 0


def test_limits_count_submissions(db, profile):
    approved_facts(db, profile)
    adapter = FakeAdapter()
    permit_auto_submit(db, adapter)
    db.get(Source, "greenhouse").daily_submit_limit = 1
    db.commit()
    a1 = approved_app(db, profile, make_job(db, ext="1", title="Senior Backend Engineer"))
    assert pipeline.try_submit(db, a1)["submitted"]
    a2 = approved_app(db, profile, make_job(db, ext="2", title="Senior Software Engineer"))
    r = pipeline.try_submit(db, a2)
    assert r["submitted"] is False and "per-source daily limit reached" in r["reasons"]


def test_challenge_creates_handoff_and_never_solves(db, ready):
    app, adapter = ready
    adapter.result = SubmitResult("CHALLENGE", challenge="captcha")
    r = pipeline.try_submit(db, app)
    assert r["submitted"] is False and app.state == "NEEDS_REVIEW"
    task = db.query(HandoffTask).filter_by(application_id=app.id).one()
    assert "captcha" in task.reasons[0]
    assert db.query(SubmissionAttempt).one().status == "CHALLENGE"


def test_unknown_outcome_is_not_retried(db, ready):
    app, adapter = ready
    adapter.raise_exc = TimeoutError("socket timeout after send")
    pipeline.try_submit(db, app)
    assert app.state == "FAILED" and db.query(SubmissionAttempt).one().status == "UNKNOWN"
    # even after the user re-approves, the UNKNOWN attempt holds the idempotency key
    from app.statemachine import transition

    transition(db, app, "APPROVED", "test", "retry")
    db.commit()
    adapter.raise_exc = None
    r = pipeline.try_submit(db, app)
    assert r["submitted"] is False and adapter.calls == 1


def test_failed_attempt_can_retry_with_same_key(db, ready):
    app, adapter = ready
    adapter.result = SubmitResult("FAILED", error="HTTP 500 before submit")
    pipeline.try_submit(db, app)
    assert app.state == "FAILED"
    from app.statemachine import transition

    transition(db, app, "APPROVED", "user", "retry")
    db.commit()
    adapter.result = SubmitResult("SUBMITTED", confirmation_id="C2")
    assert pipeline.try_submit(db, app)["submitted"] is True
    assert db.query(SubmissionAttempt).count() == 1  # same row, same idempotency key
    import json

    db.expire_all()
    snap = json.loads(db.query(SubmissionAttempt).one().snapshot)  # readable after the retry path
    assert snap["packet_id"] and snap["answers"]


def test_pause_during_submission_aborts(db, ready):
    app, adapter = ready
    orig = controls.is_paused
    calls = {"n": 0}

    def flip(db_):
        calls["n"] += 1
        return calls["n"] > 1  # gate sees "not paused"; the check right before the adapter sees "paused"

    import app.pipeline as pl

    pl.controls.is_paused = flip
    try:
        r = pipeline.try_submit(db, app)
    finally:
        pl.controls.is_paused = orig
    assert r["submitted"] is False and adapter.calls == 0


def test_duplicate_guard_across_reposted_listing(db, profile):
    approved_facts(db, profile)
    adapter = FakeAdapter()
    permit_auto_submit(db, adapter)
    a1 = approved_app(db, profile, make_job(db, ext="1"))
    assert pipeline.try_submit(db, a1)["submitted"]
    # Same role re-posted with a new external id after the original expired: dedupe key matches
    a1.job.expired_at = a1.job.last_seen_at
    db.commit()
    job2 = make_job(db, ext="99")
    pipeline.match_jobs(db, profile)
    from app.models import Application

    a2 = db.query(Application).filter_by(job_id=job2.id).one()
    pipeline.prepare(db, a2, "t")
    pipeline.approve(db, a2, "t")
    d = policy.evaluate(db, a2)
    assert d.checks["not_already_applied"] is False


def test_manual_submission_records_packet(db, profile):
    approved_facts(db, profile)
    app = approved_app(db, profile, make_job(db))
    att = pipeline.record_manual_submission(db, app, "user:x", "GH-123", "https://boards.greenhouse.io/confirm")
    assert app.state == "CONFIRMED" and att.adapter == "manual" and att.status == "CONFIRMED"
    with pytest.raises(ValueError):
        pipeline.record_manual_submission(db, app, "user:x", None, None)
