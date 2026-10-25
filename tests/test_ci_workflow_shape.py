"""The harness's own CI files, checked structurally rather than by reading them.

WHY THIS FILE EXISTS. Two defects this repo has actually paid for, both invisible to a test suite that
only runs *inside* CI:

1. A comment edit to `ci.yml` this session left **two `run:` keys** in one step. PyYAML does not error
   on a duplicate mapping key with the default loader — it keeps the last one — so that step would
   have run the old single-line command while the new `timeout`-wrapped block above it looked like the
   shipped behaviour.
2. The suite ran in one step, and that step had been dying without ever ending. Measured on run
   40190097615: the 5-second ceiling control passed (rc=124, 11:44:23 → 11:44:28), `Run test suite`
   was still `in_progress` until the ~45-minute cut at 12:29 — 25 minutes past its own 1,200 s ceiling
   plus `--kill-after=60` — the run's `artifacts` count was **0**, and the job's log endpoint answered
   **HTTP 404** while a *completed* sibling's log fetched 31,956 bytes. A job's log only exists once
   the job ends, so a step that never ends leaves no log, no annotation body and no artifact: an
   `if: always()` upload is itself a step, gated behind the one that never finishes.

   That is why the suite is now split into buckets that can each end — and why the guard below has to
   look at the *shape* of the split: a rename on one side of a cross-job hand-off, a bucket step that
   stops checking its own count, or a workflow that calls a script that no longer exists are all red
   states that CI would report as green.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO / ".github" / "workflows"

SUITE_STEP = "Run test suite"
PLAN_JOB = "suite-plan"
TEST_JOB = "python-tests"
FRONTEND_ARTIFACT = "web-dist-tarball"

# Every multi-line `run:` body must open with these two settings. `bash -e` is GitHub's default, but
# without `pipefail` a step whose last command is a filter reports the *filter's* status — which is
# how a gate ends up green over the failure it just printed.
PIPEFAIL_REQUIRED_IN = ("ci.yml",)

SCRIPT_REF = re.compile(r"scripts/([A-Za-z0-9_.\-]+\.(?:py|mjs|sh))")


class _DuplicateKeyLoader(yaml.SafeLoader):
    """A loader that refuses duplicate keys, because `SafeLoader` silently keeps the last one."""


def _construct_no_duplicates(loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False) -> dict:
    keys = [loader.construct_object(key, deep=deep) for key, _ in node.value]
    doubled = sorted({key for key in keys if keys.count(key) > 1}, key=str)
    if doubled:
        raise ValueError(
            f"duplicate mapping key(s) {doubled} at line {node.start_mark.line + 1} of "
            f"{getattr(loader, 'gp_source', '?')}"
        )
    return yaml.SafeLoader.construct_mapping(loader, node, deep)


_DuplicateKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_no_duplicates
)


def _load(name: str) -> dict:
    """Parse one workflow with the duplicate-refusing loader, and name the file in the error."""
    loader = _DuplicateKeyLoader((WORKFLOWS / name).read_text(encoding="utf-8"))
    loader.gp_source = name  # type: ignore[attr-defined]
    data: dict = loader.get_single_data()
    return data


def _workflow_names() -> list[str]:
    return sorted(path.name for path in WORKFLOWS.glob("*.yml"))


def _steps_of(job: dict) -> list[dict]:
    return [step for step in job["steps"] if isinstance(step, dict)]


def _step_body(job: dict, prefix: str) -> str:
    """The `run:` body of the one step whose name starts with `prefix` — red if that is ambiguous."""
    matches = [step for step in _steps_of(job) if str(step.get("name", "")).startswith(prefix)]
    assert len(matches) == 1, f"expected exactly one step starting with {prefix!r}, found {len(matches)}"
    return str(matches[0].get("run", ""))


def test_every_workflow_parses_without_duplicate_keys() -> None:
    """The subject of this file: a duplicated key is not a syntax error to PyYAML, so make it one.

    Control: re-inserting the duplicate `run:` into `ci.yml` reddens exactly this test, with the
    offending key and line in its message.
    """
    names = _workflow_names()
    assert names, f"no workflows found under {WORKFLOWS} — the guard above would then be blind"
    for name in names:
        data = _load(name)
        assert isinstance(data, dict) and "jobs" in data, f"{name} has no jobs to check"


def test_a_duplicated_key_in_a_step_is_not_allowed_to_hide_behind_the_last_one() -> None:
    """The specific shape that bit us: one step, two `run:` keys, PyYAML keeps the last.

    Judged on the **composed node tree**, not on the parsed dict: by the time PyYAML has built a
    mapping the duplicate is already gone, so `set(keys)` on the result cannot see it. Composing
    first keeps every key node, which is the only place this defect is still visible.
    """
    for name in _workflow_names():
        node = yaml.compose((WORKFLOWS / name).read_text(encoding="utf-8"))
        duplicates = _composed_duplicate_keys(node)
        assert not duplicates, f"{name} maps the same key twice: {duplicates}"


def _composed_duplicate_keys(node: yaml.Node | None) -> list[tuple[str, int]]:
    """Every mapping in the composed tree whose keys repeat, with the line it repeats at.

    `MappingNode.value` is a list of **(key, value) pairs** — a previous draft of this helper sliced
    it as if it were a flat list of nodes, found no repeats, and passed on a file that had one. That
    is the shape this function exists to catch, so it got its own control (M-A in the battery).
    """
    found: list[tuple[str, int]] = []
    if isinstance(node, yaml.MappingNode):
        keys = [str(key.value) if hasattr(key, "value") else str(key) for key, _ in node.value]
        found.extend((key, node.start_mark.line + 1) for key in set(keys) if keys.count(key) > 1)
        for key, value in node.value:
            found.extend(_composed_duplicate_keys(key))
            found.extend(_composed_duplicate_keys(value))
    elif isinstance(node, yaml.SequenceNode):
        for child in node.value:
            found.extend(_composed_duplicate_keys(child))
    return found


def test_multi_line_run_bodies_declare_pipefail() -> None:
    """A `run:` block whose last command is a filter reports the filter, not the suite."""
    for name in PIPEFAIL_REQUIRED_IN:
        for job_name, job in (_load(name).get("jobs") or {}).items():
            for step in _steps_of(job):
                run = step.get("run", "")
                if not isinstance(run, str) or "\n" not in run:
                    continue
                first = next((line for line in run.splitlines() if line.strip()), "")
                assert "set -" in first and "pipefail" in first, (
                    f"{name} / {job_name} / {step.get('name')!r} opens with {first!r}; a multi-line "
                    "body needs `set -euo pipefail` so a failing command inside it fails the step"
                )


def test_every_script_a_workflow_calls_still_exists() -> None:
    """A step that calls a deleted script is red only when CI runs it — and only in that one step.

    This is how `check-workflows`-style rules earn their place in this repo: the workflow references
    `scripts/split_suite.py` and `scripts/check_kernel_fixtures.py` by path, and a rename on the script
    side would otherwise be a CI failure with no local warning.
    """
    missing: list[str] = []
    for name in _workflow_names():
        text = (WORKFLOWS / name).read_text(encoding="utf-8")
        for script in sorted(set(SCRIPT_REF.findall(text))):
            if not (REPO / "scripts" / script).is_file():
                missing.append(f"{name}: scripts/{script}")
    assert not missing, "workflow step(s) call script(s) that are not in the repo: " + ", ".join(missing)


def _commands_of(run: str) -> str:
    """A step body without its comment lines.

    A comment must not be able to satisfy an assertion about the code: the first draft of the probe
    test looked for `setsid ` in the whole body, and the body's own prose mentions setsid three times,
    so replacing the command with a plain `timeout` pipeline kept the test green.
    """
    return "\n".join(
        line for line in run.splitlines() if not line.strip().startswith("#")
    )


def test_the_suite_step_keeps_the_probe_that_makes_a_death_findable() -> None:
    """The parts that turn "the step never ends" into a reading instead of a lost run.

    Each is asserted separately, with its own message: they answer different failures (an unflushed
    channel, an import-time block, a wait that outlives every signal the step can send), and an edit
    that quietly drops one has to say which ruling it is withdrawing.
    """
    steps = _steps_of(_load("ci.yml")["jobs"][TEST_JOB])
    suite = [step for step in steps if step.get("name") == SUITE_STEP]
    assert len(suite) == 1, f"expected exactly one {SUITE_STEP!r} step, found {len(suite)}"
    run = _commands_of(str(suite[0].get("run", "")))

    assert re.search(r"(?m)^\s*setsid sh -c ", run), (
        "pytest must be launched detached, as the command on its own line: this suite spawns children"
        " with `start_new_session=True`, and a step that waits on them — or on a pipe they hold —"
        " cannot be ended from the inside. Anchored to a line start because the step's own refusal"
        " message mentions setsid, and prose must not satisfy an assertion about the launcher."
    )
    assert '> pytest-ci.log 2>&1' in run and "cat pytest-ci.log" in run, (
        "output goes to a file, never to a pipe someone else can hold open, and is printed by the step"
        " itself so a finished bucket always has a log"
    )
    assert 'kill -9 -- -"$suite_pid"' in run, (
        "the kill must target the whole session, not the process `timeout` would have signalled"
    )
    assert '"$PYTEST_HARD_STOP"' in run, (
        "the budget is the step's own bound: without it the step waits for a process that may never"
        " end, and a job that never ends has no log at all"
    )
    snapshots = [line.strip() for line in run.splitlines() if line.strip().startswith("ps -eo ")]
    assert len(snapshots) == 2, (
        f"expected one `ps` snapshot before the kill and one after, found {len(snapshots)}: they are"
        " what names a survivor instead of leaving 'something survived the signal' as an inference"
    )
    assert all(line.endswith("|| true") for line in snapshots), (
        f"an instrument must not become the failure: {snapshots}"
    )
    assert all("--" not in line for line in snapshots), (
        f"measured on the runner (run 40199977015): this image's ps answers a long option like"
        f" `--ww` with 'error: unknown gnu long option', which `set -e` then turned into ten red jobs"
        f" that had each printed their passing summary — {snapshots}"
    )
    assert "pytest_exit=$rc stopped=$stopped" in run, (
        "the step has to distinguish 'pytest finished with a failure' from 'the budget ran out',"
        " because those two readings imply opposite next actions"
    )
    assert "-v" in run, (
        "one line per test is what let us rule out import-time and collection blocking; without it "
        "the log is silent by default and the death is unattributable"
    )
    assert "faulthandler_timeout" in run and "faulthandler_exit_on_timeout" in run, (
        "faulthandler is the only thing that names a test stuck mid-call — a complete `-v` list with "
        "no dump is itself a reading: the wait is then in a fixture or in pytest's exit, not in a test"
    )
    assert "PYTHONUNBUFFERED" in str(suite[0].get("env", "")), (
        "python block-buffers when stdout is not a TTY (measured: the first flush costs ~68 finished "
        "tests), and an unbuffered file is what makes 'where did the list stop' readable"
    )


def test_the_suite_log_leaves_the_machine_when_the_bucket_ends() -> None:
    """The artifact upload, with its measured limit written into the reason it exists.

    `if: always()` covers a ceiling, a cancellation, and a failing bucket. It cannot cover a wedged
    one: run 40190097615 answered `artifacts: total_count = 0`, because the upload is a step and the
    hanging step never finished. That limit is why the buckets are short, not why this step is optional.
    """
    steps = _steps_of(_load("ci.yml")["jobs"][TEST_JOB])
    uploads = [
        step
        for step in steps
        if "upload-artifact" in str(step.get("uses", ""))
        and "pytest-ci.log" in str((step.get("with") or {}).get("path", ""))
    ]
    assert len(uploads) == 1, f"expected exactly one artifact upload of the suite log, found {len(uploads)}"
    step = uploads[0]
    assert str(step.get("if", "")).startswith("always("), (
        "the upload must run on the failure paths — a ceiling and a cancellation are exactly the "
        "runs whose log nobody can otherwise read"
    )
    assert "${{ matrix.bucket.id }}" in str((step.get("with") or {}).get("name", "")), (
        "one artifact per bucket: a shared name would make twelve jobs overwrite each other's log"
    )


def test_the_ceiling_has_its_own_control_step_before_the_suite_runs() -> None:
    """A bound is only an instrument if something proves the bound works, on the same machine.

    This machine cannot: `timeout` and `gtimeout` are both absent here, so the proof has to happen on
    the runner. It has already done its job once: rc=124 in 5 s on run 40190097615, which is the
    measurement that turned "the ceiling did not fire" from a guess about the flag into a fact about
    the process — and which is why the suite step no longer relies on that flag at all.
    """
    steps = _steps_of(_load("ci.yml")["jobs"][TEST_JOB])
    names = [str(step.get("name")) for step in steps]
    control = [i for i, name in enumerate(names) if name.startswith("Prove the instruments")]
    assert len(control) == 1, f"expected exactly one instrument-control step, found {control}"
    suite = [i for i, name in enumerate(names) if name == SUITE_STEP]
    assert len(suite) == 1, f"expected exactly one {SUITE_STEP!r} step"
    assert control[0] < suite[0], "the control must run before the thing it certifies"
    body = _commands_of(steps[control[0]]["run"])
    assert "timeout 5 sleep 30" in body, "the control must be independent of the suite it certifies"
    assert "ceiling_control_exit=$rc" in body, "it has to report the code, not just pass or fail"
    assert "setsid-probe.pid" in body, (
        "the control must certify the same detached-process shape the suite step uses: a pid file "
        "written by the session itself, not a `$!` that assumes `setsid` never forks"
    )
    assert 'ps -o sess= -p "$probe"' in body, (
        "`kill -9 -- -PID` aimed at a process that is not a session leader signals the wrong group —"
        " possibly this job's own. The control proves leadership before the suite step relies on it."
    )
    assert "poll_killed_after=" in body, "the bounded poll has to report its own elapsed time"


def test_no_run_body_carries_a_backtick_that_is_not_a_comment() -> None:
    """Inside double quotes a backtick is a command substitution, and the message runs it.

    Measured on this very file: a refusal message written as
    ``echo "Without leadership, `kill -9 -- -PID` would …"`` executed `kill`, then `setsid` with no
    arguments, then `ps` — and printed whatever came out instead of the words that were meant to
    explain the failure. `$(…)` is the only command substitution allowed in a step body.
    """
    for name in _workflow_names():
        for job_name, job in (_load(name).get("jobs") or {}).items():
            for step in _steps_of(job):
                run = step.get("run", "")
                if not isinstance(run, str):
                    continue
                for line in run.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("#") or "`" not in stripped:
                        continue
                    raise AssertionError(
                        f"{name} / {job_name} / {step.get('name')!r}: a backtick in {stripped!r} "
                        "is a command substitution when it is inside double quotes — use $(…)"
                    )


def test_every_run_body_is_syntactically_valid_bash() -> None:
    """A YAML typo in a `run:` block is a red CI step; a *bash* typo can be a silently-green one.

    The suite step is now a real program — a detached launch, a poll, a group kill — so it gets parsed
    before it gets run. `bash -n` cannot be checked by behaviour on this machine for the same reason
    the ceiling control can't (no `setsid` here), but syntax is portable.
    """
    for name in _workflow_names():
        for job_name, job in (_load(name).get("jobs") or {}).items():
            for step in _steps_of(job):
                run = step.get("run", "")
                if not isinstance(run, str) or "\n" not in run:
                    continue
                with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as handle:
                    handle.write(run)
                    path = handle.name
                try:
                    parsed = subprocess.run(
                        ["bash", "-n", path], capture_output=True, text=True, check=False
                    )
                finally:
                    Path(path).unlink()
                assert parsed.returncode == 0, (
                    f"{name} / {job_name} / {step.get('name')!r} is not valid bash:"
                    f" {parsed.stderr.strip()}"
                )


def test_the_test_job_is_a_matrix_of_buckets_that_cannot_start_without_a_plan() -> None:
    """The split's own wiring: without `needs` and `fail-fast: false` this is a slower monolith.

    `needs: suite-plan` is what stops the matrix from expanding an empty list into a job that runs no
    tests; `fail-fast: false` is what stops one wedged bucket from cancelling the eleven that would
    have produced the evidence.
    """
    job = _load("ci.yml")["jobs"][TEST_JOB]
    assert job.get("needs") == PLAN_JOB, "the bucket jobs must consume the plan, not a hand-written list"
    assert job.get("timeout-minutes") == 30, (
        "the ceiling exists so a bucket ENDS — a job that ends is the precondition for its log "
        "existing (measured: an in-progress job's log endpoint answers HTTP 404); 30 minutes is room "
        "for 519 of 3,112 tests, which take 141.89 s together here, not a guess to be nudged up"
    )
    matrix = job["strategy"]["matrix"]
    versions = matrix["python-version"]
    assert versions == ["3.10", "3.11"], (
        f"the floor is `requires-python >= 3.10`; the legs are {versions} and must stay both, because "
        "a 3.11-only API mistake in a *test* file is a collection-time ImportError on 3.10 and the "
        "file's cases vanish silently"
    )
    assert "fromJson(needs.suite-plan.outputs.buckets)" in str(matrix["bucket"]), (
        "the bucket list must come from the plan step; a hand-written list is a second source of "
        "truth that drifts the first time a test file is added"
    )
    assert job["strategy"].get("fail-fast") is False


def test_a_bucket_checks_its_own_count_against_the_plan() -> None:
    """The anti-subset gate. A bucket whose paths resolve to fewer tests is a green job over a subset.

    Measured equivalence, which is what makes this gate worth its runtime: the monolith on this tree
    is `3106 passed, 6 skipped in 141.89s` and the six buckets are 519/519/519/519/518/518 = 3,112
    with the same 3,106 passed and the same 6 skipped, each bucket's own re-collected count matching
    its plan row (six rc=0).
    """
    job = _load("ci.yml")["jobs"][TEST_JOB]
    env_step = [
        step
        for step in _steps_of(job)
        if str(step.get("name", "")).startswith("Collect this bucket")
    ]
    assert len(env_step) == 1, "expected exactly one per-bucket collect step"
    env = env_step[0].get("env", {})
    assert env.get("BUCKET_COUNT") == "${{ matrix.bucket.count }}", env
    assert env.get("BUCKET_PATHS") == "${{ matrix.bucket.paths }}", env
    assert '--expect-total "$BUCKET_COUNT"' in str(env_step[0].get("run", "")), (
        "the check must be the splitter's own refusal, not a grep that could pass on an empty file"
    )


def test_collection_has_its_own_step_in_the_plan_because_faulthandler_cannot_see_it() -> None:
    """`faulthandler_timeout` only dumps while a **test** is running, so collection needs its own step.

    It earned that: on run 40179763746 `Collect the suite` finished in **13 seconds**, which is what
    ruled out import-time and collection blocking and left the run phase as the only suspect.
    """
    workflows = _load("ci.yml")["jobs"]
    plan_steps = _steps_of(workflows[PLAN_JOB])
    collect = [
        step for step in plan_steps if str(step.get("name", "")).startswith("Collect the suite")
    ]
    assert len(collect) == 1, "the plan job needs exactly one whole-suite collect step"
    assert "--collect-only" in str(collect[0].get("run", ""))
    assert "import-time block" in str(collect[0]["name"]), (
        "the step's name is part of the instrument: it says what a red here would mean"
    )


def test_the_frontend_build_reaches_the_bucket_jobs_by_the_same_name_it_left_with() -> None:
    """A cross-job hand-off has two ends; renaming one of them is invisible to every other guard.

    `uv sync` needs `frontend/web/dist`, which `npm run build` produces. Building it in twelve bucket
    jobs would put twelve `npm ci` runs between this suite and a green answer, so it is built once,
    tarred, and restored — and the artifact name must match on both sides, in this test's terms rather
    than in CI's.
    """
    jobs = _load("ci.yml")["jobs"]
    uploaded = [
        str((step.get("with") or {}).get("name", ""))
        for step in _steps_of(jobs[PLAN_JOB])
        if "upload-artifact" in str(step.get("uses", ""))
    ]
    assert FRONTEND_ARTIFACT in uploaded, f"the plan job publishes {uploaded}"
    downloaded = [
        str((step.get("with") or {}).get("name", ""))
        for step in _steps_of(jobs[TEST_JOB])
        if "download-artifact" in str(step.get("uses", ""))
    ]
    assert downloaded == [FRONTEND_ARTIFACT], f"the bucket job restores {downloaded}"
    assert "tar -xzf" in _step_body(jobs[TEST_JOB], "Unpack it"), (
        "the layout is a fact about the archive, not about an action version's path handling"
    )


@pytest.mark.parametrize("name", _workflow_names())
def test_a_workflow_file_is_readable_at_all(name: str) -> None:
    """The precondition for every assertion above: a guard that cannot read the file is not a guard."""
    assert (WORKFLOWS / name).is_file(), f"{name} moved; the structural guards are now blind"
