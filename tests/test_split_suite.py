"""Tests for `scripts/split_suite.py`, the thing that decides what CI actually runs.

WHY THIS FILE EXISTS. The harness's CI used to run the whole suite in one step, and that step had been
dying without ever ending — six runs of it, the last measured on run 40190097615, where the job's own
log endpoint answered HTTP 404 while a *completed* sibling job's log fetched 31,956 bytes. A job that
never ends has no log anywhere, so the fix is a shape in which the units end: buckets, each its own
job.

Splitting creates one failure mode that the monolith could not have: **a plan that drops a test is a
green CI over a subset.** These tests are the reason that is not possible quietly — the script refuses
unless pytest's own printed total equals the node ids it parsed, refuses unless every module lands in
exactly one bucket, refuses an empty bucket, and refuses a collection that does not match the number a
CI step was told to expect.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "split_suite.py"

# Real `-q` lines, taken from `pytest --collect-only -q` in this repo. The shapes that matter are all
# here: a test in a subdirectory, a test file sitting directly in `tests/`, a class method, and a
# parametrised id whose brackets follow the `::`.
SAMPLE_IDS = (
    "tests/test_api/test_client.py::test_anthropic_client_uses_api_key",
    "tests/test_api/test_client.py::TestResponses::test_streaming",
    "tests/test_iterate/test_evidence.py::test_pack_shape[dimension=cost]",
    "tests/test_ci_workflow_shape.py::test_every_workflow_parses_without_duplicate_keys",
    "tests/test_platforms.py::test_posix_paths",
    "tests/test_web/test_routes.py::test_health",
)

EXIT_USAGE = 2


def _collect_text(ids: tuple[str, ...] | list[str], total: int | None = None) -> str:
    """A faithful `-q --collect-only` body: one id per line, blank, then the summary pytest prints."""
    count = len(ids) if total is None else total
    return "\n".join(ids) + "\n\n" + f"{count} tests collected in 1.00s\n"


def _run(args: list[object], stdin: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *[str(arg) for arg in args]],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )


def _plan(ids: list[str], buckets: int) -> list[dict[str, object]]:
    """The JSON the script prints on its last stdout line — what CI feeds to the matrix."""
    result = _run(["--buckets", str(buckets)], stdin=_collect_text(ids))
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.splitlines()[-1])


def _all_test_modules() -> list[str]:
    return sorted(
        str(path.relative_to(REPO))
        for path in (REPO / "tests").rglob("test_*.py")
        if "__pycache__" not in path.parts
    )


def test_the_plan_over_this_repo_s_test_modules_is_an_exact_partition() -> None:
    """Every module in the tree is in exactly one bucket — checked against the real file set.

    This is the assertion that makes the split safe. It reads the repository's own test modules rather
    than a hand-written list, so a file added tomorrow is in the plan without anyone remembering.
    """
    modules = _all_test_modules()
    assert len(modules) > 40, f"only {len(modules)} module(s) found — the fixture walk is broken"
    ids = [f"{module}::test_probe" for module in modules]
    plan = _plan(ids, 6)
    assert len(plan) == 6

    paths: list[str] = []
    for bucket in plan:
        assert bucket["paths"], f"bucket {bucket['id']} is empty"
        paths.extend(str(p) for p in str(bucket["paths"]).split())
    assert sorted(paths) == modules, "buckets must cover every module exactly once"
    assert len(paths) == len(set(paths)), "a module appears in two buckets"
    assert sum(int(bucket["count"]) for bucket in plan) == len(ids)


def test_bucketing_never_splits_a_module_and_is_deterministic() -> None:
    modules = _all_test_modules()
    ids = [f"{module}::test_probe" for module in modules] + [
        f"{module}::test_second" for module in modules
    ]
    first = _plan(ids, 5)
    second = _plan(ids, 5)
    assert first == second, "two plans over one collection must be byte-identical, or CI is unreproducible"
    for bucket in first:
        members = str(bucket["paths"]).split()
        expected = sum(1 for node_id in ids if node_id.split("::")[0] in members)
        assert int(bucket["count"]) == expected, (
            f"bucket {bucket['id']} counts {bucket['count']} but its paths hold {expected}"
        )


def test_it_refuses_a_collection_it_cannot_count() -> None:
    """The count pytest prints is the second implementation; a shortfall is a silent subset."""
    result = _run(["--buckets", 2], stdin=_collect_text(list(SAMPLE_IDS), total=len(SAMPLE_IDS) + 3))
    assert result.returncode == EXIT_USAGE
    assert "SPLIT_E_COUNT_MISMATCH" in result.stderr, result.stderr


def test_it_refuses_an_empty_collection_rather_than_planning_nothing() -> None:
    result = _run(["--buckets", 2], stdin="\n\n0 tests collected in 0.01s\n")
    assert result.returncode == EXIT_USAGE
    assert "SPLIT_E_NO_TESTS" in result.stderr, result.stderr


def test_it_refuses_a_summary_line_that_is_not_there() -> None:
    """Without pytest's own total there is nothing to check the parsed ids against."""
    result = _run(["--buckets", 2], stdin="\n".join(SAMPLE_IDS) + "\n")
    assert result.returncode == EXIT_USAGE
    assert "SPLIT_E_NO_COUNT" in result.stderr, result.stderr


