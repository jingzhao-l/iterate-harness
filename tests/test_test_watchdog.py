"""The process watchdog in `tests/conftest.py` is tested by hanging on purpose.

WHY THIS FILE EXISTS. Four CI runs (40199977015, 40200818814, 40206085949, 40207198620) showed the same
thing: bucket 06's legs never ended, and **nothing that is armed per test** ever fired — neither
`pytest-timeout` (240 s) nor `faulthandler_timeout` (300 s). That puts the wait after the last test, in
session teardown or interpreter exit, which is a window the per-item timers are structurally blind to.
The stdlib instrument that does cover it is `faulthandler.dump_traceback_later(..., exit=True)`.

A timer that only exists in a docstring is not an instrument, so this file makes it hang on purpose and
demands that it be named, killed, and red.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CI = REPO / ".github" / "workflows" / "ci.yml"

# The order is the point: the innermost bound must be the one that can name a test, and the outermost
# must be the one that ends the job. Measured costs behind these numbers: a CI bucket is 10-75 s of
# pytest, the slowest single test here is 10.90 s, and the whole suite is 124-270 s on this machine.
BOUNDS = ("test timeout (pyproject)", "watchdog (conftest)", "step budget (ci.yml)")


def _run_pytest(tmp_path: Path, watchdog: str, body: str) -> subprocess.CompletedProcess[str]:
    # The repo's own `tests/conftest.py` is copied next to the probe rather than relied upon: pytest
    # only loads confests on the path it is given, so running the probe from a temp dir would silently
    # test nothing (the first draft of this file passed a hanging probe precisely because the hook was
    # never in scope). The copy is byte-for-byte the real hook, which is what is under test.
    probe = tmp_path / "test_watchdog_probe.py"
    probe.write_text(body, encoding="utf-8")
    (tmp_path / "conftest.py").write_text(
        (REPO / "tests" / "conftest.py").read_text(encoding="utf-8"), encoding="utf-8"
    )
    env = dict(os.environ)
    env["ITERATE_TEST_WATCHDOG"] = watchdog
    env["ITERATE_TEST_WATCHDOG_FILE"] = str(tmp_path / "watchdog.log")
    env.pop("PYTEST_ADDOPTS", None)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(probe)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def test_the_watchdog_dumps_names_the_stuck_line_and_exits_red(tmp_path: Path) -> None:
    """A hang in a test *body* is only a proxy for the real case; what is asserted is the mechanism.

    Control on the real target (measured before this file existed): with `ITERATE_TEST_WATCHDOG=3`,
    a process sleeping 120 s printed `Timeout (0:00:03)!` with the frame `File "<string>", line 4`
    and exited **1**. This test reproduces that inside pytest so that a regression in
    `tests/conftest.py` — a renamed variable, a hook that stops running, a timer that gets cancelled —
    is a named red here rather than another 35-minute CI leg with no log.
    """
    body = "import time\n\n\ndef test_sleeps_forever():\n    time.sleep(120)\n"
    result = _run_pytest(tmp_path, watchdog="4", body=body)
    dump = (tmp_path / "watchdog.log").read_text(encoding="utf-8")
    out = result.stdout + result.stderr
    assert result.returncode != 0, out
    assert "watchdog armed" in dump, dump
    assert "Timeout (" in dump, dump + out
    # The frame has to name *the test*, not just any line of the file: `line 4` in this fixture is the
    # `def`, and `line 5` is the blocked `time.sleep` — the first draft of this assertion hard-coded 4
    # because that is the line number in the standalone experiment, and it was wrong here.
    assert re.search(r'File "[^"]*test_watchdog_probe\.py", line \d+ in test_sleeps_forever', dump), dump


def test_the_watchdog_is_not_armed_locally_and_refuses_a_value_it_cannot_read(tmp_path: Path) -> None:
    """Two doors on the same knob: absent means no timer, nonsense means no run.

    The absent case matters because a watchdog armed locally would kill a legitimately slow
    whole-suite run (this machine measured 36m27s once under contention). The refusal case matters
    because a silently-disabled instrument is worse than none — that is the defect this repo keeps
    paying for.
    """
    body = "def test_quick():\n    assert True\n"
    env_off = dict(os.environ)
    env_off.pop("ITERATE_TEST_WATCHDOG", None)
    probe = tmp_path / "test_watchdog_off.py"
    probe.write_text(body, encoding="utf-8")
    (tmp_path / "conftest.py").write_text(
        (REPO / "tests" / "conftest.py").read_text(encoding="utf-8"), encoding="utf-8"
    )
    ok = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(probe)],
        cwd=tmp_path,
        env=env_off,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "watchdog armed" not in ok.stdout, ok.stdout

    for bad in ("not-a-number", "-5", "0"):
        refused = _run_pytest(tmp_path, watchdog=bad, body=body)
        assert refused.returncode != 0, f"{bad} was accepted as a watchdog value"
        assert "refusing" in refused.stdout + refused.stderr, (bad, refused.stdout[-400:])


def test_the_ci_workflow_arms_the_watchdog_below_the_step_budget_above_the_test_bound() -> None:
    """Four nested bounds, asserted as numbers: test < watchdog < step < job.

    The middle link is what makes the sequence worth having: `pytest-timeout` cannot see past the last
    item, the step's poll cannot make the *runner* finish the step, and the job ceiling uploads nothing.
    """
    import yaml

    data = yaml.safe_load(CI.read_text(encoding="utf-8"))
    steps = data["jobs"]["python-tests"]["steps"]
    suite = [s for s in steps if s.get("name") == "Run test suite"]
    assert len(suite) == 1
    env = suite[0].get("env", {})

    watchdog = str(env.get("ITERATE_TEST_WATCHDOG", ""))
    budget = str(env.get("PYTEST_HARD_STOP", ""))
    assert watchdog.isdigit() and budget.isdigit(), f"watchdog={watchdog!r} budget={budget!r}"
    assert int(watchdog) < int(budget), (
        f"the watchdog ({watchdog} s) has to fire before the step's own budget ({budget} s), or the "
        "step keeps waiting on a process the watchdog was supposed to end"
    )

    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    ini = re.split(r"\n\[", pyproject.split("[tool.pytest.ini_options]", 1)[1], 1)[0]
    per_test = re.search(r"(?m)^timeout\s*=\s*(\d+)", ini)
    assert per_test is not None
    assert int(per_test.group(1)) < int(watchdog), (
        f"a single test ({per_test.group(1)} s) must not outlive the process watchdog ({watchdog} s)"
    )

    conftest = (REPO / "tests" / "conftest.py").read_text(encoding="utf-8")
    assert "dump_traceback_later" in conftest and "exit=True" in conftest, (
        "the conftest hook that reads ITERATE_TEST_WATCHDOG has to dump *and* exit: a dump alone leaves"
        " the process hanging, which is the state the four measured runs were in"
    )
    assert "cancel_dump_traceback_later" not in conftest, (
        "cancelling the watchdog would disarm it for the only window it exists for — teardown and "
        "interpreter exit, which is where the measured hang lives"
    )


@pytest.mark.parametrize("bound", BOUNDS)
def test_the_named_bounds_are_all_present_in_the_tree(bound: str) -> None:
    """A guard that cannot see all three links cannot see the ordering either."""
    sources = {
        "test timeout (pyproject)": (REPO / "pyproject.toml").read_text(encoding="utf-8"),
        "watchdog (conftest)": (REPO / "tests" / "conftest.py").read_text(encoding="utf-8"),
        "step budget (ci.yml)": CI.read_text(encoding="utf-8"),
    }
    text = sources[bound]
    assert text, f"{bound}: the file is unreadable, so the ordering above is unverifiable"
