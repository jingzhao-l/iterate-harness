"""Behavioural tests for the `Run test suite` step *program*, run here rather than on the runner.

WHY THIS FILE EXISTS. That step is no longer one command: it launches pytest detached in its own
session, polls a bounded budget, kills the whole session when the budget runs out, snapshots `ps`
before and after, and reports `pytest_exit` and `stopped` separately. A program that size cannot be
guarded by asserting on strings in YAML — `test_ci_workflow_shape.py` does that, and it is not enough,
because the failure mode of a poll loop is that it ends the step for the wrong reason: a killed run
reported as a passing one.

So these tests extract the step's `run:` body from `ci.yml` itself (not a copy that can drift) and
execute it against stubs for the three commands it depends on. macOS has neither `setsid` nor
`ps -o sess=`, which is exactly why the stubs exist: they give the *logic* a place to be tested, and
the control step in CI keeps certifying the real `setsid` and the real `ps` on the real image.
"""

from __future__ import annotations

import os
import signal
import stat
import subprocess
import time
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
CI = REPO / ".github" / "workflows" / "ci.yml"

SETSID_SHIM = """#!/usr/bin/env python3
import os, sys
try:
    os.setsid()
except PermissionError:
    # util-linux setsid forks in exactly this case; do the same so the pid the caller records is
    # the session leader and not a parent that exits a moment later.
    if os.fork() > 0:
        sys.exit(0)
    os.setsid()
os.execvp(sys.argv[1], sys.argv[1:])
"""

NON_SETSID_SHIM = """#!/usr/bin/env python3
import sys, os
os.execvp(sys.argv[1], sys.argv[1:])
"""

UV_STUB = """#!/usr/bin/env python3
import os, sys, time
open(os.environ["STUB_ARGV_FILE"], "w").write("\\n".join(sys.argv[1:]))
time.sleep(float(os.environ.get("STUB_SLEEP", "0.2")))
sys.exit(int(os.environ.get("STUB_RC", "0")))
"""

PS_STUB = """#!/usr/bin/env python3
import subprocess, sys
a = sys.argv[1:]
if a[:2] == ["-o", "sess="]:
    # Linux `ps -o sess=` reports the session id, which `setsid()` sets equal to the pid; macOS's
    # `sess` keyword prints a struct address (measured: 0), so this stub answers with the real
    # process-group id instead — which is the quantity `kill -9 -- -PID` actually targets, so the
    # assertion stays a measurement and stays able to fail.
    out = subprocess.run(["/bin/ps", "-o", "pgid="] + a[2:], capture_output=True, text=True)
    print(out.stdout, end="")
    sys.exit(out.returncode)
# Every other request goes to the real /bin/ps with the same arguments and the same exit status,
    # so a format string this image does not accept fails here exactly as it failed on the runner
    # (`--ww` was one of them) instead of being smoothed over by the stub.
    out = subprocess.run(["/bin/ps"] + a, capture_output=True, text=True)
    sys.stdout.write(out.stdout)
    sys.stderr.write(out.stderr)
    sys.exit(out.returncode)
"""


def _suite_step_body() -> str:
    data = yaml.safe_load(CI.read_text(encoding="utf-8"))
    steps = data["jobs"]["python-tests"]["steps"]
    matches = [step for step in steps if step.get("name") == "Run test suite"]
    assert len(matches) == 1, f"expected one Run test suite step, found {len(matches)}"
    return matches[0]["run"]


def _control_body() -> str:
    data = yaml.safe_load(CI.read_text(encoding="utf-8"))
    steps = data["jobs"]["python-tests"]["steps"]
    matches = [
        step
        for step in steps
        if str(step.get("name", "")).startswith("Prove the instruments")
    ]
    assert len(matches) == 1
    return matches[0]["run"]


TIMEOUT_STUB = """#!/usr/bin/env python3
import os, signal, subprocess, sys
a = sys.argv[1:]
if a[:2] == ["--kill-after=60", "600"]:
    a = a[2:]
limit = float(a[0])
argv = a[1:]
proc = subprocess.Popen(argv, start_new_session=True)
try:
    sys.exit(proc.wait(timeout=limit))
except subprocess.TimeoutExpired:
    os.killpg(proc.pid, signal.SIGKILL)
    sys.exit(124)
"""


