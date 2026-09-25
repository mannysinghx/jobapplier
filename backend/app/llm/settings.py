"""Runtime LLM settings (stored in system_controls). Off by default."""
from sqlalchemy.orm import Session

from .. import audit
from ..config import get_settings
from ..models import SystemControl

KEY = "llm"


def get_llm_config(db: Session) -> dict:
    row = db.get(SystemControl, KEY)
    val = dict(row.value) if row else {}
    return {"enabled": bool(val.get("enabled", False)), "model": val.get("model") or get_settings().llm_default_model}


def set_llm_config(db: Session, enabled: bool, model: str, actor: str) -> dict:
    row = db.get(SystemControl, KEY) or SystemControl(key=KEY, value={})
    row.value = {"enabled": bool(enabled), "model": model}
    db.add(row)
    audit.record(db, actor, "llm.settings_updated", details={"enabled": bool(enabled), "model": model})
    db.commit()
    return get_llm_config(db)
