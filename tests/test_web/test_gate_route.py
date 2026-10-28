"""Recipe-gate route tests (design §17.3 P9).

The page exists to close a console/terminal disconnect: ``ih iterate gate``
could read the ④ answer out of the decision log, but the WebUI had no route for
it. These tests pin the contract the page relies on — absence is reported as
absence, the history is ordered and bounded, and ``rendered`` stays byte-equal
to the CLI text.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iterate_harness.iterate.decision_log import DecisionLogEntry, append_entry
from iterate_harness.iterate.glasspane_recipe import render_gate_state
from iterate_harness.web import security
from iterate_harness.web.api import create_app


def _append_gate(
    root: Path,
    *,
    timestamp: str,
    round_number: int,
    allowed: bool,
    verdict: str,
    **extra: object,
) -> None:
    gate: dict[str, object] = {
        "allowed": allowed,
        "invariantState": verdict,
        "steps": ["build", "test"],
    }
    gate.update(extra)
    append_entry(
        root,
        DecisionLogEntry(
            timestamp=timestamp,
            round=round_number,
            type="reverify",
            data={"recipeStepGate": gate},
        ),
    )


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


def test_absence_is_reported_as_absence(client: TestClient, tmp_path: Path) -> None:
    response = client.get("/api/v1/gate", params={"project_root": str(tmp_path)})
    assert response.status_code == 200
    payload = response.json()
    assert payload["has_gate"] is False
    assert payload["total"] == 0
    assert payload["latest"] is None
    assert payload["history"] == []
    assert "no recipe gate recorded yet" in payload["rendered"]
    assert "absence, not a pass" in payload["rendered"]


def test_latest_answer_and_history_order(client: TestClient, tmp_path: Path) -> None:
    _append_gate(
        tmp_path,
        timestamp="2026-01-01T00:00:00+00:00",
        round_number=1,
        allowed=True,
        verdict="pass",
        recipeDigest="sha256:" + "a" * 64,
    )
    _append_gate(
        tmp_path,
        timestamp="2026-01-02T00:00:00+00:00",
        round_number=2,
        allowed=False,
        verdict="fail",
        refusal={
            "code": "RECIPE_GATE_E_INVARIANT_GATE",
            "message": "the invariant verdict was not a pass",
            "remedy": "fix the failing invariant, then re-run the round",
        },
    )

    payload = client.get("/api/v1/gate", params={"project_root": str(tmp_path)}).json()
    assert payload["has_gate"] is True
    assert payload["total"] == 2
    assert [record["round"] for record in payload["history"]] == [1, 2]
    assert payload["history"][0]["allowed"] is True
    assert payload["latest"]["round"] == 2
    assert payload["latest"]["allowed"] is False
    assert payload["latest"]["invariant_verdict"] == "fail"
    assert payload["latest"]["refusal"]["code"] == "RECIPE_GATE_E_INVARIANT_GATE"
    assert payload["latest"]["refusal"]["remedy"].startswith("fix the failing invariant")
    assert payload["latest"]["fix_proof"] is None
    assert payload["rendered"] == render_gate_state(tmp_path)
    assert "round 2" in payload["rendered"]
    assert "not allowed" in payload["rendered"]


def test_limit_trims_history_but_keeps_the_latest(
    client: TestClient, tmp_path: Path
) -> None:
    for index in range(5):
        _append_gate(
            tmp_path,
            timestamp=f"2026-01-0{index + 1}T00:00:00+00:00",
            round_number=index + 1,
            allowed=index == 4,
            verdict="pass" if index == 4 else "fail",
        )

    payload = client.get(
        "/api/v1/gate", params={"project_root": str(tmp_path), "limit": 2}
    ).json()
    assert payload["total"] == 5
    assert [record["round"] for record in payload["history"]] == [4, 5]
    assert payload["latest"]["round"] == 5
    assert client.get(
        "/api/v1/gate", params={"project_root": str(tmp_path), "limit": 0}
    ).status_code == 422


def test_fix_proof_is_exposed_with_the_ledger_it_came_from(
    client: TestClient, tmp_path: Path
) -> None:
    _append_gate(
        tmp_path,
        timestamp="2026-01-01T00:00:00+00:00",
        round_number=1,
        allowed=True,
        verdict="pass",
        recipeDigest="sha256:" + "b" * 64,
        fixProof={
            "entryId": "dl_" + "A" * 26,
            "operationId": "op_" + "B" * 26,
            "outcome": "pass",
            "ledger": "/tmp/other/decisions.jsonl",
            "ledgerSource": "link",
            "createdAt": "2026-01-01T00:00:00.000Z",
        },
    )

    latest = client.get(
        "/api/v1/gate", params={"project_root": str(tmp_path)}
    ).json()["latest"]
    assert latest["recipe_digest"] == "sha256:" + "b" * 64
    assert latest["steps"] == ["build", "test"]
    assert latest["fix_proof"] == {
        "entry_id": "dl_" + "A" * 26,
        "operation_id": "op_" + "B" * 26,
        "outcome": "pass",
        "ledger": "/tmp/other/decisions.jsonl",
        "ledger_source": "link",
        "created_at": "2026-01-01T00:00:00.000Z",
    }


def test_route_rejects_a_root_outside_the_pinned_set(
    client: TestClient, tmp_path: Path
) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    security.set_allowed_roots([allowed])
    try:
        response = client.get("/api/v1/gate", params={"project_root": str(outside)})
    finally:
        security.set_allowed_roots(None)
    assert response.status_code == 403
    assert "outside the roots" in response.json()["detail"]


def test_route_404s_for_missing_root(client: TestClient, tmp_path: Path) -> None:
    response = client.get(
        "/api/v1/gate", params={"project_root": str(tmp_path / "missing")}
    )
    assert response.status_code == 404
