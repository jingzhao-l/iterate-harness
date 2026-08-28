"""Tests for iterate_harness.iterate.review (port of review.test.ts)."""

from __future__ import annotations

from iterate_harness.iterate.review import (
    ReviewAttachment,
    SEVERITY_RANK,
    aggregate_rounds,
    build_review_plan,
    build_review_report,
    compute_convergence,
    dedupe_findings,
    filter_known_intentional,
    finding_key,
    findings_schema,
    normalize_summary,
    parse_attachments,
    plan_to_dict,
    reviewer_task_prompt,
    sort_findings,
)
from iterate_harness.iterate.types import (
    IterateConfig,
    KnownIntentional,
    ReviewFinding,
    ReviewRound,
)


def f(**partial: object) -> ReviewFinding:
    base: dict[str, object] = {
        "dimension": "correctness",
        "file": "src/a.ts",
        "severity": "medium",
        "summary": "A problem",
        "failure_scenario": "fails when x happens",
        "suggested_fix": "do y instead",
        "is_atomic": True,
    }
    base.update(partial)
    return ReviewFinding(**base)  # type: ignore[arg-type]


def base_config() -> IterateConfig:
    return IterateConfig(
        goal="Improve quality",
        dimensions=["correctness", "security"],
        validation={"command_whitelist": ["pytest"], "commands": {"test": ["pytest tests/ -x -q"]}},  # type: ignore[arg-type]
    )


class TestSeveritySorting:
    def test_severity_rank_orders_critical_high_medium_low(self):
        assert SEVERITY_RANK["critical"] < SEVERITY_RANK["high"]
        assert SEVERITY_RANK["high"] < SEVERITY_RANK["medium"]
        assert SEVERITY_RANK["medium"] < SEVERITY_RANK["low"]

    def test_sorts_most_severe_first_then_file_and_line(self):
        sorted_list = sort_findings([
            f(file="z.ts", severity="low", summary="low in z"),
            f(file="a.ts", severity="high", summary="high in a"),
            f(file="a.ts", severity="critical", summary="critical in a"),
        ])
        assert [x.severity for x in sorted_list] == ["critical", "high", "low"]
        # Same severity → file asc, line asc
        tie = sort_findings([
            f(file="b.ts", line=10, severity="high"),
            f(file="a.ts", line=5, severity="high"),
            f(file="a.ts", line=1, severity="high"),
        ])
        assert [f"{x.file}:{x.line}" for x in tie] == ["a.ts:1", "a.ts:5", "b.ts:10"]

    def test_does_not_mutate_input_list(self):
        original = [f(severity="low"), f(severity="critical")]
        copy = list(original)
        sort_findings(original)
        assert original == copy


class TestDedupe:
    def test_normalize_summary_trims_lowercases_collapses_whitespace(self):
        assert normalize_summary("  Foo  Bar\tBaz  ") == "foo bar baz"

    def test_finding_key_combines_file_dimension_normalized_summary(self):
        a = f(file="a.ts", dimension="x", summary="  Crash HERE ")
        b = f(file="a.ts", dimension="x", summary="crash here")
        assert finding_key(a) == finding_key(b)
        assert finding_key(f(file="a.ts")) != finding_key(f(file="b.ts"))

    def test_removes_exact_duplicates_keeps_first(self):
        out = dedupe_findings([
            f(summary="same issue", suggested_fix="first"),
            f(summary="  SAME issue ", suggested_fix="second (duplicate)"),
            f(summary="different", suggested_fix="third"),
        ])
        assert len(out) == 2
        assert out[0].suggested_fix == "first"

    def test_keeps_findings_differing_only_by_dimension(self):
        out = dedupe_findings([
            f(dimension="correctness", summary="x"),
            f(dimension="security", summary="x"),
        ])
        assert len(out) == 2


class TestKnownIntentionalFilter:
    def test_filters_exact_line_match_same_file_dimension(self):
        out = filter_known_intentional(
            [f(file="a.ts", line=42, dimension="security", summary="x")],
            [KnownIntentional(file="a.ts", line=42, dimension="security", reason="intentional")],
        )
        assert len(out) == 0

    def test_line_zero_or_none_means_whole_file(self):
        entries = [
            KnownIntentional(file="a.ts", dimension="security", reason="whole file"),
            KnownIntentional(file="b.ts", line=0, dimension="security", reason="whole file b"),
        ]
        out = filter_known_intentional(
            [
                f(file="a.ts", line=7, dimension="security"),
                f(file="b.ts", line=99, dimension="security"),
            ],
            entries,
        )
        assert len(out) == 0

    def test_does_not_filter_when_file_dimension_or_line_differ(self):
        out = filter_known_intentional(
            [
                f(file="a.ts", line=43, dimension="security"),  # line mismatch
                f(file="a.ts", line=42, dimension="correctness"),  # dim mismatch
                f(file="c.ts", line=42, dimension="security"),  # file mismatch
            ],
            [KnownIntentional(file="a.ts", line=42, dimension="security", reason="intentional")],
        )
        assert len(out) == 3

    def test_returns_findings_unchanged_when_known_empty_or_none(self):
        original = [f(), f()]
        assert filter_known_intentional(original, None) is original
        assert len(filter_known_intentional(original, [])) == 2


