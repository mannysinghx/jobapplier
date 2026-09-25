"""Deterministic resume fact extraction with provenance (character spans into the extracted text).

Every extracted fact is PENDING until the user approves it. The heuristics are deliberately conservative:
anything uncertain goes into a field the user reviews, and nothing is guessed or filled in.
"""
import re
from dataclasses import dataclass, field

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_MON = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
_POINT = rf"(?:{_MON}\s+\d{{4}}|\d{{1,2}}/\d{{4}}|\d{{4}})"
DATE_RANGE = re.compile(
    rf"(?P<start>{_POINT})\s*(?:-|–|—|to)\s*(?P<end>{_POINT}|present|current|now|today)", re.IGNORECASE
)
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE = re.compile(r"(?:\+?\d{1,3}[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")
LINKEDIN = re.compile(r"(?:https?://)?(?:[a-z]{2,3}\.)?linkedin\.com/in/[A-Za-z0-9_-]+/?", re.IGNORECASE)
BULLET = re.compile(r"^\s*[•\-\*▪‣◦●■–]\s+")
DEGREE = re.compile(
    r"\b(b\.?s\.?c?|b\.?a\.?|b\.?e\.?|b\.?tech|bachelor'?s?|m\.?s\.?c?|m\.?a\.?|m\.?tech|m\.?eng|master'?s?|mba|ph\.?d|doctorate|associate'?s?|diploma)\b",
    re.IGNORECASE,
)
INSTITUTION = re.compile(r"\b(university|college|institute|school|academy|polytechnic)\b", re.IGNORECASE)
YEAR = re.compile(r"\b(?:19|20)\d{2}\b")

SECTION_ALIASES = {
    "experience": ["experience", "work experience", "professional experience", "employment", "employment history",
                   "work history", "career history", "relevant experience"],
    "education": ["education", "education and training", "academic background"],
    "skills": ["skills", "technical skills", "core skills", "core competencies", "key skills", "technologies",
               "skills & tools", "skills and tools", "tools"],
    "certifications": ["certifications", "certificates", "licenses & certifications", "licenses and certifications"],
    "projects": ["projects", "selected projects", "key projects"],
    "summary": ["summary", "profile", "professional summary", "about", "objective"],
    "achievements": ["achievements", "awards", "honors", "awards & honors"],
}
_HEADING_LOOKUP = {alias: sec for sec, aliases in SECTION_ALIASES.items() for alias in aliases}


@dataclass
class Line:
    text: str
    start: int
    end: int


@dataclass
class ExtractedFact:
    kind: str
    data: dict
    start: int | None
    end: int | None
    snippet: str
    children: list["ExtractedFact"] = field(default_factory=list)


def _lines(text: str) -> list[Line]:
    out, pos = [], 0
    for raw in text.split("\n"):
        stripped = raw.strip()
        if stripped:
            lead = len(raw) - len(raw.lstrip())
            out.append(Line(stripped, pos + lead, pos + lead + len(stripped)))
        pos += len(raw) + 1
    return out


def _heading(line: str) -> str | None:
    key = re.sub(r"[:\s]+$", "", line.strip().lower())
    key = re.sub(r"\s+", " ", key)
    if len(key) > 40:
        return None
    return _HEADING_LOOKUP.get(key)


def normalize_point(s: str) -> str | None:
    s = s.strip().lower().rstrip(".")
    if s in {"present", "current", "now", "today"}:
        return "present"
    m = re.match(r"(\d{1,2})/(\d{4})", s)
    if m:
        return f"{int(m.group(2)):04d}-{int(m.group(1)):02d}"
    m = re.match(r"([a-z]+)\.?\s+(\d{4})", s)
    if m and m.group(1)[:3] in MONTHS:
        return f"{int(m.group(2)):04d}-{MONTHS[m.group(1)[:3]]:02d}"
    m = re.match(r"(\d{4})$", s)
    if m:
        return f"{m.group(1)}"
    return None


def _split_title_employer(header: str) -> tuple[str, str]:
    header = re.sub(r"\s{2,}", " ", header).strip(" ,|-–—")
    m = re.match(r"(.+?)\s+at\s+(.+)", header, re.IGNORECASE)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    parts = [p.strip() for p in re.split(r"\s+[|–—-]\s+|,\s+|\s*\|\s*", header) if p.strip()]
    if len(parts) >= 2:
        return parts[0], parts[1]
    return header, ""


def _clean_bullet(s: str) -> str:
    return BULLET.sub("", s).strip()


