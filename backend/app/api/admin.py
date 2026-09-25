"""Auth, controls (pause/kill switch), audit, privacy (export/delete), retention."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit, controls, privacy
from ..config import get_settings
from ..db import get_db
from ..llm.ollama import LLMUnavailable, OllamaClient, host_is_local
from ..llm.settings import get_llm_config, set_llm_config
from ..models import AuditEvent, User
from ..security import auth
from ..security.auth import actor, current_user, require_admin
from .profile import get_profile

router = APIRouter()


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)
    totp: str | None = Field(default=None, max_length=10)


@router.post("/auth/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    client = request.client.host if request.client else "unknown"
    return auth.login(db, response, body.username, body.password, body.totp, client)


@router.post("/auth/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db), user: User = Depends(current_user)):
    auth.logout(db, request, response)
    return {"ok": True}


@router.get("/auth/me")
def me(request: Request, user: User = Depends(current_user)):
    return {"username": user.username, "role": user.role, "csrf_token": request.cookies.get(auth.CSRF_COOKIE)}


@router.get("/controls")
def get_controls(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return {**controls.pause_state(db), "retention": privacy.get_retention(db)}


class PauseIn(BaseModel):
    reason: str = Field(default="", max_length=500)


@router.post("/controls/pause")
def pause(body: PauseIn, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    return controls.set_paused(db, True, actor(user), body.reason)


@router.post("/controls/resume")
def resume(body: PauseIn, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    st = controls.set_paused(db, False, actor(user), body.reason)
    if st["kill_switch_file"]:
        st["warning"] = "kill-switch file still present; remove it on the host to fully resume"
    return st


class RetentionIn(BaseModel):
    expired_job_days: int | None = Field(default=None, ge=1)
    audit_days: int | None = Field(default=None, ge=30)
    connector_run_days: int | None = Field(default=None, ge=1)
    rejected_application_days: int | None = Field(default=None, ge=1)


@router.put("/controls/retention")
def put_retention(body: RetentionIn, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    return privacy.set_retention(db, body.model_dump(exclude_none=True), actor(user))


@router.get("/llm")
def llm_status(db: Session = Depends(get_db), user: User = Depends(current_user)):
    s = get_settings()
    out = {**get_llm_config(db), "base_url": s.llm_base_url, "endpoint_is_local": host_is_local(s.llm_base_url),
           "reachable": False, "models": [], "error": None}
    try:
        out["models"] = OllamaClient().local_models()
        out["reachable"] = True
    except LLMUnavailable as e:
        out["error"] = str(e)
    return out


class LLMIn(BaseModel):
    enabled: bool
    model: str = Field(min_length=1, max_length=200)


@router.put("/llm")
def llm_update(body: LLMIn, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    if body.enabled:
        try:
            OllamaClient().ensure_local(body.model)
        except LLMUnavailable as e:
            raise HTTPException(409, str(e)) from e
    return set_llm_config(db, body.enabled, body.model, actor(user))


@router.get("/audit")
def list_audit(limit: int = 200, before_id: int | None = None, db: Session = Depends(get_db), user: User = Depends(current_user)):
    q = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(min(limit, 1000))
    if before_id:
        q = q.where(AuditEvent.id < before_id)
    return [{"id": e.id, "ts": e.ts, "actor": e.actor, "action": e.action, "entity_type": e.entity_type,
             "entity_id": e.entity_id, "details": e.details, "hash": e.hash[:12]} for e in db.execute(q).scalars()]


@router.get("/audit/verify")
def verify(db: Session = Depends(get_db), user: User = Depends(current_user)):
    ok, bad = audit.verify_chain(db)
    return {"ok": ok, "first_bad_id": bad}


@router.get("/privacy/export")
def export(db: Session = Depends(get_db), user: User = Depends(require_admin)):
    data = privacy.export_zip(db, get_profile(db), actor(user))
    return Response(data, media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="jobapplier_export.zip"'})


class DeleteIn(BaseModel):
    confirm: str


@router.post("/privacy/delete")
def delete_everything(body: DeleteIn, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    if body.confirm != "DELETE MY DATA":
        raise HTTPException(422, "type DELETE MY DATA to confirm")
    return privacy.delete_all(db, get_profile(db), actor(user))