class TestMultiRoundConvergence:
    def test_aggregate_tracks_first_seen_round_across_duplicates(self):
        result = aggregate_rounds([
            ReviewRound(round=1, findings=[f(summary="new in r1"), f(summary="dup")]),
            ReviewRound(round=2, findings=[f(summary="DUP"), f(summary="new in r2")]),
        ])
        assert len(result.findings) == 3  # dup removed globally
        assert result.findings_by_round == [2, 1]

    def test_marks_converged_when_last_round_zero_new(self):
        c = compute_convergence([
            ReviewRound(round=1, findings=[f(summary="a"), f(summary="b")]),
            ReviewRound(round=2, findings=[f(summary="A")]),  # duplicate → 0 new
        ])
        assert c.converged is True
        assert c.stopped_reason == "converged"
        assert c.findings_by_round == [2, 0]

    def test_reports_max_rounds_when_cap_hit_without_zero_new_round(self):
        c = compute_convergence([
            ReviewRound(round=1, findings=[f(summary="a")]),
            ReviewRound(round=2, findings=[f(summary="b")]),
        ])
        assert c.converged is False
        assert c.stopped_reason == "max_rounds_reached"

    def test_handles_empty_rounds_list(self):
        c = compute_convergence([])
        assert c.total_rounds == 0
        assert c.converged is False
        assert c.stopped_reason == "no_rounds"

    def test_non_contiguous_round_numbers_are_indexed_by_round_not_position(self):
        # Resumed/failed runs may skip a round number. Findings_by_round must be
        # sized to the highest present round and indexed by the actual round
        # number so nothing is dropped (mirrors the plugin's aggregateRounds).
        c = compute_convergence([
            ReviewRound(round=1, findings=[f(summary="a")]),
            ReviewRound(round=3, findings=[f(summary="b")]),
        ])
        assert c.findings_by_round == [1, 0, 1]
        assert c.converged is False  # last present round (3) produced a new finding


class TestBuildReviewReport:
    def test_assembles_full_report_with_filter_dedupe_sort_summary(self):
        report = build_review_report(
            mode="dry-run",
            goal="Improve quality",
            dimensions=["correctness", "security"],
            max_review_rounds=3,
            rounds=[
                ReviewRound(round=1, findings=[
                    f(dimension="security", severity="critical", summary="sql injection", line=10),
                    f(dimension="correctness", severity="low", summary="typo", line=20),
                    f(dimension="security", severity="high", summary="intentional pattern", line=30),
                ]),
                ReviewRound(round=2, findings=[
                    f(dimension="security", severity="high", summary="intentional pattern", line=30),
                ]),
            ],
            known_intentional=[
                KnownIntentional(file="src/a.ts", line=30, dimension="security", reason="intentional"),
            ],
        )

        assert report.mode == "dry-run"
        assert report.summary.total_findings == 2  # 1 filtered out, 1 deduped
        assert report.summary.critical == 1
        assert report.summary.low == 1
        assert report.convergence.total_rounds == 2
        assert report.convergence.findings_by_round == [2, 0]
        assert report.convergence.converged is True
        assert report.convergence.stopped_reason == "converged"
        assert report.summary.by_dimension["security"] == 1
        assert report.summary.by_dimension["correctness"] == 1
        assert len(report.findings) == 2
        assert [x.severity for x in report.findings] == ["critical", "low"]
        assert report.findings[0].summary == "sql injection"

    def test_zero_rounds_reports_no_rounds_stop_reason(self):
        report = build_review_report(
            mode="dry-run",
            goal="g",
            dimensions=["security"],
            max_review_rounds=3,
            rounds=[],
        )
        assert report.convergence.total_rounds == 0
        assert report.convergence.converged is False
        assert report.convergence.stopped_reason == "no_rounds"


class TestReviewerTasksAndSchema:
    def test_findings_schema_object_rooted_with_required_fields(self):
        schema = findings_schema()
        assert schema["type"] == "object"
        assert schema["required"] == ["findings", "readFiles"]
        assert "readFiles" in schema["properties"]  # type: ignore[index]
        item = schema["properties"]["findings"]["items"]  # type: ignore[index]
        for key in [
            "dimension", "file", "severity", "summary",
            "failure_scenario", "suggested_fix", "is_atomic",
        ]:
            assert key in item["required"], f"missing required {key}"

    def test_reviewer_prompt_injects_coverage_rule_with_inventory(self):
        prompt = reviewer_task_prompt(
            dimension="security", goal="g", scope="full",
            mode="dry-run", output_language="English",
            scope_files=["src/a.py", "src/b.ts"],
        )
        assert "COVERAGE RULE" in prompt
        assert "readFiles" in prompt
        assert "- src/a.py" in prompt
        assert "- src/b.ts" in prompt
        assert "EVERY file" in prompt

    def test_dry_run_prompt_forbids_changes_and_mentions_round_1(self):
        prompt = reviewer_task_prompt(
            dimension="security", goal="g", scope="full",
            mode="dry-run", output_language="English",
        )
        assert "MUST NOT modify" in prompt
        assert "round 1" in prompt
        assert '"security"' in prompt

    def test_later_round_prompt_lists_already_known_findings(self):
        known = [f(summary="known issue")]
        prompt = reviewer_task_prompt(
            dimension="security", goal="g", scope="changed-only",
            mode="dry-run", output_language="Chinese (中文)",
            already_known=known,
        )
        assert "Already-known findings" in prompt
        assert "known issue" in prompt
        assert "NEW issues only" in prompt