def _write_stub(bin_dir: Path, name: str, text: str) -> None:
    path = bin_dir / name
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _run_body(
    tmp_path: Path,
    body: str,
    *,
    budget: str,
    sleep: str = "0.2",
    rc: str = "0",
    setsid: str = SETSID_SHIM,
    stubs: tuple[str, ...] = ("setsid", "uv", "ps"),
) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    if "setsid" in stubs:
        _write_stub(bin_dir, "setsid", setsid)
    if "uv" in stubs:
        _write_stub(bin_dir, "uv", UV_STUB)
    if "ps" in stubs:
        _write_stub(bin_dir, "ps", PS_STUB)
    if "timeout" in stubs:
        _write_stub(bin_dir, "timeout", TIMEOUT_STUB)
    script = tmp_path / "step.sh"
    script.write_text(body, encoding="utf-8")
    env = dict(os.environ)
    env.update(
        {
            "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
            "RUNNER_TEMP": str(tmp_path),
            "STUB_ARGV_FILE": str(tmp_path / "argv.txt"),
            "BUCKET_PATHS": "tests/test_a.py tests/test_b.py",
            "PYTEST_HARD_STOP": budget,
            "STUB_SLEEP": sleep,
            "STUB_RC": rc,
        }
    )
    # The flight recorder inside this step pushes when it has credentials. A test that executes the
    # step must therefore *remove* them rather than inherit them: otherwise a developer's exported
    # GITHUB_TOKEN turns a unit test into a real push into a real repository.
    for name in ("GITHUB_TOKEN", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"):
        env.pop(name, None)
    return subprocess.run(
        ["/bin/bash", str(script)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )


def test_a_passing_bucket_ends_the_step_with_pytest_status_zero(tmp_path: Path) -> None:
    """The happy path, and the bucket's paths arriving intact.

    `pytest_exit=0 stopped=finished` is the only reading that may be green; the argv file is what
    proves the single-quoted inner script handed `"$@"` through rather than expanding it in the step.
    """
    result = _run_body(tmp_path, _suite_step_body(), budget="30")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "pytest_exit=0 stopped=finished" in result.stdout, result.stdout
    assert "recorder started: enabled=0" in result.stdout, (
        "the recorder must be inert here; any other reading means this test could have pushed"
    )
    argv = (tmp_path / "argv.txt").read_text(encoding="utf-8").splitlines()
    assert argv[:1] == ["run"], argv
    assert "tests/test_a.py" in argv and "tests/test_b.py" in argv, argv
    # `$@` must not be empty because the step expanded it before the detached shell ever saw it.
    assert "uv" not in argv[0], argv


def test_a_failing_bucket_is_red_as_a_test_failure_not_as_a_budget(tmp_path: Path) -> None:
    """The two reds have to stay distinguishable: one says "fix a test", the other says "it never ended".

    Control: this is the assertion that a mutated `exit 0` at the end of the step would break.
    """
    result = _run_body(tmp_path, _suite_step_body(), budget="30", rc="1")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "pytest_exit=1 stopped=finished" in result.stdout, result.stdout
    assert "budget" not in result.stdout, result.stdout


def test_the_budget_exhaustion_ends_the_step_and_names_the_survivors(tmp_path: Path) -> None:
    """The whole reason the step was rewritten: a suite that never ends must still end the step.

    The stub sleeps 120 s against a 3 s budget. The step must return on its own within the budget
    plus its 5 s poll, report `stopped=budget-exhausted` with exit 124, write a `ps` snapshot into the
    log it prints — and leave no process behind.
    """
    started = time.monotonic()
    result = _run_body(tmp_path, _suite_step_body(), budget="3", sleep="120")
    elapsed = time.monotonic() - started
    assert result.returncode == 124, result.stdout[-2000:] + result.stderr
    assert "pytest_exit=124 stopped=budget-exhausted" in result.stdout, result.stdout
    assert elapsed <= 3 + 12, f"the poll did not bound the step: {elapsed:.1f}s"
    assert "BEFORE the kill" in result.stdout, result.stdout[-3000:]
    assert "processes still alive when the step returned" in result.stdout, result.stdout[-3000:]
    survivors = subprocess.run(
        ["pgrep", "-f", str(tmp_path / "bin" / "uv")], capture_output=True, text=True, check=False
    )
    assert survivors.stdout.strip() == "", f"stub processes outlived the step: {survivors.stdout}"


def test_the_step_refuses_when_the_process_is_not_its_own_session_leader(tmp_path: Path) -> None:
    """A guard that cannot refuse is not a guard, and this one decides what `kill -9 -- -PID` hits.

    `setsid` is replaced by a launcher that does *not* detach. The step must stop there, with its own
    words, rather than running a suite whose kill would signal the wrong group.
    """
    # A bucket that is already gone cannot be refused: the guard only speaks while there is still a
    # process whose group the kill would hit, so the stub has to still be running when it is asked.
    result = _run_body(
        tmp_path, _suite_step_body(), budget="30", sleep="20", setsid=NON_SETSID_SHIM
    )
    leaked = tmp_path / "gp-suite.pid"
    if leaked.is_file():
        try:
            os.killpg(int(leaked.read_text().strip()), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    assert result.returncode != 0, result.stdout + result.stderr
    assert "not its own session leader" in result.stdout, result.stdout
    assert "fix that here, not in the suite" in result.stdout, result.stdout


def test_the_control_step_that_certifies_those_primitives_runs_locally_too(tmp_path: Path) -> None:
    """The CI control proves `setsid`/`ps` on the image; here the same shape is proved with stubs.

    Red if the control's own assertions (bounded poll, group kill, no survivor) stop holding.
    """
    result = _run_body(tmp_path, _control_body(), budget="5", stubs=("setsid", "ps", "timeout"))
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr
    assert "ceiling_control_exit=124" in result.stdout, result.stdout
    assert "poll_killed_after=" in result.stdout, result.stdout
