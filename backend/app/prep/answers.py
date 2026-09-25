"""Deterministic form-answer drafting.

- Profile fields answer identity questions.
- Everything else needs an explicit, user-APPROVED standard answer.
- Sensitive/legal questions are NEVER inferred. With no approved sensitive answer the result is SENSITIVE_MISSING.
- Attestations ("I certify that...") always need the human: status ATTESTATION, which blocks auto-submit.
- Values are stored as references (profile field / answer id), not copied into the packet, so sensitive
  values stay in encrypted columns. `resolve()` fetches the values for display or submission.
"""
import re

from ..models import Profile, StandardAnswer

SENSITIVE_KEYS = {
    "work_authorization", "sponsorship", "criminal_history", "disability", "veteran", "gender", "race_ethnicity",
    "sexual_orientation", "age", "pronouns", "background_check", "drug_test", "salary_history",
}

# Order matters: sensitive and attestation patterns are checked first.
PATTERNS: list[tuple[str, str]] = [
    ("attestation", r"\b(i (hereby )?(certify|attest|affirm|acknowledge|agree|consent)|by (checking|submitting)|signature|e-?sign)\b"),
    ("sponsorship", r"\b(sponsor(ship)?|visa|h-?1b|work permit)\b"),
    ("work_authorization", r"\b(authori[sz]ed to work|legally (eligible|authori[sz]ed)|right to work|work authori[sz]ation|eligible to work)\b"),
    ("criminal_history", r"\b(convicted|conviction|criminal|felony|misdemeanou?r|arrest)\b"),
    ("disability", r"\bdisabilit(y|ies)\b"),
    ("veteran", r"\b(veteran|military service|armed forces|protected veteran)\b"),
    ("gender", r"\b(gender|sex)\b"),
    ("race_ethnicity", r"\b(race|ethnicity|hispanic|latino)\b"),
    ("sexual_orientation", r"\b(sexual orientation|lgbt)\b"),
    ("pronouns", r"\bpronouns?\b"),
    ("age", r"\b(date of birth|birth ?date|your age|over (the age of )?18|at least 18)\b"),
    ("background_check", r"\bbackground (check|screening)\b"),
    ("drug_test", r"\bdrug (test|screen)\b"),
    ("salary_history", r"\b(current|previous|past) (salary|compensation|pay)\b"),
    ("first_name", r"^\s*(legal |preferred )?first name\b"),
    ("last_name", r"^\s*(legal )?(last name|surname|family name)\b"),
    ("full_name", r"^\s*(full )?name\s*\*?$"),
    ("email", r"\be-?mail\b"),
    ("phone", r"\b(phone|mobile|telephone)\b"),
    ("linkedin", r"\blinked ?in\b"),
    ("website", r"\b(website|portfolio|github|personal site)\b"),
    ("resume", r"\b(resume|résumé|cv)\b"),
    ("cover_letter", r"\bcover letter\b"),
    ("location", r"\b(current location|where are you (located|based)|city|location)\b"),
    ("salary_expectation", r"\b(salary|compensation|pay) (expectation|requirement|range)|desired (salary|compensation)\b"),
    ("start_date", r"\b(start date|notice period|when can you start|availability)\b"),
    ("relocation", r"\breloca(te|tion)\b"),
    ("how_heard", r"\b(how did you hear|referr(al|ed)|source)\b"),
    ("remote_preference", r"\b(remote|hybrid|in[- ]office|on[- ]site)\b"),
]

PROFILE_FIELD_KEYS = {"first_name", "last_name", "full_name", "email", "phone", "linkedin", "website", "location"}
FILE_KEYS = {"resume", "cover_letter"}

DEFAULT_QUESTIONS = [
    {"label": "First Name", "required": True}, {"label": "Last Name", "required": True},
    {"label": "Email", "required": True}, {"label": "Phone", "required": True},
    {"label": "Resume/CV", "required": True}, {"label": "Cover Letter", "required": False},
    {"label": "LinkedIn Profile", "required": False},
    {"label": "Are you legally authorized to work in the country where this job is located?", "required": True},
    {"label": "Will you now or in the future require visa sponsorship?", "required": True},
]


def classify(question: str) -> str:
    q = question.lower().strip()
    for key, rx in PATTERNS:
        if re.search(rx, q):
            return key
    return "unknown"


def _profile_value(profile: Profile, key: str) -> str | None:
    return {
        "first_name": profile.first_name, "last_name": profile.last_name,
        "full_name": f"{profile.first_name} {profile.last_name}".strip(),
        "email": profile.email, "phone": profile.phone, "linkedin": profile.linkedin_url,
        "website": (profile.portfolio_links or [None])[0], "location": profile.location,
    }.get(key)


def draft_answers(questions: list[dict], profile: Profile, answers: list[StandardAnswer]) -> list[dict]:
    by_key = {a.question_key: a for a in answers}
    out = []
    for q in questions:
        label = str(q.get("label", ""))[:500]
        key = q.get("key") or classify(label)
        required = bool(q.get("required"))
        entry = {"question": label, "key": key, "required": required, "sensitive": key in SENSITIVE_KEYS,
                 "status": "NEEDS_REVIEW", "source": None, "note": ""}
        if key == "attestation":
            entry.update(status="ATTESTATION", note="Legal attestation. You must read and confirm it yourself.")
        elif key in FILE_KEYS:
            entry.update(status="SUPPORTED", source={"packet_file": key})
        elif key in PROFILE_FIELD_KEYS and _profile_value(profile, key):
            entry.update(status="SUPPORTED", source={"profile_field": key})
        elif key in by_key and by_key[key].approved:
            entry.update(status="USER_APPROVED", source={"answer_id": by_key[key].id})
        elif key in SENSITIVE_KEYS:
            entry.update(status="SENSITIVE_MISSING",
                         note="Sensitive question. Never inferred. Add an explicit approved answer or answer it on the site.")
        elif key in by_key:
            entry.update(note="A standard answer exists but has not been approved.")
        else:
            entry.update(note="No approved answer. Review needed.")
        out.append(entry)
    return out


def is_blocking(entry: dict) -> bool:
    """True if the entry prevents AUTOMATIC submission. ON_SITE (the user answers it on the site) always blocks."""
    if entry["status"] in ("SUPPORTED", "USER_APPROVED"):
        return False
    # Optional, non-sensitive, unanswered questions can be left blank. Attestations always block.
    if entry["status"] == "NEEDS_REVIEW" and not entry["required"] and not entry["sensitive"]:
        return False
    return True


def resolve(entries: list[dict], profile: Profile, answers: list[StandardAnswer]) -> list[dict]:
    by_id = {a.id: a for a in answers}
    out = []
    for e in entries:
        src = e.get("source") or {}
        val = None
        if "profile_field" in src:
            val = _profile_value(profile, src["profile_field"])
        elif "answer_id" in src and src["answer_id"] in by_id:
            val = by_id[src["answer_id"]].answer
        elif "packet_file" in src:
            val = f"[attached {src['packet_file']}]"
        out.append({**e, "answer": val})
    return out
