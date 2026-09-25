"""Loads config/sources.yaml into the `sources` table and answers permission questions."""
from datetime import date, timedelta

import yaml
from sqlalchemy.orm import Session

from .. import audit
from ..config import get_settings
from ..models import Source

REQUIRED = ("key", "name", "method", "terms_url", "date_checked", "read_permitted", "submit_permitted")


class SourceNotPermitted(PermissionError):
    pass


def load_registry(path=None) -> list[dict]:  # noqa: ANN001
    with open(path or get_settings().sources_file) as fh:
        doc = yaml.safe_load(fh) or {}
    entries = doc.get("sources") or []
    for e in entries:
        missing = [k for k in REQUIRED if k not in e]
        if missing:
            raise ValueError(f"registry entry {e.get('key')} missing {missing}")
    return entries


def sync_registry(db: Session, entries: list[dict] | None = None) -> None:
    entries = entries if entries is not None else load_registry()
    seen = set()
    for e in entries:
        seen.add(e["key"])
        s = db.get(Source, e["key"])
        if s is None:
            s = Source(key=e["key"], enabled=False, auto_submit_opt_in=False)
            db.add(s)
        before = (s.read_permitted, s.submit_permitted)
        s.name, s.method, s.terms_url = e["name"], e["method"], e["terms_url"]
        s.date_checked = str(e["date_checked"])
        s.rate_limit_per_minute = int(e.get("rate_limit_per_minute") or 10)
        s.read_permitted, s.submit_permitted = bool(e["read_permitted"]), bool(e["submit_permitted"])
        s.registry_entry = {k: (str(v) if k == "date_checked" else v) for k, v in e.items()}
        # Permission revoked in registry: force user toggles off.
        if not s.read_permitted:
            s.enabled = False
        if not s.submit_permitted:
            s.auto_submit_opt_in = False
        if before != (s.read_permitted, s.submit_permitted) and before != (None, None):
            audit.record(db, "system", "source.permission_changed", "source", s.key,
                         {"read": s.read_permitted, "submit": s.submit_permitted})
    # Sources removed from the registry are disabled, not deleted (history is kept).
    for s in db.query(Source).all():
        if s.key not in seen:
            s.enabled = s.auto_submit_opt_in = s.read_permitted = s.submit_permitted = False
    db.commit()


def review_is_current(src: Source, today: date | None = None) -> bool:
    try:
        checked = date.fromisoformat(src.date_checked)
    except ValueError:
        return False
    return (today or date.today()) - checked <= timedelta(days=get_settings().source_recheck_days)


def assert_can_read(src: Source | None) -> None:
    if src is None:
        raise SourceNotPermitted("source not in registry")
    if not src.read_permitted:
        raise SourceNotPermitted(f"{src.key}: registry does not permit reading")
    if not src.enabled:
        raise SourceNotPermitted(f"{src.key}: disabled by user")
    if not review_is_current(src):
        raise SourceNotPermitted(f"{src.key}: permission review older than {get_settings().source_recheck_days} days; re-check terms")
