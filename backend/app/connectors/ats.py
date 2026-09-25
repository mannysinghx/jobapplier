"""Greenhouse, Lever and Ashby public job-board connectors (GET only). Permission basis: config/sources.yaml."""
import re
from datetime import UTC, datetime
from urllib.parse import quote

from .base import (
    Connector,
    annualize,
    NormalizedJob,
    extract_requirements,
    infer_arrangement,
    sanitize_short,
    sanitize_text,
)

_TOKEN = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")


def _safe_token(token: str) -> str:
    if not _TOKEN.match(token):
        raise ValueError("invalid board token")
    return quote(token, safe="")


def _dt(v) -> datetime | None:  # noqa: ANN001
    if v is None:
        return None
    try:
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v / 1000 if v > 1e11 else v, UTC).replace(tzinfo=None)
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).astimezone(UTC).replace(tzinfo=None)
    except (ValueError, OSError):
        return None


def _emp_type(s: str | None) -> str:
    t = (s or "").lower().replace("-", "").replace(" ", "")
    for key, val in (("fulltime", "full_time"), ("parttime", "part_time"), ("contract", "contract"),
                     ("intern", "internship"), ("temporary", "temporary"), ("temp", "temporary")):
        if key in t:
            return val
    return "unknown"


def _https(url: str | None) -> str | None:
    return url if url and url.startswith("https://") else None


class GreenhouseConnector(Connector):
    key = "greenhouse"
    BASE = "https://boards-api.greenhouse.io/v1/boards"

    def fetch(self, board_token: str, params: dict | None = None) -> list[NormalizedJob]:
        tok = _safe_token(board_token)
        data = self._get_json(f"{self.BASE}/{tok}/jobs", params={"content": "true", "pay_transparency": "true"})
        out = []
        for j in data.get("jobs", []):
            employer = sanitize_short(j.get("company_name")) or board_token
            smin = smax = cur = None
            for pr in j.get("pay_input_ranges") or []:
                interval = "hour" if "hour" in str(pr.get("title", "")).lower() else None
                lo = annualize(pr["min_cents"] / 100, interval) if isinstance(pr.get("min_cents"), (int, float)) else None
                hi = annualize(pr["max_cents"] / 100, interval) if isinstance(pr.get("max_cents"), (int, float)) else None
                if lo is not None:
                    smin = lo if smin is None else min(smin, lo)
                if hi is not None:
                    smax = hi if smax is None else max(smax, hi)
                cur = cur or pr.get("currency_type")
            desc = sanitize_text(j.get("content"))
            loc = sanitize_short((j.get("location") or {}).get("name"))
            etype = "unknown"
            for m in j.get("metadata") or []:
                if isinstance(m, dict) and "employment" in str(m.get("name", "")).lower():
                    etype = _emp_type(str(m.get("value")))
            out.append(NormalizedJob(
                external_id=str(j.get("id")),
                employer=employer,
                title=sanitize_short(j.get("title"), 300),
                description=desc,
                location=loc or None,
                work_arrangement=infer_arrangement(loc, j.get("title")),
                employment_type=etype,
                salary_min=smin, salary_max=smax, salary_currency=cur,
                posted_at=_dt(j.get("first_published") or j.get("updated_at")),
                expires_at=_dt(j.get("application_deadline")),
                canonical_url=_https(j.get("absolute_url")),
                apply_url=_https(j.get("absolute_url")),
                requirements=extract_requirements(desc),
            ))
        return out


class LeverConnector(Connector):
    key = "lever"
    BASE = "https://api.lever.co/v0/postings"

    def fetch(self, board_token: str, params: dict | None = None) -> list[NormalizedJob]:
        tok = _safe_token(board_token)
        data = self._get_json(f"{self.BASE}/{tok}", params={"mode": "json"})
        out = []
        for j in data if isinstance(data, list) else []:
            cats = j.get("categories") or {}
            parts = [j.get("descriptionPlain") or ""]
            for lst in j.get("lists") or []:
                parts.append(f"\n{lst.get('text', '')}:\n{lst.get('content', '')}")
            parts.append(j.get("additionalPlain") or "")
            desc = sanitize_text("\n".join(parts))
            sal = j.get("salaryRange") or {}
            loc = sanitize_short(cats.get("location"))
            wt = (j.get("workplaceType") or "").lower()
            out.append(NormalizedJob(
                external_id=str(j.get("id")),
                employer=board_token,
                title=sanitize_short(j.get("text"), 300),
                description=desc,
                location=loc or None,
                work_arrangement={"remote": "remote", "hybrid": "hybrid", "onsite": "onsite", "on-site": "onsite"}.get(
                    wt, infer_arrangement(loc)),
                employment_type=_emp_type(cats.get("commitment")),
                salary_min=annualize(sal.get("min"), sal.get("interval")),
                salary_max=annualize(sal.get("max"), sal.get("interval")), salary_currency=sal.get("currency"),
                posted_at=_dt(j.get("createdAt")),
                canonical_url=_https(j.get("hostedUrl")),
                apply_url=_https(j.get("applyUrl") or j.get("hostedUrl")),
                requirements=extract_requirements(desc),
            ))
        return out


class AshbyConnector(Connector):
    key = "ashby"
    BASE = "https://api.ashbyhq.com/posting-api/job-board"

    def fetch(self, board_token: str, params: dict | None = None) -> list[NormalizedJob]:
        tok = _safe_token(board_token)
        data = self._get_json(f"{self.BASE}/{tok}", params={"includeCompensation": "true"})
        out = []
        for j in data.get("jobs", []):
            if j.get("isListed") is False:
                continue
            desc = sanitize_text(j.get("descriptionPlain") or j.get("descriptionHtml"))
            loc = sanitize_short(j.get("location"))
            wt = (j.get("workplaceType") or "").lower()
            arrangement = {"remote": "remote", "hybrid": "hybrid", "onsite": "onsite"}.get(wt) or (
                "remote" if j.get("isRemote") else infer_arrangement(loc))
            smin = smax = cur = None
            comp = j.get("compensation") or {}
            for tier in comp.get("summaryComponents") or []:
                if (tier.get("compensationType") or "").lower() == "salary":
                    iv = tier.get("interval")
                    smin, smax = annualize(tier.get("minValue"), iv), annualize(tier.get("maxValue"), iv)
                    cur = tier.get("currencyCode")
                    break
            out.append(NormalizedJob(
                external_id=str(j.get("id")),
                employer=board_token,
                title=sanitize_short(j.get("title"), 300),
                description=desc,
                location=loc or None,
                work_arrangement=arrangement,
                employment_type=_emp_type(j.get("employmentType")),
                salary_min=smin, salary_max=smax, salary_currency=cur,
                posted_at=_dt(j.get("publishedAt")),
                canonical_url=_https(j.get("jobUrl")),
                apply_url=_https(j.get("applyUrl") or j.get("jobUrl")),
                requirements=extract_requirements(desc),
            ))
        return out


from .dice import DiceConnector  # noqa: E402

CONNECTORS: dict[str, type[Connector]] = {
    c.key: c for c in (GreenhouseConnector, LeverConnector, AshbyConnector, DiceConnector)
}
