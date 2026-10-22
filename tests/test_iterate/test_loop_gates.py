"""The gate selector: what may be reported, and what must stay absent.

The kernel's ``RoundState`` has no "unknown" for ``evidence_complete``, so the only honest
protection is that the harness must not hand it a value it did not measure. These cases pin
each direction of that rule.
"""

from __future__ import annotations

from pathlib import Path

from iterate_harness.iterate.loop_gates import GATE_KEYS, measured_gates


def _write(root: Path, name: str, lines: int) -> str:
    target = root / name
    target.write_text("\n".join(f"line {i}" for i in range(1, lines + 1)) + "\n", encoding="utf-8")
    return name


def _finding(file: str, line: int) -> object:
    """The attestation reads findings by attribute (``file``/``line``), so this
    stub is the same shape the tool passes; the gate's logic is under test here,
    not the finding model."""
    from types import SimpleNamespace

    return SimpleNamespace(file=file, line=line)


def test_nothing_measured_produces_no_gates(tmp_path: Path) -> None:
    gates = measured_gates(evidence_validation=False, findings=[], cwd=tmp_path, metadata=None)
    assert gates == {}, "an unmeasured gate must be absent, not defaulted"


def test_evidence_completeness_is_computed_here_not_borrowed(tmp_path: Path) -> None:
    name = _write(tmp_path, "a.py", 3)
    good = measured_gates(
        evidence_validation=True, findings=[_finding(name, 2)], cwd=tmp_path, metadata={}
    )
    assert good == {"evidence_complete": True}
    missing = measured_gates(
        evidence_validation=True,
        findings=[_finding("no-such-file.py", 1)],
        cwd=tmp_path,
        metadata={},
    )
    assert missing == {"evidence_complete": False}


def test_zero_findings_is_complete_not_verified(tmp_path: Path) -> None:
    """Vacuously complete — `open_findings` is what says whether anything was found."""
    gates = measured_gates(evidence_validation=True, findings=[], cwd=tmp_path, metadata={})
    assert gates["evidence_complete"] is True


def test_the_invariant_gate_comes_through_when_a_kernel_reports_one(tmp_path: Path) -> None:
    from iterate_harness.defensive.invariants import InvariantReport

    class _Kernel:
        last_invariant_report = InvariantReport(passed=False, checks_run=2)

    gates = measured_gates(
        evidence_validation=False,
        findings=[],
        cwd=tmp_path,
        metadata={"__defensive_kernel__": _Kernel()},
    )
    assert gates == {"invariant_state": "fail"}


def test_breaker_level_is_never_invented(tmp_path: Path) -> None:
    """A control on what the selector EMITS, not on a constant nobody reads.

    `GATE_KEYS` is a declaration; production code never consults it, so asserting against it alone
    stays green even if `measured_gates()` starts returning `{"breaker_level": 0}` — which is the
    exact lie this rule exists to stop (there is no circuit breaker in this host, so 0 would read
    as "a breaker ran and reported no trips"). Every shape the tool can hand over is exercised.
    """
    from types import SimpleNamespace

    name = _write(tmp_path, "a.py", 3)

    class _Kernel:
        last_invariant_report = None

    shapes = [
        {"evidence_validation": False, "findings": [], "metadata": None},
        {"evidence_validation": True, "findings": [], "metadata": {}},
        {"evidence_validation": True, "findings": [SimpleNamespace(file=name, line=2)], "metadata": {}},
        {"evidence_validation": True, "findings": None, "metadata": {}},
        {"evidence_validation": False, "findings": [], "metadata": {"__defensive_kernel__": _Kernel()}},
    ]
    for shape in shapes:
        gates = measured_gates(cwd=tmp_path, **shape)
        assert "breaker_level" not in gates, shape
        assert set(gates) <= set(GATE_KEYS), shape
