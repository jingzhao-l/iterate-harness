"""③'s last mile and ④'s reader: linking the other harness's ledger, then reading the gate back.

Two gaps this file closes, both of them the "works in tests, absent in production" shape this repo
keeps getting fined for:

* ``fix_proof`` could already read a foreign chain, but the only way to tell it *where* was to export
  ``GLASSPANE_DECISION_LOG`` on the process — so on any machine nobody had configured, the capability
  silently degraded to "project ledger only", which for a GlassPane-verified fix means "no proof".
  The link file makes the hand-off a thing you do once, per project, and the reader resolves it.
* The ④ gate published a permission into the round's payload, which any test could read and nobody
  downstream could. It now travels into the ledger line the same round writes, and `/iterate gate`
  prints it back — a state with a reader is a state, and a state with none is a claim.

Everything here is asserted against real bytes: the foreign ledger is the one the published TypeScript
kernel wrote (``tests/fixtures/glasspane_chain_ts_written.jsonl``), and refusals are checked by code
name, not by "some exception".
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from iterate_harness import cli as cli_module
from iterate_harness.commands.iterate import iterate_command_handler
from iterate_harness.commands.registry import CommandContext
from iterate_harness.defensive.kernel import DEFENSIVE_KERNEL_KEY, DefensiveKernel
from iterate_harness.iterate import reverify_trigger
from iterate_harness.iterate.decision_log import LOG_DIR, read_entries
from iterate_harness.iterate.glasspane_recipe import (
    FIX_LEDGER_ENV_VAR,
    LEDGER_LINK_FILENAME,
    LEDGER_LINK_SCHEMA_VERSION,
    LINK_E_CHAIN,
    LINK_E_PATH,
    LINK_E_READ,
    LINK_E_SCHEMA,
    LINK_E_STALE,
    RECIPE_GATE_DATA_KEY,
    RecipeError,
    fix_proof,
    ledger_link_path,
    link_ledger,
    read_ledger_link,
    render_gate_state,
    render_ledger_link,
    resolve_ledger,
)
from iterate_harness.iterate.loop_gates import measured_gates

CORPUS = Path(__file__).resolve().parents[1] / "fixtures"
FOREIGN_LEDGER = CORPUS / "glasspane_chain_ts_written.jsonl"
GENESIS = "dl_" + "A" * 26
OPERATION_ID = "op_" + "C" * 26
LINKED_AT = "2026-10-25T00:00:00.000Z"


def _link(project_root: Path, ledger: Path = FOREIGN_LEDGER) -> dict[str, str]:
    return link_ledger(project_root, ledger, linked_at=LINKED_AT)


def _write_ledger(project_root: Path, lines: list[str]) -> Path:
    directory = project_root / LOG_DIR
    directory.mkdir(parents=True, exist_ok=True)
    import os

    os.chmod(directory, 0o700)
    target = directory / "decisions.jsonl"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


# ---------------------------------------------------------------------------- the link itself


def test_linking_records_the_absolute_path_and_the_genesis_entry() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as raw:
        project = Path(raw)
        document = _link(project)
        assert document["path"] == str(FOREIGN_LEDGER)
        assert document["genesisEntryId"] == GENESIS, "the first line of the TS-written chain is the identity to pin"
        recorded = json.loads(ledger_link_path(project).read_text(encoding="utf-8"))
        assert recorded == document
        assert ledger_link_path(project).name == LEDGER_LINK_FILENAME


def test_a_linked_ledger_is_what_fix_proof_reads_with_no_environment_involved() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as raw:
        project = Path(raw)
        _link(project)
        proof = fix_proof(project, env={})
        assert proof.ledger_source == "link"
        assert proof.operation_id == OPERATION_ID
        assert proof.ledger_path == str(FOREIGN_LEDGER)


def test_an_explicit_environment_still_outranks_the_recorded_link(tmp_path: Path) -> None:
    _link(tmp_path)
    path, source, link = resolve_ledger(tmp_path, env={FIX_LEDGER_ENV_VAR: str(FOREIGN_LEDGER.parent / "evidence-pack.ok-01.json")})
    assert source == "handed" and link is None
    assert path.name == "evidence-pack.ok-01.json"


def test_no_link_and_no_environment_means_the_project_ledger(tmp_path: Path) -> None:
    path, source, link = resolve_ledger(tmp_path, env={})
    assert source == "project" and link is None and path.parent.name == LOG_DIR


def test_linking_a_broken_chain_refuses_and_writes_no_marker(tmp_path: Path) -> None:
    """A link is a promise about a chain; promising one about a broken file must fail *before* storing it.

    The zero-write half is the load-bearing assertion: a marker for an unverifiable ledger turns
    every later round into a refusal, and the operator learns about it mid-run instead of at the
    command that caused it.
    """
    lines = FOREIGN_LEDGER.read_text(encoding="utf-8").splitlines()
    tampered = _write_ledger(tmp_path / "elsewhere", [lines[0].replace('"outcome":"pass"', '"outcome":"fail"'), *lines[1:]])
    with pytest.raises(RecipeError) as caught:
        link_ledger(tmp_path, tampered, linked_at=LINKED_AT)
    assert caught.value.code == LINK_E_CHAIN
    assert not ledger_link_path(tmp_path).exists(), "a refused link must leave no marker behind"


def test_linking_a_relative_path_refuses(tmp_path: Path) -> None:
    with pytest.raises(RecipeError) as caught:
        link_ledger(tmp_path, Path("decisions.jsonl"), linked_at=LINKED_AT)
    assert caught.value.code == LINK_E_PATH


def test_a_rotated_ledger_at_the_linked_path_stops_qualifying(tmp_path: Path) -> None:
    """Extension is fine; a *different* chain at the same path is not.

    The other harness has a documented way to produce one — archive the contested ledger and start a
    fresh chain — and a fresh chain verifies perfectly, so the chain check alone cannot catch it. Its
    first entry is simply another entryId, and that is exactly what the pinned genesis is for.
    """
    linked = _write_ledger(tmp_path / "linked", FOREIGN_LEDGER.read_text(encoding="utf-8").splitlines())
    link_ledger(tmp_path, linked, linked_at=LINKED_AT)
    linked.unlink()
    from iterate_harness.iterate.kernel_decision_log import append_decision_log_entry, new_decision_entry_id

    append_decision_log_entry(
        str(linked),
        {
            "entryId": new_decision_entry_id(),
            "summary": "a fresh chain, same filename",
            "outcome": "pass",
            "createdAt": "2026-10-25T05:00:00.000Z",
            "operationId": OPERATION_ID,
        },
    )
    with pytest.raises(RecipeError) as caught:
        fix_proof(tmp_path, env={})
    assert caught.value.code == LINK_E_STALE
    assert GENESIS in caught.value.message, "the refusal has to name what was expected, not just that something changed"


def test_a_truncated_ledger_is_caught_by_the_chain_before_the_link_is_consulted(tmp_path: Path) -> None:
    """Order matters: dropping the first line breaks line 2's citation, so the chain answers first.

    Asserted rather than assumed because the two refusals send the operator to different places — a
    broken link means "this is not the ledger I vouched for", a broken chain means "the file someone
    did vouch for has been edited".
    """
    lines = FOREIGN_LEDGER.read_text(encoding="utf-8").splitlines()
    linked = _write_ledger(tmp_path / "linked", lines)
    link_ledger(tmp_path, linked, linked_at=LINKED_AT)
    linked.write_text("\n".join(lines[1:]) + "\n", encoding="utf-8")
    with pytest.raises(RecipeError) as caught:
        fix_proof(tmp_path, env={})
    assert caught.value.code == "RECIPE_GATE_E_LEDGER_UNVERIFIABLE"
    assert "line 1" in caught.value.message


def test_linking_an_empty_ledger_is_allowed_and_pins_nothing(tmp_path: Path) -> None:
    directory = tmp_path / "fresh"
    directory.mkdir()
    empty = directory / "decisions.jsonl"
    empty.write_text("", encoding="utf-8")
    document = link_ledger(tmp_path, empty, linked_at=LINKED_AT)
    assert document["genesisEntryId"] == ""
    assert read_ledger_link(tmp_path) == document
    with pytest.raises(RecipeError) as caught:
        fix_proof(tmp_path, env={})
    assert caught.value.code == "RECIPE_GATE_E_FIX_UNPROVEN", "an empty linked ledger proves nothing, and says so"


def test_a_marker_that_is_not_the_contract_shape_refuses_rather_than_redirecting_quietly(tmp_path: Path) -> None:
    """A half-written link is not the same fact as "no link was recorded".

    Reading a malformed marker as absence is the silent-downgrade shape: the operator believes the
    run cites the other harness's chain, while the code falls back to this project's own ledger and
    answers from a file nobody meant to vouch for. The refusal has to come from the reader, because
    the reader is the call site that has no idea the file was ever meant to be edited by hand.
    """
    ledger_link_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    ledger_link_path(tmp_path).write_text(json.dumps({"path": "/tmp/x"}), encoding="utf-8")
    with pytest.raises(RecipeError) as caught:
        resolve_ledger(tmp_path, env={})
    assert caught.value.code == LINK_E_SCHEMA
    assert LEDGER_LINK_SCHEMA_VERSION in caught.value.message
    with pytest.raises(RecipeError) as proof:
        fix_proof(tmp_path, env={})
    assert proof.value.code == LINK_E_SCHEMA, "the gate reads through the same resolver, so it refuses too"


def test_a_marker_that_is_not_json_refuses_with_the_command_that_rewrites_it(tmp_path: Path) -> None:
    ledger_link_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    ledger_link_path(tmp_path).write_text("linked ledger = /tmp/decisions.jsonl\n", encoding="utf-8")
    with pytest.raises(RecipeError) as caught:
        read_ledger_link(tmp_path)
    assert caught.value.code == LINK_E_SCHEMA
    assert "glasspane-ledger" in caught.value.remedy, "the remedy must be the one command that fixes it"


def test_an_absent_marker_is_the_only_quiet_answer(tmp_path: Path) -> None:
    """The other half of the same rule: no file is genuinely "no link", and must not raise."""
    assert read_ledger_link(tmp_path) is None
    _, source, _ = resolve_ledger(tmp_path, env={})
    assert source == "project"


def test_linking_a_file_that_is_not_there_refuses(tmp_path: Path) -> None:
    """`this command never makes a ledger, it only vouches for one` — pinned as behaviour.

    Without this the link would be created for a path that does not exist yet, and the first round
    that reads it would report an unreadable-evidence refusal instead of the operator's typo.
    """
    with pytest.raises(RecipeError) as caught:
        link_ledger(tmp_path, tmp_path / "not-written-yet.jsonl", linked_at=LINKED_AT)
    assert caught.value.code == LINK_E_READ


def test_a_link_the_filesystem_refuses_to_write_is_a_refusal_not_a_crash(tmp_path: Path) -> None:
    """The path is held by a directory, so ``os.open`` raises on the *create*, not the write.

    Both OSError exits are wrapped, and this drives the one outside the ``try`` that a previous
    version left bare — an unwrapped OSError would reach the gate as a traceback instead of the
    ``LEDGER_LINK_E_READ`` payload every other refusal carries.
    """
    ledger_link_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    ledger_link_path(tmp_path).mkdir()
    with pytest.raises(RecipeError) as caught:
        link_ledger(tmp_path, FOREIGN_LEDGER, linked_at=LINKED_AT)
    assert caught.value.code == LINK_E_READ
    assert caught.value.path == str(ledger_link_path(tmp_path))


# ------------------------------------------------------------------------------------ renderers


def test_the_renderers_name_the_source_and_the_counts(tmp_path: Path) -> None:
    _link(tmp_path)
    shown = render_ledger_link(tmp_path)
    assert "resolved from: link" in shown
    assert GENESIS in shown
    assert "3 entries" in shown and "1 of them a passing outcome carrying an operation id" in shown


def test_an_unlinked_project_is_told_the_one_command_that_changes_it(tmp_path: Path) -> None:
    shown = render_ledger_link(tmp_path)
    assert "resolved from: project" in shown
    assert "/iterate glasspane-ledger " in shown, "the remedy must be a command, not a description"


def test_the_gate_reader_says_absence_rather_than_a_pass(tmp_path: Path) -> None:
    assert "no recipe gate recorded yet" in render_gate_state(tmp_path)


# ------------------------------------------------------------- persistence, and the two commands


async def _failing_kernel(project: Path) -> DefensiveKernel:
    (project / "README.md").write_text("# real project\n", encoding="utf-8")
    (project / "iterate.config.yaml").write_text(
        "invariants:\n  ensure:\n    - README.md\n    - not-a-real-file.md\nreviewer:\n  evidence_validation: false\n",
        encoding="utf-8",
    )
    from iterate_harness.iterate.config_loader import load_effective_config

    kernel = DefensiveKernel(project, load_effective_config(project))
    await kernel.check_invariants()
    assert kernel.last_invariant_report is not None
    return kernel


def _drive_failing_round(project: Path) -> dict[str, Any]:
    """One real round through the aggregate's own trigger, against a stubbed read-only channel."""
    from iterate_harness.iterate.decision_log import append_entry, make_entry

    kernel = asyncio.run(_failing_kernel(project))
    append_entry(
        project,
        make_entry(entry_type="decision", round_number=1, data={"kind": "evidence", "operationId": OPERATION_ID}),
    )

    def transport(tool: str, arguments: dict[str, Any]) -> object:
        if tool == reverify_trigger.REGISTRY_READ_TOOL:
            return [json.dumps({"projects": [{"recipeConfigPath": str(FOREIGN_LEDGER.parent / "nope.json")}]})]
        return [json.dumps({"evidencePack": {"operationId": OPERATION_ID, "outcome": "pass"}})]

    payload = reverify_trigger.trigger_reverification(
        project_root=project,
        metadata={DEFENSIVE_KERNEL_KEY: kernel, reverify_trigger.GLASSPANE_TRANSPORT_KEY: transport},
        gates=measured_gates(evidence_validation=False, findings=[], cwd=project, metadata={DEFENSIVE_KERNEL_KEY: kernel}),
        round_number=1,
    )
    assert payload is not None
    return payload


