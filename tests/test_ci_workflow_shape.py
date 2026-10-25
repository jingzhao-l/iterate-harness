"""The harness's own CI files, checked structurally rather than by reading them.

WHY THIS FILE EXISTS. A comment edit to `ci.yml` this session left **two `run:` keys** in one step.
PyYAML does not error on a duplicate mapping key with the default loader — it keeps the last one —
so that step would have run the old single-line command while the new `timeout`-wrapped block above
it looked like the shipped behaviour. Nothing in the suite ever looked at the workflow files, and a
gate that cannot see its own wiring is the failure this repo keeps getting fined for: the same trap
showed up once already as "an enumeration in prose that nobody re-measures" and once as
"`--log-failed` reported nothing because the step's status came from `tail`".

So this file asks two questions of every workflow in `.github/workflows/`:
  1. does any mapping carry duplicate keys (rejected by a loader that refuses them, which the default
     loader silently does not), and
  2. does the step that runs the suite still carry the parts the comments promise — the hard
     `timeout` wrapper, `-v`, and the `faulthandler` options — so a future edit that drops one is a
     red here instead of a 45-minute runner death with no log.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO / ".github" / "workflows"

SUITE_STEP = "Run test suite"

# Every multi-line `run:` body must open with these two settings. `bash -e` is GitHub's default, but
# without `pipefail` a step whose last command is a filter reports the *filter's* status — which is
# how a gate ends up green over the failure it just printed.
PIPEFAIL_REQUIRED_IN = ("ci.yml",)


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


def test_the_suite_step_keeps_the_probe_that_makes_a_death_findable() -> None:
    """Three things that turned an unobservable 45-minute death into a named one.

    Each is asserted separately, with its own message: they were added to answer three different
    failures (an unflushed log channel, an import-time block, an endless wait), and an edit that
    quietly drops one has to say which ruling it is withdrawing.
    """
    steps = _steps_of(_load("ci.yml")["jobs"]["python-tests"])
    suite = [step for step in steps if step.get("name") == SUITE_STEP]
    assert len(suite) == 1, f"expected exactly one {SUITE_STEP!r} step, found {len(suite)}"
    run = suite[0].get("run", "")

    assert "timeout " in run, (
        "the step must hard-stop pytest: a process that exits on its own uploads its log, and the "
        "whole investigation died because the runner was killed and took the log with it"
    )
    assert "> pytest-ci.log" in run and "cat pytest-ci.log" in run, (
        "pytest's output must go to a file that is printed afterwards: a detached child spawned with "
        "`start_new_session=True` inherits the step's stdout and never EOFs it, which is why the job "
        "ignored even its own SIGKILL ceiling and why no log archive ever contained that job"
    )
    assert "-v" in run, (
        "one line per test is what let us rule out import-time and collection blocking; without it "
        "the log is silent by default and the death is unattributable"
    )
    assert "faulthandler_timeout" in run and "faulthandler_exit_on_timeout" in run, (
        "faulthandler is the only thing that names a test stuck mid-call — it did not fire in the "
        "runs measured so far, which is itself evidence, and dropping the flag loses that"
    )


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


def test_the_suite_log_leaves_the_machine() -> None:
    """The one instrument that survives the failure mode: an artifact uploaded with `if: always()`.

    Every other observation this job can make — log lines, annotations, exit codes — lives in the
    runner's memory, and the measured failure erases all of them (a cancelled run's log archive
    contained only the three successful jobs). An artifact is the single channel whose output exists
    after the runner does, so it must not be dropped quietly.
    """
    steps = _steps_of(_load("ci.yml")["jobs"]["python-tests"])
    uploads = [
        step
        for step in steps
        if "upload-artifact" in str(step.get("uses", ""))
        and "pytest-ci.log" in str((step.get("with") or {}).get("path", ""))
    ]
    assert len(uploads) == 1, f"expected exactly one artifact upload of the suite log, found {len(uploads)}"
    step = uploads[0]
    assert str(step.get("if", "")).startswith("always("), (
        "the upload must run on the failure paths — a ceiling, a cancellation, a dead runner — "
        "because those are precisely the runs whose log nobody can otherwise read"
    )


def test_the_ceiling_has_its_own_control_step_before_the_suite_runs() -> None:
    """A hard stop is only an instrument if something proves the stop works.

    This machine cannot: `timeout` and `gtimeout` are both absent here, so the proof has to happen on
    the runner — which is why the control is a 5-second step that echoes its own exit code and
    elapsed wall time. If it ever goes red, every inference of the form "the ceiling fired / did not
    fire, therefore the suite hangs" is void, and that is exactly the class of claim this file's
    sibling tests were written to stop making.
    """
    steps = _steps_of(_load("ci.yml")["jobs"]["python-tests"])
    names = [str(step.get("name")) for step in steps]
    control = [i for i, name in enumerate(names) if name.startswith("Prove the ceiling is enforced")]
    assert len(control) == 1, f"expected exactly one ceiling-control step, found {control}"
    suite = [i for i, name in enumerate(names) if name == SUITE_STEP]
    assert len(suite) == 1, f"expected exactly one {SUITE_STEP!r} step"
    assert control[0] < suite[0], "the control must run before the thing it certifies"
    body = steps[control[0]]["run"]
    assert "timeout 5 sleep 30" in body, "the control must be independent of the suite it certifies"
    assert "ceiling_control_exit=$rc" in body, "it has to report the code, not just pass or fail"


def test_the_python_job_declares_a_ceiling_and_both_supported_python_legs() -> None:
    job = _load("ci.yml")["jobs"]["python-tests"]
    assert job.get("timeout-minutes") == 90, (
        "the ceiling exists so a wedged job ends with a log rather than as a runner that 'lost "
        "communication'; measured: the 45-minute cuts were NOT this setting firing, so it is a "
        "backstop and not the diagnosis"
    )
    versions = job["strategy"]["matrix"]["python-version"]
    assert versions == ["3.10", "3.11"], (
        f"the floor is `requires-python >= 3.10`; the legs are {versions} and must stay both, because "
        "a 3.11-only API mistake in a *test* file is a collection-time ImportError on 3.10 and the "
        "file's cases vanish silently"
    )


def test_collection_has_its_own_step_because_faulthandler_cannot_see_it() -> None:
    steps = _steps_of(_load("ci.yml")["jobs"]["python-tests"])
    collect = [step for step in steps if str(step.get("name", "")).startswith("Collect the suite")]
    assert len(collect) == 1, f"expected exactly one collect step, found {len(collect)}"
    assert "--collect-only" in collect[0].get("run", "")


@pytest.mark.parametrize("name", _workflow_names())
def test_a_workflow_file_is_readable_at_all(name: str) -> None:
    """The precondition for every assertion above: a guard that cannot read the file is not a guard."""
    assert (WORKFLOWS / name).is_file(), f"{name} moved; the structural guards are now blind"
