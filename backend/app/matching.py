"""Deterministic, explainable matching. Only APPROVED facts count as evidence.

Default weights: title 25, skills 25, seniority 15, location 15, domain 10, compensation 10.
Hard exclusions override the score. Missing evidence lowers the score and is never assumed.
"""
import re
from dataclasses import dataclass, field
from datetime import date

from .config import get_settings
from .connectors.base import sanitize_short
from .ingestion.extract import months_between
from .models import Fact, Job, Preferences

DEFAULT_WEIGHTS = {"title": 25, "skills": 25, "seniority": 15, "location": 15, "domain": 10, "compensation": 10}

SENIORITY_LEVELS = ["intern", "junior", "mid", "senior", "staff", "principal", "director", "vp"]
_SENIORITY_PATTERNS = [
    ("intern", r"\bintern(ship)?\b"),
    ("vp", r"\b(vp|vice president)\b"),
    ("director", r"\b(director|head of)\b"),
    ("principal", r"\b(principal|distinguished)\b"),
    ("staff", r"\b(staff|lead)\b"),
    ("senior", r"\b(senior|sr\.?)\b"),
    ("junior", r"\b(junior|jr\.?|entry[- ]level|associate|graduate)\b"),
]
# Words that define a different kind of role. If the job title has one the target title lacks, title fit is halved
# (e.g. "Engineering Manager" for someone targeting "Software Engineer").
ROLE_NOUNS = {"manager", "director", "head", "recruiter", "designer", "scientist", "analyst", "executive", "sales",
              "marketing", "counsel", "attorney", "accountant", "researcher", "intern", "consultant", "administrator",
              "coordinator", "specialist", "writer", "partner", "representative", "operations", "support"}
_STOP = {"and", "or", "the", "of", "a", "an", "to", "in", "for", "with", "i", "ii", "iii", "iv", "&", "-", "remote", "hybrid"}
_SYN = {"sr": "senior", "jr": "junior", "eng": "engineer", "engineering": "engineer", "dev": "developer",
        "swe": "software engineer", "mgr": "manager", "ml": "machine learning", "ai": "artificial intelligence"}

# Built-in skill lexicon used to detect requirements in job text (combined with the user's approved skills + keywords).
SKILL_LEXICON = {
    "python", "java", "javascript", "typescript", "golang", "rust", "c++", "c#", "ruby", "php", "scala", "kotlin",
    "swift", "sql", "postgresql", "mysql", "mongodb", "redis", "elasticsearch", "kafka", "spark", "hadoop", "airflow",
    "dbt", "snowflake", "bigquery", "aws", "gcp", "azure", "docker", "kubernetes", "terraform", "ansible", "linux",
    "react", "vue", "angular", "node.js", "django", "flask", "fastapi", "spring", "graphql", "rest api", "restful", "grpc",
    "machine learning", "deep learning", "pytorch", "tensorflow", "nlp", "llm", "computer vision", "data analysis",
    "tableau", "power bi", "microsoft excel", "ci/cd", "git", "microservices", "distributed systems", "security", "devops",
    "sre", "agile", "scrum", "product management", "figma", "salesforce", "sap", "networking", "etl",
}


def _tokens(s: str) -> set[str]:
    s = s.lower()
    for k, v in _SYN.items():
        s = re.sub(rf"\b{re.escape(k)}\b", v, s)
    return {t for t in re.split(r"[^a-z0-9+#.]+", s) if t and t not in _STOP}


def _contains(haystack: str, needle: str) -> bool:
    n = needle.lower().strip()
    if not n:
        return False
    return re.search(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])", haystack) is not None


def job_seniority(title: str) -> str:
    t = title.lower()
    for level, rx in _SENIORITY_PATTERNS:
        if re.search(rx, t):
            return level
    return "mid"


def required_years(text: str) -> int | None:
    yrs = [int(m.group(1)) for m in re.finditer(r"(\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?years?", text.lower())]
    yrs = [y for y in yrs if 0 < y <= 25]
    return min(yrs) if yrs else None


def candidate_years(roles: list[Fact], today: date | None = None) -> float:
    today = today or date.today()
    months: set[tuple[int, int]] = set()
    for r in roles:
        months.update(months_between(r.data.get("start"), r.data.get("end"), (today.year, today.month)))
    return round(len(months) / 12, 1)


