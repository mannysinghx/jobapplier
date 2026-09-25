"""Submission adapter interface.

An adapter may be registered ONLY after its route has been verified to permit automated candidate
submission, with the permission recorded in config/sources.yaml (submit_permitted: true + submit_basis).
As of 2026-09-24 no route is verified, so ADAPTERS is empty in production and every
application goes to a handoff task.
"""
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class SubmissionPayload:
    application_id: int
    idempotency_key: str
    apply_url: str
    answers: list[dict]  # resolved
    resume_docx: bytes
    cover_letter_text: str


@dataclass
class SubmitResult:
    outcome: str  # SUBMITTED | CHALLENGE | FAILED | UNKNOWN
    confirmation_id: str | None = None
    confirmation_url: str | None = None
    challenge: str | None = None  # captcha | mfa | attestation | anti_bot | new_question
    error: str | None = None


class SubmissionAdapter(ABC):
    name: str
    source_key: str
    verified: bool = False  # must be True AND source.submit_permitted for the policy gate to pass

    @abstractmethod
    def submit(self, payload: SubmissionPayload, should_abort: Callable[[], bool]) -> SubmitResult:
        """Must call should_abort() before any irreversible step and return FAILED(error='aborted') if True.
        Must return CHALLENGE on CAPTCHA/MFA/attestation/anti-bot, and never try to solve it."""


ADAPTERS: dict[str, SubmissionAdapter] = {}


def register(adapter: SubmissionAdapter) -> None:
    ADAPTERS[adapter.source_key] = adapter


def unregister(source_key: str) -> None:
    ADAPTERS.pop(source_key, None)