def _experience(lines: list[Line]) -> list[ExtractedFact]:
    date_idx = [i for i, ln in enumerate(lines) if DATE_RANGE.search(ln.text)]
    roles: list[ExtractedFact] = []
    for n, i in enumerate(date_idx):
        ln = lines[i]
        m = DATE_RANGE.search(ln.text)
        remainder = (ln.text[: m.start()] + " " + ln.text[m.end():]).strip(" ,|-–—()")
        header_start = i
        header_parts = [remainder] if remainder else []
        prev_limit = date_idx[n - 1] if n > 0 else -1
        j = i - 1
        while j > prev_limit and len(header_parts) < 2 and not BULLET.match(lines[j].text) and len(lines[j].text) < 100:
            header_parts.insert(0, lines[j].text)
            header_start = j
            j -= 1
        header = " | ".join(p for p in header_parts if p)
        title, employer = _split_title_employer(header)
        start, end = normalize_point(m.group("start")), normalize_point(m.group("end"))
        role = ExtractedFact(
            "role",
            {"title": title, "employer": employer, "start": start, "end": end, "header_raw": header},
            lines[header_start].start,
            ln.end,
            " ".join(x.text for x in lines[header_start : i + 1]),
        )
        roles.append(role)
    # bodies: lines between one role's date line and the next role's header start
    for n, i in enumerate(date_idx):
        stop = len(lines)
        if n + 1 < len(date_idx):
            nxt = roles[n + 1]
            stop = next(k for k, ln in enumerate(lines) if ln.start == nxt.start)
        current: ExtractedFact | None = None
        for ln in lines[i + 1 : stop]:
            is_bullet = bool(BULLET.match(ln.text))
            if current is not None and not is_bullet and ln.text[:1].islower():
                current.data["text"] += " " + ln.text
                current.end = ln.end
                current.snippet += " " + ln.text
                continue
            text = _clean_bullet(ln.text)
            if len(text) < 3:
                continue
            current = ExtractedFact("achievement", {"text": text}, ln.start, ln.end, ln.text)
            roles[n].children.append(current)
    return roles


def _education(lines: list[Line]) -> list[ExtractedFact]:
    out = []
    used: set[int] = set()
    for i, ln in enumerate(lines):
        if i in used or not DEGREE.search(ln.text):
            continue
        institution, year_line = "", ln
        if INSTITUTION.search(ln.text):
            institution = ln.text
        for k in (i - 1, i + 1):
            if 0 <= k < len(lines) and k not in used and INSTITUTION.search(lines[k].text) and not institution:
                institution = lines[k].text
                used.add(k)
                year_line = lines[k] if YEAR.search(lines[k].text) and not YEAR.search(ln.text) else ln
        ym = re.findall(r"\b(?:19|20)\d{2}\b", year_line.text)
        out.append(ExtractedFact(
            "education",
            {"degree": _clean_bullet(ln.text), "institution": _clean_bullet(institution), "year": (ym[-1] if ym else None)},
            ln.start, ln.end, ln.text,
        ))
        used.add(i)
    return out


def _skills(lines: list[Line]) -> list[ExtractedFact]:
    out, seen = [], set()
    for ln in lines:
        body = _clean_bullet(ln.text)
        if ":" in body and len(body.split(":", 1)[0]) < 30:
            body = body.split(":", 1)[1]
        for tok in re.split(r"[,;|•·]| {2,}|\t", body):
            t = tok.strip(" .()")
            if 1 < len(t) <= 40 and t.lower() not in seen and not re.fullmatch(r"[\d\W]+", t):
                seen.add(t.lower())
                off = ln.text.find(t)
                s = ln.start + max(off, 0)
                out.append(ExtractedFact("skill", {"name": t}, s, s + len(t), t))
    return out


def _simple_items(kind: str, key: str, lines: list[Line]) -> list[ExtractedFact]:
    out: list[ExtractedFact] = []
    for ln in lines:
        t = _clean_bullet(ln.text)
        if out and not BULLET.match(ln.text) and t[:1].islower():
            out[-1].data[key] += " " + t
            out[-1].end = ln.end
            continue
        if len(t) >= 3:
            out.append(ExtractedFact(kind, {key: t}, ln.start, ln.end, ln.text))
    return out


def extract_facts(text: str) -> list[ExtractedFact]:
    lines = _lines(text)
    sections: dict[str, list[Line]] = {}
    header_lines: list[Line] = []
    current = None
    for ln in lines:
        sec = _heading(ln.text)
        if sec:
            current = sec
            sections.setdefault(sec, [])
            continue
        if current is None:
            header_lines.append(ln)
        else:
            sections[current].append(ln)

    facts: list[ExtractedFact] = []
    # contact details, top of the document only (before first heading) or anywhere for links
    for ln in header_lines + lines[:15]:
        for rx, key in ((EMAIL, "email"), (PHONE, "phone"), (LINKEDIN, "linkedin_url")):
            for m in rx.finditer(ln.text):
                val = m.group(0)
                if not any(f.kind == "contact" and f.data.get("field") == key for f in facts):
                    facts.append(ExtractedFact("contact", {"field": key, "value": val},
                                               ln.start + m.start(), ln.start + m.end(), val))
    if "summary" in sections and sections["summary"]:
        sl = sections["summary"]
        facts.append(ExtractedFact("summary", {"text": " ".join(x.text for x in sl)}, sl[0].start, sl[-1].end,
                                   " ".join(x.text for x in sl)[:500]))
    facts += _experience(sections.get("experience", []))
    facts += _education(sections.get("education", []))
    facts += _skills(sections.get("skills", []))
    facts += _simple_items("certification", "name", sections.get("certifications", []))
    facts += _simple_items("project", "text", sections.get("projects", []))
    facts += _simple_items("achievement", "text", sections.get("achievements", []))
    return facts


def months_between(start: str | None, end: str | None, today: tuple[int, int]) -> list[tuple[int, int]]:
    """Returns [(y, m)] months covered, for overlap-safe experience totals. Year-only dates are treated as January."""
    def parse(p: str | None, default: tuple[int, int] | None) -> tuple[int, int] | None:
        if not p:
            return default
        if p == "present":
            return today
        parts = p.split("-")
        return int(parts[0]), int(parts[1]) if len(parts) > 1 else 1

    s, e = parse(start, None), parse(end, None)
    if not s or not e or s > e:
        return []
    out, (y, m) = [], s
    while (y, m) <= e and len(out) < 12 * 60:
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out