@dataclass
class MatchResult:
    score: float
    components: dict
    evidence: list[dict] = field(default_factory=list)
    unmet: list[str] = field(default_factory=list)
    exclusions: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    required_skills: list[str] = field(default_factory=list)
    matched_skill_fact_ids: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"score": self.score, "components": self.components, "evidence": self.evidence, "unmet": self.unmet,
                "exclusions": self.exclusions, "notes": self.notes, "required_skills": self.required_skills,
                "matched_skill_fact_ids": self.matched_skill_fact_ids}


def hard_exclusions(job: Job, prefs: Preferences) -> list[str]:
    out = []
    if job.expired_at is not None:
        out.append("listing expired")
    if job.duplicate_of_id is not None:
        out.append(f"duplicate of job {job.duplicate_of_id}")
    emp = job.employer.lower()
    for ex in prefs.excluded_employers or []:
        if ex and ex.lower().strip() in emp:
            out.append(f"excluded employer: {sanitize_short(ex, 80)}")
    hay = f"{job.title}\n{job.description}".lower()
    for term in prefs.excluded_terms or []:
        if term and _contains(hay, term):
            out.append(f"excluded term present: {sanitize_short(term, 80)}")
    if prefs.salary_floor and job.salary_max is not None and (
            not job.salary_currency or job.salary_currency.upper() == prefs.salary_currency.upper()):
        if job.salary_max < prefs.salary_floor:
            out.append(f"salary max {int(job.salary_max)} below floor {prefs.salary_floor}")
    allowed = set(prefs.work_arrangements or [])
    if allowed and job.work_arrangement != "unknown" and job.work_arrangement not in allowed:
        out.append(f"work arrangement '{job.work_arrangement}' not accepted")
    if prefs.employment_types and job.employment_type != "unknown" and job.employment_type not in prefs.employment_types:
        out.append(f"employment type '{job.employment_type}' not accepted")
    if job.work_arrangement == "onsite" and prefs.geographies and job.location:
        if not any(_contains(job.location.lower(), g) for g in prefs.geographies):
            out.append("on-site location outside preferred geographies")
    return out


