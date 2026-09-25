from app import pipeline
from app.db import utcnow
from app.discovery import dedupe_key
from app.models import Application, Job, Source
from app.submission.base import SubmissionAdapter, SubmitResult, register

CLEAN_DESCRIPTION = """We build data platforms.
Requirements:
- 5+ years of Python experience
- PostgreSQL and Kubernetes
"""


def make_job(db, source_key="greenhouse", ext="1", **kw) -> Job:
    fields = dict(employer="Examplecorp", title="Senior Backend Engineer", description=CLEAN_DESCRIPTION,
                  location="Remote - US", work_arrangement="remote", employment_type="full_time",
                  salary_min=140000, salary_max=180000, salary_currency="USD",
                  apply_url="https://boards.greenhouse.io/examplecorp/jobs/1",
                  canonical_url="https://boards.greenhouse.io/examplecorp/jobs/1")
    fields.update(kw)
    j = Job(source_key=source_key, board_token="examplecorp", external_id=ext, content_hash="x",
            dedupe_key=dedupe_key(fields["employer"], fields["title"], fields["location"]),
            first_seen_at=utcnow(), last_seen_at=utcnow(), **fields)
    db.add(j)
    db.commit()
    return j


class FakeAdapter(SubmissionAdapter):
    """TEST-ONLY adapter. Never registered outside tests."""

    name = "fake"
    source_key = "greenhouse"
    verified = True

    def __init__(self, result: SubmitResult | None = None, raise_exc: Exception | None = None):
        self.calls = 0
        self.result = result or SubmitResult("SUBMITTED", confirmation_id="CONF-1")
        self.raise_exc = raise_exc
        self.payloads = []

    def submit(self, payload, should_abort):
        if should_abort():
            return SubmitResult("FAILED", error="aborted")
        self.calls += 1
        self.payloads.append(payload)
        if self.raise_exc:
            raise self.raise_exc
        return self.result


def permit_auto_submit(db, adapter: FakeAdapter, source_key="greenhouse"):
    """Simulates a source whose submission route HAS been verified (test only)."""
    s = db.get(Source, source_key)
    s.submit_permitted = True
    s.enabled = True
    s.auto_submit_opt_in = True
    db.commit()
    register(adapter)


def approved_app(db, profile, job) -> Application:
    """Match -> prepare -> approve, with all default questions answered by approved standard answers."""
    from app.models import StandardAnswer

    for k, v in (("work_authorization", "Yes"), ("sponsorship", "No")):
        if not db.query(StandardAnswer).filter_by(profile_id=profile.id, question_key=k).first():
            db.add(StandardAnswer(profile_id=profile.id, question_key=k, answer=v, sensitive=True, approved=True))
    db.commit()
    pipeline.match_jobs(db, profile, "test")
    app = db.query(Application).filter_by(job_id=job.id).one()
    assert app.state == "MATCHED", (app.state, app.score, app.match.get("exclusions"))
    pipeline.prepare(db, app, "test")
    assert app.state == "PREPARED", [a for a in pipeline.policy.latest_packet(db, app.id).answers if a["status"] not in ("SUPPORTED", "USER_APPROVED")]
    pipeline.approve(db, app, "test")
    return app
