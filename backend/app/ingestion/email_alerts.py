"""Import job-alert emails that LinkedIn, Indeed, ZipRecruiter, Dice and Ladders send to the user's own inbox.

Permitted route: the user's own email. No site is contacted: links are stored, never followed.
Privacy/safety rules:
- Only messages whose From domain belongs to a known job site are parsed. Everything else in a mailbox is
  skipped without being read further, and nothing from it is stored.
- Raw emails are never stored. Only the extracted job fields are kept.
- A job link is kept only if it is https AND its host is on that site's own domain (anti-phishing for spoofed
  alerts). The mail provider's DKIM verdict (Authentication-Results) is recorded per job.
- HTML is parsed with the stdlib HTMLParser into text/links only. Nothing is executed or rendered.
"""
import email
import email.policy
import hashlib
import io
import re
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlparse

from ..connectors.base import NormalizedJob, infer_arrangement, parse_salary_text, sanitize_short

MAX_BYTES = 50 * 1024 * 1024
MAX_MESSAGES = 5000
MAX_JOBS_PER_MESSAGE = 60

# site -> (sender domains, allowed link domains)
SITES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "linkedin": (("linkedin.com",), ("linkedin.com",)),
    "indeed": (("indeed.com", "indeedemail.com"), ("indeed.com",)),
    "ziprecruiter": (("ziprecruiter.com",), ("ziprecruiter.com",)),
    "dice": (("dice.com",), ("dice.com",)),
    "ladders": (("theladders.com", "ladders.com"), ("theladders.com",)),
}
GENERIC_ANCHORS = re.compile(
    r"^(view|apply|see|show|more|all|search|manage|unsubscribe|settings|edit|help|privacy|terms|jobs?|home|"
    r"view (job|jobs|all|more|details|similar)|see (all|more)( jobs)?|easy apply|apply now|save|get the app|"
    r"linkedin|indeed|ziprecruiter|dice|ladders|learn more|update|here|click here)\b.*$", re.IGNORECASE)
LOCATION = re.compile(r"(remote|hybrid|on-?site|\b[A-Z][a-zA-Z .]+, [A-Z]{2}\b|united states|\bUSA?\b)", re.IGNORECASE)


class AlertImportError(ValueError):
    pass


def _host_ok(host: str, domains: tuple[str, ...]) -> bool:
    host = host.lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in domains)


def site_for_sender(from_header: str) -> str | None:
    addr = parseaddr(from_header or "")[1].lower()
    domain = addr.rsplit("@", 1)[-1] if "@" in addr else ""
    for site, (senders, _) in SITES.items():
        if _host_ok(domain, senders):
            return site
    return None


def dkim_verdict(msg: EmailMessage, site: str) -> str:
    """'pass' only if the receiving provider recorded dkim=pass for the site's domain."""
    results = " ".join(str(h) for h in (msg.get_all("Authentication-Results") or [])).lower()
    if not results:
        return "unknown"
    for d in SITES[site][0]:
        if re.search(rf"dkim=pass[^;]*header\.(d|i)=@?([a-z0-9.-]*\.)?{re.escape(d)}", results):
            return "pass"
    return "fail" if "dkim=fail" in results else "unknown"


def job_ref(site: str, href: str) -> tuple[str, str] | None:
    """(external_id, canonical https url) if href is a job link on the site's own domain, else None."""
    if not href.lower().startswith("https://"):
        return None
    u = urlparse(href)
    if not _host_ok(u.hostname or "", SITES[site][1]):
        return None
    decoded = unquote(unquote(href))
    if site == "linkedin":
        m = re.search(r"/jobs/view/(?:[^/?]*-)?(\d{6,})", decoded)
        return (m.group(1), f"https://www.linkedin.com/jobs/view/{m.group(1)}/") if m else None
    if site == "indeed":
        m = re.search(r"[?&]jk=([0-9a-f]{12,20})", decoded)
        return (m.group(1), f"https://www.indeed.com/viewjob?jk={m.group(1)}") if m else None
    if site == "dice":
        m = re.search(r"/job-detail/([0-9a-fA-F-]{36})", decoded)
        return (m.group(1).lower(), f"https://www.dice.com/job-detail/{m.group(1).lower()}") if m else None
    if site == "ladders":
        m = re.search(r"/job/([a-z0-9-]+?[-_](\d{5,}))(?:[/?#]|$)", decoded)
        return (m.group(2), f"https://www.theladders.com/job/{m.group(1)}") if m else None
    if site == "ziprecruiter":
        if not re.search(r"/(jobs?|k|c|ekm|km)/", u.path) and "jobs" not in u.path:
            return None
        qs = parse_qs(u.query)
        jid = (qs.get("jid") or qs.get("job_id") or [None])[0]
        key = jid or hashlib.sha256(u.path.encode()).hexdigest()[:20]
        # tracking links are kept as-is; the user clicks them from the handoff
        return key, href.split("#", 1)[0]
    return None


