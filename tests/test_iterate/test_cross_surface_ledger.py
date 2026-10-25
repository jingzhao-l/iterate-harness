"""③ made mechanical: each harness verifies the chain the *other* one wrote.

The claim in 综述 §8.5 row 3 is "opID ↔ decision 条目单向哈希链互通". Before this file the
honest state was weaker than the claim: both sides wrote entries that satisfy one contract
(pinned by ``kernel/fixtures/decision-log-chain.ok-01.json``), but **no code on either side ever
read the other's ledger** — two files in the same format, verified separately, is not a shared
chain. The gate this harness added for scenario ④ (``glasspane_recipe.fix_proof``) is the first
consumer that reads a foreign ledger, so the pairing needed bytes that really came from the other
implementation rather than a hand-written stand-in.

Provenance of the fixture, measured 2026-10-25 on this machine, not copied from a document:

* ``tests/fixtures/glasspane_chain_ts_written.jsonl`` — three entries appended through the
  published package's ``appendDecisionLogEntry`` (``iterate-kernel/decision-log``, 0.1.10), run
  with ``bun`` from ``harness/glasspane-harness/packages/opencode`` over drafts with fixed
  ``entryId``/``createdAt`` so the bytes are reproducible.
* The same three drafts appended through ``kernel_decision_log.append_decision_log_entry`` here
  produced **byte-identical** output (sha256 ``eaa9dc7dc9fabb076ca9a9cdecec10f94f50566351402cc6abb9d32f01c4dc06``),
  so the two writers agree on canonical form, key order and every chain hash — the claim the
  corpus makes at fixture level, now demonstrated on a three-line file with a ``payload``,
  a ``round`` and an entry that carries no ``operationId`` at all.

The tests below keep both halves of that: read the other side's bytes, and refuse a tampered
version of them. A reader that only ever accepts good files is not a verifier.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from iterate_harness.iterate.glasspane_recipe import (
    FIX_LEDGER_ENV_VAR,
    GATE_E_LEDGER,
    RecipeError,
    fix_proof,
)
from iterate_harness.iterate.kernel_decision_log import read_decision_log

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "glasspane_chain_ts_written.jsonl"

#: The bytes the TypeScript writer produced, pinned so a silent re-generation is a finding here
#: rather than a file that quietly stops being the other implementation's output.
FIXTURE_SHA = "eaa9dc7dc9fabb076ca9a9cdecec10f94f50566351402cc6abb9d32f01c4dc06"


def _text() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def test_the_fixture_is_the_bytes_the_typescript_writer_produced() -> None:
    assert hashlib.sha256(_text().encode("utf-8")).hexdigest() == FIXTURE_SHA


def test_python_verifies_a_chain_written_by_the_typescript_kernel() -> None:
    verified = read_decision_log(str(FIXTURE))
    assert verified.ok, verified.reason
    assert len(verified.entries) == 3
    # Optional fields are absent, not null — the stored bytes are what gets hashed, and a
    # normaliser that wrote nulls would re-hash every line differently from the TS writer.
    lines = _text().splitlines()
    assert '"operationId":null' not in lines[2]
    assert json.loads(lines[2])["outcome"] == "blocked"


def test_a_fix_cited_from_the_other_harnesss_ledger_is_accepted(tmp_path: Path) -> None:
    """``fix_proof`` reading a foreign chain is the whole point of pinning this file."""
    proof = fix_proof(tmp_path, env={FIX_LEDGER_ENV_VAR: str(FIXTURE)})
    assert proof.ledger_source == "handed"
    assert proof.operation_id == "op_" + "C" * 26
    assert proof.outcome == "pass"
    assert proof.entry_id == "dl_" + "A" * 26


def test_the_newest_proving_line_in_a_foreign_chain_wins(tmp_path: Path) -> None:
    # Line 1 is ``fail`` and line 2 carries no operation id, so the only licit citation is
    # line 0. Appending a passing entry would move this — the order is newest-first, and the
    # assertion below is what notices if that ever stops being true.
    proof = fix_proof(tmp_path, env={FIX_LEDGER_ENV_VAR: str(FIXTURE)})
    assert proof.entry_id == "dl_" + "A" * 26
    assert len(_text().splitlines()) == 3


def test_a_tampered_line_in_the_foreign_chain_is_refused_by_name(tmp_path: Path) -> None:
    # The tampered copy goes under ``tmp_path``, never next to the fixture: a test that writes
    # into ``tests/fixtures/`` leaves an untracked file in the repository after a green run, and
    # the next person's ``git status`` becomes the only thing that notices.
    broken = tmp_path / "tampered_ts_chain.jsonl"
    lines = _text().splitlines()
    # Edit the *first* line: its hash is what line 1 cites, so the break surfaces at line 2.
    broken.write_text("\n".join([lines[0].replace('"outcome":"pass"', '"outcome":"fail"'), lines[1], lines[2]]) + "\n", encoding="utf-8")
    with pytest.raises(RecipeError) as caught:
        fix_proof(tmp_path, env={FIX_LEDGER_ENV_VAR: str(broken)})
    assert caught.value.code == GATE_E_LEDGER
    assert "line 2" in caught.value.message
