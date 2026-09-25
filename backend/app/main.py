from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from . import metrics
from .api import admin, jobs, profile
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
    yield


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
        if not request.url.path.startswith("/api/docs"):
            resp.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        return resp

    @app.exception_handler(CryptoConfigError)
    async def crypto_err(_, exc):  # noqa: ANN001
        return JSONResponse({"detail": str(exc)}, status_code=500)

    for r in (admin.router, profile.router, jobs.router):
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
        metrics.refresh()
        return PlainTextResponse(generate_latest().decode(), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()
