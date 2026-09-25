"""Global pause / kill switch. Two independent signals, and either one halts polling and submission:

1. DB flag `system_controls.global_pause` (toggled from the UI/API)
2. The file at settings.kill_switch_file (`touch data/KILL_SWITCH`), which works even when the API or DB is down
"""
from sqlalchemy.orm import Session

from . import audit
from .config import get_settings
from .models import SystemControl

PAUSE_KEY = "global_pause"


def kill_switch_file_present() -> bool:
    return get_settings().kill_switch_file.exists()


def is_paused(db: Session) -> bool:
    if kill_switch_file_present():
        return True
    row = db.get(SystemControl, PAUSE_KEY)
    return bool(row and row.value.get("paused"))


def pause_state(db: Session) -> dict:
    row = db.get(SystemControl, PAUSE_KEY)
    return {
        "paused": is_paused(db),
        "db_flag": bool(row and row.value.get("paused")),
        "kill_switch_file": kill_switch_file_present(),
        "reason": (row.value.get("reason") if row else None),
    }


def set_paused(db: Session, paused: bool, actor: str, reason: str = "") -> dict:
    row = db.get(SystemControl, PAUSE_KEY) or SystemControl(key=PAUSE_KEY, value={})
    row.value = {"paused": paused, "reason": reason[:500]}
    db.add(row)
    audit.record(db, actor, "control.pause" if paused else "control.resume", details={"reason": reason[:200]})
    db.commit()
    return pause_state(db)
