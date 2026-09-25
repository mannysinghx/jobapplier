from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from . import metrics
from .api import admin, assist, jobs, profile
from .config import get_settings
from .connectors.registry import sync_registry
from .db import SessionLocal
from .security.crypto import CryptoConfigError


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup only syncs the source registry. It never deletes data, kills jobs or triggers polling.
    db = SessionLocal()
    try:
        sync_registry(db)
    finally:
        db.close()
    s = get_settings()
    if s.embedded_scheduler:
        from . import scheduler

        scheduler.start()
    yield
    if s.embedded_scheduler:
        from . import scheduler

        scheduler.stop()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="jobApplier API", version="0.1.0", lifespan=lifespan,
                  docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_credentials=True,
                       allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"], allow_headers=["content-type", "x-csrf-token"])

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("Cache-Control", "no-store")
        path = request.url.path
        if path.startswith("/api/docs"):
            pass
        elif path.startswith("/api/") or path == "/metrics":
            resp.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        else:  # the single-page UI (same policy as frontend/nginx.conf)
            resp.headers.setdefault("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'")
            if path.startswith("/assets/"):
                resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        if s.cookie_secure:
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return resp

    @app.exception_handler(CryptoConfigError)
    async def crypto_err(_, exc):  # noqa: ANN001
        return JSONResponse({"detail": str(exc)}, status_code=500)

    for r in (admin.router, profile.router, jobs.router, assist.router):
        app.include_router(r, prefix="/api")

    @app.get("/api/health")
    def health():
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
            return {"ok": True}
        finally:
            db.close()

    @app.get("/metrics")
    def prom():
        # Aggregate counts only, with no personal data. Expose on the private network only.
        if not s.metrics_public:
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        metrics.refresh()
        return PlainTextResponse(generate_latest().decode(), media_type=CONTENT_TYPE_LATEST)

    if s.static_dir and (s.static_dir / "index.html").is_file():
        _mount_ui(app, s.static_dir)
    return app


def _mount_ui(app: FastAPI, root: Path) -> None:
    """Serve the built React UI (same origin as the API, so SameSite=Strict cookies and CSRF work unchanged)."""
    root = root.resolve()
    index = root / "index.html"
    if (root / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=root / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/") or path == "api":
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        candidate = (root / path).resolve()
        if path and candidate.is_relative_to(root) and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-store"})


app = create_app()
