"""Tests for PR comment mode (markdown rendering + gh posting flow)."""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

from typer.testing import CliRunner

from iterate_harness import cli
from iterate_harness.iterate import pr_comment
from iterate_harness.iterate.ci_report import ReportSummary
from iterate_harness.iterate.decision_log import append_entry, make_entry


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
    listing = ("api", "repos/jingzhao-l/iterate-harness/issues/123/comments?per_page=100&page=1")
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
                (("api", "repos/o/r/issues/7/comments?per_page=100&page=1"), 1, "Server Error"),
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
                (("api", "repos/o/r/issues/7/comments?per_page=100&page=1"), 0, "<<not-json>>"),
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
            (("api", "repos/o/r/issues/7/comments?per_page=100&page=1"), 0, "[]"),
            (("api", "repos/o/r/issues/7/comments", "--input", "-"), 1, "403 Forbidden"),
        ]
        result = pr_comment.post_pr_comment(pr_comment.PR_COMMENT_MARKER, cwd=".", runner=FakeGh(routes))
        assert result.status == "skipped"
        assert "create" in result.detail

    def test_patch_failure_degrades_to_skipped(self):
        routes = [
            (("pr", "view"), 0, "7"),
            (("repo", "view"), 0, "o/r"),
            (("api", "repos/o/r/issues/7/comments?per_page=100&page=1"), 0, POSTED_COMMENT),
            (("api", "-X", "PATCH"), 1, "422 Unprocessable"),
        ]
        result = pr_comment.post_pr_comment(pr_comment.PR_COMMENT_MARKER, cwd=".", runner=FakeGh(routes))
        assert result.status == "skipped"
        assert "update" in result.detail


class TestFindMarkerCommentPagination:
    """Giant-PR pagination: the marker beyond the first 100 comments is found."""

    @staticmethod
    def _full_page(start_id: int, marker_ids: tuple[int, ...] = (), count: int = 100) -> str:
        comments = [
            {"id": start_id + i, "body": f"regular comment {start_id + i}"} for i in range(count)
        ]
        for marker_id in marker_ids:
            entry = next(c for c in comments if c["id"] == marker_id)
            entry["body"] = pr_comment.PR_COMMENT_MARKER + "\nold"
        return json.dumps(comments)

    def _routes(self, pages: list[str]) -> list[tuple[tuple[str, ...], int, str]]:
        routes = [
            (("pr", "view"), 0, "7"),
            (("repo", "view"), 0, "o/r"),
        ]
        for index, payload in enumerate(pages, start=1):
            routes.append((("api", f"repos/o/r/issues/7/comments?per_page=100&page={index}"), 0, payload))
        return routes

    def test_marker_on_later_page_is_found(self):
        # Page 1: full, no marker. Page 2 (short, stops the scan): marker.
        pages = [self._full_page(1000), self._full_page(1100, marker_ids=(1115,), count=99)]
        gh = FakeGh(self._routes(pages))
        result = pr_comment.post_pr_comment(pr_comment.PR_COMMENT_MARKER, cwd=".", runner=gh)
        assert result.status == "skipped"  # no PATCH route configured
        listing_calls = [c for c in gh.calls if "comments?per_page" in " ".join(c[0])]
        assert len(listing_calls) == 2

    def test_marker_on_later_page_triggers_patch_on_that_id(self):
        pages = [self._full_page(1000), self._full_page(1100, marker_ids=(1115,), count=99)]
        routes = self._routes(pages)
        routes.append((("api", "-X", "PATCH", "repos/o/r/issues/comments/1115"), 0, "{}"))
        result = pr_comment.post_pr_comment(pr_comment.PR_COMMENT_MARKER, cwd=".", runner=FakeGh(routes))
        assert result.status == "updated"

    def test_latest_marker_across_pages_wins(self):
        # Marker on page 1 (id 1055) AND page 2 (id 1115): the newer page wins.
        pages = [
            self._full_page(1000, marker_ids=(1055,)),
            self._full_page(1100, marker_ids=(1115,), count=99),
        ]
        routes = self._routes(pages)
        routes.append((("api", "-X", "PATCH"), 0, "{}"))
        gh = FakeGh(routes)
        result = pr_comment.post_pr_comment(pr_comment.PR_COMMENT_MARKER, cwd=".", runner=gh)
        assert result.status == "updated"
        patch_calls = [c for c in gh.calls if "PATCH" in c[0]]
        assert "issues/comments/1115" in "/".join(patch_calls[0][0])

    def test_page_cap_stops_scanning_and_falls_back_to_create(self):
        # Every page is full (100 comments) with no marker anywhere: the
        # defensive cap stops the scan and the comment is (re)created.
        pages = [self._full_page(1000 + 100 * i) for i in range(pr_comment.MAX_COMMENT_PAGES)]
        routes = self._routes(pages)
        routes.append((("api", "repos/o/r/issues/7/comments", "--input", "-"), 0, "{}"))
        gh = FakeGh(routes)
        result = pr_comment.post_pr_comment(pr_comment.PR_COMMENT_MARKER, cwd=".", runner=gh)
        assert result.status == "posted"
        listing_calls = [c for c in gh.calls if "comments?per_page" in " ".join(c[0])]
        assert len(listing_calls) == pr_comment.MAX_COMMENT_PAGES

    def test_short_page_stops_pagination_early(self):
        # Page 2 returns a short page (3 comments, no marker) → scan stops
        # even though the cap allows more pages.
        pages = [self._full_page(1000), json.dumps([{"id": 1, "body": "tail"}])]
        routes = self._routes(pages)
        routes.append((("api", "repos/o/r/issues/7/comments", "--input", "-"), 0, "{}"))
        gh = FakeGh(routes)
        result = pr_comment.post_pr_comment(pr_comment.PR_COMMENT_MARKER, cwd=".", runner=gh)
        assert result.status == "posted"
        listing_calls = [c for c in gh.calls if "comments?per_page" in " ".join(c[0])]
        assert len(listing_calls) == 2


class TestReportCliPrMode:
    """`ih iterate report --pr` end-to-end (posting is faked at module level)."""

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