class TestBuildReviewPlan:
    def test_maps_every_dimension_to_prompt_and_schema(self):
        plan = build_review_plan(config=base_config(), mode="dry-run", max_review_rounds=4)
        assert plan.mode == "dry-run"
        assert plan.goal == "Improve quality"
        assert [d.id for d in plan.dimensions] == ["correctness", "security"]
        assert plan.max_review_rounds == 4
        for d in plan.dimensions:
            assert f'"{d.id}"' in d.reviewer_prompt
            assert d.findings_schema

    def test_full_scope_batches_inventory_into_dimension_chunk_tasks(self):
        # 2 dimensions × 2 chunks (chunk size 2, 4 files) → 4 reviewer tasks,
        # each owning only its own file inventory via the COVERAGE RULE.
        cfg = base_config()
        cfg.reviewer.scope_chunk_size = 2
        inventory = ["src/a.py", "src/b.py", "src/c.py", "src/d.py"]
        plan = build_review_plan(
            config=cfg, mode="dry-run", max_review_rounds=4, scope_files=inventory
        )
        assert plan.scope == "full"
        assert [d.id for d in plan.dimensions] == [
            "correctness#1", "correctness#2", "security#1", "security#2",
        ]
        inventory_tasks = [d for d in plan.dimensions if d.id.startswith("correctness")]
        assert "- src/a.py" in inventory_tasks[0].reviewer_prompt
        assert "- src/b.py" in inventory_tasks[0].reviewer_prompt
        assert "- src/c.py" in inventory_tasks[1].reviewer_prompt
        assert "- src/d.py" in inventory_tasks[1].reviewer_prompt
        assert "COVERAGE RULE" in inventory_tasks[0].reviewer_prompt

    def test_full_scope_without_inventory_stays_unbatched(self):
        plan = build_review_plan(config=base_config(), mode="dry-run", max_review_rounds=4)
        assert [d.id for d in plan.dimensions] == ["correctness", "security"]
        assert "COVERAGE RULE" not in plan.dimensions[0].reviewer_prompt

    def test_parse_attachments_drops_malformed_entries(self):
        raw = [
            {"path": "screens/a.png", "caption": "layout bug"},
            {"data": "QUJD", "media_type": "image/png"},
            {"path": "   "},  # blank path — dropped
            {"caption": "no source"},  # no path/data — dropped
            "not-a-dict",  # non-dict — dropped
            42,  # non-dict — dropped
        ]
        parsed = parse_attachments(raw)
        assert len(parsed) == 2
        assert parsed[0].path == "screens/a.png"
        assert parsed[0].caption == "layout bug"
        assert parsed[1].data == "QUJD"
        assert parsed[1].media_type == "image/png"

    def test_parse_attachments_empty_input(self):
        assert parse_attachments(None) == []
        assert parse_attachments([]) == []
        assert parse_attachments("nope") == []

    def test_reviewer_prompt_injects_attachment_clause_when_provided(self):
        prompt = reviewer_task_prompt(
            dimension="security",
            goal="Improve quality",
            scope="full",
            mode="dry-run",
            output_language="English",
            attachments=[
                ReviewAttachment(path="screens/hits.png", caption="layout bug"),
                ReviewAttachment(data="QUJD", media_type="image/png", caption="repro"),
            ],
        )
        assert "ATTACHED VISUAL CONTEXT" in prompt
        assert "screens/hits.png (layout bug)" in prompt
        assert "inline image/png image (repro)" in prompt
        assert "image_to_text" in prompt

    def test_reviewer_prompt_omits_attachment_clause_by_default(self):
        prompt = reviewer_task_prompt(
            dimension="security",
            goal="Improve quality",
            scope="full",
            mode="dry-run",
            output_language="English",
        )
        assert "ATTACHED VISUAL CONTEXT" not in prompt

    def test_plan_threads_attachments_into_every_reviewer_prompt(self):
        plan = build_review_plan(
            config=base_config(),
            mode="dry-run",
            max_review_rounds=2,
            raw_attachments=[{"path": "mock/onboarding.png", "caption": "onboarding"}],
        )
        assert len(plan.attachments) == 1
        assert plan.attachments[0].path == "mock/onboarding.png"
        for dimension in plan.dimensions:
            assert "mock/onboarding.png (onboarding)" in dimension.reviewer_prompt

    def test_plan_without_attachments_serializes_empty_list(self):
        plan = build_review_plan(config=base_config(), mode="dry-run", max_review_rounds=2)
        assert plan.attachments == []
        serialized = plan_to_dict(plan)
        assert serialized["attachments"] == []
