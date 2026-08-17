"""Tests for the webhook notification module (Slack / Lark / generic)."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import URLError

import pytest
from typer.testing import CliRunner

from iterate_harness import cli
from iterate_harness.iterate import ci_report
from iterate_harness.iterate import webhook
from iterate_harness.iterate.decision_log import append_entry, make_entry


def make_summary(findings: list[dict] | None = None, **kwargs) -> ci_report.ReportSummary:
    data: dict[str, Any] = {"verdict": "converged", "mode": "dry-run"}
    if findings is not None:
        data["findings"] = findings
    data.update(kwargs)
    entry = make_entry(entry_type="report", round_number=2, data=data)
    return ci_report.ReportSummary.from_entry(entry)


class FakeResponse:
    """Minimal stand-in for urllib HTTPResponse used with `with`."""

    def __init__(self, status: int = 200, body: bytes = b"ok"):
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


@pytest.fixture
def captured_request(monkeypatch):
    """Patch webhook.urlopen to record the Request and return a fake 200."""
    captured: dict[str, Any] = {}

    def fake_urlopen(req, timeout=None):
        captured["req"] = req
        captured["data"] = req.data
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(webhook, "urlopen", fake_urlopen)
    return captured


class TestDetectWebhookType:
    def test_slack_url(self):
        assert webhook.detect_webhook_type("https://hooks.slack.com/services/T0/B0/x") == "slack"

    def test_slack_url_case_insensitive(self):
        assert webhook.detect_webhook_type("HTTPS://HOOKS.SLACK.COM/services/x") == "slack"

    def test_lark_feishu_url(self):
        assert webhook.detect_webhook_type("https://open.feishu.cn/open-apis/bot/v2/hook/x") == "lark"

    def test_lark_domain(self):
        assert webhook.detect_webhook_type("https://open.larksuite.com/open-apis/bot/v2/hook/x") == "lark"

    def test_generic_url(self):
        assert webhook.detect_webhook_type("https://example.com/hooks/receiver") == "generic"

    def test_empty_url_is_generic(self):
        assert webhook.detect_webhook_type("") == "generic"


class TestSendWebhook:
    def test_success_returns_status_and_body(self, monkeypatch):
        monkeypatch.setattr(webhook, "urlopen", lambda req, timeout=None: FakeResponse(status=200, body=b"hello"))
        result = webhook.send_webhook("https://example.com/h", {"k": "v"})
        assert result.success is True
        assert result.status_code == 200
        assert result.body == "hello"
        assert result.error == ""

    def test_urlerror_returns_failure(self, monkeypatch):
        def boom(req, timeout=None):
            raise URLError("connection refused")

        monkeypatch.setattr(webhook, "urlopen", boom)
        result = webhook.send_webhook("https://example.com/h", {"k": "v"})
        assert result.success is False
        assert result.status_code == 0
        assert "connection refused" in result.error

    def test_oserror_returns_failure(self, monkeypatch):
        def boom(req, timeout=None):
            raise OSError("timed out")

        monkeypatch.setattr(webhook, "urlopen", boom)
        result = webhook.send_webhook("https://example.com/h", {"k": "v"})
        assert result.success is False
        assert result.status_code == 0
        assert "timed out" in result.error

    def test_posts_json_with_headers(self, captured_request):
        webhook.send_webhook("https://example.com/h", {"answer": 42})
        req = captured_request["req"]
        assert req.get_method() == "POST"
        sent_headers = dict(req.header_items())
        assert sent_headers["Content-type"] == "application/json"
        assert json.loads(captured_request["data"].decode("utf-8")) == {"answer": 42}
        assert captured_request["timeout"] == webhook.WEBHOOK_REQUEST_TIMEOUT


class TestSlackPayload:
    def test_blocks_structure(self, captured_request):
        summary = make_summary(
            findings=[
                {"severity": "high", "file": "a.py", "line": 3, "summary": "x", "dimension": "security"},
                {"severity": "low", "file": "b.py", "summary": "y", "dimension": "style"},
            ],
            mode="normal",
        )
        result = webhook.notify_report("https://hooks.slack.com/services/T0/B0/x", summary)
        assert result.success is True
        payload = json.loads(captured_request["data"].decode("utf-8"))
        assert payload["text"].startswith("iterate report (normal): 2 finding(s), verdict=converged")
        block_types = [b["type"] for b in payload["blocks"]]
        assert block_types == ["header", "section", "section"]
        assert payload["blocks"][0]["text"]["text"] == "iterate Review Report"
        assert "[high]" in payload["blocks"][2]["text"]["text"]
        assert "`a.py`" in payload["blocks"][2]["text"]["text"]

    def test_gate_line(self, captured_request):
        summary = make_summary([])
        gate = {"passed": False, "violations": []}
        webhook.notify_report("https://hooks.slack.com/services/T0/B0/x", summary, gate)
        payload = json.loads(captured_request["data"].decode("utf-8"))
        assert any("❌ FAIL" in b["text"]["text"] for b in payload["blocks"])

    def test_truncates_at_20_findings(self, captured_request):
        findings = [{"severity": "low", "file": f"f{i}.py", "summary": "s", "dimension": "d"} for i in range(25)]
        summary = make_summary(findings)
        webhook.notify_report("https://hooks.slack.com/services/T0/B0/x", summary)
        payload = json.loads(captured_request["data"].decode("utf-8"))
        summary_text = payload["blocks"][-1]["text"]["text"]
        assert summary_text.count("🔵 [low]") == 20  # exactly 20 finding lines
        assert summary_text.count("\n") == 20  # 20 lines + the trailing "more" line
        assert "... and 5 more findings" in summary_text


class TestLarkPayload:
    def test_card_structure(self, captured_request):
        summary = make_summary(
            findings=[{"severity": "critical", "file": "c.py", "line": 9, "summary": "z", "dimension": "security"}],
            mode="normal",
        )
        webhook.notify_report("https://open.feishu.cn/open-apis/bot/v2/hook/x", summary)
        payload = json.loads(captured_request["data"].decode("utf-8"))
        assert payload["msg_type"] == "interactive"
        card = payload["card"]
        assert card["header"]["title"]["content"] == "iterate Review Report"
        content = "\n".join(el["content"] for el in card["elements"] if el["tag"] == "markdown")
        assert "iterate report (normal): 1 finding(s), verdict=converged" in content
        assert "🔴[critical] security `c.py`: z" in content

    def test_lark_domain_detected(self, captured_request):
        summary = make_summary([])
        webhook.notify_report("https://open.larksuite.com/open-apis/bot/v2/hook/x", summary)
        payload = json.loads(captured_request["data"].decode("utf-8"))
        assert payload["msg_type"] == "interactive"

    def test_gate_line(self, captured_request):
        summary = make_summary([])
        gate = {"passed": True, "violations": []}
        webhook.notify_report("https://open.feishu.cn/open-apis/bot/v2/hook/x", summary, gate)
        payload = json.loads(captured_request["data"].decode("utf-8"))
        content = "\n".join(el["content"] for el in payload["card"]["elements"] if el["tag"] == "markdown")
        assert "✅ PASS" in content


class TestGenericPayload:
    def test_flat_json_shape(self, captured_request):
        summary = make_summary(
            findings=[{"severity": "medium", "file": "m.py", "line": 2, "summary": "s", "dimension": "perf"}],
            mode="normal",
        )
        webhook.notify_report("https://example.com/hooks/receiver", summary)
        payload = json.loads(captured_request["data"].decode("utf-8"))
        assert payload["source"] == "iterate-harness"
        assert payload["mode"] == "normal"
        assert payload["total_findings"] == 1
        assert payload["verdict"] == "converged"
        assert payload["findings"][0]["file"] == "m.py"
        assert payload["findings"][0]["line"] == 2

    def test_caps_generic_findings_at_50(self, captured_request):
        findings = [{"severity": "low", "file": f"f{i}.py", "summary": "s", "dimension": "d"} for i in range(60)]
        summary = make_summary(findings)
        webhook.notify_report("https://example.com/hooks/receiver", summary)
        payload = json.loads(captured_request["data"].decode("utf-8"))
        assert len(payload["findings"]) == 50


class TestIterateReportWebhookCli:
    """`ih iterate report --webhook` end-to-end wiring."""

    def test_sends_slack_webhook_and_prints_status(self, tmp_path, monkeypatch, captured_request):
        monkeypatch.chdir(tmp_path)
        append_entry(
            tmp_path,
            make_entry(
                entry_type="report",
                round_number=2,
                data={
                    "verdict": "converged",
                    "mode": "dry-run",
                    "findings": [{"severity": "low", "file": "a.py", "line": 1, "summary": "s", "dimension": "style"}],
                },
            ),
        )
        result = CliRunner().invoke(
            cli.app,
            ["iterate", "report", "--webhook", "https://hooks.slack.com/services/T0/B0/x"],
        )
        assert result.exit_code == 0
        assert "Webhook notification sent (HTTP 200)." in result.stderr
        req = captured_request["req"]
        assert req.full_url == "https://hooks.slack.com/services/T0/B0/x"
        payload = json.loads(captured_request["data"].decode("utf-8"))
        assert payload["blocks"][0]["text"]["text"] == "iterate Review Report"

    def test_failure_reported_but_exit_code_unchanged(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        def boom(req, timeout=None):
            raise URLError("network down")

        monkeypatch.setattr(webhook, "urlopen", boom)
        append_entry(
            tmp_path,
            make_entry(
                entry_type="report",
                round_number=2,
                data={"verdict": "converged", "mode": "dry-run", "findings": []},
            ),
        )
        result = CliRunner().invoke(
            cli.app,
            ["iterate", "report", "--webhook", "https://open.feishu.cn/open-apis/bot/v2/hook/x"],
        )
        assert result.exit_code == 0
        assert "<urlopen error network down>" in result.stderr

    def test_without_webhook_no_network_call(self, tmp_path, monkeypatch, captured_request):
        monkeypatch.chdir(tmp_path)
        append_entry(
            tmp_path,
            make_entry(entry_type="report", round_number=2, data={"verdict": "converged", "mode": "dry-run", "findings": []}),
        )
        result = CliRunner().invoke(cli.app, ["iterate", "report"])
        assert result.exit_code == 0
        assert "req" not in captured_request

    def test_module_re_exports(self):
        from iterate_harness.iterate import WebhookResult, notify_report

        assert notify_report is webhook.notify_report
        assert WebhookResult is webhook.WebhookResult
