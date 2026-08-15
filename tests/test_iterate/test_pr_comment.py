"""Tests for PR comment mode (markdown rendering + gh posting flow)."""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

from typer.testing import CliRunner

from openharness import cli
from openharness.iterate import pr_comment
from openharness.iterate.ci_report import ReportSummary
from openharness.iterate.decision_log import append_entry, make_entry


def _summary(**overrides) -> ReportSummary:
    defaults = {
        "verdict": "converged",
        "total_findings": 2,
        "findings": [
            {"severity": "high", "file": "a.py", "line": 4, "dimension": "security", "summary": "injection risk"},
            {"severity": "low", "file": "b.py", "line": None, "dimension": "style", "summary": "naming"},
        ],
        "mode": "dry-run",
    }
    defaults.update(overrides)
    return ReportSummary(**defaults)


class FakeGh:
    """Scriptable gh runner: maps argv-prefix → canned (returncode, stdout)."""

    def __init__(self, routes: list[tuple[tuple[str, ...], int, str]]):
        self.routes = routes
        self.calls: list[tuple[list[str], str | None]] = []

    def __call__(self, args: list[str], cwd: str, input_text: str | None):
        self.calls.append((list(args), input_text))
        # Longest prefix wins so the POST route (…--input -) is not shadowed
        # by the listing route (same first two argv elements).
        for prefix, returncode, stdout in sorted(self.routes, key=lambda r: -len(r[0])):
            if tuple(args[: len(prefix)]) == prefix:
                return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="unmatched route")


POSTED_COMMENT = json.dumps(
    [
        {"id": 111, "body": "someone else's review"},
        {"id": 222, "body": pr_comment.PR_COMMENT_MARKER + "\n## iterate report\nold"},
    ]
)


def _happy_routes(*, existing: bool = True) -> list[tuple[tuple[str, ...], int, str]]:
    listing = ("api", "repos/jingzhao-l/iterate-harness/issues/123/comments?per_page=100")
    routes = [
        (("pr", "view"), 0, "123"),
        (("repo", "view"), 0, "jingzhao-l/iterate-harness"),
        (listing, 0, POSTED_COMMENT if existing else "[]"),
    ]
    if existing:
        routes.append((("api", "-X", "PATCH", "repos/jingzhao-l/iterate-harness/issues/comments/222"), 0, "{}"))
    else:
        routes.append(
            (("api", "repos/jingzhao-l/iterate-harness/issues/123/comments", "--input", "-"), 0, "{}")
        )
    return routes


class TestRenderMarkdown:
    def test_marker_anchors_the_comment(self):
        body = pr_comment.render_markdown(_summary())
        assert body.startswith(pr_comment.PR_COMMENT_MARKER)

    def test_header_line_and_findings_table(self):
        body = pr_comment.render_markdown(_summary())
        assert "**mode:** dry-run" in body
        assert "**verdict:** converged" in body
        assert "**findings:** 2" in body
        assert "| high | security | a.py:4 | injection risk |" in body
        # line missing/invalid → file-only location
        assert "| low | style | b.py | naming |" in body

    def test_no_findings_placeholder(self):
        body = pr_comment.render_markdown(ReportSummary(findings=[]))
        assert "No findings recorded." in body

    def test_pipe_and_newline_escaping_in_cells(self):
        summary = _summary(
            findings=[{"severity": "low", "file": "a.py", "line": 1, "dimension": "d", "summary": "a|b\nc"}]
        )
        body = pr_comment.render_markdown(summary)
        assert "a\\|b c" in body

    def test_findings_truncated_at_cap(self):
        findings = [
            {"severity": "low", "file": "f.py", "line": i, "dimension": "d", "summary": "s"}
            for i in range(pr_comment.MAX_FINDINGS_ROWS + 3)
        ]
        body = pr_comment.render_markdown(_summary(findings=findings, total_findings=len(findings)))
        assert f"({3} more finding(s) truncated)" in body

    def test_missing_file_renders_placeholder(self):
        summary = _summary(findings=[{"severity": "low", "line": 1, "dimension": "d", "summary": "s"}])
        assert "*(no file)*" in pr_comment.render_markdown(summary)

    def test_gate_pass_and_fail_lines(self):
        passed = pr_comment.render_markdown(_summary(), {"passed": True, "violations": []})
        assert "**threshold gate:** PASS" in passed
        failed = pr_comment.render_markdown(
            _summary(),
            {"passed": False, "violations": [{"scope": "global", "metric": "low", "actual": 3, "limit": 0}]},
        )
        assert "**threshold gate:** FAIL" in failed
        assert "`global:low 3>0`" in failed

    def test_gate_violations_capped_at_five(self):
        violations = [
            {"scope": "global", "metric": "low", "actual": i, "limit": 0} for i in range(7)
        ]
        body = pr_comment.render_markdown(_summary(), {"passed": False, "violations": violations})
        assert "(2 more violation(s))" in body