@dataclass
class _Chunk:
    kind: str  # "a" or "text"
    text: str
    href: str = ""


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[_Chunk] = []
        self._href: str | None = None
        self._buf: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):  # noqa: ANN001
        if tag in ("script", "style", "head"):
            self._skip += 1
        if tag == "a":
            self._flush()
            self._href = dict(attrs).get("href") or ""
        if tag in ("br", "p", "div", "tr", "td", "li", "table", "h1", "h2", "h3", "h4", "span"):
            self._flush()

    def handle_endtag(self, tag):  # noqa: ANN001
        if tag in ("script", "style", "head"):
            self._skip = max(0, self._skip - 1)
        if tag == "a":
            text = " ".join("".join(self._buf).split())
            self.chunks.append(_Chunk("a", text, self._href or ""))
            self._buf, self._href = [], None
        elif tag in ("p", "div", "tr", "td", "li", "h1", "h2", "h3", "h4", "span"):
            self._flush()

    def handle_data(self, data):  # noqa: ANN001
        if not self._skip:
            self._buf.append(data)

    def _flush(self) -> None:
        if self._href is not None:
            return  # inside a link: keep accumulating the anchor text
        text = " ".join("".join(self._buf).split())
        if text:
            self.chunks.append(_Chunk("text", text))
        self._buf = []


def _chunks_from_text(body: str) -> list[_Chunk]:
    out = []
    for ln in body.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        for m in re.finditer(r"https://\S+", ln):
            prev = ln[: m.start()].strip(" :-<>()")
            out.append(_Chunk("a", prev, m.group(0).rstrip(">).,")))
        rest = re.sub(r"https://\S+", "", ln).strip(" :-<>()")
        if rest and not re.search(r"https://", ln):
            out.append(_Chunk("text", rest))
    return out


@dataclass
class ParsedAlert:
    site: str
    jobs: list[NormalizedJob] = field(default_factory=list)
    rejected_links: int = 0


