"""File intake: consented folder listing, strict validation, optional ClamAV scan, isolated parsing."""
import json
import os
import socket
import struct
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from ..config import get_settings

ALLOWED = {".pdf": "application/pdf", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
BACKEND_DIR = Path(__file__).resolve().parents[2]


class IntakeError(ValueError):
    pass


@dataclass
class FolderFile:
    name: str
    size: int
    modified: float


def resolve_consented_folder(folder: str) -> Path:
    p = Path(folder).expanduser().resolve()
    if not p.is_dir():
        raise IntakeError("folder does not exist or is not a directory")
    return p


def list_folder(folder: str) -> list[FolderFile]:
    """Top-level only, PDF/DOCX only, and no symlinks escaping the folder."""
    root = resolve_consented_folder(folder)
    out = []
    for entry in sorted(root.iterdir()):
        if entry.suffix.lower() not in ALLOWED or entry.name.startswith("."):
            continue
        real = entry.resolve()
        if real.parent != root or not real.is_file():
            continue
        st = real.stat()
        out.append(FolderFile(entry.name, st.st_size, st.st_mtime))
    return out


def read_from_folder(folder: str, name: str) -> bytes:
    root = resolve_consented_folder(folder)
    if "/" in name or "\\" in name or name.startswith("."):
        raise IntakeError("invalid file name")
    real = (root / name).resolve()
    if real.parent != root or not real.is_file():
        raise IntakeError("file is not inside the consented folder")
    if real.stat().st_size > get_settings().max_upload_bytes:
        raise IntakeError("file exceeds size limit")
    with open(real, "rb") as fh:  # read-only
        return fh.read()


def validate(filename: str, data: bytes) -> tuple[str, str]:
    """Returns (kind, mime). Checks extension, size and magic bytes."""
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED:
        raise IntakeError("only .pdf and .docx files are accepted")
    if len(data) == 0:
        raise IntakeError("empty file")
    if len(data) > get_settings().max_upload_bytes:
        raise IntakeError("file exceeds size limit")
    if ext == ".pdf" and not data[:1024].lstrip().startswith(b"%PDF-"):
        raise IntakeError("file content is not a PDF")
    if ext == ".docx":
        if not data.startswith(b"PK\x03\x04") or b"word/" not in data[:200_000]:
            raise IntakeError("file content is not a DOCX document")
    return ext[1:], ALLOWED[ext]


def av_scan(data: bytes) -> None:
    """ClamAV INSTREAM scan when JA_CLAMAV_HOST is configured. Raises IntakeError on detection."""
    s = get_settings()
    if not s.clamav_host:
        return
    try:
        with socket.create_connection((s.clamav_host, s.clamav_port), timeout=30) as sock:
            sock.sendall(b"zINSTREAM\0")
            for i in range(0, len(data), 8192):
                chunk = data[i : i + 8192]
                sock.sendall(struct.pack("!L", len(chunk)) + chunk)
            sock.sendall(struct.pack("!L", 0))
            reply = sock.recv(4096).decode(errors="replace").strip("\0\n ")
    except OSError as e:
        raise IntakeError(f"virus scanner unavailable: {e}") from e
    if not reply.endswith("OK"):
        raise IntakeError(f"file rejected by virus scanner: {reply[:120]}")


def parse_isolated(kind: str, data: bytes) -> str:
    s = get_settings()
    # -I: isolated mode (no user site, no PYTHON* env vars). The env is rebuilt from scratch so the
    # parser never sees JA_ENCRYPTION_KEY, DB credentials or any other secret.
    env = {"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8"}
    worker = Path(__file__).with_name("parse_worker.py")
    try:
        proc = subprocess.run(
            [sys.executable, "-I", str(worker), kind, str(s.parser_memory_mb), str(s.parser_timeout_seconds)],
            input=data,
            capture_output=True,
            timeout=s.parser_timeout_seconds + 5,
            env=env,
            cwd=str(BACKEND_DIR),
        )
    except subprocess.TimeoutExpired as e:
        raise IntakeError("parser timed out") from e
    try:
        out = json.loads(proc.stdout or b"{}")
    except json.JSONDecodeError as e:
        raise IntakeError(f"parser crashed (exit {proc.returncode})") from e
    if not out.get("ok"):
        raise IntakeError(f"could not parse document: {out.get('error', 'unknown error')}")
    return out["text"]
