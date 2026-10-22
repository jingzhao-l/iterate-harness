"""Tests for iterate_harness.iterate.decision_log (port of the decision-log tool core)."""

from __future__ import annotations

import hashlib
import json
import stat
from datetime import datetime

import pytest

from iterate_harness.iterate.decision_log import (
    KNOWN_ENTRY_TYPES,
    LOG_DIR,
    LOG_FILE,
    DecisionLogWriteError,
    append_entry,
    archive_contested_log,
    is_decision_kind,
    log_path,
    make_entry,
    read_entries,
)
from iterate_harness.iterate.kernel_decision_log import read_decision_log
from iterate_harness.iterate.types import DecisionLogEntry


def entry(**kwargs: object) -> DecisionLogEntry:
    base: dict[str, object] = {
        "timestamp": "2026-08-14T00:00:00+00:00",
        "round": 1,
        "type": "round_start",
        "data": {},
    }
    base.update(kwargs)
    return DecisionLogEntry(**base)  # type: ignore[arg-type]


class TestAppendAndRead:
    def test_creates_log_under_iterate_dir_and_appends(self, tmp_path):
        count1, path = append_entry(tmp_path, entry())
        assert path == tmp_path / LOG_DIR / LOG_FILE
        assert count1 == 1
        count2, _ = append_entry(tmp_path, entry(round=2, type="validation"))
        assert count2 == 2

    def test_log_path_creates_directory(self, tmp_path):
        target = tmp_path / LOG_DIR
        assert not target.exists()
        path = log_path(tmp_path)
        assert path.parent is target or path.parent == target
        assert target.exists()
        # The ledger refuses a group/other-writable parent, and `mkdir(mode=…)` is masked by
        # the process umask — so the mode this function leaves behind is what the check sees.
        assert stat.S_IMODE(target.stat().st_mode) == 0o700

    def test_read_returns_entries_in_order_with_fields(self, tmp_path):
        append_entry(tmp_path, entry(data={"phase": "start"}))
        append_entry(tmp_path, entry(round=2, type="atomic_fix", data={"file": "a.py"}))
        entries = read_entries(tmp_path)
        assert len(entries) == 2
        assert entries[0].type == "round_start"
        assert entries[0].data == {"phase": "start"}
        assert entries[1].round == 2
        assert entries[1].data == {"file": "a.py"}

    def test_read_returns_empty_when_log_missing(self, tmp_path):
        assert read_entries(tmp_path) == []

    def test_each_line_is_a_canonical_verified_chain_entry(self, tmp_path):
        """The flip: what reaches the disk is the kernel's entry, not the loop's record."""
        append_entry(tmp_path, entry(data={"k": "v"}))
        raw = (tmp_path / LOG_DIR / LOG_FILE).read_text(encoding="utf-8")
        lines = [line for line in raw.split("\n") if line.strip()]
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["kind"] == "round_start"
        assert parsed["sequence"] == 0
        assert parsed["prevEntryHash"] == ""
        assert parsed["payload"] == {"k": "v"}
        assert parsed["outcome"] == "inconclusive", "the record says nothing, so the ledger must not say more"
        assert "timestamp" not in parsed and "data" not in parsed, "the legacy shape must not survive on disk"
        # Optional fields are absent, never null: a null would change the bytes every later
        # line hashes over.
        assert "operationId" not in parsed and "dimension" not in parsed

    def test_the_chain_on_disk_verifies_and_a_foreign_hash_recomputes_it(self, tmp_path):
        """Recompute the link with ``hashlib`` from the stored bytes, not with this module."""
        append_entry(tmp_path, entry(data={"phase": "start"}))
        append_entry(tmp_path, entry(round=2, type="decision", data={"outcome": "pass"}))
        path = tmp_path / LOG_DIR / LOG_FILE
        lines = [line for line in path.read_text(encoding="utf-8").split("\n") if line.strip()]
        assert len(lines) == 2
        parsed = [json.loads(line) for line in lines]
        assert parsed[1]["prevEntryHash"] == hashlib.sha256(lines[0].encode("utf-8")).hexdigest()
        verified = read_decision_log(str(path))
        assert verified.ok, verified.reason
        assert [entry.sequence for entry in verified.entries] == [0, 1]

    def test_a_legacy_history_is_migrated_into_the_chain_on_the_next_write(self, tmp_path):
        log_dir = tmp_path / LOG_DIR
        log_dir.mkdir()
        history = [
            {"timestamp": "2026-08-13T00:00:00+00:00", "round": 1, "type": "round_start", "data": {"phase": "start"}},
            {"timestamp": "2026-08-13T00:05:00+00:00", "round": 1, "type": "review_result", "data": {"findings": []}},
        ]
        original = "".join(json.dumps(record) + "\n" for record in history)
        log_file = log_dir / LOG_FILE
        log_file.write_text(original, encoding="utf-8")

        count, _ = append_entry(tmp_path, entry(round=2, type="validation", data={"passed": True}))
        assert count == 3

        lines = [line for line in log_file.read_text(encoding="utf-8").split("\n") if line.strip()]
        assert len(lines) == 3
        verified = read_decision_log(str(log_file))
        assert verified.ok, verified.reason
        assert [entry.kind for entry in verified.entries] == ["round_start", "review_result", "validation"]

        # The bytes that were there before the flip are kept, not discarded — and they are
        # byte-identical to what was on disk.
        backups = sorted(p.name for p in log_dir.glob("*.pre-ledger-*.jsonl.bak"))
        assert len(backups) == 1, f"expected exactly one backup, got {backups}"
        assert (log_dir / backups[0]).read_text(encoding="utf-8") == original

    def test_migrated_entry_ids_carry_the_record_instant_not_the_migration_clock(self, tmp_path):
        """The id prefix must decode to *when the decision happened*.

        Minting it from ``time.now()`` during migration would sort a 2026-08 decision after a
        later one, and the chain would still verify — so verification alone cannot catch it.
        """
        log_dir = tmp_path / LOG_DIR
        log_dir.mkdir()
        (log_dir / LOG_FILE).write_text(
            json.dumps({"timestamp": "2026-08-13T00:00:00+00:00", "round": 1, "type": "decision", "data": {}})
            + "\n"
            + json.dumps({"timestamp": "2026-08-13T00:00:01+00:00", "round": 2, "type": "report", "data": {}})
            + "\n",
            encoding="utf-8",
        )
        append_entry(tmp_path, entry(round=3, type="validation"))
        lines = [json.loads(line) for line in (log_dir / LOG_FILE).read_text(encoding="utf-8").splitlines() if line.strip()]
        assert [line["kind"] for line in lines] == ["decision", "report", "validation"]

        crockford = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

        def id_millis(entry_id: str) -> int:
            value = 0
            for char in entry_id[3:13]:
                value = value * 32 + crockford.index(char)
            return value

        stamps = [id_millis(line["entryId"]) for line in lines]
        assert stamps == sorted(stamps), "the chain must stay time-ordered by the records' own instants"
        first = datetime.fromisoformat(lines[0]["createdAt"].replace("Z", "+00:00"))
        assert abs(stamps[0] - first.timestamp() * 1000) < 2, f"id says {stamps[0]}, createdAt says {first}"
        assert read_decision_log(str(log_dir / LOG_FILE)).ok

    def test_a_legacy_line_after_the_chain_began_is_refused_and_changes_nothing(self, tmp_path):
        append_entry(tmp_path, entry(round=1, type="round_start"))
        log_file = tmp_path / LOG_DIR / LOG_FILE
        before = log_file.read_bytes()
        # This is the shape the TypeScript plugin still produces: a legacy record appended to a
        # log that has already become a chain. Renumbering it would forge continuity.
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "timestamp": "2026-08-14T00:00:01+00:00", "round": 2, "type": "report", "data": {},
            }) + "\n")
        tampered = log_file.read_bytes()

        with pytest.raises(DecisionLogWriteError) as raised:
            append_entry(tmp_path, entry(round=3, type="validation"))
        assert raised.value.code == "ITERATE_E_DECISION_LOG_INTERLEAVED"
        assert "archive_contested_log" in raised.value.remedy, "the refusal must name the operator's action"
        assert log_file.read_bytes() == tampered, "the refusal must not rewrite a single byte"
        assert before != tampered  # the guard fires on the state the other writer produced

        # The named action: archive losslessly, then the chain restarts at sequence 0.
        archive = archive_contested_log(tmp_path)
        assert archive.read_bytes() == tampered
        assert stat.S_IMODE(archive.stat().st_mode) == 0o600
        assert not log_file.exists()
        count, _ = append_entry(tmp_path, entry(round=3, type="validation"))
        assert count == 1
        assert json.loads(log_file.read_text(encoding="utf-8").splitlines()[0])["sequence"] == 0

    def test_archiving_a_healthy_ledger_is_refused(self, tmp_path):
        """Otherwise this is one careless call away from erasing a chain."""
        append_entry(tmp_path, entry(round=1, type="round_start"))
        append_entry(tmp_path, entry(round=2, type="validation"))
        with pytest.raises(DecisionLogWriteError) as raised:
            archive_contested_log(tmp_path)
        assert raised.value.code == "ITERATE_E_DECISION_LOG_INTERLEAVED"
        assert (tmp_path / LOG_DIR / LOG_FILE).exists()

    def test_archiving_an_all_legacy_log_is_refused_too(self, tmp_path):
        """A legacy log has a supported path that keeps its history in the chain: migration."""
        log_dir = tmp_path / LOG_DIR
        log_dir.mkdir()
        (log_dir / LOG_FILE).write_text(
            json.dumps({"timestamp": "2026-08-13T00:00:00+00:00", "round": 1, "type": "decision", "data": {}}) + "\n",
            encoding="utf-8",
        )
        with pytest.raises(DecisionLogWriteError):
            archive_contested_log(tmp_path)
        assert (log_dir / LOG_FILE).exists()