def test_the_gate_is_recorded_in_the_same_ledger_line_as_the_send(tmp_path: Path) -> None:
    payload = _drive_failing_round(tmp_path)
    assert payload[RECIPE_GATE_DATA_KEY]["allowed"] is False
    entries = read_entries(tmp_path)
    recorded = [entry for entry in entries if isinstance(entry.data, dict) and RECIPE_GATE_DATA_KEY in entry.data]
    assert len(recorded) == 1, "one send, one line, and the line carries the gate it was sent under"
    assert recorded[0].data[RECIPE_GATE_DATA_KEY]["invariantState"] == "fail"


def test_the_gate_reader_prints_what_the_last_round_decided(tmp_path: Path) -> None:
    _drive_failing_round(tmp_path)
    shown = render_gate_state(tmp_path)
    assert "not allowed" in shown
    assert "RECIPE_GATE_E_INVARIANT_GATE" in shown, "the refusal code, not a paraphrase of it"
    assert "remedy:" in shown


@pytest.mark.asyncio
async def test_both_commands_answer_through_the_slash_handler(tmp_path: Path) -> None:
    context = CommandContext(engine=None, cwd=str(tmp_path))  # type: ignore[arg-type]
    unlinked = await iterate_command_handler("glasspane-ledger", context)
    assert "resolved from: project" in (unlinked.message or "")
    linked = await iterate_command_handler(f"glasspane-ledger {FOREIGN_LEDGER}", context)
    assert "linked the GlassPane ledger" in (linked.message or "")
    assert GENESIS in (linked.message or "")
    gate = await iterate_command_handler("gate", context)
    assert "no recipe gate recorded yet" in (gate.message or ""), "a project that never ran a round is absence, not a pass"


