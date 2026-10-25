"""Guard: the harness still supports Python 3.10 (``requires-python``), yet
development and nearly every local run happen on 3.11+ — where a 3.11-only
API imports, type-checks and tests green.

This bit us for real in the 2.5.0 cycle: the prompt/agent hook timeout used
``async with asyncio.timeout(...)`` (3.11+). On 3.10 the attribute lookup
raised ``AttributeError`` *inside* the surrounding ``try``, so the except arm
turned it into a contained "hook failed" — every prompt/agent hook silently
stopped working on the oldest runtime we claim to support, and only the
``Python tests (3.10)`` CI job noticed. Nothing in a 3.13 local run could
see it.

The check below is static on purpose: it behaves the same on 3.10 and 3.13,
so it catches the mistake when it is written instead of days later in CI. It
is deliberately narrow — only constructs that raise ``AttributeError`` /
``ImportError`` / ``SyntaxError`` on 3.10 — and it resolves module aliases
(``import asyncio as aio``) plus ``from X import name`` forms, so the
obvious evasions fail too.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_HARNESS_ROOT = Path(__file__).resolve().parents[1]
_SRC_ROOT = _HARNESS_ROOT / "src" / "iterate_harness"
_TESTS_ROOT = _HARNESS_ROOT / "tests"
_PYPROJECT = _HARNESS_ROOT / "pyproject.toml"

# module -> attribute/imported-name -> why it is off-limits on 3.10.
_FORBIDDEN_NAMES: dict[str, dict[str, str]] = {
    "asyncio": {
        "timeout": "3.11 — use asyncio.wait_for",
        "TaskGroup": "3.11",
        "Runner": "3.11",
        "eager_task_factory": "3.12",
    },
    "contextlib": {"chdir": "3.11"},
    "datetime": {"UTC": "3.11 — use timezone.utc"},
    "enum": {"StrEnum": "3.11", "member": "3.11", "verify": "3.12", "global_enum": "3.11"},
    "hashlib": {"file_digest": "3.11"},
    "itertools": {"batched": "3.12"},
    "typing": {"Self": "3.11", "assert_never": "3.11", "override": "3.12"},
    "os": {"process_cpu_count": "3.13"},
    "subprocess": {"process_group": "3.11"},
    "glob": {"translate": "3.13"},
}

# Modules that do not exist at all before 3.11.
_FORBIDDEN_MODULES: dict[str, str] = {
    "tomllib": "3.11 (use a yaml/toml dependency, or guard the import)",
    "tomli_w": "3.11",
    "asyncio_task_group": "3.11",
}


def _module_aliases(tree: ast.Module) -> dict[str, str]:
    """Map local binding name -> real module name for ``import x as y``."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                aliases[alias.asname or alias.name] = alias.name.split(".")[0]
    return aliases


def _handler_exception_names(handler: ast.ExceptHandler) -> set[str]:
    """The exception classes one `except` arm names, for a guarded-import check."""
    node = handler.type
    if node is None:
        return set()
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, ast.Tuple):
        return {element.id for element in node.elts if isinstance(element, ast.Name)}
    return set()


def _guarded_import_nodes(tree: ast.Module) -> set[int]:
    """Node ids of whole-module ``import x`` statements inside a `try:` that catches
    ``ModuleNotFoundError``/``ImportError``.

    That shape *is* the documented answer for a module which does not exist before 3.11
    (`try: import tomllib / except ModuleNotFoundError: import tomli as tomllib`), so a scan that
    flags it is a scan people defeat by deleting the fallback — which is the failure this guard was
    written to prevent, in the other direction.

    Deliberately limited to whole-module imports of the `_FORBIDDEN_MODULES` kind. A guarded
    *name* — `try: from datetime import UTC` — is not exempted, because that is the 2.5.0 shape
    exactly: the 3.10 failure lands inside the `try`, the handler swallows it, and the code
    degrades quietly instead of raising at import where anybody would see it. For names this guard
    wants the 3.10 form written directly. A bare `except:` or `except Exception` earns nothing.
    """
    guarded: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        caught = {name for handler in node.handlers for name in _handler_exception_names(handler)}
        if not caught & {"ModuleNotFoundError", "ImportError"}:
            continue
        for statement in node.body:
            if isinstance(statement, ast.Import):
                guarded.add(id(statement))
    return guarded


def _violations(tree: ast.Module) -> list[str]:
    """Return human-readable 3.10 incompatibilities found in ``tree``."""
    found: list[str] = []
    aliases = _module_aliases(tree)
    guarded = _guarded_import_nodes(tree)
    for node in ast.walk(tree):
        if id(node) in guarded:
            continue
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            # Fall back to the raw receiver name when it is not a known import:
            # only names that *are* module names in the tables above are looked
            # up, so `request.timeout` stays legal while a file that forgot
            # `import asyncio` is still flagged (it would NameError anyway).
            module = aliases.get(node.value.id, node.value.id)
            why = _FORBIDDEN_NAMES.get(module, {}).get(node.attr)
            if why is not None:
                found.append(f"{module}.{node.attr} ({why})")
        elif isinstance(node, ast.ImportFrom) and node.module:
            banned_names = _FORBIDDEN_NAMES.get(node.module, {})
            for alias in node.names:
                why = banned_names.get(alias.name)
                if why is not None:
                    found.append(f"from {node.module} import {alias.name} ({why})")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                why = _FORBIDDEN_MODULES.get(alias.name)
                if why is not None:
                    found.append(f"import {alias.name} ({why})")
    return found


def _requires_python_floor() -> tuple[int, int]:
    text = _PYPROJECT.read_text(encoding="utf-8")
    match = re.search(r'requires-python\s*=\s*">=\s*(\d+)\.(\d+)"', text)
    assert match is not None, "could not read requires-python from pyproject.toml"
    return int(match.group(1)), int(match.group(2))