class TestCorruptionHandling:
    def test_malformed_lines_are_skipped_not_fatal(self, tmp_path):
        log_dir = tmp_path / LOG_DIR
        log_dir.mkdir()
        (log_dir / LOG_FILE).write_text(
            "not-json\n"
            + json.dumps({"timestamp": "t", "round": 1, "type": "decision", "data": {}})
            + "\n\n",
            encoding="utf-8",
        )
        entries = read_entries(tmp_path)
        assert len(entries) == 1
        assert entries[0].type == "decision"

    def test_non_dict_lines_are_skipped(self, tmp_path):
        log_dir = tmp_path / LOG_DIR
        log_dir.mkdir()
        (log_dir / LOG_FILE).write_text('["a","list"]\n', encoding="utf-8")
        assert read_entries(tmp_path) == []

    def test_non_numeric_round_is_skipped_not_fatal(self, tmp_path):
        log_dir = tmp_path / LOG_DIR
        log_dir.mkdir()
        (log_dir / LOG_FILE).write_text(
            json.dumps({"timestamp": "t", "round": "abc", "type": "decision", "data": {}})
            + "\n"
            + json.dumps({"timestamp": "t", "round": 2, "type": "validation", "data": {}})
            + "\n",
            encoding="utf-8",
        )
        entries = read_entries(tmp_path)
        assert len(entries) == 1
        assert entries[0].round == 2

    def test_non_mapping_data_is_skipped_not_fatal(self, tmp_path):
        log_dir = tmp_path / LOG_DIR
        log_dir.mkdir()
        (log_dir / LOG_FILE).write_text(
            json.dumps({"timestamp": "t", "round": 1, "type": "decision", "data": ["a", "b"]})
            + "\n"
            + json.dumps({"timestamp": "t", "round": 1, "type": "report", "data": {}})
            + "\n",
            encoding="utf-8",
        )
        entries = read_entries(tmp_path)
        assert len(entries) == 1
        assert entries[0].type == "report"

    def test_a_history_the_kernel_cannot_read_refuses_the_append_and_is_left_intact(self, tmp_path):
        """Replaced behaviour, stated: the loop used to append over an unreadable line.

        Silently continuing is what makes a ledger's gaps invisible, so the refusal is the
        product — the file must still be exactly what it was, byte for byte.
        """
        log_dir = tmp_path / LOG_DIR
        log_dir.mkdir()
        log_file = log_dir / LOG_FILE
        original = json.dumps({"timestamp": "t", "round": "bad", "type": "decision", "data": {}}) + "\n"
        log_file.write_text(original, encoding="utf-8")

        with pytest.raises(DecisionLogWriteError) as raised:
            append_entry(tmp_path, entry(round=2, type="validation"))
        assert raised.value.code == "ITERATE_E_DECISION_LOG_MIGRATION"
        assert "cannot be entered into the ledger" in str(raised.value)
        assert log_file.read_text(encoding="utf-8") == original
        assert not list(log_dir.glob("*.bak")), "a refused migration must not leave a backup behind"
        # Reading keeps its pre-flip tolerance: this line has a round that cannot be an int, so
        # the report path drops it instead of raising — the audit refusal above is a different
        # question, and a report must not be silenced by it.
        assert read_entries(tmp_path) == []
        assert read_entries(tmp_path / "does-not-exist") == []