def parse_message(msg: EmailMessage) -> ParsedAlert | None:
    site = site_for_sender(str(msg.get("From", "")))
    if site is None:
        return None
    try:
        received = parsedate_to_datetime(str(msg.get("Date"))).astimezone(UTC).replace(tzinfo=None)
    except (TypeError, ValueError):
        received = None
    verdict = dkim_verdict(msg, site)
    html_part = msg.get_body(preferencelist=("html",))
    text_part = msg.get_body(preferencelist=("plain",))
    if html_part is not None:
        ex = _Extractor()
        try:
            ex.feed(html_part.get_content()[:2_000_000])
        except Exception:  # noqa: BLE001 - malformed HTML: treat as data, skip
            return ParsedAlert(site)
        ex.close()
        chunks = ex.chunks
    elif text_part is not None:
        chunks = _chunks_from_text(text_part.get_content()[:2_000_000])
    else:
        return ParsedAlert(site)

    result = ParsedAlert(site)
    by_id: dict[str, dict] = {}
    order: list[str] = []
    current: str | None = None
    for ch in chunks:
        if ch.kind == "a" and ch.href:
            ref = job_ref(site, ch.href)
            if ref is None:
                if ch.href.lower().startswith("http") and re.search(r"job", ch.href, re.IGNORECASE):
                    result.rejected_links += int(not _host_ok(urlparse(ch.href).hostname or "", SITES[site][1]))
                current = None if ch.text and not GENERIC_ANCHORS.match(ch.text) else current
                continue
            jid, url = ref
            entry = by_id.get(jid)
            if entry is None:
                entry = {"url": url, "title": "", "context": []}
                by_id[jid] = entry
                order.append(jid)
            text = sanitize_short(ch.text, 200)
            if text and not GENERIC_ANCHORS.match(text) and len(text) > len(entry["title"]) and len(text) >= 4:
                entry["title"] = text
            current = jid
        elif ch.kind == "text" and current and len(by_id[current]["context"]) < 6:
            t = sanitize_short(ch.text, 200)
            if t and t != by_id[current]["title"]:
                by_id[current]["context"].append(t)
    for jid in order[:MAX_JOBS_PER_MESSAGE]:
        e = by_id[jid]
        title = e["title"] or (e["context"][0] if e["context"] else "")
        ctx = [c for c in e["context"] if c != title]
        if not title or GENERIC_ANCHORS.match(title):
            continue
        location = next((c for c in ctx if LOCATION.search(c) and len(c) < 80), None)
        salary_line = next((c for c in ctx if "$" in c or re.search(r"\b(salary|per hour|/hr|/yr)\b", c, re.I)), None)
        employer = next((c for c in ctx if c not in (location, salary_line) and 1 < len(c) < 80
                         and not re.search(r"(ago|new|actively|recruiting|alumni|connections?|promoted|applicants?|easy apply)", c, re.I)), "")
        smin, smax, cur = parse_salary_text(salary_line)
        snippet = " · ".join(ctx)[:1000]
        result.jobs.append(NormalizedJob(
            external_id=f"{site}:{jid}", employer=employer or "Unknown employer", title=title,
            description=snippet, location=location, work_arrangement=infer_arrangement(location, title),
            salary_min=smin, salary_max=smax, salary_currency=cur, posted_at=received,
            canonical_url=e["url"], apply_url=e["url"],
            meta={"site": site, "via": "email_alert", "dkim": verdict, "summary_only": True,
                  "email_date": received.isoformat() if received else None, "employer_guessed": bool(employer)},
        ))
    return result


def iter_messages(filename: str, data: bytes):
    """Yields EmailMessage objects from .eml, .mbox or a .zip of .eml files."""
    if len(data) > MAX_BYTES:
        raise AlertImportError("file too large (max 50 MB)")
    name = filename.lower()
    if name.endswith(".zip") or data.startswith(b"PK\x03\x04"):
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as e:
            raise AlertImportError("invalid zip") from e
        infos = [i for i in zf.infolist() if i.filename.lower().endswith((".eml", ".mbox"))]
        if len(zf.infolist()) > MAX_MESSAGES or sum(i.file_size for i in zf.infolist()) > 4 * MAX_BYTES:
            raise AlertImportError("archive too large")
        for i in infos:
            yield from iter_messages(i.filename, zf.read(i))
        return
    if name.endswith(".mbox") or data.startswith(b"From "):
        parts = re.split(rb"(?m)^From .*\r?\n", data)
        for raw in [p for p in parts if p.strip()][:MAX_MESSAGES]:
            raw = re.sub(rb"(?m)^>From ", b"From ", raw)
            yield email.message_from_bytes(raw, policy=email.policy.default)
        return
    if name.endswith(".eml") or b"\nFrom:" in data[:20000] or data.startswith(b"From:"):
        yield email.message_from_bytes(data, policy=email.policy.default)
        return
    raise AlertImportError("expected .eml, .mbox or a .zip of .eml files")


def parse_file(filename: str, data: bytes) -> dict:
    """Returns {"jobs": [NormalizedJob], "messages": n, "alert_messages": n, "by_site": {...}, "rejected_links": n}."""
    jobs: list[NormalizedJob] = []
    stats = {"messages": 0, "alert_messages": 0, "by_site": {}, "rejected_links": 0}
    for msg in iter_messages(filename, data):
        stats["messages"] += 1
        parsed = parse_message(msg)
        if parsed is None:
            continue
        stats["alert_messages"] += 1
        stats["rejected_links"] += parsed.rejected_links
        stats["by_site"][parsed.site] = stats["by_site"].get(parsed.site, 0) + len(parsed.jobs)
        jobs.extend(parsed.jobs)
    return {"jobs": jobs, **stats}


__all__ = ["parse_file", "parse_message", "job_ref", "site_for_sender", "AlertImportError", "SITES", "datetime"]
