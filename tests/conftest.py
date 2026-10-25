"""Shared test fixtures."""

from __future__ import annotations

import os
import sys
import threading
import time

import pytest_asyncio

from iterate_harness.tasks.manager import shutdown_task_manager

# Held for the life of the process: the descriptor the watchdog writes through must not
# be closed, and a module-level name is what keeps it (and its thread) referenced.
_WATCHDOG_HANDLE: list = []
_WATCHDOG_THREAD: list = []


def _watchdog_seconds() -> int | None:
    """Read `ITERATE_TEST_WATCHDOG`, refusing to guess at a malformed one.

    Returns None when the variable is absent, which is the local default: no timer, no surprise. A
    value that is set but not a positive integer is a *refusal*, not a silent disable — the same rule
    this repo applies everywhere else to a misconfigured knob.
    """
    raw = os.environ.get("ITERATE_TEST_WATCHDOG")
    if raw is None or not raw.strip():
        return None
    try:
        seconds = int(raw)
    except ValueError:
        print(
            f"[conftest] ITERATE_TEST_WATCHDOG={raw!r} is not an integer; refusing to arm a"
            " watchdog I cannot read",
            file=sys.stderr,
        )
        raise SystemExit(2)
    if seconds <= 0:
        print(
            f"[conftest] ITERATE_TEST_WATCHDOG={seconds} is not positive; refusing it as a"
            " watchdog value",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return seconds


def pytest_configure(config: object) -> None:
    """Arm a process-wide watchdog, because every per-test timer is disarmed before the phase that hangs.

    Measured on runs 40199977015 / 40200818814 / 40206085949 / 40207198620: bucket 06's legs never
    finished, and none of the bounds that live *inside* a test — `pytest-timeout` (240 s per item,
    covering setup/call/teardown), `faulthandler_timeout` (also per item) — ever fired. Nor could the
    step end itself: a `timeout --kill-after`, a detached `setsid` session with a bounded poll that
    provably breaks at 600 s, and `gh run cancel` all left the step `in_progress`, and only GitHub's
    job timeout ended the *job*, which uploads no artifact and leaves no log.

    That localises the wait to after the last item — session teardown or interpreter exit, where a
    non-daemon thread or a detached child can hold the process — and the only stdlib instrument that
    covers that window is `faulthandler.dump_traceback_later(..., exit=True)`: a `SIGALRM` timer that
    is not tied to an item, dumps every thread's stack, and exits the process. Measured: with
    `ITERATE_TEST_WATCHDOG=3` a process sleeping 120 s printed `Timeout (0:00:03)!`, named the stuck
    `File "<string>", line 4 in <module>`, and exited **1** — a watchdog kill is a red run, never a
    quiet green one, so it cannot fake success.

    It is deliberately never cancelled: cancelling in `pytest_sessionfinish` would disarm exactly the
    window this is for. CI arms it at 420 s, which sits between the per-test bound (240 s) and the
    step's own budget (600 s), and is ~6x the slowest measured bucket on the runner (10-75 s).
    """
    seconds = _watchdog_seconds()
    if seconds is None:
        return
    import faulthandler

    # Own the descriptor, because pytest owns fd 1 and 2 by the time this hook runs. The default
    # capture mode is `fd`, which *redirects the descriptors themselves* into pytest's own temp files;
    # two earlier drafts of this hook proved the consequence — one wrote to `sys.stderr` (captured
    # object) and one to `sys.__stderr__` (fd 2, now stolen) — and both produced an utterly empty run
    # when the timer fired, because the dump went into a capture buffer that dies with the process.
    # Opening our own file is the only way the traceback survives being killed.
    path = os.environ.get("ITERATE_TEST_WATCHDOG_FILE", "pytest-watchdog.log")
    handle = open(path, "a", encoding="utf-8")
    handle.write(
        f"[conftest] watchdog armed: dump every thread and exit after {seconds}s"
        " (collection, teardown and interpreter exit included)\n"
    )
    handle.flush()
    _WATCHDOG_HANDLE.append(handle)

    # Not `dump_traceback_later`: that mechanism is a `SIGALRM` timer, and SIGALRM has other owners in
    # this process — `pytest-timeout` with `timeout_method = "signal"` installs its own handler per
    # item, and pytest's own `faulthandler_timeout` calls `dump_traceback_later` per item too, which
    # replaces a pending one. A timer armed on top of that is a timer that can be silently cancelled:
    # measured on run 40244447182, where a collection that had been stuck for 11 minutes produced no
    # dump at its 420 s deadline. A daemon thread sleeps and then dumps every stack itself, so nothing
    # else in the process can arm, disarm or outrank it — and unlike the per-item timers it also
    # covers **collection**, which is where that run actually stopped ("collecting ..." was the last
    # line the log ever wrote).
    def _run_watchdog(duration: int, sink: object) -> None:
        time.sleep(duration)
        try:
            faulthandler.dump_traceback(file=sink, all_threads=True)
            sink.flush()  # type: ignore[union-attr]
        except Exception:  # a dying process must not be stopped by its own report
            pass
        os._exit(1)

    thread = threading.Thread(
        target=_run_watchdog, args=(seconds, handle), name="iterate-test-watchdog", daemon=True
    )
    thread.start()
    _WATCHDOG_THREAD.append(thread)


@pytest_asyncio.fixture(autouse=True)
async def _reset_background_task_manager():
    yield
    await shutdown_task_manager()