@pytest.mark.asyncio
async def test_a_link_command_that_cannot_verify_reports_the_code_and_changes_nothing(tmp_path: Path) -> None:
    context = CommandContext(engine=None, cwd=str(tmp_path))  # type: ignore[arg-type]
    broken = tmp_path / "broken.jsonl"
    lines = FOREIGN_LEDGER.read_text(encoding="utf-8").splitlines()
    broken.write_text("\n".join([lines[0].replace('"outcome":"pass"', '"outcome":"fail"'), *lines[1:]]) + "\n", encoding="utf-8")
    result = await iterate_command_handler(f"glasspane-ledger {broken}", context)
    assert LINK_E_CHAIN in (result.message or "")
    assert "remedy:" in (result.message or "")
    assert read_ledger_link(tmp_path) is None


def test_the_new_subcommands_are_in_the_usage_text(tmp_path: Path) -> None:
    """A command nobody can discover is a command nobody uses; the help text is its interface."""
    from iterate_harness.commands import iterate as module

    source = Path(str(module.__file__)).read_text(encoding="utf-8")
    assert "gate|glasspane-ledger" in source
    assert "recipe-gate answer" in source
    # And the terminal twin is named where the file's own convention puts it (see the `personalize`
    # line): an operator reading `/iterate`'s usage must be able to type what they just read.
    assert "ih iterate gate" in source
    assert "ih iterate glasspane-ledger" in source


