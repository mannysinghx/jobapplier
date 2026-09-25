"""Field and file encryption (Fernet: AES-128-CBC + HMAC-SHA256, from `cryptography`, Apache-2.0/BSD)."""
import hashlib
import secrets
from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet
from sqlalchemy import String, Text
from sqlalchemy.types import TypeDecorator

from ..config import get_settings


class CryptoConfigError(RuntimeError):
    pass


@lru_cache
def _fernet() -> Fernet:
    key = get_settings().encryption_key
    if not key:
        raise CryptoConfigError("JA_ENCRYPTION_KEY is not set. Generate one with: python -m app.cli gen-key")
    return Fernet(key.encode())


def reset_crypto_cache() -> None:
    _fernet.cache_clear()


def generate_key() -> str:
    return Fernet.generate_key().decode()


def encrypt_bytes(data: bytes) -> bytes:
    return _fernet().encrypt(data)


def decrypt_bytes(token: bytes) -> bytes:
    return _fernet().decrypt(token)


def encrypt_str(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_str(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def random_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


class EncryptedString(TypeDecorator):
    """Transparently encrypts a string column at rest."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):  # noqa: ANN001
        if value is None:
            return None
        return encrypt_str(str(value))

    def process_result_value(self, value, dialect):  # noqa: ANN001
        if value is None:
            return None
        return decrypt_str(value)


class EncryptedFileStore:
    """Content-addressed, encrypted-at-rest file storage under data_dir/store."""

    def __init__(self, root: Path | None = None):
        self.root = (root or get_settings().data_dir) / "store"
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, data: bytes, kind: str = "blob") -> tuple[str, str]:
        digest = sha256_hex(data)
        rel = f"{kind}/{digest}.enc"
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(encrypt_bytes(data))
            path.chmod(0o600)
        return rel, digest

    def get(self, rel: str) -> bytes:
        return decrypt_bytes(self._safe(rel).read_bytes())

    def delete(self, rel: str) -> None:
        p = self._safe(rel)
        if p.exists():
            p.unlink()

    def _safe(self, rel: str) -> Path:
        p = (self.root / rel).resolve()
        if not str(p).startswith(str(self.root.resolve())):
            raise ValueError("path escapes store")
        return p


__all__ = ["EncryptedString", "EncryptedFileStore", "String"]
