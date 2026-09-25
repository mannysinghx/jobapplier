"""Dice job search via Dice's official MCP server (https://www.dice.com/about/mcp).

Permission basis (config/sources.yaml): the server is documented for "Custom MCP clients" and needs no auth for basic
use. Dice's tool terms, which are honored here:
  - search only: never used for applying (applications are handoffs on dice.com);
  - get_job_details is NOT called automatically for every result, only when the user asks for one job;
  - results must be shown with an AI-assisted search disclosure (the registry attribution, shown in the UI).
Dice ToS 8.2(k) forbids using Dice data to train or improve ML models. Dice descriptions are never sent to the
local LLM (which only ever receives approved candidate facts plus the job title/employer).
"""
import re
from datetime import UTC, datetime

from .base import Connector, ConnectorError, NormalizedJob, extract_requirements, parse_salary_text, sanitize_short, sanitize_text
from .mcp import MCPClient

ENDPOINT = "https://mcp.dice.com/mcp"
WORKPLACE = {"Remote", "On-Site", "Hybrid"}
EMPLOYMENT = {"FULLTIME", "CONTRACTS", "PARTTIME", "THIRD_PARTY", "INTERNSHIP"}
POSTED = {"ONE", "THREE", "SEVEN"}
_GUID = re.compile(r"^[0-9a-fA-F-]{36}$")


def validate_params(p: dict) -> dict:
    """Allowlisted saved-search parameters. Raises ValueError for anything else."""
    kw = str(p.get("keyword") or "").strip()
    if not kw or len(kw) > 100:
        raise ValueError("keyword is required (max 100 chars)")
    out: dict = {"keyword": kw}
    if p.get("location"):
        out["location"] = str(p["location"]).strip()[:100]
    wt = [w for w in (p.get("workplace_types") or []) if w in WORKPLACE]
    et = [e for e in (p.get("employment_types") or []) if e in EMPLOYMENT]
    if wt:
        out["workplace_types"] = wt
    if et:
        out["employment_types"] = et
    pd = p.get("posted_date") or "SEVEN"
    if pd not in POSTED:
        raise ValueError("posted_date must be ONE, THREE or SEVEN")
    out["posted_date"] = pd
    unknown = set(p) - {"keyword", "location", "workplace_types", "employment_types", "posted_date"}
    if unknown:
        raise ValueError(f"unsupported search fields: {sorted(unknown)}")
    return out


def _clean_url(url: str | None) -> str | None:
    if not url or not url.startswith("https://www.dice.com/"):
        return None
    return url.split("?", 1)[0] + "?utm_source=jobapplier&utm_medium=mcp"


def _dt(v) -> datetime | None:  # noqa: ANN001
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).astimezone(UTC).replace(tzinfo=None) if v else None
    except ValueError:
        return None


def _emp(v: str | None) -> str:
    t = (v or "").lower().replace("-", "").replace(" ", "")
    if "fulltime" in t:
        return "full_time"
    if "parttime" in t:
        return "part_time"
    if "contract" in t or "thirdparty" in t:
        return "contract"
    if "intern" in t:
        return "internship"
    return "unknown"


class DiceConnector(Connector):
    key = "dice"
    complete_listing = False  # search results, not a complete board listing
    PAGE_SIZE = 50

    def __init__(self, client, mcp: MCPClient | None = None):  # noqa: ANN001
        super().__init__(client)
        self.mcp = mcp or MCPClient(client, ENDPOINT)

    def fetch(self, board_token: str, params: dict | None = None) -> list[NormalizedJob]:
        args = {**validate_params(params or {}), "jobs_per_page": self.PAGE_SIZE, "page_number": 1, "sort": "datePosted"}
        data = self.mcp.call_tool("search_jobs", args)
        out = []
        for j in data.get("data") or []:
            guid = str(j.get("guid") or "")
            if not _GUID.match(guid):
                continue
            wt = [w for w in (j.get("workplaceTypes") or []) if isinstance(w, str)]
            arrangement = "remote" if (j.get("isRemote") or "Remote" in wt) else "hybrid" if "Hybrid" in wt else \
                "onsite" if "On-Site" in wt else "unknown"
            loc = j.get("jobLocation")
            loc_text = sanitize_short(loc.get("displayName") if isinstance(loc, dict) else loc) or ("Remote" if arrangement == "remote" else None)
            smin, smax, cur = parse_salary_text(j.get("salary"))
            summary = sanitize_text(j.get("summary"), 5000)
            out.append(NormalizedJob(
                external_id=guid, employer=sanitize_short(j.get("companyName")) or "Unknown employer",
                title=sanitize_short(j.get("title"), 300), description=summary, location=loc_text,
                work_arrangement=arrangement, employment_type=_emp(j.get("employmentType")),
                salary_min=smin, salary_max=smax, salary_currency=cur, posted_at=_dt(j.get("postedDate")),
                canonical_url=_clean_url(j.get("detailsPageUrl")), apply_url=_clean_url(j.get("detailsPageUrl")),
                requirements=extract_requirements(summary),
                meta={"site": "dice", "dice_guid": guid, "summary_only": True, "salary_text": sanitize_short(j.get("salary"), 120),
                      "employer_type": sanitize_short(j.get("employerType"), 40), "easy_apply": bool(j.get("easyApply"))},
            ))
        return out

    def job_details(self, guid: str) -> tuple[str, list[str]]:
        """User-initiated only (Dice asks clients not to fetch details for every result)."""
        if not _GUID.match(guid):
            raise ConnectorError("invalid Dice job id", permanent=True)
        d = self.mcp.call_tool("get_job_details", {"job_id": guid})
        desc = sanitize_text(d.get("description"))
        skills = []
        for s in d.get("skills") or []:
            name = s.get("name") if isinstance(s, dict) else s
            if isinstance(name, str) and 1 < len(name) <= 60:
                skills.append(sanitize_short(name, 60))
        return desc, skills
