"""Application state machine. This table is the single source of truth for allowed transitions."""
from sqlalchemy.orm import Session

from . import audit
from .models import Application

STATES = (
    "DISCOVERED", "MATCHED", "PREPARED", "NEEDS_REVIEW", "APPROVED", "SUBMITTED", "CONFIRMED",
    "REJECTED_BY_USER", "BLOCKED_BY_POLICY", "EXPIRED", "DUPLICATE", "FAILED", "FOLLOW_UP",
)

TRANSITIONS: dict[str, set[str]] = {
    "DISCOVERED": {"MATCHED", "BLOCKED_BY_POLICY", "EXPIRED", "DUPLICATE", "REJECTED_BY_USER"},
    # Re-scoring (e.g. after preference edits) may move a MATCHED job back below threshold.
    "MATCHED": {"PREPARED", "NEEDS_REVIEW", "DISCOVERED", "BLOCKED_BY_POLICY", "EXPIRED", "DUPLICATE", "REJECTED_BY_USER"},
    "PREPARED": {"NEEDS_REVIEW", "APPROVED", "PREPARED", "BLOCKED_BY_POLICY", "EXPIRED", "DUPLICATE", "REJECTED_BY_USER"},
    "NEEDS_REVIEW": {"PREPARED", "NEEDS_REVIEW", "APPROVED", "BLOCKED_BY_POLICY", "EXPIRED", "DUPLICATE", "REJECTED_BY_USER"},
    "APPROVED": {"SUBMITTED", "FAILED", "NEEDS_REVIEW", "BLOCKED_BY_POLICY", "EXPIRED", "DUPLICATE", "REJECTED_BY_USER"},
    "SUBMITTED": {"CONFIRMED", "FAILED"},
    "CONFIRMED": {"FOLLOW_UP"},
    "FOLLOW_UP": {"FOLLOW_UP"},
    "FAILED": {"APPROVED", "NEEDS_REVIEW", "REJECTED_BY_USER"},
    # A policy block can clear if the user changes preferences/exclusions: re-evaluate from DISCOVERED.
    "BLOCKED_BY_POLICY": {"DISCOVERED", "EXPIRED", "DUPLICATE", "REJECTED_BY_USER"},
    "REJECTED_BY_USER": {"DISCOVERED"},  # undo
    "EXPIRED": set(),
    "DUPLICATE": set(),
}

# States after which nothing automatic may touch the application.
USER_OWNED = {"SUBMITTED", "CONFIRMED", "FOLLOW_UP", "REJECTED_BY_USER"}
# States in which the automatic re-matcher may re-evaluate the application.
RESCORABLE = {"DISCOVERED", "MATCHED", "BLOCKED_BY_POLICY"}


class InvalidTransition(ValueError):
    pass


def can_transition(src: str, dst: str) -> bool:
    return dst in TRANSITIONS.get(src, set())


def transition(db: Session, app: Application, dst: str, actor: str, reason: str = "", **details) -> Application:
    src = app.state
    if src == dst and dst not in TRANSITIONS.get(src, set()):
        return app  # idempotent no-op
    if not can_transition(src, dst):
        audit.record(db, actor, "application.transition_rejected", "application", app.id,
                     {"from": src, "to": dst, "reason": reason[:200]})
        raise InvalidTransition(f"{src} -> {dst} is not allowed")
    app.state = dst
    audit.record(db, actor, "application.transition", "application", app.id,
                 {"from": src, "to": dst, "reason": reason[:200], **details})
    return app
