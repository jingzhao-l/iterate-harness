"""PR comment mode for ``oh iterate report --pr``.

Posts the final iterate report as a Markdown comment on the current pull
request via the GitHub CLI (``gh``), and UPDATES the previously posted
iterate comment instead of duplicating it on every CI run.

Design constraints:

- **Graceful degradation, never raise**: ``gh`` missing, not inside a PR,
  or any API failure degrades to a status result so the CI pipeline keeps
  its ``--fail-on`` exit-code semantics untouched.
- **Idempotent**: the comment body carries a hidden HTML marker; posting
  twice edits the existing comment rather than adding a new one.
- **Testable**: the process boundary is a single injectable ``runner``
  callable ``(args, cwd, input_text) -> (returncode, output)``.

All rendering is pure; ``post_pr_comment`` is the only side-effectful
entry point.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from .ci_report import ReportSummary

#: Hidden marker embedded in every posted comment (never rendered by
#: GitHub). Used to find — and later update — OUR comment among all the
#: PR's review comments.
PR_COMMENT_MARKER = "<!-- iterate-report -->"

#: Findings cap in the rendered Markdown table (keeps huge reports readable).
MAX_FINDINGS_ROWS = 50

#: Hard timeout for every gh invocation (seconds) — CI must never hang.
GH_TIMEOUT_SECONDS = 60

#: Runner contract: argv after "gh", working dir, optional stdin text.
GhRunner = Callable[[list[str], str, str | None], "subprocess.CompletedProcess[str]"]


def default_gh_runner(args: list[str], cwd: str, input_text: str | None) -> subprocess.CompletedProcess[str]:
    """Run ``gh <args>`` capturing output (the production runner)."""
    return subprocess.run(
        ["gh", *args],
        cwd=cwd,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=GH_TIMEOUT_SECONDS,
        check=False,
    )


def _cell(text: Any) -> str:
    """Sanitize one Markdown table cell (escape pipes, collapse newlines)."""
    return str(text if text is not None else "").replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()


def _location(finding: dict[str, Any]) -> str:
    file_value = finding.get("file")
    if not isinstance(file_value, str) or not file_value:
        return "*(no file)*"
    line_value = finding.get("line")
    if isinstance(line_value, int) and line_value > 0:
        return f"{_cell(file_value)}:{line_value}"
    return _cell(file_value)


def render_markdown(summary: ReportSummary, gate: dict[str, Any] | None = None) -> str:
    """Render the report as a Markdown PR comment (marker-anchored)."""
    header = (
        f"**mode:** {_cell(summary.mode)} · **verdict:** {_cell(summary.verdict)} · "
        f"**findings:** {summary.total_findings}"
    )
    lines = [
        PR_COMMENT_MARKER,
        "## iterate report",
        "",
        header,
        "",
    ]
    if not summary.findings:
        lines.append("No findings recorded.")
    else:
        lines.extend(
            [
                "| severity | dimension | location | summary |",
                "| --- | --- | --- | --- |",
            ]
        )
        for finding in summary.findings[:MAX_FINDINGS_ROWS]:
            lines.append(
                f"| {_cell(finding.get('severity') or '?')} "
                f"| {_cell(finding.get('dimension') or 'general')} "
                f"| {_location(finding)} "
                f"| {_cell(finding.get('summary') or '')} |"
            )
        if len(summary.findings) > MAX_FINDINGS_ROWS:
            hidden = len(summary.findings) - MAX_FINDINGS_ROWS
            lines.append(f"| … | | | *({hidden} more finding(s) truncated)* |")
    if gate is not None:
        passed = bool(gate.get("passed", True))
        status = "PASS ✅" if passed else "FAIL ❌"
        lines.extend(["", f"**threshold gate:** {status}"])
        violations_raw = gate.get("violations")
        violations = [v for v in violations_raw if isinstance(v, dict)] if isinstance(violations_raw, list) else []
        for violation in violations[:5]:
            lines.append(
                f"- `{_cell(violation.get('scope'))}:{_cell(violation.get('metric'))} "
                f"{violation.get('actual')}>{violation.get('limit')}`"
            )
        if len(violations) > 5:
            lines.append(f"- … ({len(violations) - 5} more violation(s))")
    return "\n".join(lines)


@dataclass
class PostResult:
    """Outcome of one post attempt (status vocabulary is stable for logs)."""

    status: str
    detail: str = ""

    def __str__(self) -> str:
        return self.status if not self.detail else f"{self.status} ({self.detail})"


def _output(proc: subprocess.CompletedProcess[str]) -> str:
    return (proc.stdout + proc.stderr).strip()


def post_pr_comment(
    body: str,
    cwd: str,
    *,
    runner: GhRunner = default_gh_runner,
) -> PostResult:
    """Post (or update) the iterate report comment on the current PR.

    Status vocabulary: ``posted`` / ``updated`` / ``skipped`` (with
    detail). Never raises — every failure mode degrades to ``skipped``.
    """
    if PR_COMMENT_MARKER not in body:
        return PostResult("skipped", "body missing the iterate-report marker")

    try:
        proc = runner(["pr", "view", "--json", "number", "--jq", ".number"], cwd, None)
    except FileNotFoundError:
        return PostResult("skipped", "gh CLI not installed")
    except subprocess.TimeoutExpired:
        return PostResult("skipped", "gh pr view timed out")
    if proc.returncode != 0:
        return PostResult("skipped", "no pull request context (or gh auth missing)")

    number = _parse_number(_output(proc))
    if number is None:
        return PostResult("skipped", "could not parse PR number from gh output")

    proc = _safe_run(runner, ["repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"], cwd)
    if proc is None or proc.returncode != 0:
        return PostResult("skipped", "could not resolve repository via gh repo view")
    repo = _output(proc).strip().strip('"')
    if not repo:
        return PostResult("skipped", "empty repository name from gh repo view")

    comment_id = _find_marker_comment(runner, cwd, repo, number)
    if comment_id == "error":
        return PostResult("skipped", "gh api failed while listing PR comments")

    payload = json.dumps({"body": body})
    if comment_id is None:
        proc = _safe_run(
            runner,
            ["api", f"repos/{repo}/issues/{number}/comments", "--input", "-"],
            cwd,
            payload,
        )
        if proc is None or proc.returncode != 0:
            return PostResult("skipped", "gh api failed to create the comment")
        return PostResult("posted", f"#{number}")

    proc = _safe_run(
        runner,
        ["api", "-X", "PATCH", f"repos/{repo}/issues/comments/{comment_id}", "--input", "-"],
        cwd,
        payload,
    )
    if proc is None or proc.returncode != 0:
        return PostResult("skipped", f"gh api failed to update comment {comment_id}")
    return PostResult("updated", f"#{number}")


def _safe_run(
    runner: GhRunner,
    args: list[str],
    cwd: str,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str] | None:
    """Runner wrapper converting crashes/timeouts into ``None``."""
    try:
        return runner(args, cwd, input_text)
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        return None


def _parse_number(text: str) -> int | None:
    """Parse a PR number from gh's JSON-quoted or plain output."""
    cleaned = text.strip().strip('"').strip("'")
    try:
        value = int(cleaned)
    except ValueError:
        return None
    return value if value > 0 else None


def _find_marker_comment(
    runner: GhRunner,
    cwd: str,
    repo: str,
    number: int,
) -> int | Literal["error"]:
    """Return the id of OUR latest comment on the PR, None or "error"."""
    proc = _safe_run(
        runner,
        ["api", f"repos/{repo}/issues/{number}/comments?per_page=100"],
        cwd,
    )
    if proc is None or proc.returncode != 0:
        return "error"
    try:
        comments = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return "error"
    if not isinstance(comments, list):
        return "error"
    for comment in reversed(comments):
        if not isinstance(comment, dict):
            continue
        body = comment.get("body")
        comment_id = comment.get("id")
        if isinstance(body, str) and PR_COMMENT_MARKER in body and isinstance(comment_id, int):
            return comment_id
    return None
