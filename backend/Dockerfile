FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

RUN useradd --create-home --uid 10001 app && mkdir -p /data && chown app /data
COPY requirements.txt requirements-dev.txt ./
RUN pip install -r requirements-dev.txt
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
COPY tests ./tests

USER app
EXPOSE 8100
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8100 --proxy-headers --forwarded-allow-ips='*'"]
