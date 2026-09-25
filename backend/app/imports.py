"""User-initiated job imports: job-alert emails, manual adds, LinkedIn export (saved jobs + past applications),
and on-demand Dice job details. None of these scrape a job site."""
import csv
import hashlib
import io
import re
import zipfile
from datetime import timedelta
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import audit
from .config import get_settings
from .connectors.base import NormalizedJob, infer_arrangement, parse_salary_text, sanitize_short, sanitize_text
from .connectors.registry import SourceNotPermitted
from .db import utcnow
from .discovery import _expire_applications, dedupe_key, upsert_jobs
from .ingestion import email_alerts
from .models import Application, Job, Source
from .pipeline import idempotency_key
from .policy import prior_applications  # noqa: F401  (re-exported for the API)

EMAIL_SOURCE = "email_alert"
IMPORT_SOURCE = "user_import"
SITE_HOSTS = {"linkedin.com": "linkedin", "indeed.com": "indeed", "ziprecruiter.com": "ziprecruiter",
              "dice.com": "dice", "theladders.com": "ladders", "greenhouse.io": "greenhouse", "lever.co": "lever",
              "ashbyhq.com": "ashby"}
ATS_PATTERNS = [
    ("greenhouse", re.compile(r"^https://(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/([A-Za-z0-9_.-]+)(?:/|$)")),
    ("lever", re.compile(r"^https://jobs\.lever\.co/([A-Za-z0-9_.-]+)(?:/|$)")),
    ("ashby", re.compile(r"^https://jobs\.ashbyhq\.com/([A-Za-z0-9_.-]+)(?:/|$)")),
]


def _source(db: Session, key: str) -> Source:
    s = db.get(Source, key)
    if s is None or not s.read_permitted:
        raise SourceNotPermitted(f"{key}: not permitted by the source registry")
    return s


def site_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    for dom, site in SITE_HOSTS.items():
        if host == dom or host.endswith("." + dom):
            return site
    return "other"


def suggest_ats_board(url: str) -> dict | None:
    """If a listing lives on a permitted ATS board, suggest following that board via its official API."""
    for key, rx in ATS_PATTERNS:
        m = rx.match(url or "")
        if m and m.group(1).lower() not in ("embed", "jobs"):
            return {"source_key": key, "board_token": m.group(1)}
    return None


def expire_stale_imports(db: Session, now=None) -> int:  # noqa: ANN001
    """Imported/alert listings have no 'taken down' signal: expire them after imported_listing_ttl_days unseen."""
    now = now or utcnow()
    cutoff = now - timedelta(days=get_settings().imported_listing_ttl_days)
    n = 0
    for job in db.execute(select(Job).where(Job.source_key.in_([EMAIL_SOURCE, IMPORT_SOURCE]),
                                            Job.expired_at.is_(None), Job.last_seen_at < cutoff)).scalars():
        job.expired_at = now
        _expire_applications(db, job)
        n += 1
    return n


def import_alert_emails(db: Session, filename: str, data: bytes, actor: str) -> dict:
    src = _source(db, EMAIL_SOURCE)
    parsed = email_alerts.parse_file(filename, data)
    now = utcnow()
    new_total = 0
    by_site: dict[str, list[NormalizedJob]] = {}
    for nj in parsed["jobs"]:
        by_site.setdefault(nj.meta["site"], []).append(nj)
    for site, jobs in by_site.items():
        new, _ = upsert_jobs(db, src, None, jobs, now, complete_listing=False, board_token=site)
        new_total += new
    expired = expire_stale_imports(db, now)
    audit.record(db, actor, "import.alert_emails", "source", EMAIL_SOURCE,
                 {"messages": parsed["messages"], "alert_messages": parsed["alert_messages"], "jobs_found": len(parsed["jobs"]),
                  "jobs_new": new_total, "rejected_links": parsed["rejected_links"], "by_site": parsed["by_site"]})
    db.commit()
    return {"messages": parsed["messages"], "alert_messages": parsed["alert_messages"],
            "skipped_non_alert_messages": parsed["messages"] - parsed["alert_messages"], "jobs_found": len(parsed["jobs"]),
            "jobs_new": new_total, "by_site": parsed["by_site"], "rejected_links": parsed["rejected_links"],
            "expired_stale": expired}


def add_manual_job(db: Session, url: str, title: str, employer: str, actor: str, location: str | None = None,
                   description: str | None = None, salary: str | None = None) -> tuple[Job, dict | None]:
    if not url.startswith("https://"):
        raise ValueError("URL must start with https://")
    src = _source(db, IMPORT_SOURCE)
    site = site_of(url)
    ext = f"{site}:{hashlib.sha256(url.split('#')[0].encode()).hexdigest()[:24]}"
    desc = sanitize_text(description or "", 60_000)
    smin, smax, cur = parse_salary_text(salary)
    nj = NormalizedJob(external_id=ext, employer=sanitize_short(employer, 200) or "Unknown employer",
                       title=sanitize_short(title, 300), description=desc, location=sanitize_short(location, 200) or None,
                       work_arrangement=infer_arrangement(location, title), salary_min=smin, salary_max=smax,
                       salary_currency=cur, canonical_url=url, apply_url=url,
                       meta={"site": site, "via": "manual", "summary_only": not desc, "description_by_user": bool(desc)})
    if not nj.title:
        raise ValueError("title is required")
    upsert_jobs(db, src, None, [nj], utcnow(), complete_listing=False, board_token=site)
    job = db.execute(select(Job).where(Job.source_key == IMPORT_SOURCE, Job.external_id == ext)).scalar()
    audit.record(db, actor, "import.manual_job", "job", job.id, {"site": site})
    db.commit()
    return job, suggest_ats_board(url)