# ---------------------------------------------------------------------- the same answer, two doors

def _slash(command: str, cwd: Path) -> str:
    context = CommandContext(engine=None, cwd=str(cwd))  # type: ignore[arg-type]
    return asyncio.run(iterate_command_handler(command, context)).message or ""


def _terminal(args: list[str], cwd: Path) -> Any:
    import os

    previous = os.getcwd()
    os.chdir(cwd)
    try:
        return CliRunner().invoke(cli_module.app, ["iterate", *args])
    finally:
        os.chdir(previous)


def _without_stamp(text: str) -> str:
    """The link's `linkedAt` is wall-clock, so two calls two milliseconds apart are not a wording drift.

    Everything else in the two surfaces' answers is compared character for character; the timestamp
    is the one field that is *supposed* to differ between two separate link operations.
    """
    return re.sub(r"\d{2}:\d{2}:\d{2}\.\d{3}Z", "<stamp>", text)


@pytest.mark.parametrize("command, args", [("gate", ["gate"]), ("glasspane-ledger", ["glasspane-ledger"])])
def test_the_terminal_surface_and_the_slash_surface_answer_the_same_thing(
    tmp_path: Path, command: str, args: list[str]
) -> None:
    """Two doors into one reader, pinned to the same words — including the absence sentence.

    The alternative (each surface calling the same module but formatting on its own) is how a
    command drifts: the operator's terminal and the agent's session start telling different stories
    about the same project, and nobody can tell which one a remedy refers to.
    """
    before = _terminal(args, tmp_path)
    assert before.exit_code == 0, before.output
    assert _without_stamp(before.output).strip() == _without_stamp(_slash(command, tmp_path)).strip()

    if command == "glasspane-ledger":
        linked = _terminal([*args, str(FOREIGN_LEDGER)], tmp_path)
        assert linked.exit_code == 0, linked.output
        assert _without_stamp(linked.output).strip() == _without_stamp(
            _slash(f"glasspane-ledger {FOREIGN_LEDGER}", tmp_path)
        ).strip()
        assert GENESIS in linked.output
    else:
        assert "no recipe gate recorded yet" in before.output


