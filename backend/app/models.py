"""ORM models. Portable across PostgreSQL (prod) and SQLite (tests)."""
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, utcnow
from .security.crypto import EncryptedString


def _ts() -> Mapped[datetime]:
    return mapped_column(DateTime, default=utcnow, nullable=False)


# --------------------------------------------------------------------------- auth
class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="admin")  # admin | viewer
    totp_secret: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    created_at: Mapped[datetime] = _ts()


class UserSession(Base):
    __tablename__ = "sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = _ts()
    user: Mapped[User] = relationship()


# --------------------------------------------------------------------------- profile
class Profile(Base):
    __tablename__ = "profiles"
    id: Mapped[int] = mapped_column(primary_key=True)
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(EncryptedString)
    phone: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    location: Mapped[str | None] = mapped_column(String(200), nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    linkedin_url: Mapped[str | None] = mapped_column(String(300), nullable=True)
    portfolio_links: Mapped[list] = mapped_column(JSON, default=list)
    resume_folder: Mapped[str | None] = mapped_column(Text, nullable=True)
    folder_consent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(30))  # resume | linkedin_export | generated_resume | cover_letter
    filename: Mapped[str] = mapped_column(String(255))
    mime: Mapped[str] = mapped_column(String(100))
    sha256: Mapped[str] = mapped_column(String(64))
    store_path: Mapped[str] = mapped_column(String(300))
    size_bytes: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer, default=1)
    parse_status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|parsed|failed|rejected
    parse_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    text_path: Mapped[str | None] = mapped_column(String(300), nullable=True)  # encrypted extracted text
    created_at: Mapped[datetime] = _ts()
    __table_args__ = (UniqueConstraint("profile_id", "sha256", name="uq_document_hash"),)


class Fact(Base):
    __tablename__ = "facts"
    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(30))  # role|achievement|education|skill|certification|project|contact|summary
    data: Mapped[dict] = mapped_column(JSON)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("facts.id", ondelete="CASCADE"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")  # PENDING|APPROVED|REJECTED
    # provenance
    origin: Mapped[str] = mapped_column(String(30))  # resume|linkedin_export|user
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"), nullable=True)
    char_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    char_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = _ts()


class FactConflict(Base):
    __tablename__ = "fact_conflicts"
    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"))
    fact_a_id: Mapped[int] = mapped_column(ForeignKey("facts.id", ondelete="CASCADE"))
    fact_b_id: Mapped[int | None] = mapped_column(ForeignKey("facts.id", ondelete="CASCADE"), nullable=True)
    field: Mapped[str] = mapped_column(String(60))
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="OPEN")  # OPEN|RESOLVED
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _ts()


class StandardAnswer(Base):
    __tablename__ = "standard_answers"
    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"))
    question_key: Mapped[str] = mapped_column(String(80))
    answer: Mapped[str] = mapped_column(EncryptedString)
    sensitive: Mapped[bool] = mapped_column(Boolean, default=False)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    __table_args__ = (UniqueConstraint("profile_id", "question_key", name="uq_answer_key"),)


class Preferences(Base):
    __tablename__ = "preferences"
    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), unique=True)
    titles: Mapped[list] = mapped_column(JSON, default=list)
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    seniority: Mapped[list] = mapped_column(JSON, default=list)
    geographies: Mapped[list] = mapped_column(JSON, default=list)
    work_arrangements: Mapped[list] = mapped_column(JSON, default=lambda: ["remote", "hybrid", "onsite"])
    employment_types: Mapped[list] = mapped_column(JSON, default=lambda: ["full_time"])
    salary_floor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    salary_currency: Mapped[str] = mapped_column(String(3), default="USD")
    industries: Mapped[list] = mapped_column(JSON, default=list)
    excluded_employers: Mapped[list] = mapped_column(JSON, default=list)
    excluded_terms: Mapped[list] = mapped_column(JSON, default=list)
    min_score: Mapped[int] = mapped_column(Integer, default=70)
    polling_minutes: Mapped[int] = mapped_column(Integer, default=60)
    daily_application_limit: Mapped[int] = mapped_column(Integer, default=10)
    weights: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


# --------------------------------------------------------------------------- sources & jobs
class Source(Base):
    __tablename__ = "sources"
    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    method: Mapped[str] = mapped_column(String(60))
    terms_url: Mapped[str] = mapped_column(String(400))
    date_checked: Mapped[str] = mapped_column(String(10))
    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, default=30)
    read_permitted: Mapped[bool] = mapped_column(Boolean, default=False)
    submit_permitted: Mapped[bool] = mapped_column(Boolean, default=False)
    registry_entry: Mapped[dict] = mapped_column(JSON, default=dict)  # full registry entry snapshot
    # user toggles
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_submit_opt_in: Mapped[bool] = mapped_column(Boolean, default=False)
    daily_submit_limit: Mapped[int] = mapped_column(Integer, default=5)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class SourceBoard(Base):
    __tablename__ = "source_boards"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_key: Mapped[str] = mapped_column(ForeignKey("sources.key", ondelete="CASCADE"))
    board_token: Mapped[str] = mapped_column(String(120))
    employer_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Search-based sources (e.g. Dice): the saved search, e.g. {"keyword": "...", "location": "..."}
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    __table_args__ = (UniqueConstraint("source_key", "board_token", name="uq_board"),)