class TestPostPrComment:
    def test_body_without_marker_is_rejected(self):
        result = pr_comment.post_pr_comment("no marker here", cwd=".", runner=FakeGh([]))
        assert result.status == "skipped"
        assert "marker" in result.detail

    def test_gh_missing_degrades_to_skipped(self):
        def missing_gh(args, cwd, input_text):
            raise FileNotFoundError("gh")

        result = pr_comment.post_pr_comment(pr_comment.PR_COMMENT_MARKER, cwd=".", runner=missing_gh)
        assert result.status == "skipped"
        assert "not installed" in result.detail

    def test_gh_timeout_degrades_to_skipped(self):
        def slow_gh(args, cwd, input_text):
            raise subprocess.TimeoutExpired(cmd="gh", timeout=1)

        result = pr_comment.post_pr_comment(pr_comment.PR_COMMENT_MARKER, cwd=".", runner=slow_gh)
        assert result.status == "skipped"
        assert "timed out" in result.detail

    def test_no_pull_request_context(self):
        gh = FakeGh([(("pr", "view"), 1, "no pull requests found")])
        result = pr_comment.post_pr_comment(pr_comment.PR_COMMENT_MARKER, cwd=".", runner=gh)
        assert result.status == "skipped"
        assert "no pull request" in result.detail

    def test_unparseable_pr_number(self):
        gh = FakeGh([(("pr", "view"), 0, "not-a-number")])
        result = pr_comment.post_pr_comment(pr_comment.PR_COMMENT_MARKER, cwd=".", runner=gh)
        assert result.status == "skipped"
        assert "PR number" in result.detail

    def test_repo_view_failure_skips(self):
        gh = FakeGh([(("pr", "view"), 0, '"7"'), (("repo", "view"), 1, "fatal: not a repo")])
        result = pr_comment.post_pr_comment(pr_comment.PR_COMMENT_MARKER, cwd=".", runner=gh)
        assert result.status == "skipped"
        assert "repository" in result.detail

    def test_comment_listing_failure_skips(self):
        gh = FakeGh(
            [
                (("pr", "view"), 0, "7"),
                (("repo", "view"), 0, "o/r"),
                (("api", "repos/o/r/issues/7/comments?per_page=100"), 1, "Server Error"),
            ]
        )
        result = pr_comment.post_pr_comment(pr_comment.PR_COMMENT_MARKER, cwd=".", runner=gh)
        assert result.status == "skipped"
        assert "listing" in result.detail

    def test_malformed_comment_json_skips(self):
        gh = FakeGh(
            [
                (("pr", "view"), 0, "7"),
                (("repo", "view"), 0, "o/r"),
                (("api", "repos/o/r/issues/7/comments?per_page=100"), 0, "<<not-json>>"),
            ]
        )
        result = pr_comment.post_pr_comment(pr_comment.PR_COMMENT_MARKER, cwd=".", runner=gh)
        assert result.status == "skipped"
        assert "listing" in result.detail

    def test_creates_comment_when_none_marked(self):
        gh = FakeGh(_happy_routes(existing=False))
        result = pr_comment.post_pr_comment(pr_comment.render_markdown(_summary()), cwd=".", runner=gh)
        assert result.status == "posted"
        assert result.detail == "#123"
        post_calls = [c for c in gh.calls if "--input" in c[0]]
        assert len(post_calls) == 1
        assert json.loads(post_calls[0][1])["body"].startswith(pr_comment.PR_COMMENT_MARKER)

    def test_updates_existing_marked_comment(self):
        gh = FakeGh(_happy_routes(existing=True))
        result = pr_comment.post_pr_comment(pr_comment.render_markdown(_summary()), cwd=".", runner=gh)
        assert result.status == "updated"
        patch_calls = [c for c in gh.calls if "PATCH" in c[0]]
        assert len(patch_calls) == 1
        assert "issues/comments/222" in "/".join(patch_calls[0][0])
        assert json.loads(patch_calls[0][1])["body"].startswith(pr_comment.PR_COMMENT_MARKER)

    def test_post_failure_degrades_to_skipped(self):
        routes = [
            (("pr", "view"), 0, "7"),
            (("repo", "view"), 0, "o/r"),
            (("api", "repos/o/r/issues/7/comments?per_page=100"), 0, "[]"),
            (("api", "repos/o/r/issues/7/comments", "--input", "-"), 1, "403 Forbidden"),
        ]
        result = pr_comment.post_pr_comment(pr_comment.PR_COMMENT_MARKER, cwd=".", runner=FakeGh(routes))
        assert result.status == "skipped"
        assert "create" in result.detail

    def test_patch_failure_degrades_to_skipped(self):
        routes = [
            (("pr", "view"), 0, "7"),
            (("repo", "view"), 0, "o/r"),
            (("api", "repos/o/r/issues/7/comments?per_page=100"), 0, POSTED_COMMENT),
            (("api", "-X", "PATCH"), 1, "422 Unprocessable"),
        ]
        result = pr_comment.post_pr_comment(pr_comment.PR_COMMENT_MARKER, cwd=".", runner=FakeGh(routes))
        assert result.status == "skipped"
        assert "update" in result.detail


