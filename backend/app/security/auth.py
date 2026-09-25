"""Local authentication: argon2id passwords, TOTP MFA, server-side sessions, CSRF double-submit, RBAC."""
import hashlib
import hmac
import time
from datetime import timedelta

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..config import get_settings
from ..db import get_db, utcnow
from ..models import User, UserSession
from .crypto import random_token

SESSION_COOKIE = "ja_session"
CSRF_COOKIE = "ja_csrf"
CSRF_HEADER = "x-csrf-token"
_ph = PasswordHasher()
_failures: dict[str, list[float]] = {}
MAX_FAILURES, LOCK_SECONDS = 5, 300


def hash_password(pw: str) -> str:
    if len(pw) < 12:
        raise ValueError("password must be at least 12 characters")
    return _ph.hash(pw)


def _token_hash(tok: str) -> str:
    return hashlib.sha256(tok.encode()).hexdigest()


def _locked(key: str) -> bool:
    now = time.time()
    recent = [t for t in _failures.get(key, []) if now - t < LOCK_SECONDS]
    _failures[key] = recent
    return len(recent) >= MAX_FAILURES


def create_user(db: Session, username: str, password: str, role: str = "admin", with_totp: bool = True) -> tuple[User, str | None]:
    if role not in ("admin", "viewer"):
        raise ValueError("role must be admin or viewer")
    secret = pyotp.random_base32() if with_totp else None
    u = User(username=username, password_hash=hash_password(password), role=role, totp_secret=secret)
    db.add(u)
    db.flush()
    audit.record(db, "system", "user.created", "user", u.id, {"role": role, "mfa": bool(secret)})
    db.commit()
    uri = pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name="jobApplier") if secret else None
    return u, uri


def reset_totp(db: Session, username: str, actor: str) -> tuple[User, str, str]:
    """Issue a NEW TOTP secret for an existing user and sign them out everywhere. Returns (user, otpauth_uri, secret).
    The old authenticator entry stops working immediately."""
    user = db.execute(select(User).where(User.username == username)).scalar()
    if user is None:
        raise ValueError(f"no user named {username!r}")
    secret = pyotp.random_base32()
    user.totp_secret = secret
    n = db.query(UserSession).filter(UserSession.user_id == user.id).delete(synchronize_session=False)
    audit.record(db, actor, "user.mfa_reset", "user", user.id, {"sessions_revoked": n})
    db.commit()
    return user, pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name="jobApplier"), secret


def login(db: Session, response: Response, username: str, password: str, totp: str | None, client: str) -> dict:
    key = f"{username}|{client}"
    if _locked(key):
        raise HTTPException(429, "too many failed attempts; try again later")
    user = db.execute(select(User).where(User.username == username)).scalar()
    ok = False
    if user is not None:
        try:
            ok = _ph.verify(user.password_hash, password)
        except VerificationError:
            ok = False
    else:
        _ph.hash("timing-equalizer-password")
    s = get_settings()
    needs_mfa = user is not None and (user.totp_secret is not None or (s.mfa_required and user.role == "admin"))
    if ok and needs_mfa:
        ok = bool(user.totp_secret) and bool(totp) and pyotp.TOTP(user.totp_secret).verify(totp or "", valid_window=1)
    if not ok:
        _failures.setdefault(key, []).append(time.time())
        audit.record(db, f"user:{username[:40]}", "auth.login_failed")
        db.commit()
        raise HTTPException(401, "invalid credentials")
    _failures.pop(key, None)
    token, csrf = random_token(), random_token(24)
    db.add(UserSession(user_id=user.id, token_hash=_token_hash(token), csrf_token=csrf,
                       expires_at=utcnow() + timedelta(minutes=s.session_ttl_minutes)))
    audit.record(db, f"user:{user.username}", "auth.login", "user", user.id)
    db.commit()
    common = {"secure": s.cookie_secure, "samesite": "strict", "path": "/", "max_age": s.session_ttl_minutes * 60}
    response.set_cookie(SESSION_COOKIE, token, httponly=True, **common)
    response.set_cookie(CSRF_COOKIE, csrf, httponly=False, **common)
    return {"username": user.username, "role": user.role, "csrf_token": csrf}


def logout(db: Session, request: Request, response: Response) -> None:
    tok = request.cookies.get(SESSION_COOKIE)
    if tok:
        sess = db.execute(select(UserSession).where(UserSession.token_hash == _token_hash(tok))).scalar()
        if sess:
            audit.record(db, f"user:{sess.user.username}", "auth.logout", "user", sess.user_id)
            db.delete(sess)
            db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    tok = request.cookies.get(SESSION_COOKIE)
    if not tok:
        raise HTTPException(401, "not authenticated")
    sess = db.execute(select(UserSession).where(UserSession.token_hash == _token_hash(tok))).scalar()
    if sess is None or sess.expires_at < utcnow():
        raise HTTPException(401, "session expired")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        sent = request.headers.get(CSRF_HEADER, "")
        if not sent or not hmac.compare_digest(sent, sess.csrf_token):
            raise HTTPException(403, "CSRF token missing or invalid")
    request.state.actor = f"user:{sess.user.username}"
    return sess.user


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(403, "admin role required")
    return user


def actor(user: User) -> str:
    return f"user:{user.username}"
