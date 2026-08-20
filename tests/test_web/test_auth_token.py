"""Tests for the WebUI access-token authentication (web/api.py + web/token.py).

Covers: requests rejected without / with a wrong token, accepted via the
``Authorization: Bearer`` header and the ``?token=`` query parameter (used by
EventSource), auth disabled when no token is configured, static assets not
gated, and the persistent token file behaviour.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iterate_harness.web.api import create_app
from iterate_harness.web.token import get_or_create_webui_token, webui_token_path

_SECRET = "secret-token"


@pytest.fixture()
def client(tmp_path: Path):
    return TestClient(create_app(project_root=tmp_path, token=_SECRET))


def _status(client: TestClient, tmp_path: Path, **kwargs):
    params = {"project_root": str(tmp_path)}
    params.update(kwargs.pop("params", {}))
    return client.get("/api/v1/status", params=params, **kwargs)


def test_api_rejects_request_without_token(client: TestClient, tmp_path: Path):
    response = _status(client, tmp_path)
    assert response.status_code == 401


def test_api_rejects_wrong_token(client: TestClient, tmp_path: Path):
    response = _status(client, tmp_path, headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401


def test_api_rejects_wrong_query_token(client: TestClient, tmp_path: Path):
    response = _status(client, tmp_path, params={"token": "wrong"})
    assert response.status_code == 401


def test_api_accepts_bearer_header(client: TestClient, tmp_path: Path):
    response = _status(client, tmp_path, headers={"Authorization": f"Bearer {_SECRET}"})
    assert response.status_code == 200


def test_api_accepts_query_param_token(client: TestClient, tmp_path: Path):
    # The SSE stream cannot set custom headers, so ?token= must also work.
    response = _status(client, tmp_path, params={"token": _SECRET})
    assert response.status_code == 200


def test_mutating_route_is_also_guarded(client: TestClient, tmp_path: Path):
    response = client.post("/api/v1/checkpoints/restore", params={"project_root": str(tmp_path)})
    assert response.status_code == 401


def test_auth_disabled_when_no_token_configured(tmp_path: Path):
    client = TestClient(create_app(project_root=tmp_path))
    response = client.get("/api/v1/status", params={"project_root": str(tmp_path)})
    assert response.status_code == 200


def test_static_frontend_is_not_gated(client: TestClient):
    # Static assets carry no secrets and are served without a token.
    response = client.get("/")
    assert response.status_code in (200, 404)


# ---------------------------------------------------------------------------
# token.py
# ---------------------------------------------------------------------------


def test_get_or_create_token_persists(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ITERATE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("ITERATE_DATA_DIR", str(tmp_path / "data"))

    token_a = get_or_create_webui_token()
    token_b = get_or_create_webui_token()

    assert token_a == token_b
    assert len(token_a) >= 32
    path = webui_token_path()
    assert path.exists()
    assert path.read_text(encoding="utf-8").strip() == token_a
    assert path.stat().st_mode & 0o777 == 0o600


def test_get_or_create_token_returns_stored_value(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ITERATE_CONFIG_DIR", str(tmp_path))
    webui_token_path().write_text("pre-seeded-token\n", encoding="utf-8")

    assert get_or_create_webui_token() == "pre-seeded-token"
