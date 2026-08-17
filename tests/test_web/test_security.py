"""Tests for the WebUI security primitives (design §17.4).

Covers the loopback origin allow-list, path whitelisting (traversal
rejection), credential redaction, and the append-only audit journal.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from iterate_harness.web.security import (
    AUDIT_DIR,
    AUDIT_FILE,
    AuditLog,
    is_loopback,
    is_loopback_origin,
    read_audit_entries,
    redact_mapping,
    redact_secret,
    resolve_within,
)


class TestLoopback:
    def test_accepts_loopback_names(self):
        for host in ("127.0.0.1", "localhost", "::1"):
            assert is_loopback(host)

    def test_accepts_ipv4_loopback_range(self):
        assert is_loopback("127.0.0.0")
        assert is_loopback("127.255.255.254")

    def test_rejects_public_hosts(self):
        assert not is_loopback("192.168.1.10")
        assert not is_loopback("8.8.8.8")
        assert not is_loopback("example.com")

    def test_rejects_empty(self):
        assert not is_loopback("")
        assert not is_loopback(None)  # type: ignore[arg-type]


class TestLoopbackOrigin:
    def test_none_is_allowed(self):
        assert is_loopback_origin(None)

    def test_null_origin_is_allowed(self):
        assert is_loopback_origin("null")

    def test_loopback_with_ports(self):
        assert is_loopback_origin("http://127.0.0.1:8787")
        assert is_loopback_origin("https://localhost:8443")
        assert is_loopback_origin("http://[::1]:5173")

    def test_rejects_remote_origin(self):
        assert not is_loopback_origin("http://evil.example.com")
        assert not is_loopback_origin("http://192.168.1.5")

    def test_rejects_bad_schemes_and_bare_strings(self):
        assert not is_loopback_origin("file:///etc/passwd")
        assert not is_loopback_origin("127.0.0.1")  # scheme-less
        assert not is_loopback_origin("")

    def test_rejects_non_string(self):
        assert not is_loopback_origin(12345)  # type: ignore[arg-type]


class TestResolveWithin:
    def test_accepts_nested_file(self, tmp_path: Path):
        (tmp_path / "sub").mkdir()
        resolved = resolve_within(tmp_path, "sub", "report.html")
        assert resolved == (tmp_path / "sub" / "report.html").resolve()
        assert tmp_path.resolve() in resolved.parents

    def test_accepts_plain_name(self, tmp_path: Path):
        resolved = resolve_within(tmp_path, "report.html")
        assert resolved == (tmp_path / "report.html").resolve()

    def test_rejects_dotdot_traversal(self, tmp_path: Path):
        with pytest.raises(ValueError):
            resolve_within(tmp_path, "../secret.txt")

    def test_rejects_deep_traversal(self, tmp_path: Path):
        with pytest.raises(ValueError):
            resolve_within(tmp_path, "a/../../secret.txt")

    def test_rejects_absolute_path(self, tmp_path: Path):
        with pytest.raises(ValueError):
            resolve_within(tmp_path, str(tmp_path.parent / "outside.txt"))

    def test_rejects_symlink_escaping(self, tmp_path: Path):
        outside = tmp_path.parent / "outside-link-target.txt"
        outside.write_text("x", encoding="utf-8")
        link = tmp_path / "link.txt"
        link.symlink_to(outside)
        with pytest.raises(ValueError):
            resolve_within(tmp_path, "link.txt")


class TestRedaction:
    def test_redacts_credential_keys(self):
        assert redact_secret("api_key", "sk-abc123").startswith("<redacted:")
        assert redact_secret("apiToken", "tok").startswith("<redacted:")
        assert redact_secret("password", "hunter2").startswith("<redacted:")
        assert redact_secret("secret", "s3cret").startswith("<redacted:")

    def test_keeps_non_credential_values(self):
        assert redact_secret("goal", "improve quality") == "improve quality"
        assert redact_secret("max_rounds", 5) == 5
        assert redact_secret("api_key", 123) == 123  # non-string untouched

    def test_redact_mapping_is_deep(self):
        mapping = {
            "provider": {"api_key": "sk-abcdef123456", "model": "claude"},
            "list": [{"token": "t-xyz"}, {"goal": "ok"}],
            "plain": "value",
        }
        redacted = redact_mapping(mapping)
        assert redacted["provider"]["api_key"] != "sk-abcdef123456"
        assert redacted["provider"]["model"] == "claude"
        assert redacted["list"][0]["token"] != "t-xyz"
        assert redacted["list"][1]["goal"] == "ok"
        assert redacted["plain"] == "value"

    def test_redact_mapping_does_not_mutate_input(self):
        mapping = {"api_key": "sk-secret"}
        redact_mapping(mapping)
        assert mapping["api_key"] == "sk-secret"


class TestAuditLog:
    def test_record_writes_jsonl(self, tmp_path: Path):
        log = AuditLog(tmp_path)
        log.record("checkpoint.restore", "checkpoint.json", summary={"round": 3})
        path = tmp_path / AUDIT_DIR / AUDIT_FILE
        assert path.exists()
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["action"] == "checkpoint.restore"
        assert entry["target"] == "checkpoint.json"
        assert entry["summary"]["round"] == 3
        assert "timestamp" in entry

    def test_entries_returns_parsed(self, tmp_path: Path):
        log = AuditLog(tmp_path)
        log.record("a", "x")
        log.record("b", "y")
        entries = log.entries()
        assert [e["action"] for e in entries] == ["a", "b"]

    def test_entries_caps_limit(self, tmp_path: Path):
        log = AuditLog(tmp_path)
        for i in range(5):
            log.record(f"op{i}", "t")
        assert len(log.entries(limit=2)) == 2

    def test_entries_empty_when_missing(self, tmp_path: Path):
        assert AuditLog(tmp_path).entries() == []

    def test_read_audit_entries_helper(self, tmp_path: Path):
        AuditLog(tmp_path).record("config.update", "iterate.config.yaml")
        entries = read_audit_entries(tmp_path)
        assert len(entries) == 1
        assert entries[0]["action"] == "config.update"
