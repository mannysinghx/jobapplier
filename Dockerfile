# Single-service image for PaaS hosting (Railway): the React UI is built and served by the FastAPI app.
# Self-hosting with Docker Compose uses backend/Dockerfile + frontend/Dockerfile instead (unchanged).

# ---- frontend build ------------------------------------------------------------
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/tsconfig.json frontend/tsconfig.app.json frontend/tsconfig.node.json frontend/vite.config.ts frontend/index.html ./
COPY frontend/src ./src
RUN npm run build

# ---- runtime ---------------------------------------------------------------------
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    JA_STATIC_DIR=/app/static JA_SOURCES_FILE=/app/config/sources.yaml JA_DATA_DIR=/data
WORKDIR /app/backend
RUN useradd --create-home --uid 10001 app && mkdir -p /data && chown app /data
COPY backend/requirements.txt ./
RUN pip install -r requirements.txt
COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./
COPY config /app/config
COPY --from=web /web/dist /app/static
# Railway mounts the volume at /data as root; the app needs to write there, so it runs as root on PaaS.
# (backend/Dockerfile for Compose runs as uid 10001.)
EXPOSE 8100
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8100} --proxy-headers --forwarded-allow-ips='*'"]
