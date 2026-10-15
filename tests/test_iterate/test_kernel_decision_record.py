"""The record→entry transcription, driven entirely by the kernel's fixture.

Scenario 3 (§8.5 / R40) claims the two ecosystems' decision logs become ONE shape. That claim
is only checkable if the Python side is coded from the same anchor the TypeScript side is:
``kernel/fixtures/decision-record-transcription.ok-01.json`` carries the rules in its
``comment`` and the expected values in its cases, and nothing here re-states a rule as a
literal — an expectation written into this file would only prove the test and the code agree
with each other.

The negative controls at the bottom are not in the fixture. They hold the two properties the
conversion is actually for: an absent optional field stays absent (a ``null`` would change the
canonical bytes the chain hashes), and a timestamp is not something the host gets to guess.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from iterate_harness.iterate.canonical_json import canonical_json
from iterate_harness.iterate.kernel_decision_record import (
    DECISION_TRANSCRIBE_CODES,
    KernelDecisionTranscribeError,
    decision_log_draft_from_record,
    is_legacy_decision_record,
)

CORPUS = Path(__file__).resolve().parents[1] / "kernel_fixtures"
DRAFT_FIELDS = (
    "entryId",
    "summary",
    "outcome",
    "createdAt",
    "kind",
    "round",
    "dimension",
    "operationId",
    "payload",
)


def _fixture() -> dict[str, Any]:
    path = CORPUS / "decision-record-transcription.ok-01.json"
    assert path.is_file(), (
        "decision-record-transcription.ok-01.json is missing from the vendored corpus; restore "
        "it from iterate-kernel's fixtures/ and re-pin it in manifest.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def _expand(case: dict[str, Any]) -> dict[str, Any]:
    """Rule 10 of the fixture: size cases stay readable by repeating a character here."""
    record = json.loads(json.dumps(case["record"]))
    if "blobRepeat" in case:
        record["data"][case.get("blobKey", "blob")] = "x" * case["blobRepeat"]
    if "summaryRepeat" in case:
        record["data"]["summary"] = "s" * case["summaryRepeat"]
    return record


def _cases() -> list[dict[str, Any]]:
    return _fixture()["cases"]


@pytest.mark.parametrize("case", [c for c in _cases() if "expect" in c], ids=lambda c: c["id"])
def test_a_record_transcribes_to_the_pinned_draft(case: dict[str, Any]) -> None:
    draft = decision_log_draft_from_record(_expand(case), case["entryId"])
    expected = case["expect"]
    for field, value in expected.items():
        assert draft[field] == value, f"{case['id']}: {field}"
    for field in DRAFT_FIELDS:
        if field not in expected:
            assert field not in draft, f"{case['id']}: {field} must be ABSENT, not null"


@pytest.mark.parametrize("case", [c for c in _cases() if "expectError" in c], ids=lambda c: c["id"])
def test_an_untranscribable_record_refuses_by_name(case: dict[str, Any]) -> None:
    with pytest.raises(KernelDecisionTranscribeError) as caught:
        decision_log_draft_from_record(_expand(case), case["entryId"])
    assert caught.value.code == case["expectError"]
    # A refusal the caller cannot act on is not a remedy, it is a stack trace with a name.
    assert caught.value.remedy


def test_the_refusal_codes_are_the_ones_the_documentation_names() -> None:
    used = {c["expectError"] for c in _cases() if "expectError" in c}
    assert used <= set(DECISION_TRANSCRIBE_CODES.values()), used - set(DECISION_TRANSCRIBE_CODES.values())
    assert DECISION_TRANSCRIBE_CODES["payload"] in used
    assert DECISION_TRANSCRIBE_CODES["summary"] in used
    assert DECISION_TRANSCRIBE_CODES["timestamp"] in used


def test_an_absent_optional_field_is_absent_not_null() -> None:
    """The chain hashes canonical bytes, so `null` and "not there" are different ledgers."""
    draft = decision_log_draft_from_record(
        {"timestamp": "2026-10-11T02:28:11.000Z", "type": "decision", "data": {}},
        "dl_0123456789ABCDEFGHJKMNPQRT",
    )
    assert "round" not in draft and "dimension" not in draft and "operationId" not in draft
    assert None not in draft.values()
    assert "null" not in canonical_json(draft)


def test_a_timestamp_the_host_would_have_to_guess_is_refused() -> None:
    """`11/10/2026 02:28` is a real V8 success and a real Python refusal — see rule 4."""
    for raw in ("11/10/2026 02:28", "2026-10-11 02:28:11", "2026-10-11T02:28:11", "2026-10-11"):
        with pytest.raises(KernelDecisionTranscribeError) as caught:
            decision_log_draft_from_record(
                {"timestamp": raw, "round": 1, "type": "decision", "data": {}},
                "dl_0123456789ABCDEFGHJKMNPQRT",
            )
        assert caught.value.code == DECISION_TRANSCRIBE_CODES["timestamp"], raw


def test_offsets_and_micro_seconds_normalize_without_rounding_up() -> None:
    draft = decision_log_draft_from_record(
        {"timestamp": "2026-10-11T10:28:11.9999+08:00", "round": 1, "type": "report", "data": {}},
        "dl_0123456789ABCDEFGHJKMNPQRT",
    )
    # 02:28:11.999 UTC: the 4th digit truncates toward zero rather than rounding to .000+.
    assert draft["createdAt"] == "2026-10-11T02:28:11.999Z"


def test_is_legacy_decision_record_separates_the_two_shapes() -> None:
    assert is_legacy_decision_record({"timestamp": "x", "round": 1, "type": "decision", "data": {}})
    assert is_legacy_decision_record({"timestamp": "x", "type": "decision"})
    assert not is_legacy_decision_record(
        {"entryId": "dl_0", "sequence": 0, "prevEntryHash": "", "summary": "s", "outcome": "pass", "createdAt": "x"}
    )
    assert not is_legacy_decision_record({"timestamp": "x", "type": "decision", "author": "agent"})
    assert not is_legacy_decision_record([])
    assert not is_legacy_decision_record("timestamp=1")