def test_a_refused_link_fails_the_shell_with_the_code_a_terminal_reader_needs(tmp_path: Path) -> None:
    """Exit 1 here, an answer there: the *decision* is surface-specific, the wording is not.

    A terminal reader groups failures by exit code; an agent reading the slash result has no exit
    code to look at. Both must still see the same `LEDGER_LINK_E_LEDGER_CHAIN` sentence, or the
    remedy printed in one place points at a file the other place never mentioned.
    """
    lines = FOREIGN_LEDGER.read_text(encoding="utf-8").splitlines()
    broken = tmp_path / "broken.jsonl"
    broken.write_text("\n".join([lines[0].replace('"outcome":"pass"', '"outcome":"fail"'), *lines[1:]]) + "\n", encoding="utf-8")

    refused = _terminal(["glasspane-ledger", str(broken)], tmp_path)
    assert refused.exit_code == 1, refused.output
    assert LINK_E_CHAIN in refused.output
    assert "remedy:" in refused.output
    assert refused.output.strip() == _slash(f"glasspane-ledger {broken}", tmp_path).strip()
    assert read_ledger_link(tmp_path) is None, "a refusal on either door still writes nothing"


def test_the_link_stamp_is_formatted_where_the_link_shape_lives() -> None:
    """One `linkedAt` format, defined by the module that owns the link's schema.

    This batch moved it out of `commands/iterate.py` (where it was written as `datetime.UTC`,
    3.11-only, and caught by `tests/test_py310_compat.py`) and the terminal command was about to
    copy it a second time. The guard is on the *count*: a second call site is invisible to a
    type check and to every test that only ever reads one of them.
    """
    from iterate_harness.commands import iterate as iterate_module
    from iterate_harness.iterate import glasspane_recipe as recipe_module

    recipe_source = Path(str(recipe_module.__file__)).read_text(encoding="utf-8")
    assert recipe_source.count('isoformat(timespec="milliseconds")') == 1
    for module in (cli_module, iterate_module):
        source = Path(str(module.__file__)).read_text(encoding="utf-8")
        assert 'isoformat(timespec="milliseconds")' not in source, f"{module.__name__} reformats the link stamp"


