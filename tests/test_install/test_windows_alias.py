"""Installer regressions for Windows command aliases."""

from __future__ import annotations

from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib


def test_pyproject_exposes_openh_console_script():
    data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    scripts = data["project"]["scripts"]
    assert scripts["openh"] == "openharness.cli:app"
    assert scripts["oh"] == "openharness.cli:app"
    assert scripts["iterate-harness"] == "openharness.cli:app"


def test_powershell_installer_recommends_iterate_harness_for_windows():
    script = Path("scripts/install.ps1").read_text(encoding="utf-8")
    assert "iterate-harness.exe" in script
    assert "Launch (PowerShell):     iterate-harness" in script
    assert "Out-Host" in script


def test_powershell_installer_falls_back_when_iterate_harness_exe_missing():
    """When the dedicated launcher is absent from the venv, the installer
    must still pick a working launcher (`oh.exe`) and guide the user to it
    rather than telling them to run a binary that doesn't exist (issue #144).
    """
    script = Path("scripts/install.ps1").read_text(encoding="utf-8")
    # Every launcher produced by the pyproject `[project.scripts]` table is
    # probed during verification.
    assert "iterate-harness.exe" in script
    assert "oh.exe" in script
    # Fallback guidance for users without the dedicated launcher.
    assert "Launch (PowerShell):     oh.exe" in script
    assert "'iterate-harness'" in script