def _iter_sources() -> list[Path]:
    """``src/`` **and** ``tests/``.

    The test half is not optional decoration: a 3.11-only name inside a test file breaks
    *collection* on the 3.10 CI job, which silently drops every test in that file from the run a
    `from datetime import UTC` in the 2026-10-25 ledger-link test would have caused. Static and
    root-wide is the only shape that answers on both runtimes.
    """
    return sorted(
        path
        for root in (_SRC_ROOT, _TESTS_ROOT)
        for path in root.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def test_requires_python_floor_is_310_so_this_guard_stays_honest() -> None:
    floor = _requires_python_floor()
    assert floor == (3, 10), (
        f"requires-python floor moved to {floor[0]}.{floor[1]}: update "
        "_FORBIDDEN_NAMES/_FORBIDDEN_MODULES in this module (or delete it) so "
        "the guard keeps matching reality."
    )


def test_source_avoids_python_311_only_apis() -> None:
    offenders: list[str] = []
    for path in _iter_sources():
        rel = path.relative_to(_HARNESS_ROOT)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(rel))
        except SyntaxError as exc:  # pragma: no cover - the suite imports these
            offenders.append(f"{rel}: not parseable ({exc})")
            continue
        for violation in _violations(tree):
            offenders.append(f"{rel}:{violation}")
    assert not offenders, "Python 3.10 compatibility violations:\n  " + "\n  ".join(offenders)


def test_guard_flags_planted_311_only_usages() -> None:
    """The guard itself must be able to fail — otherwise it is decoration.

    Each snippet is the natural way someone would actually write the code, so
    this also documents the shapes the scanner has to keep understanding.
    """
    planted = {
        "async with asyncio.timeout(1)": "async with asyncio.timeout(1):\n    pass\n",
        "asyncio.timeout call": "x = asyncio.timeout(1)\n",
        "aliased import": "import asyncio as aio\nx = aio.timeout(1)\n",
        "from-import": "from itertools import batched\ny = batched([1], 2)\n",
        "whole module": "import tomllib\n",
        "datetime.UTC": "import datetime\nx = datetime.UTC\n",
    }
    missed: list[str] = []
    for label, snippet in planted.items():
        if not _violations(ast.parse(snippet)):
            missed.append(label)
    assert not missed, f"guard failed to flag: {', '.join(missed)}"


def test_guard_ignores_legal_310_lookalikes() -> None:
    """False positives make a guard get deleted, so pin the safe shapes too."""
    legal = {
        "asyncio.wait_for": "import asyncio\nawait asyncio.wait_for(f(), timeout=1)\n",
        "kwarg named timeout": "httpx.AsyncClient(timeout=5)\n",
        "object attribute": "request.timeout = 1\nclient.timeout = 2\n",
        "typing_extensions.Self": "from typing_extensions import Self\ndef f() -> Self: ...\n",
        "timezone.utc": "from datetime import timezone\nx = timezone.utc\n",
        "guarded 3.11-only import": "try:\n    import tomllib\nexcept ModuleNotFoundError:\n    import tomli as tomllib\n",
    }
    false_positives: list[str] = []
    for label, snippet in legal.items():
        if _violations(ast.parse(snippet)):
            false_positives.append(label)
    assert not false_positives, f"guard flagged legal code: {', '.join(false_positives)}"


def test_the_guarded_import_exemption_does_not_swallow_an_unguarded_one() -> None:
    """The exemption above has to stay narrow, or `try:` becomes the way past the guard.

    Three shapes, each one a plausible way to write the fallback badly: `except Exception` catches
    the ImportError but hides the reason (and on 3.10 the alias then binds nothing useful); a bare
    `except:` does the same with less; and an import *after* the try is unguarded by construction.
    """
    still_flagged = {
        "except Exception": "try:\n    import tomllib\nexcept Exception:\n    import tomli as tomllib\n",
        "bare except": "try:\n    import tomllib\nexcept:\n    import tomli as tomllib\n",
        "unguarded": "import tomllib\n",
        "guarded from-import of a forbidden name": (
            "try:\n    from datetime import UTC\nexcept ImportError:\n    from datetime import timezone as _tz\n    UTC = _tz.utc\n"
        ),
    }
    missed: list[str] = []
    for label, snippet in still_flagged.items():
        if not _violations(ast.parse(snippet)):
            missed.append(label)
    assert not missed, f"the guarded-import exemption swallowed code it should flag: {', '.join(missed)}"


def test_the_scan_covers_both_roots_that_can_break_on_310() -> None:
    """A scan widened in prose only is still the old scan.

    Both roots are named because the two failures are different: a violation in `src/` degrades at
    runtime, a violation in `tests/` removes a whole file from the 3.10 run before anything executes.
    """
    scanned = {path.relative_to(_HARNESS_ROOT).parts[0] for path in _iter_sources()}
    assert {"src", "tests"} <= scanned, f"the 3.10 scan only reads {sorted(scanned)}"
    assert any(path.name == "test_py310_compat.py" for path in _iter_sources()), (
        "this file is not in its own scan, so a future `from datetime import UTC` here would pass it"
    )


def test_executor_uses_a_310_compatible_timeout() -> None:
    """Belt-and-braces: the exact regression, pinned to the file that had it."""
    executor = _SRC_ROOT / "hooks" / "executor.py"
    source = executor.read_text(encoding="utf-8")
    assert not _violations(ast.parse(source)), (
        "hooks/executor.py uses a 3.11+ API; on Python 3.10 it raises inside the "
        "try and silently degrades every prompt/agent hook to 'hook failed' — "
        "use asyncio.wait_for."
    )
    assert "asyncio.wait_for" in source
