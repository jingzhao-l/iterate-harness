"""The run-plan producer, checked against the kernel's published fixture.

Two separate claims, and both have to be falsifiable: the digest must match what the
TypeScript kernel computes for the same bytes (cross-language parity), and the plan
built from a config must be the plan a human wrote (order and focus preserved,
nothing invented, refusals loud).
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest

from iterate_harness.iterate.run_plan import (
    RUN_PLAN_SCHEMA_VERSION,
    RunPlanError,
    build_run_plan,
    run_plan_digest,
    write_run_plan,

)

CORPUS = Path(__file__).resolve().parents[1] / "kernel_fixtures"


def _plan_fixture() -> dict[str, Any]:
    path = CORPUS / "run-plan.ok-01.json"
    assert path.is_file(), (
        "run-plan.ok-01.json is missing from the vendored corpus; restore it from "
        "iterate-kernel's fixtures/ and re-pin it in manifest.json"
    )
    parsed: Any = json.loads(path.read_text(encoding="utf-8"))
    return parsed


def test_the_digest_matches_the_published_kernel_implementation() -> None:
    """The Python digest must equal the one the kernel publishes for the same plan.

    This is the whole reason a third implementation is allowed to exist: if it
    disagrees, the two harnesses' ledgers stop being linkable, and nothing else in
    either repo would notice.
    """
    fixture = _plan_fixture()
    assert run_plan_digest(fixture["input"]) == fixture["expectedDigest"]
    assert fixture["expectedDigest"].startswith("rp_")
    assert len(fixture["expectedDigest"]) == len("rp_") + 64


def test_the_digest_is_bound_to_content_not_serialisation() -> None:
    fixture = _plan_fixture()
    plan = dict(fixture["input"])
    assert run_plan_digest(plan) == run_plan_digest(dict(plan))

    reordered_dimensions = {**plan, "dimensions": list(reversed(plan["dimensions"]))}
    assert run_plan_digest(reordered_dimensions) != fixture["expectedDigest"], (
        "planned order is part of the digest: a re-ordered plan is a different plan"
    )

    dropped_focus = {
        **plan,
        "dimensions": [{k: v for k, v in d.items() if k != "focus"} for d in plan["dimensions"]],
    }
    assert run_plan_digest(dropped_focus) != fixture["expectedDigest"]


def test_a_config_plan_keeps_the_human_order_and_the_scopes_focus() -> None:
    config = {
        "dimensions": ["correctness", "security", "ui-ux", "performance"],
        "dimension_sets": {
            "engine": {"dimensions": ["performance", "correctness"], "focus": {"performance": "latency budget"}},
            "gui": {"dimensions": ["ui-ux"], "focus": {"ui-ux": "只呈现 daemon 实测结果"}},
        },
    }
    plan = build_run_plan(config, scopes=("engine", "gui"), source_name="iterate.config.yaml")
    ids = [str(d["id"]) for d in plan["dimensions"]]
    assert ids == ["performance", "correctness", "ui-ux", "security"], (
        f"scope order must lead and the base list fill in, got {ids}"
    )
    focus = {str(d["id"]): d.get("focus") for d in plan["dimensions"]}
    assert focus["performance"] == "latency budget"
    assert focus["ui-ux"] == "只呈现 daemon 实测结果"
    # No focus text for a dimension means the key is absent, not "" — an empty
    # instruction rendered as a real one is the invention this contract refuses.
    assert "focus" not in plan["dimensions"][1]
    assert plan["schemaVersion"] == RUN_PLAN_SCHEMA_VERSION
    assert plan["source"] == {"kind": "config", "name": "iterate.config.yaml"}


def test_no_scopes_means_the_configs_full_list() -> None:
    config = {"dimensions": ["security", "correctness"]}
    plan = build_run_plan(config)
    assert [str(d["id"]) for d in plan["dimensions"]] == ["security", "correctness"]
    assert plan["source"] == {"kind": "config"}


def test_an_empty_plan_is_refused_rather_than_written() -> None:
    with pytest.raises(RunPlanError) as caught:
        build_run_plan({"dimensions": []})
    assert caught.value.code == "RUN_PLAN_E_NO_DIMENSIONS"
    assert caught.value.remedy


def test_a_config_misnaming_a_scope_is_refused_and_names_the_ones_that_exist() -> None:
    config = {
        "dimensions": ["correctness"],
        "dimension_sets": {"engine": {"dimensions": ["correctness"]}},
    }
    with pytest.raises(RunPlanError) as caught:
        build_run_plan(config, scopes=("engines",))
    assert caught.value.code == "RUN_PLAN_E_SCOPE_UNKNOWN"
    assert "engine" in caught.value.remedy


def test_an_id_outside_the_contract_vocabulary_shape_is_refused() -> None:
    with pytest.raises(RunPlanError) as caught:
        build_run_plan({"dimensions": ["Security"]})
    assert caught.value.code == "RUN_PLAN_E_ID"


def test_a_duplicate_in_the_config_collapses_without_inventing_a_second_entry() -> None:
    """Collapsing *ids* here is not the refusal the kernel makes.

    The kernel refuses a plan that names one dimension twice with different focus,
    because it cannot pick between them. This producer builds a plan out of a config
    where the same id appearing in two scopes is the ordinary cross-layer case, so it
    keeps one entry and the first scope's focus — and the digest then covers what was
    actually handed over.
    """
    config = {
        "dimensions": ["correctness"],
        "dimension_sets": {
            "engine": {"dimensions": ["correctness"], "focus": {"correctness": "first"}},
            "gui": {"dimensions": ["correctness"], "focus": {"correctness": "second"}},
        },
    }
    plan = build_run_plan(config, scopes=("engine", "gui"))
    assert [d["id"] for d in plan["dimensions"]] == ["correctness"]
    assert plan["dimensions"][0]["focus"] == "first"


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_written_plans_are_owner_only_and_refuse_the_engines_state_root(tmp_path: Path) -> None:
    plan = build_run_plan({"dimensions": ["correctness"]})
    target = tmp_path / "run" / "plan.json"
    written = write_run_plan(plan, target)
    assert written.path == target
    assert _mode(target) == 0o600
    assert written.digest == run_plan_digest(plan)
    assert run_plan_digest(json.loads(target.read_text(encoding="utf-8"))) == written.digest

    with pytest.raises(RunPlanError) as caught:
        write_run_plan(plan, "relative/plan.json")
    assert caught.value.code == "RUN_PLAN_E_PATH"

    with pytest.raises(RunPlanError) as caught:
        write_run_plan(plan, Path.home() / ".glasspane" / "plan.json")
    assert caught.value.code == "RUN_PLAN_E_STATE_ROOT"


def test_a_directory_that_cannot_be_created_is_reported_not_swallowed(tmp_path: Path) -> None:
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory", encoding="utf-8")
    plan = build_run_plan({"dimensions": ["correctness"]})
    with pytest.raises(RunPlanError) as caught:
        write_run_plan(plan, blocker / "sub" / "plan.json")
    assert caught.value.code == "RUN_PLAN_E_WRITE"


def test_a_non_bmp_key_is_refused_instead_of_ordered_differently() -> None:
    plan = {"schemaVersion": RUN_PLAN_SCHEMA_VERSION, "\U0001f600": 1, "dimensions": [{"id": "x"}]}
    with pytest.raises(RunPlanError) as caught:
        run_plan_digest(plan)
    assert caught.value.code == "RUN_PLAN_E_KEY_ORDER"


def test_a_non_string_value_is_refused_rather_than_stringified() -> None:
    with pytest.raises(RunPlanError) as caught:
        run_plan_digest({"dimensions": [{"id": "x"}], "extra": {1, 2}})
    assert caught.value.code == "RUN_PLAN_E_VALUE"


def test_umask_cannot_widen_the_written_mode(tmp_path: Path) -> None:
    """0600 must survive a permissive umask, or the file leaks a runnable plan."""
    previous = os.umask(0o000)
    try:
        plan = build_run_plan({"dimensions": ["correctness"]})
        written = write_run_plan(plan, tmp_path / "plan.json")
    finally:
        os.umask(previous)
    assert _mode(written.path) == 0o600


def test_a_plan_that_cannot_be_digest_is_never_written(tmp_path: Path) -> None:
    """The pre-flight digest is not decoration: a bad plan must leave no file behind."""
    target = tmp_path / "plan.json"
    with pytest.raises(RunPlanError) as caught:
        write_run_plan({"dimensions": [{"id": "x"}], "extra": {1, 2}}, target)
    assert caught.value.code == "RUN_PLAN_E_VALUE"
    assert not target.exists()
