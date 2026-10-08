import pytest
from fastapi.testclient import TestClient

from app import main


@pytest.fixture
def protected_client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA", tmp_path)
    monkeypatch.setenv("PL_SWMM_REQUIRE_AUTH", "1")
    monkeypatch.setenv("PL_SWMM_PASSWORD", "test-password-only")
    with TestClient(main.app) as client:
        yield client


def test_health_is_public_but_workspace_and_artifacts_require_login(protected_client):
    client = protected_client
    assert client.get("/api/health").json()["engine_code"] == 52004
    for route in ["/", "/static/app.js", "/vendor/leaflet/leaflet.js", "/api/models", "/docs"]:
        response = client.get(route)
        assert response.status_code == 401
        assert response.headers["www-authenticate"].startswith("Basic ")
    assert client.post("/api/models/demo").status_code == 401
    for header in ["Basic bad-base64", "Bearer token", "Basic /w=="]:
        assert client.get("/api/models", headers={"Authorization": header}).status_code == 401
    assert client.get("/", auth=("swmm", "wrong")).status_code == 401
    assert client.get("/", auth=("wrong", "test-password-only")).status_code == 401
    auth = ("swmm", "test-password-only")
    assert client.get("/", auth=auth).status_code == 200
    assert client.get("/static/app.js", auth=auth).status_code == 200
    model = client.post("/api/models/demo", auth=auth).json()
    identifier = model["id"]
    assert client.get(f"/api/models/{identifier}/input").status_code == 401
    assert client.get(f"/api/models/{identifier}/input", auth=auth).status_code == 200
    run = client.post(f"/api/models/{identifier}/runs", auth=auth).json()
    run_id = run["id"]
    assert client.get(f"/api/runs/{run_id}", auth=auth).json()["status"] == "completed"
    assert client.get(f"/api/runs/{run_id}/files/report").status_code == 401
    assert "Build 5.2.4" in client.get(f"/api/runs/{run_id}/files/report", auth=auth).text


def test_hosted_startup_fails_if_password_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA", tmp_path)
    monkeypatch.setenv("PL_SWMM_REQUIRE_AUTH", "1")
    monkeypatch.delenv("PL_SWMM_PASSWORD", raising=False)
    with pytest.raises(RuntimeError, match="requires PL_SWMM_PASSWORD"):
        with TestClient(main.app):
            pass