class TestDecisionKinds:
    """The vocabulary is the kernel's shape rule, not either shell's list."""

    def test_known_types_are_documentation_not_a_gate(self):
        assert KNOWN_ENTRY_TYPES == {
            "round_start", "review_result", "atomic_fix", "architectural_fix",
            "revert", "validation", "decision", "report",
        }
        # The divergence this batch ends: the plugin's two kinds the Python list rejected.
        assert {"round_failed", "resume"} - KNOWN_ENTRY_TYPES == {"round_failed", "resume"}
        assert all(is_decision_kind(kind) for kind in ("round_failed", "resume"))

    def test_a_kind_the_kernel_cannot_carry_is_refused_before_any_write(self, tmp_path):
        for bad in ("Round_Failed", "with space", "x" * 33, "", None, 7):
            with pytest.raises(DecisionLogWriteError) as raised:
                append_entry(tmp_path, entry(type=bad))  # type: ignore[arg-type]
            assert raised.value.code == "KERNEL_E_DECISION_KIND"
        assert not (tmp_path / LOG_DIR).exists(), "a refusal must not leave a state directory behind"

    def test_a_new_kind_reaches_the_ledger_without_touching_the_whitelist(self, tmp_path):
        """No kernel release is needed to record a decision the loop learned yesterday."""
        append_entry(tmp_path, entry(round=4, type="round_failed", data={"why": "timeout"}))
        entries = read_entries(tmp_path)
        assert entries[0].type == "round_failed"
        assert entries[0].round == 4
        assert entries[0].data == {"why": "timeout"}

