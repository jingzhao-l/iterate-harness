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
        async def fake_start(project_root, mode, changed, ref, permission_mode="full_auto"):
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
        async def fake_start(project_root, mode, changed, ref, permission_mode="full_auto"):
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
        async def fake_start(project_root, mode, changed, ref, permission_mode="full_auto"):
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

        async def fake_start(project_root, mode, changed, ref, permission_mode="full_auto"):
            captured["mode"] = mode
            captured["changed"] = changed
            captured["ref"] = ref
            captured["permission_mode"] = permission_mode
            return "run-1"

        monkeypatch.setattr(run_manager_module.run_manager, "start", fake_start)
        client.post(
            "/api/v1/chat/start",
            params={"project_root": str(tmp_path)},
            json={},
        )
        assert captured == {
            "mode": "review",
            "changed": False,
            "ref": "HEAD",
            # The WebUI must not silently escalate the permission posture: an
            # omitted body defers to the configured mode instead of the old
            # hard-coded "full_auto" (write-without-asking).
            "permission_mode": "default",
        }

    def test_start_forwards_permission_mode(self, client: TestClient, tmp_path, monkeypatch):
        captured: dict[str, object] = {}

        async def fake_start(project_root, mode, changed, ref, permission_mode="full_auto"):
            captured["permission_mode"] = permission_mode
            return "run-1"

        monkeypatch.setattr(run_manager_module.run_manager, "start", fake_start)
        body = client.post(
            "/api/v1/chat/start",
            params={"project_root": str(tmp_path)},
            json={"mode": "run", "permission_mode": "plan"},
        )
        assert body.status_code == 200
        assert captured["permission_mode"] == "plan"

    def test_start_invalid_permission_mode_422(self, client: TestClient, tmp_path):
        body = client.post(
            "/api/v1/chat/start",
            params={"project_root": str(tmp_path)},
            json={"mode": "run", "permission_mode": "explode"},
        )
        assert body.status_code == 422


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
            "permission_mode", "driver", "error", "message",
        ):
            assert key in payload
        assert payload["driver"] is None

    def test_status_surfaces_foreign_console_driver(self, client: TestClient, tmp_path):
        """Idle must not be reported for a project the console is iterating."""
        import time

        from iterate_harness.iterate.run_lock import lease_path

        (tmp_path / ".iterate").mkdir(parents=True)
        lease_path(tmp_path).write_text(
            '{"holder": "console:999@another-host", "role": "console",'
            ' "pid": 999, "host": "another-host", "acquired_at": %f}' % time.time(),
            encoding="utf-8",
        )
        run_manager.project_root = str(tmp_path)
        body = client.get("/api/v1/chat/status")
        assert body.status_code == 200
        payload = body.json()
        assert payload["state"] == "idle"
        driver = payload["driver"]
        assert driver is not None
        assert driver["role"] == "console"
        assert driver["pid"] == 999


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
    def test_reset_requires_confirm(self, client: TestClient, tmp_path):
        """Reset tears down a live run, so it is a secondary-confirm operation
        like every other mutating route (a stray click / replayed request must
        not kill the loop)."""
        body = client.post("/api/v1/chat/reset", params={"project_root": str(tmp_path)})
        assert body.status_code == 422
        assert "confirm=true" in body.json()["detail"]

    def test_reset_returns_idle(self, client: TestClient, tmp_path):
        body = client.post(
            "/api/v1/chat/reset",
            params={"project_root": str(tmp_path), "confirm": "true"},
        )
        assert body.status_code == 200
        assert body.json() == {"ok": True, "status": "idle", "cancelledRunId": ""}

    def test_reset_is_audited(self, client: TestClient, tmp_path):
        body = client.post(
            "/api/v1/chat/reset",
            params={"project_root": str(tmp_path), "confirm": "true"},
        )
        assert body.status_code == 200
        audit = (tmp_path / ".iterate" / "web-audit.jsonl").read_text(encoding="utf-8")
        assert "run.reset" in audit

    def test_reset_audited_entry_names_the_cancelled_run(self, client: TestClient, tmp_path, monkeypatch):
        """The audit entry must identify the run it tore down — ``reset()``
        clears ``run_id``, so the id has to be captured before the reset."""
        monkeypatch.setattr(run_manager_module.run_manager, "run_id", "run-cancelled-1")
        client.post(
            "/api/v1/chat/reset",
            params={"project_root": str(tmp_path), "confirm": "true"},
        )
        audit = (tmp_path / ".iterate" / "web-audit.jsonl").read_text(encoding="utf-8")
        assert "run-cancelled-1" in audit

    def test_reset_unknown_project_root_404(self, client: TestClient, tmp_path):
        body = client.post(
            "/api/v1/chat/reset",
            params={"project_root": str(tmp_path / "nope"), "confirm": "true"},
        )
        assert body.status_code == 404