def test_the_refusal_sentence_has_one_definition_not_three() -> None:
    """`format_recipe_error` is the only place a refusal is spelled out.

    Asserted against the call sites rather than the helper, because the drift this prevents is a
    second `f"{error.code}: …"` appearing in a command module and quietly diverging in wording.
    """
    from iterate_harness.commands import iterate as iterate_module

    for module in (cli_module, iterate_module):
        source = Path(str(module.__file__)).read_text(encoding="utf-8")
        assert 'f"{error.code}: {error.message}' not in source, (
            f"{module.__name__} formats a refusal by hand instead of calling format_recipe_error"
        )
    recipe_source = Path(
        str(Path(__file__).resolve().parents[2] / "src" / "iterate_harness" / "iterate" / "glasspane_recipe.py")
    ).read_text(encoding="utf-8")
    assert "def format_recipe_error(" in recipe_source
    assert recipe_source.count('f"{error.code}: {error.message}') == 1


@pytest.mark.asyncio
async def test_the_stamp_the_link_command_writes_is_the_shape_every_ledger_line_carries(tmp_path: Path) -> None:
    """Checked where the stamp is chosen, because `link_ledger` takes it as a parameter.

    Asserting an inline format string would prove nothing about what the command stores; the value
    that lands in the file is what a later reader sees — and `fromisoformat` here is the 3.10-safe
    call, so this doubles as the proof that the stamp is parseable by the oldest runtime we claim.
    """
    context = CommandContext(engine=None, cwd=str(tmp_path))  # type: ignore[arg-type]
    result = await iterate_command_handler(f"glasspane-ledger {FOREIGN_LEDGER}", context)
    assert "linked" in (result.message or "").lower()
    stamp = read_ledger_link(tmp_path)["linkedAt"]
    assert stamp.endswith("Z") and "." in stamp, "millisecond ISO with a Z suffix, like every createdAt in this directory"
    parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None, "a naive stamp in a link would be read as local time by the next reader"
