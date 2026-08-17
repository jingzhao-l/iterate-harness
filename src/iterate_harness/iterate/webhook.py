"""Webhook notification for iterate report results.

Supports Slack Incoming Webhook and Lark/Feishu custom bot formats.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any
from urllib.request import Request, urlopen
from urllib.error import URLError

from .ci_report import ReportSummary

log = logging.getLogger(__name__)

#: Timeout for webhook HTTP requests (seconds).
WEBHOOK_REQUEST_TIMEOUT = 15

#: Maximum text length for a single message block.
MAX_BLOCK_TEXT = 2000


@dataclass
class WebhookResult:
    success: bool
    status_code: int
    body: str = ""
    error: str = ""


def detect_webhook_type(url: str) -> str:
    """Detect webhook type from URL."""
    url_lower = url.lower()
    if "hooks.slack.com" in url_lower:
        return "slack"
    if "feishu.cn" in url_lower or "lark" in url_lower:
        return "lark"
    return "generic"


def _build_slack_payload(summary: ReportSummary, gate: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build Slack message payload (rich Blocks format)."""
    severity_emoji = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵"}
    header_text = f"iterate report ({summary.mode}): {summary.total_findings} finding(s), verdict={summary.verdict}"
    blocks: list[dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": "iterate Review Report"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": header_text}},
    ]
    if gate is not None:
        passed = bool(gate.get("passed", True))
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"Threshold gate: {'✅ PASS' if passed else '❌ FAIL'}"}})
    if summary.findings:
        lines = []
        for f in list(summary.findings)[:20]:
            sev = f.get("severity", "")
            emoji = severity_emoji.get(sev, "⚪")
            dim = f.get("dimension", "?")
            file = f.get("file", "?")
            summ = f.get("summary", "")
            lines.append(f"{emoji} [{sev}] *{dim}* `{file}`: {summ}")
        summary_text = "\n".join(lines)
        if len(summary.findings) > 20:
            summary_text += f"\n... and {len(summary.findings) - 20} more findings"
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": summary_text}})
    return {"text": header_text, "blocks": blocks}


def _build_lark_payload(summary: ReportSummary, gate: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build Lark/Feishu message payload (interactive card format)."""
    severity_emoji = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵"}
    header_text = f"iterate report ({summary.mode}): {summary.total_findings} finding(s), verdict={summary.verdict}"
    elements: list[dict[str, Any]] = [
        {"tag": "markdown", "content": header_text},
    ]
    if gate is not None:
        passed = bool(gate.get("passed", True))
        elements.append({"tag": "markdown", "content": f"Threshold gate: {'✅ PASS' if passed else '❌ FAIL'}"})
    if summary.findings:
        lines = []
        for f in list(summary.findings)[:20]:
            sev = f.get("severity", "")
            emoji = severity_emoji.get(sev, "⚪")
            dim = f.get("dimension", "?")
            file = f.get("file", "?")
            summ = f.get("summary", "")
            lines.append(f"{emoji}[{sev}] {dim} `{file}`: {summ}")
        if len(summary.findings) > 20:
            lines.append(f"... +{len(summary.findings) - 20} more")
        elements.append({"tag": "markdown", "content": "\n".join(lines)})
    return {"msg_type": "interactive", "card": {"header": {"title": {"tag": "plain_text", "content": "iterate Review Report"}}, "elements": elements}}


def send_webhook(url: str, payload: dict[str, Any]) -> WebhookResult:
    """Send a JSON payload to a webhook URL.

    Returns a WebhookResult with success/failure information.
    """
    data = json.dumps(payload).encode("utf-8")
    req = Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urlopen(req, timeout=WEBHOOK_REQUEST_TIMEOUT) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return WebhookResult(success=True, status_code=resp.status, body=body[:500])
    except URLError as exc:
        return WebhookResult(success=False, status_code=0, error=str(exc))
    except OSError as exc:
        return WebhookResult(success=False, status_code=0, error=str(exc))


def notify_report(
    webhook_url: str,
    summary: ReportSummary,
    gate: dict[str, Any] | None = None,
) -> WebhookResult:
    """Send an iterate report to a webhook.

    Auto-detects whether the URL is a Slack webhook, Lark/Feishu webhook,
    or a generic JSON webhook endpoint.
    """
    wh_type = detect_webhook_type(webhook_url)
    if wh_type == "slack":
        payload = _build_slack_payload(summary, gate)
    elif wh_type == "lark":
        payload = _build_lark_payload(summary, gate)
    else:
        # Generic: send a flat JSON with severity counts
        payload = {
            "source": "iterate-harness",
            "mode": summary.mode,
            "total_findings": summary.total_findings,
            "verdict": summary.verdict,
            "findings": [
                {
                    "severity": f.get("severity", ""),
                    "dimension": f.get("dimension", ""),
                    "file": f.get("file", ""),
                    "line": f.get("line", 0),
                    "summary": f.get("summary", ""),
                }
                for f in summary.findings[:50]
            ],
        }
    return send_webhook(webhook_url, payload)


__all__ = [
    "WebhookResult",
    "detect_webhook_type",
    "notify_report",
    "send_webhook",
]
