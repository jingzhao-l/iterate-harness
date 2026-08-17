"""Integration tests for the WebUI chat routes (design §18.3).

Uses FastAPI's TestClient against ``create_app()``. The real iterate engine
is never launched here: the module-level ``run_manager`` singleton's engine-
touching methods are monkeypatched so we exercise routing, status-code
mapping and response shapes without spinning up an LLM loop.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import iterate_harness.web.run_manager as run_manager_module
from iterate_harness.web.api import create_app
from iterate_harness.web.run_manager import RunManagerError, run_manager


@pytest.fixture()
def client():
    return TestClient(create_app())


@pytest.fixture(autouse=True)
async def _reset_run_manager():
    yield
    await run_manager.reset()


class TestStart:
    def test_start_returns_run_id(self, client: TestClient, tmp_path, monkeypatch):
        async def fake_start(project_root, mode, changed, ref):
            return "run-abc123"

        monkeypatch.setattr(run_manager_module.run_manager, "start", fake_start)
        monkeypatch.setattr(run_manager_module.run_manager, "state", "running")

        body = client.post(
            "/api/v1/chat/start",
            params={"project_root": str(tmp_path)},
            json={"mode": "run", "changed": False, "ref": "HEAD"},
        )
        assert body.status_code == 200
        assert body.json() == {"runId": "run-abc123", "status": "running"}

    def test_start_conflict_when_active(self, client: TestClient, tmp_path, monkeypatch):
        async def fake_start(project_root, mode, changed, ref):
            raise RunManagerError("已有运行中的 iterate 循环，请先停止或等待结束")

        monkeypatch.setattr(run_manager_module.run_manager, "start", fake_start)
        body = client.post(
            "/api/v1/chat/start",
            params={"project_root": str(tmp_path)},
            json={"mode": "run"},
        )
        assert body.status_code == 409
        assert "运行中" in body.json()["detail"]

    def test_start_bad_request_for_other_errors(self, client: TestClient, tmp_path, monkeypatch):
        async def fake_start(project_root, mode, changed, ref):
            raise RunManagerError("无效的 --ref：bogus")

        monkeypatch.setattr(run_manager_module.run_manager, "start", fake_start)
        body = client.post(
            "/api/v1/chat/start",
            params={"project_root": str(tmp_path)},
            json={"mode": "run", "changed": True, "ref": "bogus"},
        )
        assert body.status_code == 400
        assert "bogus" in body.json()["detail"]

    def test_start_missing_project_root_404(self, client: TestClient, tmp_path):
        body = client.post(
            "/api/v1/chat/start",
            params={"project_root": str(tmp_path / "nope")},
            json={"mode": "run"},
        )
        assert body.status_code == 404

    def test_start_invalid_mode_422(self, client: TestClient, tmp_path):
        body = client.post(
            "/api/v1/chat/start",
            params={"project_root": str(tmp_path)},
            json={"mode": "explode"},
        )
        assert body.status_code == 422

    def test_start_defaults_to_review(self, client: TestClient, tmp_path, monkeypatch):
        captured: dict[str, object] = {}

        async def fake_start(project_root, mode, changed, ref):
            captured["mode"] = mode
            captured["changed"] = changed
            captured["ref"] = ref
            return "run-1"

        monkeypatch.setattr(run_manager_module.run_manager, "start", fake_start)
        client.post(
            "/api/v1/chat/start",
            params={"project_root": str(tmp_path)},
            json={},
        )
        assert captured == {"mode": "review", "changed": False, "ref": "HEAD"}


class TestStatus:
    def test_status_idle_shape(self, client: TestClient):
        body = client.get("/api/v1/chat/status")
        assert body.status_code == 200
        payload = body.json()
        assert payload["state"] == "idle"
        assert payload["waiting_for"] == "none"
        assert payload["run_id"] == ""
        for key in (
            "mode", "project_root", "round", "new_findings", "total_findings",
            "cost_usd", "converged", "question", "options", "permission",
            "error", "message",
        ):
            assert key in payload


class TestHistory:
    def test_history_empty(self, client: TestClient):
        body = client.get("/api/v1/chat/history")
        assert body.status_code == 200
        assert body.json() == []

    def test_history_echoes_persisted_chat(self, client: TestClient, tmp_path, monkeypatch):
        run_manager._chat_dir = tmp_path / ".iterate"
        import asyncio

        async def persist():
            await run_manager._publish_chat("user", "继续", kind="decision")

        asyncio.run(persist())
        body = client.get("/api/v1/chat/history")
        assert body.status_code == 200
        entries = body.json()
        assert len(entries) == 1
        assert entries[0]["role"] == "user"
        assert entries[0]["content"] == "继续"
        assert entries[0]["kind"] == "decision"


class TestMessage:
    def test_message_empty_content_422(self, client: TestClient):
        body = client.post("/api/v1/chat/message", json={"content": ""})
        assert body.status_code == 422

    def test_message_no_run_400(self, client: TestClient):
        body = client.post("/api/v1/chat/message", json={"content": "你好"})
        assert body.status_code == 400
        assert "没有运行中的循环" in body.json()["detail"]

    def test_message_nudge_routing(self, client: TestClient, monkeypatch):
        async def fake_send(content):
            return {"answered": True, "nudged": True}

        monkeypatch.setattr(run_manager_module.run_manager, "send_message", fake_send)
        body = client.post("/api/v1/chat/message", json={"content": "快点干"})
        assert body.status_code == 200
        assert body.json() == {"answered": True, "nudged": True}


class TestControl:
    def test_control_unknown_action_422(self, client: TestClient):
        body = client.post("/api/v1/chat/control", json={"action": "explode"})
        assert body.status_code == 422

    def test_control_conflict(self, client: TestClient, monkeypatch):
        async def fake_control(action):
            raise RunManagerError("当前状态无法暂停（仅在运行中可暂停）")

        monkeypatch.setattr(run_manager_module.run_manager, "control", fake_control)
        body = client.post("/api/v1/chat/control", json={"action": "pause"})
        assert body.status_code == 409

    def test_control_ok(self, client: TestClient, monkeypatch):
        async def fake_control(action):
            return {"ok": True, "message": "已继续运行"}

        monkeypatch.setattr(run_manager_module.run_manager, "control", fake_control)
        body = client.post("/api/v1/chat/control", json={"action": "resume"})
        assert body.status_code == 200
        assert body.json()["ok"] is True


class TestReset:
    def test_reset_returns_idle(self, client: TestClient):
        body = client.post("/api/v1/chat/reset")
        assert body.status_code == 200
        assert body.json() == {"ok": True, "status": "idle"}