class TestReportCliPrMode:
    """`oh iterate report --pr` end-to-end (posting is faked at module level)."""

    def _seed_report(self, tmp_path):
        append_entry(
            tmp_path,
            make_entry(
                entry_type="report",
                round_number=1,
                data={"verdict": "converged", "mode": "dry-run", "findings": []},
            ),
        )

    def test_pr_flag_posts_comment_and_suppresses_text(self, tmp_path, monkeypatch):
        self._seed_report(tmp_path)
        monkeypatch.chdir(tmp_path)
        captured: dict[str, object] = {}

        def fake_post(body, cwd, **kwargs):
            captured["body"] = body
            return pr_comment.PostResult("updated", "#9")

        monkeypatch.setattr(pr_comment, "post_pr_comment", fake_post)
        result = CliRunner().invoke(cli.app, ["iterate", "report", "--pr"])
        assert result.exit_code == 0
        assert "PR comment: updated (#9)" in result.output
        assert str(captured["body"]).startswith(pr_comment.PR_COMMENT_MARKER)
        # text render suppressed in --pr mode
        assert "iterate report (dry-run): " not in result.output

    def test_pr_flag_degrades_without_gh(self, tmp_path, monkeypatch):
        self._seed_report(tmp_path)
        monkeypatch.chdir(tmp_path)

        def unavailable_post(body, cwd, **kwargs):
            return pr_comment.PostResult("skipped", "gh CLI not installed")

        monkeypatch.setattr(pr_comment, "post_pr_comment", unavailable_post)
        result = CliRunner().invoke(cli.app, ["iterate", "report", "--pr"])
        assert result.exit_code == 0
        assert "skipped" in result.output

    def test_pr_and_github_can_combine(self, tmp_path, monkeypatch):
        self._seed_report(tmp_path)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            pr_comment,
            "post_pr_comment",
            lambda body, cwd, **kw: pr_comment.PostResult("posted", "#9"),
        )
        result = CliRunner().invoke(cli.app, ["iterate", "report", "--pr", "--github"])
        assert result.exit_code == 0
        assert "::notice::iterate report" in result.output
        assert "PR comment: posted (#9)" in result.output
