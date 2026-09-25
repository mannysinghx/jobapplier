"""Single-service (Railway) mode: UI served from the API, DB URL normalization, metrics toggle, HSTS."""
import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings


@pytest.mark.parametrize("url,want", [
    ("postgres://u:p@h:5432/db", "postgresql+psycopg://u:p@h:5432/db"),
    ("postgresql://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
    ("postgresql+psycopg://u@h/db", "postgresql+psycopg://u@h/db"),
    ("sqlite:///x.db", "sqlite:///x.db"),
])
def test_database_url_normalized(url, want):
    assert Settings(database_url=url).database_url == want


@pytest.fixture()
def ui_client(db, tmp_path, monkeypatch):
    (tmp_path / "static").mkdir()
    (tmp_path / "static" / "index.html").write_text("<!doctype html><div id=root></div>")
    (tmp_path / "static" / "assets").mkdir()
    (tmp_path / "static" / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path / "static-evil").mkdir()
    (tmp_path / "static-evil" / "secret.txt").write_text("nope")
    s = get_settings()
    monkeypatch.setattr(s, "static_dir", tmp_path / "static")
    monkeypatch.setattr(s, "metrics_public", False)
    monkeypatch.setattr(s, "cookie_secure", True)
    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c


def test_ui_served_same_origin_with_spa_fallback(ui_client):
    r = ui_client.get("/")
    assert r.status_code == 200 and "id=root" in r.text
    assert r.headers["content-security-policy"] == "default-src 'self'; frame-ancestors 'none'"
    assert "max-age" in r.headers["strict-transport-security"]
    assert ui_client.get("/jobs/42").text == r.text  # client-side route falls back to index.html
    a = ui_client.get("/assets/app.js")
    assert a.status_code == 200 and "immutable" in a.headers["cache-control"]
    api = ui_client.get("/api/health")
    assert api.json() == {"ok": True} and api.headers["content-security-policy"].startswith("default-src 'none'")
    assert ui_client.get("/api/nope").status_code == 404


def test_ui_path_traversal_blocked(ui_client):
    for p in ("/../static-evil/secret.txt", "/%2e%2e/static-evil/secret.txt", "/..%2fstatic-evil%2fsecret.txt"):
        r = ui_client.get(p)
        assert "nope" not in r.text


def test_metrics_can_be_hidden(ui_client):
    assert ui_client.get("/metrics").status_code == 404


def test_scheduler_off_by_default():
    assert get_settings().embedded_scheduler is False
