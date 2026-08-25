"""Regression tests for token double-issuance + checkpoint malformed-round.

Covers:
- ``get_or_create_webui_token``: concurrent first calls must all return the
  same persisted token (no double-issuance), and a failed disk write must
  raise instead of silently returning an in-memory token.
- ``POST /checkpoints/restore``: malformed ``round`` values in a persisted
  checkpoint must degrade to a safe default instead of raising 500.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iterate_harness.web import token as token_module
from iterate_harness.web.api import create_app


@pytest.fixture()
def client():
    return TestClient(create_app())


def _isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ITERATE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("ITERATE_DATA_DIR", str(tmp_path / "data"))


# ---------------------------------------------------------------------------
# Defect 5: concurrent double token issuance + silent write failure
# ---------------------------------------------------------------------------


class TestTokenConcurrency:
    def test_concurrent_first_calls_issue_single_token(self, tmp_path: Path, monkeypatch):
        """8 threads racing the first call must all observe the same token
        (a previous implementation had no lock, so each thread generated and
        returned a different token while the file kept only one of them)."""
        _isolated_config(tmp_path, monkeypatch)

        real_write = token_module.atomic_write_text

        def slow_write(path, data, *, encoding="utf-8", mode=None):
            time.sleep(0.02)  # widen the issuance race window
            real_write(path, data, encoding=encoding, mode=mode)

        monkeypatch.setattr(token_module, "atomic_write_text", slow_write)

        with ThreadPoolExecutor(max_workers=8) as pool:
            tokens = list(
                pool.map(lambda _: token_module.get_or_create_webui_token(), range(8))
            )

        assert len(set(tokens)) == 1, f"double-issued tokens: {set(tokens)}"
        on_disk = token_module.webui_token_path().read_text(encoding="utf-8").strip()
        assert on_disk == tokens[0]

    def test_write_failure_raises_instead_of_silent_token(
        self, tmp_path: Path, monkeypatch
    ):
        """A token that cannot be persisted must never be handed out: the
        caller learns the server cannot start protecting the API."""
        _isolated_config(tmp_path, monkeypatch)

        def failing_write(path, data, *, encoding="utf-8", mode=None):
            raise OSError("config dir is read-only")

        monkeypatch.setattr(token_module, "atomic_write_text", failing_write)
        with pytest.raises(OSError, match="read-only"):
            token_module.get_or_create_webui_token()

    def test_stored_token_still_reused_after_lock(self, tmp_path: Path, monkeypatch):
        _isolated_config(tmp_path, monkeypatch)
        first = token_module.get_or_create_webui_token()
        second = token_module.get_or_create_webui_token()
        assert first == second


# ---------------------------------------------------------------------------
# Defect 8: bare int() on a malformed checkpoint "round" -> 500
# ---------------------------------------------------------------------------


class TestCheckpointMalformedRound:
    @pytest.mark.parametrize("bad_round", ["not-a-number", {"nested": 1}, [1, 2], 3.7])
    def test_restore_with_malformed_round_no_500(
        self, client: TestClient, tmp_path: Path, bad_round
    ):
        (tmp_path / ".iterate").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".iterate" / "checkpoint.json").write_text(
            json.dumps({"round": bad_round, "converged": False}), encoding="utf-8"
        )
        response = client.post(
            "/api/v1/checkpoints/restore",
            params={"project_root": str(tmp_path), "confirm": "true"},
        )
        assert response.status_code == 200
        assert response.json()["detail"]["round"] == 0

    def test_restore_with_normal_round_unchanged(self, client: TestClient, tmp_path: Path):
        (tmp_path / ".iterate").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".iterate" / "checkpoint.json").write_text(
            json.dumps({"round": 4, "converged": False}), encoding="utf-8"
        )
        response = client.post(
            "/api/v1/checkpoints/restore",
            params={"project_root": str(tmp_path), "confirm": "true"},
        )
        assert response.status_code == 200
        assert response.json()["detail"]["round"] == 4

    def test_restore_with_numeric_string_round(self, client: TestClient, tmp_path: Path):
        (tmp_path / ".iterate").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".iterate" / "checkpoint.json").write_text(
            json.dumps({"round": "7", "converged": False}), encoding="utf-8"
        )
        response = client.post(
            "/api/v1/checkpoints/restore",
            params={"project_root": str(tmp_path), "confirm": "true"},
        )
        assert response.status_code == 200
        assert response.json()["detail"]["round"] == 7