class TestMakeEntry:
    def test_builds_timestamped_utc_entry(self):
        made = make_entry(entry_type="revert", round_number=3, data={"reason": "test fail"})
        assert made.type == "revert"
        assert made.round == 3
        assert made.data == {"reason": "test fail"}
        assert "T" in made.timestamp  # ISO-8601
        assert made.timestamp.endswith("+00:00")  # UTC

    def test_findings_from_report_handles_all_legacy_shapes(self):
        from iterate_harness.iterate.decision_log import findings_from_report

        canonical = {"findings": [{"dimension": "security", "file": "a.py", "summary": "x"}]}
        assert len(findings_from_report(canonical)) == 1

        trimmed = {"topFindings": [{"dimension": "correctness", "file": "b.py", "summary": "y"}]}
        assert len(findings_from_report(trimmed)) == 1

        notable = {"notableFindings": [{"dimension": "architecture", "file": "c.py", "summary": "z"}]}
        assert len(findings_from_report(notable)) == 1

        nested = {"summary": {"findings": [{"dimension": "performance", "file": "d.py", "summary": "w"}]}}
        assert len(findings_from_report(nested)) == 1

        # Precedence: the fully-qualified ``findings`` wins over a trimmer slice.
        mixed = {"findings": [{"dimension": "a", "file": "n", "summary": "1"}], "topFindings": [{"dimension": "b", "file": "m", "summary": "2"}]}
        assert findings_from_report(mixed)[0]["dimension"] == "a"

        # Non-dict payloads and shapes without any carrier yield [].
        assert findings_from_report(None) == []
        assert findings_from_report({"verdict": "converged"}) == []
        assert findings_from_report({"topFindings": ["not-a-dict"]}) == []
