"""Regression tests for /chat/start conflict mapping and config list restore.

Covers:

- ``POST /chat/start`` maps ``RunConflictError`` to HTTP 409 *structurally*
  (fix 4): in-process state conflict, cross-process foreign driver, and the
  lease-conflict message path — while validation errors stay 400.
- ``_restore_redacted`` with a shortened incoming list never pads ``null``
  entries into the saved YAML (fix 3).
"""

from __future__ import annotations

import json
import time

import pytest
import yaml
from fastapi.testclient import TestClient

import iterate_harness.web.run_manager as run_manager_module
from iterate_harness.iterate.run_lock import lease_path
from iterate_harness.web.api import create_app
from iterate_harness.web.run_manager import RunConflictError, RunManagerError, run_manager
from iterate_harness.web.routes.config import REDACTION_PREFIX, _restore_redacted

#: A config dict that passes ``validate_config`` (mirrors test_routes).
VALID_CONFIG: dict[str, object] = {
    "goal": "improve the codebase",
    "dimensions": ["code_review", "security"],
    "validation": {"command_whitelist": ["python"], "commands": {"python": ["python -m pytest"]}},
    "max_rounds": 5,
}


@pytest.fixture()
def client():
    return TestClient(create_app())


@pytest.fixture(autouse=True)
async def _reset_run_manager():
    yield
    await run_manager.reset()


def _write_foreign_lease(tmp_path) -> None:
    (tmp_path / ".iterate").mkdir(parents=True, exist_ok=True)
    lease_path(tmp_path).write_text(
        json.dumps(
            {
                "holder": "console:999@another-host",
                "role": "console",
                "pid": 999,
                "host": "another-host",
                "acquired_at": time.time(),
            }
        ),
        encoding="utf-8",
    )


class TestStartConflictMapping:
    def test_in_process_conflict_returns_409(self, client: TestClient, tmp_path):
        """A run already active in this server is a structural 409 raised as
        ``RunConflictError`` — no message-substring matching involved."""
        run_manager.state = "running"
        body = client.post(
            "/api/v1/chat/start",
            params={"project_root": str(tmp_path)},
            json={"mode": "run"},
        )
        assert body.status_code == 409
        assert "已有运行中的 iterate 循环" in body.json()["detail"]

    def test_foreign_driver_conflict_returns_409(
        self, client: TestClient, tmp_path
    ):
        """The real foreign-lease path (console holds the run lease) must
        answer 409, not 400 — the message does not contain "运行中" in the
        in-process phrasing the old substring check looked for."""
        _write_foreign_lease(tmp_path)
        body = client.post(
            "/api/v1/chat/start",
            params={"project_root": str(tmp_path)},
            json={"mode": "review"},
        )
        assert body.status_code == 409
        detail = body.json()["detail"]
        assert "另一个 iterate 循环正在运行" in detail
        assert "console" in detail

    def test_lease_conflict_style_error_returns_409(
        self, client: TestClient, tmp_path, monkeypatch
    ):
        async def fake_start(project_root, mode, changed, ref, permission_mode="default"):
            raise RunConflictError(
                "该项目的 iterate 循环已在运行（console 进程 pid=999@another-host）。"
                "辅助通道只用于观察与干预，不能并发启动第二个循环；"
                "请先在原终端停止，或等待其结束。"
            )

        monkeypatch.setattr(run_manager_module.run_manager, "start", fake_start)
        body = client.post(
            "/api/v1/chat/start",
            params={"project_root": str(tmp_path)},
            json={"mode": "run"},
        )
        assert body.status_code == 409
        assert "已在运行" in body.json()["detail"]

    def test_validation_error_stays_400(self, client: TestClient, tmp_path, monkeypatch):
        async def fake_start(project_root, mode, changed, ref, permission_mode="default"):
            raise RunManagerError("无效的 --ref：bogus")

        monkeypatch.setattr(run_manager_module.run_manager, "start", fake_start)
        body = client.post(
            "/api/v1/chat/start",
            params={"project_root": str(tmp_path)},
            json={"mode": "run", "changed": True, "ref": "bogus"},
        )
        assert body.status_code == 400
        assert "bogus" in body.json()["detail"]


class TestRestoreRedactedShorterList:
    def test_shorter_incoming_list_produces_no_nulls(self):
        prior = {
            "agents": [
                {"name": "alice", "api_key": "sk-real-alice"},
                {"name": "bob", "api_key": "sk-real-bob"},
            ]
        }
        incoming = {
            "agents": [
                {"name": "alice", "api_key": f"{REDACTION_PREFIX}api_key"},
            ]
        }
        restored = _restore_redacted(incoming, prior)
        # The deleted second agent stays deleted — no zip_longest ``None`` pad.
        assert restored["agents"] == [{"name": "alice", "api_key": "sk-real-alice"}]

    def test_shorter_scalar_list_stays_short(self):
        prior = {"dimensions": ["code_review", "security"]}
        incoming = {"dimensions": ["code_review"]}
        assert _restore_redacted(incoming, prior) == {"dimensions": ["code_review"]}

    def test_shortened_save_writes_no_null_yaml(
        self, client: TestClient, tmp_path
    ):
        """End-to-end: shrinking a list in the editor and saving must not
        materialize ``- null`` lines in the on-disk YAML."""
        first_config = {
            **VALID_CONFIG,
            "validation": {
                "command_whitelist": ["python", "ruff"],
                "commands": {"python": ["python -m pytest"], "ruff": ["ruff check"]},
            },
        }
        first = client.put(
            "/api/v1/config",
            params={"project_root": str(tmp_path), "confirm": "true"},
            json=first_config,
        )
        assert first.status_code == 200

        edited = {
            **VALID_CONFIG,
            "validation": {
                "command_whitelist": ["python"],
                "commands": {"python": ["python -m pytest"]},
            },
        }
        second = client.put(
            "/api/v1/config",
            params={"project_root": str(tmp_path), "confirm": "true"},
            json=edited,
        )
        assert second.status_code == 200
        text = (tmp_path / "iterate.config.yaml").read_text(encoding="utf-8")
        assert "null" not in text
        on_disk = yaml.safe_load(text)
        assert on_disk["validation"]["command_whitelist"] == ["python"]
