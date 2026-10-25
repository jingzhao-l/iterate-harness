"""The recipe hand-off (④'s producer) and the gate that answers "may the recipe proceed".

Two claims are tested here, and they are the two that a reviewer cannot check by reading the code
alone:

1. the recipe the *other* product configured reaches this harness as a value that is validated
   against the pinned contract, digested, and carried verbatim — never re-shaped, never invented;
2. the gate that grants permission reads only measured things, in a fixed order, and refuses by
   name when any of them is missing — including the case the project has been fined for before,
   a gate that never ran reading as a gate that passed.

The negative table is the load-bearing half. ``recipe_step_gate`` has six refusal codes, and a
test that only walks the happy path would pass against a version of this module that ignores the
verdict, the recipe, or the ledger alike. Each refusal is therefore pinned to the *one* condition
that produces it, and three tests pin the order in which the conditions are consulted.

Cross-language anchor: ``tests/kernel_fixtures/recipe-config.ok-01.json`` (pinned by sha256 in
``manifest.json``). The rules below are the contract's, restated from the schema and the kernel
module so this file stands on its own — the same discipline ``invariant_report.py`` follows.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
from pathlib import Path
from typing import Any

import pytest

from iterate_harness.defensive.kernel import DEFENSIVE_KERNEL_KEY, DefensiveKernel
from iterate_harness.iterate import reverify_trigger
from iterate_harness.iterate.glasspane_recipe import (
    FIX_LEDGER_ENV_VAR,
    GATE_E_GATE,
    GATE_E_LEDGER,
    GATE_E_NO_VERDICT,
    GATE_E_UNPROVEN,
    RECIPE_E_ABSENT,
    RECIPE_E_CONTEST,
    RECIPE_E_NOT_ASKED,
    RECIPE_E_PATH,
    RECIPE_E_READ,
    RECIPE_E_SCHEMA,
    RECIPE_E_STATE_ROOT,
    RECIPE_ENV_VAR,
    Recipe,
    RecipeError,
    RecipeRead,
    fix_proof,
    parse_recipe_text,
    read_recipe,
    read_recipe_or_refusal,
    recipe_digest,
    recipe_path_from_project_list,
    recipe_step_gate,
    validate_recipe_document,
)
from iterate_harness.iterate.decision_log import append_entry, make_entry
from iterate_harness.iterate.kernel_decision_log import (
    append_decision_log_entry,
    new_decision_entry_id,
)
from iterate_harness.iterate.loop_gates import measured_gates
from iterate_harness.iterate.reverify_trigger import RECIPE_GATE_STATE_KEY

CORPUS = Path(__file__).resolve().parents[1] / "kernel_fixtures"

#: Valid per the contract's own pattern (Crockford base32, no I/L/O/U).
OPERATION_ID = "op_AAAAAAAAAAAAAAAAAAAAAAAAAA"
OTHER_OPERATION_ID = "op_BBBBBBBBBBBBBBBBBBBBBBBBBB"


def _fixture_document() -> dict[str, Any]:
    return json.loads((CORPUS / "recipe-config.ok-01.json").read_text(encoding="utf-8"))


def _write_recipe(tmp_path: Path, document: dict[str, Any] | None = None, name: str = "recipe.json") -> str:
    path = tmp_path / name
    path.write_text(json.dumps(document if document is not None else _fixture_document()), encoding="utf-8")
    return str(path)


def _chained_ledger(tmp_path: Path, *drafts: dict[str, object], dirname: str = ".iterate") -> str:
    """A real chain ledger, written by the kernel API this gate itself trusts."""
    directory = tmp_path / dirname
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    path = directory / "decision-log.jsonl"
    for draft in drafts:
        append_decision_log_entry(str(path), draft)
    return str(path)


def _passing_draft(operation_id: str = OPERATION_ID, outcome: str = "pass") -> dict[str, object]:
    return {
        "entryId": new_decision_entry_id(),
        "summary": "act landed on the window the action targeted",
        "outcome": outcome,
        "createdAt": "2026-10-24T04:00:00.000Z",
        "operationId": operation_id,
        "kind": "evidence",
    }


# --------------------------------------------------------------------- the pinned contract itself


def test_the_pinned_recipe_validates_and_the_steps_arrive_verbatim() -> None:
    document = _fixture_document()
    name, steps, normalised = validate_recipe_document(document)
    assert name == "smoke-check-submit"
    assert [step.kind for step in steps] == ["act", "observe", "assert", "diagnose"]
    assert normalised == document, (
        "the parsed document must round-trip to the pinned bytes: a normaliser that rewrites "
        "params would change what the engine is asked to re-run"
    )
    assert json.dumps(normalised, sort_keys=True) == json.dumps(document, sort_keys=True)


def test_the_digest_covers_the_bytes_the_contract_was_read_from() -> None:
    document = _fixture_document()
    assert recipe_digest(document) == "rc_" + hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def test_key_insertion_order_does_not_move_the_digest() -> None:
    document = _fixture_document()
    reordered = {key: document[key] for key in ("steps", "schemaVersion", "name")}
    assert recipe_digest(reordered) == recipe_digest(document)


def test_a_reordered_step_list_digests_differ_because_order_is_the_recipes_meaning() -> None:
    document = _fixture_document()
    swapped = dict(document, steps=list(reversed(document["steps"])))
    assert recipe_digest(swapped) != recipe_digest(document)


@pytest.mark.parametrize(
    ("case", "mutate", "expected_path"),
    [
        ("wrong-version", lambda d: {**d, "schemaVersion": "glasspane.recipe/0.2"}, "schemaVersion"),
        ("extra-top-key", lambda d: {**d, "owner": "me"}, None),
        ("missing-name", lambda d: {k: v for k, v in d.items() if k != "name"}, None),
        ("name-too-long", lambda d: {**d, "name": "n" * 257}, None),
        ("no-steps", lambda d: {**d, "steps": []}, None),
        ("sixty-five-steps", lambda d: {**d, "steps": d["steps"] * 17}, None),
        ("steps-not-a-list", lambda d: {**d, "steps": "act"}, None),
        ("bad-step-kind", lambda d: {**d, "steps": [{**d["steps"][0], "kind": "press"}]}, "steps.0.kind"),
        ("step-extra-key", lambda d: {**d, "steps": [{**d["steps"][0], "when": "soon"}]}, "steps.0"),
        ("params-not-an-object", lambda d: {**d, "steps": [{"kind": "act", "params": None}]}, "steps.0.params"),
        ("step-as-a-string", lambda d: {**d, "steps": ["act"]}, "steps.0"),
    ],
)
def test_the_validator_refuses_every_shape_the_schema_refuses(case: str, mutate: Any, expected_path: str | None) -> None:
    del case
    broken = mutate(_fixture_document())
    with pytest.raises(RecipeError) as caught:
        validate_recipe_document(broken)
    assert caught.value.code == RECIPE_E_SCHEMA
    if expected_path is not None:
        assert caught.value.path == expected_path


def test_a_document_that_is_not_an_object_is_refused_not_crashed() -> None:
    for raw in ("act", 7, None, ["a"]):
        with pytest.raises(RecipeError) as caught:
            validate_recipe_document(raw)
        assert caught.value.code == RECIPE_E_SCHEMA


def test_bad_json_is_a_named_read_refusal() -> None:
    with pytest.raises(RecipeError) as caught:
        parse_recipe_text("{schemaVersion:", path="/x/recipe.json")
    assert caught.value.code == RECIPE_E_READ
    assert "/x/recipe.json" in caught.value.message


# --------------------------------------------------------------------------------- the hand-off


def test_the_environment_hands_a_recipe_and_it_is_held(tmp_path: Path) -> None:
    path = _write_recipe(tmp_path)
    held = read_recipe(env={RECIPE_ENV_VAR: path})
    assert held is not None and held.source == "env" and held.path == str(Path(path).resolve())


def test_a_relative_path_is_refused_because_cwd_is_not_configuration(tmp_path: Path) -> None:
    path = _write_recipe(tmp_path)
    with pytest.raises(RecipeError) as caught:
        read_recipe(env={RECIPE_ENV_VAR: os.path.relpath(path)})
    assert caught.value.code == RECIPE_E_PATH


def test_a_missing_recipe_file_is_refused_rather_than_read_as_absent(tmp_path: Path) -> None:
    with pytest.raises(RecipeError) as caught:
        read_recipe(env={RECIPE_ENV_VAR: str(tmp_path / "gone.json")})
    assert caught.value.code == RECIPE_E_READ


def test_a_recipe_inside_the_engines_state_root_is_refused(tmp_path: Path) -> None:
    engine_root = tmp_path / ".glasspane"
    engine_root.mkdir()
    path = _write_recipe(engine_root, name="recipe.json")
    with pytest.raises(RecipeError) as caught:
        read_recipe(env={RECIPE_ENV_VAR: path})
    assert caught.value.code == RECIPE_E_STATE_ROOT


def test_env_and_registry_disagreeing_is_a_contest_not_a_tie_break(tmp_path: Path) -> None:
    handed = _write_recipe(tmp_path, name="a.json")
    other = _write_recipe(tmp_path, name="b.json")
    with pytest.raises(RecipeError) as caught:
        read_recipe(env={RECIPE_ENV_VAR: handed}, shell_reported_path=other)
    assert caught.value.code == RECIPE_E_CONTEST
    assert handed in caught.value.message and other in caught.value.message


def test_agreement_between_the_two_producers_keeps_the_explicit_one(tmp_path: Path) -> None:
    path = _write_recipe(tmp_path)
    held = read_recipe(env={RECIPE_ENV_VAR: path}, shell_reported_path=path)
    assert held is not None and held.source == "env"


def test_nothing_declared_is_none_and_not_an_error() -> None:
    assert read_recipe(env={}) is None


def test_a_host_that_never_asked_the_registry_says_so_instead_of_claiming_emptiness() -> None:
    read = read_recipe_or_refusal(env={}, shell_reported_path=None, registry_asked=False)
    assert read.recipe is None and read.refusal is not None
    assert read.refusal.code == RECIPE_E_NOT_ASKED


def test_a_host_that_never_asked_but_handed_a_path_still_holds_the_recipe(tmp_path: Path) -> None:
    path = _write_recipe(tmp_path)
    read = read_recipe_or_refusal(env={RECIPE_ENV_VAR: path}, shell_reported_path=None, registry_asked=False)
    assert read.held and read.recipe is not None and read.recipe.path == str(Path(path).resolve())


# ------------------------------------------------------------------- reading the registry's reply

_REPLY_FRAME = {"projects": [{"projectId": "prj_A", "recipeConfigPath": "/srv/a.json"}]}


def test_the_frame_is_found_among_the_side_items_the_shell_sends() -> None:
    reply = ["update state: status=needs-consent", json.dumps(_REPLY_FRAME)]
    assert recipe_path_from_project_list(reply) == "/srv/a.json"


def test_a_nullish_registry_answers_with_an_absence_not_a_guess() -> None:
    reply = [json.dumps({"projects": [{"recipeConfigPath": None}, {"projectId": "prj_B"}]})]
    assert recipe_path_from_project_list(reply) is None


def test_two_projects_each_with_a_recipe_is_a_contest() -> None:
    reply = [
        json.dumps(
            {
                "projects": [
                    {"recipeConfigPath": "/srv/a.json"},
                    {"recipeConfigPath": "/srv/b.json"},
                ]
            }
        )
    ]
    with pytest.raises(RecipeError) as caught:
        recipe_path_from_project_list(reply)
    assert caught.value.code == RECIPE_E_CONTEST
    assert "/srv/a.json" in caught.value.message and "/srv/b.json" in caught.value.message


def test_the_same_path_written_by_two_projects_is_one_recipe() -> None:
    reply = [
        json.dumps({"projects": [{"recipeConfigPath": "/srv/a.json"}, {"recipeConfigPath": "/srv/a.json"}]})
    ]
    assert recipe_path_from_project_list(reply) == "/srv/a.json"


def test_a_coded_refusal_is_not_relabelled_as_no_recipe() -> None:
    reply = ["GP_E_INTERNAL: the registry file could not be read | remedy: repair the state root"]
    with pytest.raises(RecipeError) as caught:
        recipe_path_from_project_list(reply)
    assert caught.value.code == RECIPE_E_READ
    assert "refused" in caught.value.message


def test_a_reply_from_an_unrelated_mcp_server_refuses() -> None:
    with pytest.raises(RecipeError) as caught:
        recipe_path_from_project_list([json.dumps({"servers": []})])
    assert caught.value.code == RECIPE_E_READ
    assert "glasspane-mcp" in caught.value.remedy


def test_a_joined_reply_is_refused_rather_than_resplit_on_newlines() -> None:
    """The #20 shape: the frame plus a second item, flattened into one string.

    Re-splitting on newlines would be guessing at a boundary the transport destroyed, and a guess
    that happens to parse would be reported as a configuration the other product never wrote.
    """
    joined = json.dumps(_REPLY_FRAME) + "\nupdate state: status=needs-consent"
    with pytest.raises(RecipeError) as caught:
        recipe_path_from_project_list(joined)
    assert caught.value.code == RECIPE_E_READ


def test_a_reply_that_is_not_content_at_all_refuses() -> None:
    with pytest.raises(RecipeError) as caught:
        recipe_path_from_project_list(7)
    assert caught.value.code == RECIPE_E_READ


def test_non_object_project_entries_are_skipped_and_the_real_one_still_answers() -> None:
    reply = [json.dumps({"projects": ["junk", 3, {"recipeConfigPath": "/srv/a.json"}]})]
    assert recipe_path_from_project_list(reply) == "/srv/a.json"


# --------------------------------------------------------------------------------- fix proof


def test_a_chained_passing_line_proves_the_fix(tmp_path: Path) -> None:
    _chained_ledger(tmp_path, _passing_draft())
    proof = fix_proof(tmp_path, env={})
    assert proof.operation_id == OPERATION_ID and proof.outcome == "pass"
    assert proof.ledger_source == "project" and proof.ledger_path.endswith(".iterate/decision-log.jsonl")


def test_the_newest_proving_line_wins(tmp_path: Path) -> None:
    _chained_ledger(tmp_path, _passing_draft(OPERATION_ID), _passing_draft(OTHER_OPERATION_ID))
    assert fix_proof(tmp_path, env={}).operation_id == OTHER_OPERATION_ID


def test_a_handed_ledger_is_read_as_handed(tmp_path: Path) -> None:
    path = _chained_ledger(tmp_path, _passing_draft(), dirname="elsewhere")
    proof = fix_proof(tmp_path, env={FIX_LEDGER_ENV_VAR: path})
    assert proof.ledger_source == "handed" and proof.ledger_path == path


def test_a_handed_ledger_must_be_absolute(tmp_path: Path) -> None:
    path = _chained_ledger(tmp_path, _passing_draft(), dirname="elsewhere")
    with pytest.raises(RecipeError) as caught:
        fix_proof(tmp_path, env={FIX_LEDGER_ENV_VAR: os.path.relpath(path)})
    assert caught.value.code == RECIPE_E_PATH


def test_a_handed_ledger_inside_the_engine_root_is_refused(tmp_path: Path) -> None:
    engine_root = tmp_path / ".glasspane"
    path = _chained_ledger(engine_root, _passing_draft(), dirname="ledgers")
    with pytest.raises(RecipeError) as caught:
        fix_proof(tmp_path, env={FIX_LEDGER_ENV_VAR: path})
    assert caught.value.code == RECIPE_E_STATE_ROOT


@pytest.mark.parametrize("outcome", ["fail", "blocked", "inconclusive"])
def test_only_a_passing_line_proves_a_fix(tmp_path: Path, outcome: str) -> None:
    _chained_ledger(tmp_path, _passing_draft(outcome=outcome))
    with pytest.raises(RecipeError) as caught:
        fix_proof(tmp_path, env={})
    assert caught.value.code == GATE_E_UNPROVEN


def test_a_line_without_an_operation_id_proves_nothing(tmp_path: Path) -> None:
    draft = _passing_draft()
    del draft["operationId"]
    _chained_ledger(tmp_path, draft)
    with pytest.raises(RecipeError) as caught:
        fix_proof(tmp_path, env={})
    assert caught.value.code == GATE_E_UNPROVEN


def test_an_empty_or_missing_ledger_proves_nothing(tmp_path: Path) -> None:
    with pytest.raises(RecipeError) as caught:
        fix_proof(tmp_path, env={})
    assert caught.value.code == GATE_E_UNPROVEN


def test_editing_the_first_line_of_a_chain_is_caught_by_the_next_lines_hash(tmp_path: Path) -> None:
    """What a hash chain actually protects, asserted rather than assumed.

    Line 1 carries the hash of line 0's canonical bytes, so rewriting line 0 breaks line 1. The
    refusal has to name the line where the chain stops, because that is the number an investigator
    opens the file at.
    """
    path = Path(_chained_ledger(tmp_path, _passing_draft(), _passing_draft(OTHER_OPERATION_ID)))
    lines = path.read_text(encoding="utf-8").splitlines()
    tampered = lines[0].replace('"outcome":"pass"', '"outcome":"fail"')
    assert tampered != lines[0], "the pinned draft must carry a passing outcome to edit"
    path.write_text("\n".join([tampered, lines[1]]) + "\n", encoding="utf-8")
    with pytest.raises(RecipeError) as caught:
        fix_proof(tmp_path, env={})
    assert caught.value.code == GATE_E_LEDGER
    assert "line 2" in caught.value.message
    assert "investigate" in caught.value.remedy


def test_a_lone_lines_own_content_is_beyond_what_the_chain_can_see(tmp_path: Path) -> None:
    """The limit, pinned so nobody later claims more than the mechanism gives.

    A one-line ledger has no successor to hash it, so rewriting that line's outcome stays
    self-consistent. The gate then refuses for the honest reason — the only line it can read no
    longer says a fix passed — rather than reporting a chain break it did not observe.
    """
    path = Path(_chained_ledger(tmp_path, _passing_draft()))
    path.write_text(
        path.read_text(encoding="utf-8").replace('"outcome":"pass"', '"outcome":"fail"'), encoding="utf-8"
    )
    with pytest.raises(RecipeError) as caught:
        fix_proof(tmp_path, env={})
    assert caught.value.code == GATE_E_UNPROVEN


def test_a_pre_chain_ledger_line_gets_its_own_sentence(tmp_path: Path) -> None:
    """Legacy history is not corruption, and the remedy must not send the operator hunting for one.

    The write side flipped to the chain and migrates on the next append; a log that still holds
    only the old ``{timestamp, round, type, data}`` shape is the state of every project upgraded
    before that append, so this refusal has to name the shape rather than a broken hash.
    """
    directory = tmp_path / ".iterate"
    directory.mkdir()
    os.chmod(directory, 0o700)
    legacy = {"timestamp": "2026-09-01T00:00:00Z", "round": 1, "type": "decision", "data": {"x": 1}}
    (directory / "decision-log.jsonl").write_text(json.dumps(legacy) + "\n", encoding="utf-8")
    with pytest.raises(RecipeError) as caught:
        fix_proof(tmp_path, env={})
    assert caught.value.code == GATE_E_LEDGER
    assert "predates the kernel chain" in caught.value.remedy


# -------------------------------------------------------------------------------------- the gate


def _record_an_operation(project_root: Path, operation_id: str) -> None:
    """Seed the shape ``recorded_operation_ids`` reads: a loop record carrying ``data.operationId``.

    Worth saying out loud because the two ledger readers answer different questions. ①'s target list
    comes from ``decision_log.read_entries``, which projects a canonical line's ``payload`` into
    ``data`` — so an operation id belongs at the top level of a canonical entry does *not* put it in
    that list, and a fork-written line (which carries it top level) feeds ④'s proof through
    ``fix_proof`` without ever becoming something ① can ask the engine about. Both readers are right
    about their own question; this test seeds the shape ① needs.
    """
    append_entry(
        project_root,
        make_entry(
            entry_type="decision",
            round_number=1,
            data={"kind": "evidence", "operationId": operation_id, "summary": "engine recorded"},
        ),
    )


def _read_for(tmp_path: Path) -> RecipeRead:
    return read_recipe_or_refusal(env={RECIPE_ENV_VAR: _write_recipe(tmp_path)}, registry_asked=True)


def test_no_verdict_grants_nothing_and_touches_no_file() -> None:
    """The order is load-bearing: a round with no verdict must not read anything to answer.

    The env names a recipe file that does not exist — if the gate consulted the hand-off before the
    verdict, the refusal would be RECIPE_E_READ, and a test that only asserted ``allowed is False``
    would never notice the difference.
    """
    gate = recipe_step_gate(
        project_root="/nonexistent",
        invariant_verdict=None,
        read=RecipeRead(refusal=RecipeError(RECIPE_E_READ, "would be read", "would be read")),
    )
    assert gate.allowed is False
    assert gate.refusal is not None and gate.refusal["code"] == GATE_E_NO_VERDICT


@pytest.mark.parametrize("verdict", ["fail", "error", "unmeasured"])
def test_a_gate_that_did_not_pass_grants_nothing(verdict: str) -> None:
    gate = recipe_step_gate(
        project_root="/nonexistent",
        invariant_verdict=verdict,
        read=RecipeRead(refusal=RecipeError(RECIPE_E_READ, "unreadable", "unreadable")),
    )
    assert gate.allowed is False and gate.verdict == verdict
    assert gate.refusal is not None and gate.refusal["code"] == GATE_E_GATE


def test_the_verdict_outranks_a_contested_handoff(tmp_path: Path) -> None:
    """Both conditions are broken; the published answer is the gate's, because that is the order.

    Swapping the order would publish a recipe complaint for a run whose invariant re-check failed —
    which reads as "fix your configuration" instead of "the checks did not hold".
    """
    gate = recipe_step_gate(
        project_root=tmp_path,
        invariant_verdict="fail",
        read=RecipeRead(refusal=RecipeError(RECIPE_E_CONTEST, "two paths", "pick one")),
    )
    assert gate.refusal is not None and gate.refusal["code"] == GATE_E_GATE


def test_a_pass_with_nothing_declared_names_the_absence(tmp_path: Path) -> None:
    gate = recipe_step_gate(project_root=tmp_path, invariant_verdict="pass", read=RecipeRead(), env={})
    assert gate.allowed is False
    assert gate.refusal is not None and gate.refusal["code"] == RECIPE_E_ABSENT


def test_a_pass_with_a_recipe_and_no_proven_fix_is_still_refused(tmp_path: Path) -> None:
    gate = recipe_step_gate(
        project_root=tmp_path, invariant_verdict="pass", read=_read_for(tmp_path), env={}
    )
    assert gate.allowed is False
    assert gate.refusal is not None and gate.refusal["code"] == GATE_E_UNPROVEN
    assert gate.recipe_digest is not None and gate.steps == ("act", "observe", "assert", "diagnose"), (
        "the recipe the run does hold is still published, because 'what is configured' is a fact "
        "the reader needs even when permission is refused"
    )


def test_all_three_conditions_together_are_what_grants_permission(tmp_path: Path) -> None:
    _chained_ledger(tmp_path, _passing_draft())
    gate = recipe_step_gate(
        project_root=tmp_path, invariant_verdict="pass", read=_read_for(tmp_path), env={}
    )
    assert gate.allowed is True and gate.refusal is None
    payload = gate.to_payload()
    assert payload["invariantState"] == "pass"
    assert payload["recipeDigest"] == gate.recipe_digest
    assert payload["fixProof"]["operationId"] == OPERATION_ID
    assert payload["fixProof"]["entryId"].startswith("dl_")
    assert payload["steps"] == ["act", "observe", "assert", "diagnose"]


def test_a_pass_with_a_broken_chain_refuses_and_says_which_line(tmp_path: Path) -> None:
    path = Path(_chained_ledger(tmp_path, _passing_draft(), _passing_draft(OTHER_OPERATION_ID)))
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join([lines[0].replace('"outcome":"pass"', '"outcome":"fail"'), lines[1]]) + "\n", encoding="utf-8")
    gate = recipe_step_gate(
        project_root=tmp_path, invariant_verdict="pass", read=_read_for(tmp_path), env={}
    )
    assert gate.allowed is False
    assert gate.refusal is not None and gate.refusal["code"] == GATE_E_LEDGER
    assert gate.recipe_digest is not None, "the recipe it does hold is still named"


# ------------------------------------------------------------- the trigger, wired through for real


async def _passing_kernel(tmp_path: Path) -> DefensiveKernel:
    (tmp_path / "README.md").write_text("# real project\n", encoding="utf-8")
    (tmp_path / "iterate.config.yaml").write_text(
        "invariants:\n  ensure:\n    - README.md\nreviewer:\n  evidence_validation: false\n",
        encoding="utf-8",
    )
    from iterate_harness.iterate.config_loader import load_effective_config

    kernel = DefensiveKernel(tmp_path, load_effective_config(tmp_path))
    await kernel.check_invariants()
    assert kernel.last_invariant_report is not None
    return kernel


def _registry_reply(path: str) -> list[str]:
    return [json.dumps({"projects": [{"projectId": "prj_A", "recipeConfigPath": path}]})]


def _evidence_reply(payload: object) -> list[str]:
    return [json.dumps({"evidencePack": payload})]


def test_a_passing_round_publishes_the_gate_with_the_recipe_it_read(tmp_path: Path, monkeypatch) -> None:
    """The publish site is the only consumer that makes ④ real; a gate nobody calls is a claim.

    Everything here goes through ``trigger_reverification`` with a host transport of the shape the
    harness binds — no direct calls into the gate — so the test fails if the publish site stops
    consulting it, and it fails if the recipe it publishes is anything other than the one the
    registry named.
    """
    import asyncio

    kernel = asyncio.run(_passing_kernel(tmp_path))
    _chained_ledger(tmp_path, _passing_draft())
    monkeypatch.delenv(RECIPE_ENV_VAR, raising=False)
    monkeypatch.delenv(FIX_LEDGER_ENV_VAR, raising=False)
    recipe_path = _write_recipe(tmp_path)
    asked: list[tuple[str, dict[str, Any]]] = []

    def transport(tool: str, arguments: dict[str, Any]) -> object:
        asked.append((tool, arguments))
        if tool == reverify_trigger.REGISTRY_READ_TOOL:
            return _registry_reply(recipe_path)
        return _evidence_reply({"operationId": OPERATION_ID, "outcome": "pass"})

    payload = reverify_trigger.trigger_reverification(
        project_root=tmp_path,
        metadata={DEFENSIVE_KERNEL_KEY: kernel, reverify_trigger.GLASSPANE_TRANSPORT_KEY: transport},
        gates=measured_gates(evidence_validation=False, findings=[], cwd=tmp_path, metadata={DEFENSIVE_KERNEL_KEY: kernel}),
        round_number=1,
    )
    assert payload is not None and payload["spawned"] is False
    gate = payload[RECIPE_GATE_STATE_KEY]
    assert gate["allowed"] is True, gate
    assert gate["invariantState"] == "pass"
    assert gate["fixProof"]["operationId"] == OPERATION_ID
    assert asked[0] == (reverify_trigger.REGISTRY_READ_TOOL, {})


def test_a_failing_recheck_publishes_the_refusal_and_names_no_steps(tmp_path: Path, monkeypatch) -> None:
    """④'s ordering, expressed as the one thing this round can actually be made to obey.

    The scenario says the recipe proceeds to re-verification *after* the invariant re-check passes.
    The contract this round builds under refuses to make a re-verification request from a passing
    report at all (both implementations return null on ``pass``) — so there is no send that carries
    permission. What this module can be made to do, and does, is the safe half: while the re-check
    has not passed, a request must not name recipe steps, because naming one is asking the other
    product to move the app. That is the assertion below, and the gate that justifies it is
    published next to it rather than only on the passing branch.

    The first version of this file asserted the opposite — that a failing round *did* carry the
    recipe's steps — which read as a closed scenario while contradicting the row it cites.
    """
    import asyncio

    kernel = asyncio.run(_failing_kernel(tmp_path))
    _record_an_operation(tmp_path, OPERATION_ID)
    monkeypatch.delenv(RECIPE_ENV_VAR, raising=False)
    monkeypatch.delenv(FIX_LEDGER_ENV_VAR, raising=False)
    recipe_path = _write_recipe(tmp_path)

    def transport(tool: str, arguments: dict[str, Any]) -> object:
        if tool == reverify_trigger.REGISTRY_READ_TOOL:
            return _registry_reply(recipe_path)
        return _evidence_reply({"operationId": OPERATION_ID, "outcome": "pass"})

    payload = reverify_trigger.trigger_reverification(
        project_root=tmp_path,
        metadata={DEFENSIVE_KERNEL_KEY: kernel, reverify_trigger.GLASSPANE_TRANSPORT_KEY: transport},
        gates=measured_gates(
            evidence_validation=False, findings=[], cwd=tmp_path, metadata={DEFENSIVE_KERNEL_KEY: kernel}
        ),
        round_number=1,
    )
    assert payload is not None and payload["spawned"] is True, "the failing re-check still earns ①'s read-only send"
    assert "recipeSteps" not in payload["targets"], (
        "a request built while the re-check is failing must not ask the engine to re-run recipe steps"
    )
    gate = payload[RECIPE_GATE_STATE_KEY]
    assert gate["allowed"] is False and gate["invariantState"] == "fail"
    assert gate["refusal"]["code"] == GATE_E_GATE
    assert gate["steps"] == [], (
        "the verdict outranks the hand-off, so on this branch no recipe file was even opened — the "
        "same rule that keeps a round without a verdict from reading files. Where the recipe *was* "
        "read and permission still fails (the no-proof case) the steps are published; both shapes "
        "have their own test above."
    )
    assert payload["targets"]["operationIds"] == [OPERATION_ID], (
        "①'s read-only send is unchanged by ④: it asks about recorded operations, nothing else"
    )


def test_targets_never_carry_recipeSteps_however_the_recipe_looks(tmp_path: Path) -> None:
    """The unit half of the same rule: the target builder has no path to a recipe step."""
    held = read_recipe(env={RECIPE_ENV_VAR: _write_recipe(tmp_path)})
    assert held is not None and len(held.steps) == 4
    targets, _ = reverify_trigger.reverification_targets(tmp_path, ("spec-compliance",))
    assert "recipeSteps" not in targets
    assert "recipeSteps" not in inspect.signature(reverify_trigger.reverification_targets).parameters, (
        "the parameter itself must not come back: a caller that *can* hand in a recipe will, and "
        "the next reader has to re-derive which of the two paths was reachable in production"
    )


def test_a_recipe_object_carries_its_steps_as_the_contract_names_them(tmp_path: Path) -> None:
    held = read_recipe(env={RECIPE_ENV_VAR: _write_recipe(tmp_path)})
    assert isinstance(held, Recipe)
    assert held.targets() == _fixture_document()["steps"]


async def _failing_kernel(tmp_path: Path) -> DefensiveKernel:
    (tmp_path / "README.md").write_text("# real project\n", encoding="utf-8")
    (tmp_path / "iterate.config.yaml").write_text(
        "invariants:\n  ensure:\n    - README.md\n    - not-a-real-file.md\n"
        "reviewer:\n  evidence_validation: false\n",
        encoding="utf-8",
    )
    from iterate_harness.iterate.config_loader import load_effective_config

    kernel = DefensiveKernel(tmp_path, load_effective_config(tmp_path))
    await kernel.check_invariants()
    assert kernel.last_invariant_report is not None
    return kernel
