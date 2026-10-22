"""The ruler must refuse a build that is not the artifact the corpus pins.

`diff_kernel_implementations` compares the Python port against the *published* TypeScript dist. If
it resolves an older build, every case the newer contract added comes back as `TS refused, Python
accepted` — a reading that looks like the two implementations diverged, and would get "fixed" by
weakening whichever side is actually right. So the refusal has to be explicit and testable, not a
warning a reader can miss.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "diff_kernel_implementations.py"


@pytest.fixture(scope="module")
def ruler():
    spec = importlib.util.spec_from_file_location("diff_kernel_implementations_probe", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build(root: Path, version: str | None, name: str = "iterate-kernel") -> Path:
    kernel = root / name
    (kernel / "dist").mkdir(parents=True)
    (kernel / "dist" / "convergence.js").write_text("module.exports = {};", encoding="utf-8")
    if version is not None:
        (kernel / "package.json").write_text(
            json.dumps({"name": "iterate-kernel", "version": version}), encoding="utf-8"
        )
    return kernel


def test_the_pinned_build_is_accepted(ruler, tmp_path: Path) -> None:
    assert ruler._reject_stale_artifact(_build(tmp_path, "0.1.8"), "0.1.8") is None


def test_a_stale_build_is_refused_by_naming_both_versions(ruler, tmp_path: Path) -> None:
    reason = ruler._reject_stale_artifact(_build(tmp_path, "0.1.7"), "0.1.8")
    assert reason is not None
    assert "0.1.7" in reason and "0.1.8" in reason
    assert "wrong artifact" in reason, "it must not read as a cross-language disagreement"


def test_an_unreadable_version_is_not_assumed_to_be_the_pin(ruler, tmp_path: Path) -> None:
    reason = ruler._reject_stale_artifact(_build(tmp_path, None), "0.1.8")
    assert reason is not None and "no readable version" in reason


def test_without_a_pin_there_is_no_judgement_to_make(ruler, tmp_path: Path) -> None:
    assert ruler._reject_stale_artifact(_build(tmp_path, "0.1.7"), "") is None


def test_the_search_refuses_a_stale_build_rather_than_verdicting_with_it(
    ruler, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The candidate walk must not hand back the first built dist it finds."""
    empty = tmp_path / "elsewhere"
    empty.mkdir()
    monkeypatch.setattr(ruler, "REPO", empty)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"version": "0.1.8"}), encoding="utf-8")
    monkeypatch.setattr(ruler, "MANIFEST", manifest)

    stale = _build(tmp_path / "old", "0.1.7")
    assert ruler._kernel_dir(str(stale)) is None

    current = _build(tmp_path / "new", "0.1.8")
    assert ruler._kernel_dir(str(current)) == current
