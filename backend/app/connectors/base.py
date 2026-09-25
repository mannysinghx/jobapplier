"""Connector interface. Connectors only fetch and normalize. They never decide policy or submit."""
import html
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

import httpx

_TAG = re.compile(r"<[^>]+>")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏‪-‮⁦-⁩]")
_WS = re.compile(r"[ \t]+")


def sanitize_text(s: str | None, limit: int = 60_000) -> str:
    """Untrusted text -> plain text. Unescapes entities, strips markup, control and bidi-override characters."""
    if not s:
        return ""
    s = html.unescape(html.unescape(s))
    s = re.sub(r"<\s*(br|/p|/li|/h\d|/div)\s*/?>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"<\s*(script|style)[^>]*>.*?<\s*/\s*\1\s*>", " ", s, flags=re.IGNORECASE | re.DOTALL)
    s = _TAG.sub(" ", s)
    s = _CTRL.sub("", s)
    s = "\n".join(_WS.sub(" ", ln).strip() for ln in s.splitlines())
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    return s[:limit]


def sanitize_short(s: str | None, limit: int = 200) -> str:
    return sanitize_text(s, limit).replace("\n", " ").strip()


@dataclass
class NormalizedJob:
    external_id: str
    employer: str
    title: str
    description: str
    location: str | None = None
    work_arrangement: str = "unknown"  # remote|hybrid|onsite|unknown
    employment_type: str = "unknown"  # full_time|part_time|contract|internship|temporary|unknown
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    posted_at: datetime | None = None
    expires_at: datetime | None = None  # employer-stated application deadline
    canonical_url: str | None = None
    apply_url: str | None = None
    requirements: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)


class ConnectorError(RuntimeError):
    def __init__(self, msg: str, retry_after: float | None = None, permanent: bool = False):
        super().__init__(msg)
        self.retry_after = retry_after
        self.permanent = permanent


_PERIODS = {"hour": 2080, "day": 260, "week": 52, "month": 12, "year": 1, "annual": 1}


def annualize(value, interval: str | None):  # noqa: ANN001, ANN201
    """Convert a pay figure to a yearly amount. Returns None when the interval is unknown or the figure implausible,
    so an ambiguous salary never triggers a salary-floor exclusion."""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    t = (interval or "").lower()
    factor = next((f for k, f in _PERIODS.items() if k in t), None)
    if factor is None:
        factor = 1 if v >= 1000 else None  # no interval given: only trust figures that look annual
    if factor is None:
        return None
    annual = v * factor
    return annual if 1000 <= annual <= 10_000_000 else None


_UNIT = r"(?:\s*(?:/|per|an?)\s*(?:yr|year|hr|hour|annum|month|mo|week|wk|day))?"
_SAL = re.compile(r"(?P<cur>[$£€]|USD|GBP|EUR|CAD)?\s*(?P<lo>\d[\d,]*(?:\.\d+)?)\s*(?P<lok>[kK])?(?P<lou>" + _UNIT + r")\s*"
                  r"(?:-|–|to)\s*(?:[$£€]|USD|GBP|EUR|CAD)?\s*(?P<hi>\d[\d,]*(?:\.\d+)?)\s*(?P<hik>[kK])?(?P<rest>.{0,40})")
_CUR = {"$": "USD", "£": "GBP", "€": "EUR"}


def parse_salary_text(s: str | None) -> tuple[float | None, float | None, str | None]:
    """'$60 - $70 per hour' -> annualized (124800, 145600, 'USD'). Returns Nones when unclear."""
    if not s:
        return None, None, None
    m = _SAL.search(s)
    if not m:
        return None, None, None
    lo = float(m.group("lo").replace(",", "")) * (1000 if m.group("lok") else 1)
    hi = float(m.group("hi").replace(",", "")) * (1000 if m.group("hik") else 1)
    rest = (m.group("rest") + " " + (m.group("lou") or "")).lower()
    interval = next((k for k in ("hour", "hr", "day", "week", "wk", "month", "mo", "year", "yr", "annum", "annual") if k in rest), None)
    interval = {"hr": "hour", "annum": "year", "yr": "year", "wk": "week", "mo": "month"}.get(interval, interval)
    cur = m.group("cur")
    return annualize(lo, interval), annualize(hi, interval), _CUR.get(cur, cur) if cur else None


def infer_arrangement(*texts: str | None) -> str:
    """Only from explicit markers in the listing's own location/workplace fields."""
    t = " ".join((x or "") for x in texts).lower()
    if "hybrid" in t:
        return "hybrid"
    if "remote" in t:
        return "remote"
    if any(k in t for k in ("on-site", "onsite", "in office", "in-office")):
        return "onsite"
    return "unknown"


def extract_requirements(description: str) -> list[str]:
    """Bullet lines under a requirements-style heading. Used for display and matching only."""
    out, capture = [], False
    for ln in description.splitlines():
        low = ln.lower().strip(" :")
        if re.search(r"(requirements|qualifications|what you.ll need|what we.re looking for|you have|must have|skills)", low) and len(low) < 60:
            capture = True
            continue
        if capture:
            if not ln.strip():
                continue
            if len(ln) < 60 and ln.strip().endswith(":"):
                capture = False
                continue
            out.append(ln.strip(" •-*"))
            if len(out) >= 40:
                break
    return out


class Connector(ABC):
    key: str
    # True: fetch() returns the board's COMPLETE listing, so absence means the job was taken down (expiry).
    # False (search-based sources): absence means nothing; jobs expire after a TTL instead.
    complete_listing: bool = True

    def __init__(self, client: httpx.Client):
        self.client = client

    @abstractmethod
    def fetch(self, board_token: str, params: dict | None = None) -> list[NormalizedJob]:
        """Return the jobs for a board (complete listing) or a saved search (params)."""

    def _get_json(self, url: str, params: dict | None = None):
        try:
            r = self.client.get(url, params=params)
        except httpx.HTTPError as e:
            raise ConnectorError(f"network error: {type(e).__name__}") from e
        if r.status_code == 429:
            ra = r.headers.get("Retry-After")
            raise ConnectorError("rate limited (429)", retry_after=float(ra) if ra and ra.isdigit() else 60.0)
        if r.status_code in (401, 403, 404):
            raise ConnectorError(f"HTTP {r.status_code} (board missing or access denied)", permanent=True)
        if r.status_code >= 400:
            raise ConnectorError(f"HTTP {r.status_code}")
        if len(r.content) > 25 * 1024 * 1024:
            raise ConnectorError("response too large", permanent=True)
        try:
            return r.json()
        except ValueError as e:
            raise ConnectorError("invalid JSON") from e
