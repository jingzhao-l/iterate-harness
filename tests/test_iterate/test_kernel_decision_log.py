"""The decision-log hash chain, in Python, pinned to the kernel's own anchor.

The anchor (``decision-log-chain.ok-01.json``) is shared: the kernel test recomputes its hashes
with ``openssl``/``shasum`` and this suite recomputes them with ``hashlib``, both from the stored
bytes. If the two languages disagreed on canonical JSON or the hash rule, the recomputed values
would not match the pinned ones — which is the whole point of a cross-implementation anchor
rather than a fixture generated from the code under test.

Everything the ledger refuses (broken link, non-canonical storage, a forged chain field, a
group-writable directory) is asserted here too, because a ledger that appends onto a broken tail
is forging its own continuity.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from iterate_harness.iterate.canonical_json import canonical_json
from iterate_harness.iterate.kernel_decision_log import (
    DECISION_LOG_CODES,
    KernelDecisionLogError,
    append_decision_log_entry,
    build_decision_log_entry,
    decision_log_entry_hash,
    decision_log_head,
    new_decision_entry_id,
    read_decision_log,
    serialize_decision_log_entry,
    verify_decision_log_text,
)

CORPUS = Path(__file__).resolve().parents[1] / "kernel_fixtures"


def _chain() -> dict[str, Any]:
    path = CORPUS / "decision-log-chain.ok-01.json"
    assert path.is_file(), "decision-log-chain.ok-01.json missing from the vendored corpus"
    return json.loads(path.read_text(encoding="utf-8"))


def _ledger_dir(tmp_path: Path) -> Path:
    d = tmp_path / "ledger"
    d.mkdir(mode=0o700)
    return d


def _draft(entry_id: str, outcome: str = "pass", **extra: object) -> dict[str, object]:
    base: dict[str, object] = {
        "entryId": entry_id,
        "summary": "attach matched the window the action targeted",
        "outcome": outcome,
        "createdAt": "2026-09-25T04:00:00.000Z",
    }
    base.update(extra)
    return base


def test_python_hashlib_reproduces_every_pinned_chain_hash() -> None:
    fx = _chain()
    for i, line in enumerate(fx["lines"]):
        obj = json.loads(line)
        assert canonical_json(obj) == line, f"line {i}: Python canonicaliser drifted from the anchor"
        assert hashlib.sha256(line.encode("utf-8")).hexdigest() == fx["hashes"][i], f"line {i} hash"


def test_the_pinned_ledger_verifies_and_links() -> None:
    fx = _chain()
    text = "\n".join(fx["lines"]) + "\n"
    result = verify_decision_log_text(text)
    assert result.ok, result.reason
    assert [e.sequence for e in result.entries] == [0, 1, 2]
    # genesis prev is empty; each later line prev equals the sha of the line before it.
    assert result.entries[0].prev_entry_hash == ""
    assert result.entries[1].prev_entry_hash == fx["hashes"][0]


def test_a_one_byte_edit_to_the_first_line_breaks_the_link_at_the_second() -> None:
    fx = _chain()
    tampered = list(fx["lines"])
    tampered[0] = tampered[0].replace('"pass"', '"fail"')
    result = verify_decision_log_text("\n".join(tampered) + "\n")
    assert not result.ok
    assert result.broken_kind == "chain"
    assert result.first_broken_line == 2


def test_a_non_canonical_storage_form_is_caught_not_rehashed() -> None:
    fx = _chain()
    spaced = fx["lines"][0].replace('","', '", "')  # valid JSON, not canonical bytes
    result = verify_decision_log_text(spaced + "\n")
    assert not result.ok and result.broken_kind == "corrupt"
    assert "canonical" in (result.reason or "")


def test_append_builds_a_chain_whose_links_the_reader_recomputes(tmp_path: Path) -> None:
    path = str(_ledger_dir(tmp_path) / "decisions.jsonl")
    first = append_decision_log_entry(path, _draft(new_decision_entry_id()))
    second = append_decision_log_entry(path, _draft(new_decision_entry_id(), "fail"))
    assert (first.sequence, second.sequence) == (0, 1)
    assert second.prev_entry_hash == decision_log_entry_hash(first)

    verified = read_decision_log(path)
    assert verified.ok, verified.reason
    assert len(verified.entries) == 2

    # Stored bytes equal the canonical form; hash of line 0 recomputes line 1's prev.
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    assert lines[1].startswith('{"createdAt"')  # keys sorted, so createdAt leads
    assert hashlib.sha256(lines[0].encode("utf-8")).hexdigest() == json.loads(lines[1])["prevEntryHash"]


def test_the_file_is_0600_in_a_directory_that_is_not_group_writable(tmp_path: Path) -> None:
    d = _ledger_dir(tmp_path)
    path = str(d / "decisions.jsonl")
    append_decision_log_entry(path, _draft(new_decision_entry_id()))
    assert os.stat(path).st_mode & 0o777 == 0o600


def test_a_group_writable_directory_is_refused(tmp_path: Path) -> None:
    d = tmp_path / "loose"
    d.mkdir()
    # mkdir(mode=...) is masked by umask, so the guard would never see a loose directory.
    os.chmod(d, 0o777)
    path = str(d / "decisions.jsonl")
    with pytest.raises(KernelDecisionLogError) as caught:
        append_decision_log_entry(path, _draft(new_decision_entry_id()))
    assert caught.value.code == DECISION_LOG_CODES["location"]


def test_a_relative_path_is_refused(tmp_path: Path) -> None:
    with pytest.raises(KernelDecisionLogError) as caught:
        read_decision_log("relative/ledger.jsonl")
    assert caught.value.code == DECISION_LOG_CODES["path"]


def test_a_caller_cannot_send_chain_fields(tmp_path: Path) -> None:
    path = str(_ledger_dir(tmp_path) / "decisions.jsonl")
    with pytest.raises(KernelDecisionLogError) as caught:
        append_decision_log_entry(path, {"entryId": new_decision_entry_id(), "summary": "s", "outcome": "pass",
                                          "createdAt": "2026-09-25T04:00:00.000Z", "sequence": 0})
    assert caught.value.code == DECISION_LOG_CODES["draft"]
    assert not Path(path).exists(), "a refused draft must not touch disk"


def test_extended_fields_survive_the_round_trip_and_absent_means_absent(tmp_path: Path) -> None:
    path = str(_ledger_dir(tmp_path) / "decisions.jsonl")
    entry = append_decision_log_entry(path, _draft(
        new_decision_entry_id(),
        kind="review_result", round=3, dimension="ui-consistency", payload={"blocking": 1},
    ))
    assert entry.kind == "review_result" and entry.round_number == 3
    line = Path(path).read_text(encoding="utf-8").rstrip("\n")
    assert '"kind":"review_result"' in line and '"payload":{"blocking":1}' in line
    assert "null" not in line, "an absent optional field is spelled by omission, never by null"
    assert "operationId" not in json.loads(line)


def test_build_decision_log_entry_matches_the_ledger_head(tmp_path: Path) -> None:
    path = str(_ledger_dir(tmp_path) / "decisions.jsonl")
    append_decision_log_entry(path, _draft(new_decision_entry_id()))
    sequence, prev = decision_log_head(path)
    entry = build_decision_log_entry(_draft(new_decision_entry_id(), "fail"), sequence, prev)
    assert entry.sequence == 1 and entry.prev_entry_hash == prev
    assert serialize_decision_log_entry(entry) == canonical_json(entry.to_json_obj())


def test_entry_id_is_crockford_ulid_and_time_sortable() -> None:
    early = new_decision_entry_id(0)
    late = new_decision_entry_id(10**12)
    assert early.startswith("dl_") and len(early) == 29
    for ch in early[3:]:
        assert ch in "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    assert early[3:13] < late[3:13], "the 10-char time prefix must sort by ms"