def score_job(job: Job, prefs: Preferences, facts: list[Fact], today: date | None = None) -> MatchResult:
    approved = [f for f in facts if f.status == "APPROVED"]
    roles = [f for f in approved if f.kind == "role"]
    skills = [f for f in approved if f.kind == "skill"]
    w = {**DEFAULT_WEIGHTS, **(prefs.weights or {})}
    comp: dict[str, dict] = {}
    evidence: list[dict] = []
    unmet: list[str] = []
    notes: list[str] = []
    text = f"{job.title}\n{job.description}\n" + "\n".join(job.requirements or [])
    low = text.lower()

    # ---- title (25)
    jt = _tokens(job.title)
    best, best_src = 0.0, None
    targets = [(t, "preference") for t in prefs.titles or []] + [(r.data.get("title", ""), f"fact:{r.id}") for r in roles]
    for t, src in targets:
        tt = _tokens(t)
        if not tt or not jt:
            continue
        sim = len(jt & tt) / len(tt)  # share of the target title's words found in the job title
        if (jt & ROLE_NOUNS) - tt:
            sim *= 0.5
        if sim > best:
            best, best_src = sim, (t, src)
    comp["title"] = {"points": round(w["title"] * best, 1), "max": w["title"]}
    if best_src:
        evidence.append({"criterion": "title", "detail": f"'{job.title}' vs '{best_src[0]}' ({int(best * 100)}%)",
                         "fact_ids": [int(best_src[1].split(':')[1])] if best_src[1].startswith("fact:") else []})
    else:
        unmet.append("title does not resemble preferred or held titles")

    # ---- skills (25), evidence-backed only
    vocab = {s.lower() for s in SKILL_LEXICON} | {k.lower() for k in prefs.keywords or []} | {
        (s.data.get("name") or "").lower() for s in skills}
    required = sorted({v for v in vocab if v and len(v) > 1 and _contains(low, v)})
    skill_index: dict[str, list[int]] = {}
    for s in skills:
        skill_index.setdefault((s.data.get("name") or "").lower(), []).append(s.id)
    matched = {r: skill_index[r] for r in required if r in skill_index}
    if required:
        ratio = len(matched) / len(required)
        comp["skills"] = {"points": round(w["skills"] * ratio, 1), "max": w["skills"]}
        for r, ids in matched.items():
            evidence.append({"criterion": "skill", "detail": r, "fact_ids": ids})
        unmet += [f"no approved evidence for skill: {r}" for r in required if r not in matched]
    else:
        comp["skills"] = {"points": 0.0, "max": w["skills"]}
        notes.append("no recognizable skill requirements in listing; skills scored 0 (no assumption)")

    # ---- seniority / experience (15)
    level = job_seniority(job.title)
    level_pts = 0.0
    if prefs.seniority:
        if level in prefs.seniority:
            level_pts = 1.0
        elif any(abs(SENIORITY_LEVELS.index(level) - SENIORITY_LEVELS.index(p)) == 1
                 for p in prefs.seniority if p in SENIORITY_LEVELS):
            level_pts = 0.5
    else:
        level_pts = 0.5
        notes.append("no seniority preference set")
    need = required_years(job.description)
    have = candidate_years(roles, today)
    if need is None:
        years_pts = 0.5
        notes.append("listing states no years requirement")
    else:
        years_pts = min(have / need, 1.0) if need else 1.0
        if have < need:
            unmet.append(f"requires {need}+ years; approved roles total {have} years")
    comp["seniority"] = {"points": round(w["seniority"] * (level_pts + years_pts) / 2, 1), "max": w["seniority"],
                         "job_level": level, "required_years": need, "candidate_years": have}
    if roles:
        evidence.append({"criterion": "experience", "detail": f"{have} years across approved roles",
                         "fact_ids": [r.id for r in roles]})

    # ---- location / arrangement (15)
    allowed = set(prefs.work_arrangements or [])
    arr_ok = job.work_arrangement in allowed if job.work_arrangement != "unknown" else None
    geo_ok = None
    if prefs.geographies and job.location:
        geo_ok = any(_contains(job.location.lower(), g) for g in prefs.geographies) or job.work_arrangement == "remote"
    parts = [x for x in (arr_ok, geo_ok) if x is not None]
    loc_ratio = (sum(parts) / len(parts)) if parts else 0.5
    if not parts:
        notes.append("work arrangement/location not stated clearly")
    comp["location"] = {"points": round(w["location"] * loc_ratio, 1), "max": w["location"],
                        "arrangement": job.work_arrangement, "location": job.location}
    if arr_ok is False or geo_ok is False:
        unmet.append("location or work arrangement mismatch")

    # ---- domain (10)
    if prefs.industries:
        hits = [i for i in prefs.industries if _contains(low, i) or _contains(job.employer.lower(), i)]
        comp["domain"] = {"points": round(w["domain"] * (1.0 if hits else 0.0), 1), "max": w["domain"], "hits": hits}
        if not hits:
            unmet.append("no preferred industry terms in listing")
    else:
        comp["domain"] = {"points": round(w["domain"] * 0.5, 1), "max": w["domain"], "hits": []}
        notes.append("no industry preference set")

    # ---- compensation (10)
    if job.salary_max is not None and prefs.salary_floor:
        ok = job.salary_max >= prefs.salary_floor
        comp["compensation"] = {"points": w["compensation"] if ok else 0, "max": w["compensation"]}
    elif job.salary_max is not None:
        comp["compensation"] = {"points": w["compensation"], "max": w["compensation"]}
    else:
        comp["compensation"] = {"points": round(w["compensation"] * 0.5, 1), "max": w["compensation"]}
        notes.append("salary not disclosed")

    if (job.meta or {}).get("summary_only"):
        notes.append("only a summary is available: add or fetch the full description for a fuller match")
    if job.posted_at is not None:
        age = ((today or date.today()) - job.posted_at.date()).days
        if age > get_settings().listing_old_after_days:
            notes.append(f"posted {age} days ago (still listed)")
    total_max = sum(c["max"] for c in comp.values()) or 1
    score = round(100 * sum(c["points"] for c in comp.values()) / total_max, 1)
    excl = hard_exclusions(job, prefs)
    return MatchResult(score=0.0 if excl else score, components=comp, evidence=evidence, unmet=unmet,
                       exclusions=excl, notes=notes, required_skills=required,
                       matched_skill_fact_ids={k: v for k, v in matched.items()})
