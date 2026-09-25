"""Runtime configuration. All values come from environment / .env; nothing secret is hard-coded."""
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(REPO_ROOT / ".env"), env_prefix="JA_", extra="ignore")

    env: str = "dev"  # dev | test | prod
    database_url: str = f"sqlite:///{REPO_ROOT / 'data' / 'dev.sqlite3'}"
    redis_url: str = "redis://localhost:6389/0"

    # Fernet key (urlsafe base64, 32 bytes). Required outside tests. Generate: python -m app.cli gen-key
    encryption_key: str = ""

    data_dir: Path = REPO_ROOT / "data"
    sources_file: Path = REPO_ROOT / "config" / "sources.yaml"
    # Presence of this file halts polling and submission even if the DB/API is unavailable.
    kill_switch_file: Path = REPO_ROOT / "data" / "KILL_SWITCH"

    # Auth
    session_ttl_minutes: int = 8 * 60
    mfa_required: bool = True
    cookie_secure: bool = False  # set True behind TLS
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5180"])

    # Uploads / parsing
    max_upload_bytes: int = 10 * 1024 * 1024
    parser_timeout_seconds: int = 20
    parser_memory_mb: int = 512
    clamav_host: str = ""  # empty = scanning disabled
    clamav_port: int = 3310

    # Sources
    source_recheck_days: int = 90
    http_timeout_seconds: float = 20.0
    user_agent: str = "jobApplier/0.1 (self-hosted personal job search)"

    # Local LLM (Ollama) for cover-letter drafting. Off unless enabled in the UI/API (system_controls 'llm').
    llm_base_url: str = "http://127.0.0.1:11434"  # Docker: http://host.docker.internal:11434
    llm_default_model: str = "qwen3.6:35b"
    llm_timeout_seconds: float = 180.0
    # Hosts other than loopback/private/host.docker.internal, and Ollama ":cloud" models, are refused unless True.
    llm_allow_remote: bool = False

    # Freshness
    listing_old_after_days: int = 60  # matching note only; never expires a still-published listing
    listing_stale_hours: int = 48  # listing must have been seen within this window to be submittable


@lru_cache
def get_settings() -> Settings:
    return Settings()