class ConnectorRun(Base):
    __tablename__ = "connector_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_key: Mapped[str] = mapped_column(String(50))
    board_token: Mapped[str] = mapped_column(String(120))
    started_at: Mapped[datetime] = _ts()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="running")  # running|ok|error|skipped
    jobs_seen: Mapped[int] = mapped_column(Integer, default=0)
    jobs_new: Mapped[int] = mapped_column(Integer, default=0)
    jobs_expired: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_key: Mapped[str] = mapped_column(ForeignKey("sources.key"))
    board_token: Mapped[str] = mapped_column(String(120))
    external_id: Mapped[str] = mapped_column(String(200))
    employer: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    requirements: Mapped[list] = mapped_column(JSON, default=list)
    location: Mapped[str | None] = mapped_column(String(300), nullable=True)
    work_arrangement: Mapped[str] = mapped_column(String(20), default="unknown")  # remote|hybrid|onsite|unknown
    employment_type: Mapped[str] = mapped_column(String(30), default="unknown")
    salary_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    salary_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    salary_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    first_seen_at: Mapped[datetime] = _ts()
    last_seen_at: Mapped[datetime] = _ts()
    expired_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # employer deadline, if stated
    canonical_url: Mapped[str | None] = mapped_column(String(600), nullable=True)
    apply_url: Mapped[str | None] = mapped_column(String(600), nullable=True)
    permission: Mapped[dict] = mapped_column(JSON, default=dict)
    # Source-specific extras: {"site": "linkedin", "summary_only": true, "dice_guid": "...", "dkim": "pass", ...}
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    dedupe_key: Mapped[str] = mapped_column(String(64), index=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    duplicate_of_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True)
    __table_args__ = (UniqueConstraint("source_key", "external_id", name="uq_job_source_ext"),)

    @property
    def is_active(self) -> bool:
        return self.expired_at is None and self.duplicate_of_id is None


# --------------------------------------------------------------------------- applications
class Application(Base):
    __tablename__ = "applications"
    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"))
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    state: Mapped[str] = mapped_column(String(30), default="DISCOVERED", index=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    match: Mapped[dict] = mapped_column(JSON, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    job: Mapped[Job] = relationship()
    __table_args__ = (UniqueConstraint("profile_id", "job_id", name="uq_app_profile_job"),)


class Packet(Base):
    __tablename__ = "packets"
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    resume_document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"), nullable=True)
    resume_lines: Mapped[list] = mapped_column(JSON, default=list)  # [{text, fact_ids}]
    cover_letter: Mapped[list] = mapped_column(JSON, default=list)  # [{text, fact_ids, answer_ids, job_fields}]
    answers: Mapped[list] = mapped_column(JSON, default=list)  # [{question, key, answer, status, evidence}]
    unsupported_count: Mapped[int] = mapped_column(Integer, default=0)
    # How materials were generated, e.g. {"cover_letter": {"generator": "ollama:qwen3.6:35b", "kept": 4, "dropped": [...]}}
    generation: Mapped[dict] = mapped_column(JSON, default=dict)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = _ts()


class SubmissionAttempt(Base):
    __tablename__ = "submission_attempts"
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"))
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    source_key: Mapped[str] = mapped_column(String(50))
    adapter: Mapped[str] = mapped_column(String(60))  # adapter name or "manual"
    packet_id: Mapped[int | None] = mapped_column(ForeignKey("packets.id", ondelete="SET NULL"), nullable=True)
    # PENDING|SUBMITTED|CONFIRMED|FAILED|CHALLENGE|UNKNOWN (UNKNOWN = outcome uncertain; never auto-retried)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    # Encrypted JSON snapshot of exactly what was submitted (resolved answers, material ids/hashes).
    snapshot: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    confirmation_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    confirmation_url: Mapped[str | None] = mapped_column(String(600), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = _ts()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class HandoffTask(Base):
    __tablename__ = "handoff_tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"))
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="OPEN")  # OPEN|DONE|DISMISSED
    created_at: Mapped[datetime] = _ts()
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# --------------------------------------------------------------------------- controls & audit
class SystemControl(Base):
    __tablename__ = "system_controls"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AuditEvent(Base):
    """Append-only. UPDATE/DELETE are blocked by a DB trigger (see audit.install_audit_guards)."""

    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = _ts()
    actor: Mapped[str] = mapped_column(String(80))  # user:<name> | system | worker
    action: Mapped[str] = mapped_column(String(80))
    entity_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


class MaintenanceFlag(Base):
    """While a row with active=1 exists, audit purge (retention) is allowed by the trigger."""

    __tablename__ = "maintenance_flags"
    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    active: Mapped[bool] = mapped_column(Boolean, default=False)
