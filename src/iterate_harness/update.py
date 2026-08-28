"""Self-update support for iterate-harness.

Provides install-method detection (npm-managed venv / source checkout / plain
pip), latest-version discovery from the GitHub release feed (with a raw-file
fallback), cached update checks for the ``--version`` auto-notification, and
the actual update application used by ``ih update``.

Design notes
------------
* The npm wrapper (``bin/ih.js``) pins the Python harness to the npm package
  version and re-bootstraps the managed venv on the next run whenever the
  stamp does not match. ``ih update`` therefore asks npm to update the global
  package; the wrapper applies the new release automatically on the following
  invocation.
* A source install (``scripts/install.sh``) is a git clone + editable pip
  install; ``ih update`` pulls the newest code and re-runs the editable
  install so new dependencies are picked up.
* When neither layout is detected (or npm is unavailable), ``ih update``
  falls back to reinstalling the latest release tarball into the current
  interpreter.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional, Protocol

import httpx

from iterate_harness import __version__
from iterate_harness.config.paths import get_config_dir

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Named constants (no magic strings in the business logic below)
# ---------------------------------------------------------------------------

GITHUB_REPO_OWNER = "jingzhao-l"
GITHUB_REPO_NAME = "iterate-harness"
GITHUB_API_LATEST_RELEASE_URL = (
    f"https://api.github.com/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/releases/latest"
)
GITHUB_RAW_VERSION_URL = (
    f"https://raw.githubusercontent.com/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/"
    "main/src/iterate_harness/__init__.py"
)
GITHUB_RELEASE_TARBALL_URL = (
    f"https://github.com/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/archive/refs/tags"
)

NPM_PACKAGE_NAME = "iterate-harness"

# npm-managed runtime layout (matches npm/lib/bootstrap.js)
NPM_RUNTIME_DIR_NAME = ".iterate-harness-npm"
NPM_VENV_DIR_NAME = "venv"
NPM_STAMP_FILE_NAME = "version.stamp"

# source install layout (matches scripts/install.sh)
SOURCE_INSTALL_DIR_NAME = ".iterate-harness-src"
SOURCE_VENV_DIR_NAME = ".iterate-harness-venv"

INSTALL_METHOD_NPM = "npm"
INSTALL_METHOD_SOURCE = "source"
INSTALL_METHOD_PIP = "pip"

INSTALL_METHOD_LABELS = {
    INSTALL_METHOD_NPM: "npm package (managed venv)",
    INSTALL_METHOD_SOURCE: "source checkout (editable install)",
    INSTALL_METHOD_PIP: "pip install in the current interpreter",
}

CACHE_FILE_NAME = "update-check.json"
CACHE_TTL_SECONDS = 24 * 60 * 60
HTTP_TIMEOUT_SECONDS = 10.0
HINT_TIMEOUT_SECONDS = 3.0
GIT_TIMEOUT_SECONDS = 120
NPM_TIMEOUT_SECONDS = 300
PIP_TIMEOUT_SECONDS = 600

GIT_PULL_MAX_ATTEMPTS = 1
MIN_VERSION_PARTS = 3
VERSION_PATTERN = re.compile(r"\d+\.\d+\.\d+")


# ---------------------------------------------------------------------------
# Small protocols (kept structural so tests can inject fakes)
# ---------------------------------------------------------------------------


class _Response(Protocol):
    status_code: int

    def json(self) -> object: ...

    @property
    def text(self) -> str: ...


class _CurlResponse:
    """Minimal _Response built from curl output (used as an httpx fallback)."""

    def __init__(self, text: str) -> None:
        self._text = text

    @property
    def status_code(self) -> int:
        return 200

    def json(self) -> object:
        return json.loads(self._text)

    @property
    def text(self) -> str:
        return self._text


class _GetFn(Protocol):
    def __call__(self, url: str, *, timeout: float, headers: dict[str, str]) -> _Response: ...


# ---------------------------------------------------------------------------
# Version helpers
# ---------------------------------------------------------------------------


def normalize_version(raw: object) -> Optional[str]:
    """Extract a ``X.Y.Z`` version from arbitrary release text/tag/JSON value."""
    text = str(raw or "").strip()
    match = VERSION_PATTERN.search(text)
    return match.group(0) if match else None


def _version_tuple(version: str) -> tuple[int, int, int]:
    numbers = [int(part) for part in re.findall(r"\d+", version) if part]
    while len(numbers) < MIN_VERSION_PARTS:
        numbers.append(0)
    return (numbers[0], numbers[1], numbers[2])


def compare_versions(current: str, latest: str) -> int:
    """Return ``-1``/``0``/``1`` comparing ``latest`` against ``current``.

    A positive result means ``latest`` is newer than ``current``.
    """
    current_tuple = _version_tuple(current)
    latest_tuple = _version_tuple(latest)
    if latest_tuple > current_tuple:
        return 1
    if latest_tuple < current_tuple:
        return -1
    return 0


# ---------------------------------------------------------------------------
# Install-method detection
# ---------------------------------------------------------------------------


def _is_within(candidate: str, parent: Path) -> bool:
    try:
        Path(candidate).resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def detect_install_method(home: Path, sys_prefix: Optional[str] = None) -> str:
    """Classify the installation layout the harness is running from.

    Order of checks: npm-managed venv (stamp/venv on disk, or the running
    interpreter lives inside it), then the source checkout layout, then a
    generic pip install.
    """
    npm_root = home / NPM_RUNTIME_DIR_NAME
    if (npm_root / NPM_VENV_DIR_NAME).exists() or (npm_root / NPM_STAMP_FILE_NAME).exists():
        return INSTALL_METHOD_NPM
    if sys_prefix and _is_within(sys_prefix, npm_root):
        return INSTALL_METHOD_NPM

    source_dir = home / SOURCE_INSTALL_DIR_NAME
    source_venv = home / SOURCE_VENV_DIR_NAME
    if (source_dir / ".git").exists() or source_venv.exists():
        return INSTALL_METHOD_SOURCE
    if sys_prefix and _is_within(sys_prefix, source_venv):
        return INSTALL_METHOD_SOURCE

    return INSTALL_METHOD_PIP


def current_install_method() -> str:
    """Convenience wrapper detecting the method for the live interpreter."""
    return detect_install_method(Path.home(), sys_prefix=sys.prefix)


# ---------------------------------------------------------------------------
# Latest-version discovery
# ---------------------------------------------------------------------------


def _default_get(url: str, *, timeout: float, headers: dict[str, str]) -> _Response:
    try:
        return httpx.get(url, timeout=timeout, headers=headers)
    except Exception as error:
        # httpx can fail on machines with a stale/broken CA store
        # (SSL: CERTIFICATE_VERIFY_FAILED). curl reads the system CA store, so
        # fall back to it — matching the bootstrap.js / installer strategy.
        if not _which_curl_in_path():
            raise error
        return _get_via_curl(url, timeout=timeout)


def _which_curl_in_path() -> bool:
    from shutil import which

    return which("curl") is not None


def _get_via_curl(url: str, *, timeout: float) -> _CurlResponse:
    result = subprocess.run(
        ["curl", "-fsSL", "--max-time", str(int(timeout)), url],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"curl request to {url} failed (exit {result.returncode})")
    return _CurlResponse(result.stdout)


def _fetch_from_releases(get_fn: _GetFn, timeout: float) -> Optional[str]:
    try:
        response = get_fn(
            GITHUB_API_LATEST_RELEASE_URL,
            timeout=timeout,
            headers={"Accept": "application/vnd.github+json"},
        )
    except Exception as error:  # network/TLS/JSON errors are all non-fatal
        log.debug("latest-release fetch failed: %s", error)
        return None
    if response.status_code != 200:
        log.debug("latest-release HTTP %s", response.status_code)
        return None
    try:
        payload = response.json()
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    return normalize_version(payload.get("tag_name"))


def _fetch_from_raw_file(get_fn: _GetFn, timeout: float) -> Optional[str]:
    try:
        response = get_fn(
            GITHUB_RAW_VERSION_URL,
            timeout=timeout,
            headers={"Accept": "text/plain"},
        )
    except Exception as error:  # network/TLS errors are non-fatal
        log.debug("raw version fetch failed: %s", error)
        return None
    if response.status_code != 200:
        return None
    return normalize_version(re.search(r"__version__\s*=\s*[\"']([^\"']+)[\"']", response.text))


def fetch_latest_version(get_fn: Optional[_GetFn] = None, timeout: float = HTTP_TIMEOUT_SECONDS) -> Optional[str]:
    """Return the newest published ``X.Y.Z`` version, or ``None`` if unreachable."""
    fetcher = get_fn if get_fn is not None else _default_get
    latest = _fetch_from_releases(fetcher, timeout)
    if latest is not None:
        return latest
    return _fetch_from_raw_file(fetcher, timeout)


# ---------------------------------------------------------------------------
# Update-check cache (used by the ``--version`` auto-notification)
# ---------------------------------------------------------------------------


def get_check_cache_path() -> Path:
    return get_config_dir() / CACHE_FILE_NAME


def read_check_cache(cache_path: Path) -> dict[str, str]:
    if not cache_path.is_file():
        return {}
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(key): str(value) for key, value in payload.items() if value}


def write_check_cache(cache_path: Path, latest: str) -> None:
    payload = {
        "latest": latest,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        cache_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    except OSError as error:
        log.debug("could not persist update-check cache: %s", error)


def is_check_stale(cache: dict[str, str], ttl_seconds: int = CACHE_TTL_SECONDS) -> bool:
    checked_at = cache.get("checked_at")
    if not checked_at:
        return True
    try:
        timestamp = datetime.fromisoformat(checked_at)
    except ValueError:
        return True
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - timestamp
    return age > timedelta(seconds=ttl_seconds)


def maybe_print_update_hint(
    *,
    current: Optional[str] = None,
    cache_path: Optional[Path] = None,
    get_fn: Optional[_GetFn] = None,
    timeout: float = HINT_TIMEOUT_SECONDS,
) -> None:
    """Best-effort one-line notification when a newer release exists.

    Never raises: the hint is advisory and must not break ``--version``.
    """
    try:
        hint = _build_update_hint(current=current, cache_path=cache_path, get_fn=get_fn, timeout=timeout)
    except Exception as error:  # advisory only — never crash the caller
        log.debug("update hint suppressed: %s", error)
        return
    if hint:
        print(hint)


def _build_update_hint(
    *,
    current: Optional[str],
    cache_path: Optional[Path],
    get_fn: Optional[_GetFn],
    timeout: float,
) -> Optional[str]:
    cache_file = cache_path if cache_path is not None else get_check_cache_path()
    cache = read_check_cache(cache_file)
    if is_check_stale(cache):
        latest = fetch_latest_version(get_fn=get_fn, timeout=timeout)
        if latest is not None:
            write_check_cache(cache_file, latest)
            cache = read_check_cache(cache_file)
    latest = cache.get("latest")
    if not latest:
        return None
    installed = current if current is not None else __version__
    if compare_versions(installed, latest) <= 0:
        return None
    return (
        f"A new version {latest} is available (you have {installed}). "
        "Run `ih update` to upgrade."
    )


# ---------------------------------------------------------------------------
# Update application
# ---------------------------------------------------------------------------


@dataclass
class UpdateResult:
    """Outcome of an ``apply_update`` attempt."""

    method: str
    success: bool
    message: str


def _default_runner(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True)


def _run_command(
    argv: list[str],
    runner: Callable[[list[str]], subprocess.CompletedProcess[str]],
    timeout: float,
    what: str,
) -> None:
    try:
        result = runner(argv)
    except OSError as error:
        raise UpdateError(f"{what} failed to start: {error}") from error
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        suffix = f": {detail}" if detail else ""
        raise UpdateError(f"{what} failed (exit {result.returncode}){suffix}")


def _win_python_rel(venv_dir: Path) -> str:
    return str(venv_dir / "Scripts" / "python.exe")


def _posix_python_rel(venv_dir: Path) -> str:
    return str(venv_dir / "bin" / "python")


def _update_from_source(
    home: Path,
    runner: Callable[[list[str]], subprocess.CompletedProcess[str]],
) -> UpdateResult:
    source_dir = home / SOURCE_INSTALL_DIR_NAME
    if not (source_dir / ".git").is_dir():
        return UpdateResult(
            method=INSTALL_METHOD_SOURCE,
            success=False,
            message=f"source checkout not found at {source_dir}",
        )
    _run_command(
        ["git", "-C", str(source_dir), "pull", "--ff-only"],
        runner,
        GIT_TIMEOUT_SECONDS,
        "git pull",
    )
    venv_dir = home / SOURCE_VENV_DIR_NAME
    python_rel = _win_python_rel(venv_dir) if os.name == "nt" else _posix_python_rel(venv_dir)
    python_bin = Path(python_rel)
    if not python_bin.is_file():
        python_bin = Path(sys.executable)
    _run_command(
        [str(python_bin), "-m", "pip", "install", "-e", str(source_dir)],
        runner,
        PIP_TIMEOUT_SECONDS,
        "pip install -e",
    )
    return UpdateResult(
        method=INSTALL_METHOD_SOURCE,
        success=True,
        message=f"source updated and reinstalled from {source_dir}",
    )


def _update_from_npm(
    runner: Callable[[list[str]], subprocess.CompletedProcess[str]],
    latest_version: str,
) -> UpdateResult:
    npm_executable = os.environ.get("ITERATE_HARNESS_NPM")
    if not npm_executable:
        from shutil import which

        npm_executable = which("npm")
    if npm_executable:
        _run_command(
            [npm_executable, "install", "-g", f"{NPM_PACKAGE_NAME}@latest"],
            runner,
            NPM_TIMEOUT_SECONDS,
            "npm install -g",
        )
        return UpdateResult(
            method=INSTALL_METHOD_NPM,
            success=True,
            message=(
                f"updated the global {NPM_PACKAGE_NAME} npm package; "
                "the managed environment re-installs the new release on the next run"
            ),
        )
    return _update_from_pip(runner, latest_version)


def _update_from_pip(
    runner: Callable[[list[str]], subprocess.CompletedProcess[str]],
    latest_version: str,
) -> UpdateResult:
    tarball_url = f"{GITHUB_RELEASE_TARBALL_URL}/v{latest_version}.tar.gz"
    _run_command(
        [sys.executable, "-m", "pip", "install", "--upgrade", "--force-reinstall", tarball_url],
        runner,
        PIP_TIMEOUT_SECONDS,
        "pip install release tarball",
    )
    return UpdateResult(
        method=INSTALL_METHOD_PIP,
        success=True,
        message=f"installed {latest_version} into the current interpreter ({sys.executable})",
    )


class UpdateError(RuntimeError):
    """Raised when an update step fails (converted to a user-facing message)."""


def apply_update(
    *,
    method: str,
    home: Path,
    latest_version: str,
    runner: Optional[Callable[[list[str]], subprocess.CompletedProcess[str]]] = None,
) -> UpdateResult:
    """Apply an update for the given install method and return its outcome.

    The optional ``runner`` callable replaces ``subprocess.run`` for tests.
    """
    command_runner = runner if runner is not None else _default_runner
    try:
        if method == INSTALL_METHOD_SOURCE:
            return _update_from_source(home, command_runner)
        if method == INSTALL_METHOD_NPM:
            return _update_from_npm(command_runner, latest_version)
        return _update_from_pip(command_runner, latest_version)
    except UpdateError as error:
        return UpdateResult(method=method, success=False, message=str(error))


def verify_installed_version(
    runner: Optional[Callable[[list[str]], subprocess.CompletedProcess[str]]] = None,
) -> Optional[str]:
    """Run ``python -m iterate_harness --version`` to confirm the live version."""
    command_runner = runner if runner is not None else _default_runner
    try:
        result = command_runner([sys.executable, "-m", "iterate_harness", "--version"])
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return normalize_version(result.stdout or "")


# ---------------------------------------------------------------------------
# High-level orchestration used by the CLI ``update`` command
# ---------------------------------------------------------------------------


def perform_update(
    *,
    current: str,
    home: Path,
    method: str,
    latest: str,
    runner: Optional[Callable[[list[str]], subprocess.CompletedProcess[str]]] = None,
) -> UpdateResult:
    """Apply the update and verify the new version afterwards."""
    result = apply_update(
        method=method,
        home=home,
        latest_version=latest,
        runner=runner,
    )
    if not result.success:
        return result
    verified = verify_installed_version(runner=runner)
    if verified and compare_versions(current, verified) > 0:
        result.message = f"{result.message} (now running {verified})"
    return result


# ---------------------------------------------------------------------------
# Auto-update (throttled background check + silent install on CLI start)
# ---------------------------------------------------------------------------

AUTO_UPDATE_ENV = "ITERATE_AUTO_UPDATE"
AUTO_UPDATE_INTERVAL_ENV = "ITERATE_AUTO_UPDATE_INTERVAL_HOURS"
AUTO_UPDATE_STATE_FILE = "auto-update-state.json"
AUTO_UPDATE_DEFAULT_INTERVAL_HOURS = 24

# Presence of any of these means we are inside a CI runner: auto-updating
# there would silently mutate the build environment, so it is skipped.
CI_ENV_VARS = ("CI", "GITHUB_ACTIONS", "GITLAB_CI", "TRAVIS", "CIRCLECI", "JENKINS_URL")


def auto_update_enabled() -> bool:
    """Return whether the automatic update check is enabled (default: on)."""
    raw = os.environ.get(AUTO_UPDATE_ENV, "1").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def auto_update_interval_hours() -> int:
    """Return the throttling interval for auto-update checks (min 1h)."""
    raw = os.environ.get(AUTO_UPDATE_INTERVAL_ENV, "").strip()
    try:
        hours = int(raw)
    except (TypeError, ValueError):
        return AUTO_UPDATE_DEFAULT_INTERVAL_HOURS
    return max(1, hours)


def _is_ci_environment() -> bool:
    return any(os.environ.get(name) for name in CI_ENV_VARS)


def get_auto_update_state_path() -> Path:
    """Return the path of the auto-update throttle state file."""
    return get_config_dir() / AUTO_UPDATE_STATE_FILE


def read_auto_update_state(state_path: Optional[Path] = None) -> dict[str, str]:
    """Read the auto-update state (best-effort; returns {} on any problem)."""
    path = state_path if state_path is not None else get_auto_update_state_path()
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(key): str(value) for key, value in payload.items() if value}


def write_auto_update_state(state: dict[str, str], state_path: Optional[Path] = None) -> None:
    """Persist the auto-update state (best-effort; failures are logged only)."""
    path = state_path if state_path is not None else get_auto_update_state_path()
    try:
        path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    except OSError as error:
        log.debug("could not persist auto-update state: %s", error)


def should_run_auto_update(
    state: dict[str, str],
    interval_hours: int = AUTO_UPDATE_DEFAULT_INTERVAL_HOURS,
) -> bool:
    """Return True when the throttle window since the last attempt has passed."""
    last_attempt = state.get("last_attempt_at")
    if not last_attempt:
        return True
    try:
        timestamp = datetime.fromisoformat(last_attempt)
    except ValueError:
        return True
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - timestamp
    return age > timedelta(hours=interval_hours)


def run_auto_update(
    *,
    get_fn: Optional[_GetFn] = None,
    runner: Optional[Callable[[list[str]], subprocess.CompletedProcess[str]]] = None,
    state_path: Optional[Path] = None,
    timeout: float = HTTP_TIMEOUT_SECONDS,
) -> Optional[UpdateResult]:
    """Check for a newer release and silently install it when due.

    Designed to run in a background thread during normal CLI use: it never
    raises, respects ``ITERATE_AUTO_UPDATE`` (default on) and skips CI
    environments. Returns the UpdateResult when an install was attempted,
    otherwise None.
    """
    if not auto_update_enabled():
        return None
    if _is_ci_environment():
        log.debug("auto-update skipped: CI environment detected")
        return None

    state_file = state_path if state_path is not None else get_auto_update_state_path()
    state = read_auto_update_state(state_file)
    if not should_run_auto_update(state, auto_update_interval_hours()):
        return None

    now_iso = datetime.now(timezone.utc).isoformat()
    latest = fetch_latest_version(get_fn=get_fn, timeout=timeout)
    if latest is None:
        write_auto_update_state(
            {"last_attempt_at": now_iso, "last_attempt_version": "", "last_status": "unreachable"},
            state_file,
        )
        return None

    installed = __version__
    if compare_versions(installed, latest) <= 0:
        write_auto_update_state(
            {"last_attempt_at": now_iso, "last_attempt_version": latest, "last_status": "up-to-date"},
            state_file,
        )
        return None

    method = current_install_method()
    try:
        result = apply_update(
            method=method,
            home=Path.home(),
            latest_version=latest,
            runner=runner,
        )
    except Exception as error:  # auto-update is best-effort; never crash the caller
        log.debug("auto-update failed: %s", error)
        write_auto_update_state(
            {"last_attempt_at": now_iso, "last_attempt_version": latest, "last_status": "error"},
            state_file,
        )
        return None

    status = "ok" if result.success else "failed"
    write_auto_update_state(
        {"last_attempt_at": now_iso, "last_attempt_version": latest, "last_status": status},
        state_file,
    )
    print(
        f"iterate-harness auto-updated {installed} -> {latest} ({result.method}).",
        file=sys.stderr,
    )
    return result
