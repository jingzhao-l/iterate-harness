"""iterate_harness — the iterate review/fix loop agent harness.

This package is the single source of truth for the project version. The
``[project].version`` field in ``pyproject.toml`` is read from this
``__version__`` at build time (see ``[tool.hatch.version]``), and the CLI
(``cli.py``) reports it for ``--version``.
"""

from __future__ import annotations

__version__ = "1.16.0"

__all__ = ["__version__"]