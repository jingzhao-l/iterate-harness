"""WebUI triage: a permanent ``ignore`` must reach the engine, not just the journal."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iterate_harness.iterate import personalization, triage
from iterate_harness.iterate.types import KnownIntentional
from iterate_harness.web import findings_triage
from iterate_harness.web.api import create_app


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


def _ignore(client: TestClient, root: Path, *, file: str = "src/a.py", dimension: str = "security"):
    return client.post(
        "/api/v1/runs/findings/triage",
        params={"project_root": str(root), "confirm": "true"},
        json={"file": file, "line": 10, "dimension": dimension, "decision": "ignore"},
    )


def test_ignore_persists_the_engine_filter_and_the_journal(client: TestClient, tmp_path: Path) -> None:
    (tmp_path / "iterate.config.yaml").write_text("goal: test\n", encoding="utf-8")
    response = _ignore(client, tmp_path)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "永久忽略" in payload["message"]
    assert payload["detail"]["filter"]["added"]
    assert payload["detail"]["record"]["decision"] == "ignore"

    stored = personalization.known_intentional_of(None, tmp_path)
    assert [(entry.file, entry.dimension, entry.line) for entry in stored] == [
        ("src/a.py", "security", 10)
    ]
    assert stored[0].reason.startswith(triage.IGNORE_REASON_PREFIX)
    assert (tmp_path / "iterate.config.yaml").read_text(encoding="utf-8").count("src/a.py") == 1


def test_ignore_is_listed_as_a_decision(client: TestClient, tmp_path: Path) -> None:
    assert _ignore(client, tmp_path).status_code == 200
    response = client.get(
        "/api/v1/runs/findings/triage", params={"project_root": str(tmp_path)}
    )
    assert response.status_code == 200
    assert response.json()[0]["decision"] == "ignore"


def test_ignore_requires_confirmation(client: TestClient, tmp_path: Path) -> None:
    response = client.post(
        "/api/v1/runs/findings/triage",
        params={"project_root": str(tmp_path)},
        json={"file": "src/a.py", "line": 10, "dimension": "security", "decision": "ignore"},
    )
    assert response.status_code == 422
    assert personalization.known_intentional_of(None, tmp_path) == []


def test_reject_does_not_touch_the_engine_filter(client: TestClient, tmp_path: Path) -> None:
    response = client.post(
        "/api/v1/runs/findings/triage",
        params={"project_root": str(tmp_path), "confirm": "true"},
        json={"file": "src/a.py", "line": 10, "dimension": "security", "decision": "reject"},
    )
    assert response.status_code == 200, response.text
    assert personalization.known_intentional_of(None, tmp_path) == []
    assert findings_triage.load_decisions(tmp_path)["src/a.py:::10:::security"]["decision"] == "reject"


def test_unknown_decision_is_rejected(client: TestClient, tmp_path: Path) -> None:
    response = client.post(
        "/api/v1/runs/findings/triage",
        params={"project_root": str(tmp_path), "confirm": "true"},
        json={"file": "src/a.py", "line": 10, "dimension": "security", "decision": "maybe"},
    )
    assert response.status_code == 422
    assert personalization.known_intentional_of(None, tmp_path) == []


def test_dismiss_lifts_the_permanent_ignore(client: TestClient, tmp_path: Path) -> None:
    assert _ignore(client, tmp_path).status_code == 200
    response = client.request(
        "DELETE",
        "/api/v1/runs/findings/triage/dismiss",
        params={"project_root": str(tmp_path), "confirm": "true"},
        json={"file": "src/a.py", "line": 10, "dimension": "security"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["detail"]["liftedIgnores"] == 1
    assert personalization.known_intentional_of(None, tmp_path) == []
    assert findings_triage.load_decisions(tmp_path) == {}


def test_clear_all_lifts_auto_ignores_but_keeps_hand_written_entries(
    client: TestClient, tmp_path: Path
) -> None:
    assert _ignore(client, tmp_path).status_code == 200
    assert _ignore(client, tmp_path, file="src/b.py").status_code == 200
    personalization.save(
        None,
        tmp_path,
        personalization.PersonalizationData(
            known_intentional=[
                *personalization.known_intentional_of(None, tmp_path),
                KnownIntentional(file="src/manual.py", dimension="security", reason="manual"),
            ]
        ),
    )

    response = client.delete(
        "/api/v1/runs/findings/triage",
        params={"project_root": str(tmp_path), "confirm": "true"},
    )
    assert response.status_code == 200, response.text
    detail = response.json()["detail"]
    assert detail["liftedIgnores"] == 2
    remaining = personalization.known_intentional_of(None, tmp_path)
    assert [entry.file for entry in remaining] == ["src/manual.py"]
    assert findings_triage.load_decisions(tmp_path) == {}


def test_clear_requires_confirmation(client: TestClient, tmp_path: Path) -> None:
    response = client.delete(
        "/api/v1/runs/findings/triage", params={"project_root": str(tmp_path)}
    )
    assert response.status_code == 422