def test_it_refuses_more_buckets_than_modules_because_an_empty_bucket_is_green_over_nothing() -> None:
    ids = ["tests/test_a/test_one.py::test_x", "tests/test_b/test_two.py::test_y"]
    result = _run(["--buckets", 3], stdin=_collect_text(ids))
    assert result.returncode == EXIT_USAGE
    assert "SPLIT_E_TOO_MANY_BUCKETS" in result.stderr, result.stderr


def test_expect_total_is_the_ci_step_that_checks_its_own_bucket() -> None:
    """`--expect-total` is how a bucket notices it is not the bucket the plan promised."""
    text = _collect_text(list(SAMPLE_IDS))
    ok = _run(["--buckets", 1, "--expect-total", str(len(SAMPLE_IDS))], stdin=text)
    assert ok.returncode == 0, ok.stderr

    short = _run(["--buckets", 1, "--expect-total", str(len(SAMPLE_IDS) + 40)], stdin=text)
    assert short.returncode == EXIT_USAGE
    assert "SPLIT_E_PLAN_MISMATCH" in short.stderr, short.stderr


def test_a_module_directly_in_tests_is_not_grouped_with_a_directory() -> None:
    """`tests/test_platforms.py` must be addressable on its own; the `tests/<dir>` shape is not universal.

    Control: grouping by first directory segment would name this module `tests` — a path that would
    drag the whole tree into one bucket.
    """
    ids = ["tests/test_platforms.py::test_posix_paths", "tests/test_web/test_routes.py::test_health"]
    plan = _plan(ids, 2)
    paths = {p for bucket in plan for p in str(bucket["paths"]).split()}
    assert paths == {node_id.split("::")[0] for node_id in ids}, paths


def test_the_packed_buckets_are_even_on_the_real_weight_distribution() -> None:
    """A bucket holding half the suite is the monolith with a number on it.

    The weights come from this repository's own files (a `def test_` count per module), because the
    skew is the thing being bounded: grouping by directory measured 1,355 of 3,099 tests in
    `tests/test_iterate` alone, which is 44% of the suite in one job. Bounding the spread is the
    difference between a split and a rename.
    """
    weights = {module: _test_defs_in(module) for module in _all_test_modules()}
    ids = [
        f"{module}::test_{index}"
        for module, count in weights.items()
        for index in range(max(count, 1))
    ]
    total = len(ids)
    plan = _plan(ids, 6)
    counts = [int(bucket["count"]) for bucket in plan]
    assert max(counts) <= total * 0.4, (
        f"the largest of {len(plan)} buckets holds {max(counts)} of {total} tests (44% is what "
        "directory-level grouping measured; it is not a bucket)"
    )
    assert sum(counts) == total


def _test_defs_in(module: str) -> int:
    text = (REPO / module).read_text(encoding="utf-8", errors="replace")
    return sum(1 for line in text.splitlines() if line.startswith(("def test_", "    def test_")))


def test_the_script_exists_and_is_the_one_ci_calls() -> None:
    """A workflow that calls a missing script is red in the step that calls it, not here — so ask here."""
    assert SCRIPT.is_file()
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "scripts/split_suite.py" in ci, "ci.yml no longer uses the splitter this file guards"