def set_description(db: Session, job: Job, description: str, actor: str) -> Job:
    """User pastes the full description of an imported job (from their own browser)."""
    if job.source_key not in (EMAIL_SOURCE, IMPORT_SOURCE):
        raise ValueError("only imported jobs can have their description set by hand")
    job.description = sanitize_text(description, 60_000)
    from .connectors.base import extract_requirements

    job.requirements = extract_requirements(job.description)
    job.meta = {**(job.meta or {}), "summary_only": False, "description_by_user": True}
    audit.record(db, actor, "job.description_set", "job", job.id)
    db.commit()
    return job


def fetch_dice_details(db: Session, job: Job, actor: str, connector=None) -> Job:  # noqa: ANN001
    """Dice asks MCP clients to fetch details only for jobs the user picks; this is only called from a user action."""
    if job.source_key != "dice" or not (job.meta or {}).get("dice_guid"):
        raise ValueError("details can only be fetched for Dice search results")
    src = _source(db, "dice")
    if not src.enabled:
        raise SourceNotPermitted("dice: disabled by user")
    if connector is None:
        from .connectors.dice import DiceConnector
        from .discovery import make_client

        connector = DiceConnector(make_client())
    desc, skills = connector.job_details(job.meta["dice_guid"])
    job.description = desc or job.description
    from .connectors.base import extract_requirements

    job.requirements = (skills or []) + extract_requirements(desc)
    job.meta = {**(job.meta or {}), "summary_only": False, "details_fetched": utcnow().isoformat(), "dice_skills": skills[:50]}
    audit.record(db, actor, "job.details_fetched", "job", job.id, {"source": "dice"})
    db.commit()
    return job


# ------------------------------------------------------------------ LinkedIn export: saved jobs + job applications
def _find_csvs(zf: zipfile.ZipFile, prefix: str) -> list[str]:
    return [i.filename for i in zf.infolist()
            if i.filename.rsplit("/", 1)[-1].lower().startswith(prefix.lower()) and i.filename.lower().endswith(".csv")
            and i.file_size < 20 * 1024 * 1024]


def _col(row: dict, *needles: str) -> str:
    for k, v in row.items():
        lk = (k or "").lower()
        if all(n in lk for n in needles):
            return (v or "").strip()
    return ""


def import_linkedin_jobs(db: Session, profile_id: int, data: bytes, actor: str) -> dict:
    """Reads 'Saved Jobs*.csv' and 'Job Applications*.csv' from the user's own LinkedIn export.
    Past applications become SUBMITTED (external) records so the duplicate guard knows about them. Recruiter contact
    details and question answers in that CSV are deliberately NOT stored."""
    src = _source(db, IMPORT_SOURCE)
    zf = zipfile.ZipFile(io.BytesIO(data))
    now = utcnow()
    saved = applied = 0
    for kind, prefix in (("saved", "Saved Jobs"), ("applied", "Job Applications")):
        for name in _find_csvs(zf, prefix):
            text = zf.read(name).decode("utf-8-sig", errors="replace")
            for row in csv.DictReader(io.StringIO(text)):
                url = _col(row, "url")
                title = sanitize_short(_col(row, "title"), 300)
                company = sanitize_short(_col(row, "company"), 200)
                if not title or not url.startswith("https://"):
                    continue
                m = re.search(r"/jobs/view/(?:[^/?]*-)?(\d{6,})", url)
                jid = m.group(1) if m else hashlib.sha256(url.encode()).hexdigest()[:24]
                nj = NormalizedJob(external_id=f"linkedin:{jid}", employer=company or "Unknown employer", title=title,
                                   description="", canonical_url=url, apply_url=url,
                                   meta={"site": "linkedin", "via": "linkedin_export", "summary_only": True,
                                         "linkedin_" + kind + "_date": _col(row, "date")[:40]})
                upsert_jobs(db, src, None, [nj], now, complete_listing=False, board_token="linkedin")
                job = db.execute(select(Job).where(Job.source_key == IMPORT_SOURCE, Job.external_id == nj.external_id)).scalar()
                if kind == "saved":
                    saved += 1
                    continue
                applied += 1
                app = db.execute(select(Application).where(Application.profile_id == profile_id,
                                                           Application.job_id == job.id)).scalar()
                if app is None:
                    app = Application(profile_id=profile_id, job_id=job.id, idempotency_key=idempotency_key(profile_id, job),
                                      state="SUBMITTED", notes=f"Applied outside jobApplier (LinkedIn export, {_col(row, 'date')[:40]})")
                    db.add(app)
                    db.flush()
                    audit.record(db, actor, "application.imported_external", "application", app.id, {"site": "linkedin"})
    audit.record(db, actor, "import.linkedin_jobs", "source", IMPORT_SOURCE, {"saved": saved, "applied": applied})
    db.commit()
    return {"saved_jobs": saved, "past_applications": applied}
