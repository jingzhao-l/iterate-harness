"""WebUI management console for iterate-harness (design §17).

A read-mostly FastAPI backend that exposes the project's decision log,
checkpoints, budget meter, config, and reports through a local REST + SSE
API consumed by the React frontend in ``frontend/web``.

Security posture (design §17.4): loopback-only binding, CORS restricted to
loopback origins, path whitelisting against traversal, credential
desensitization, and an audit log for every mutating operation.

The package is intentionally thin over the existing ``iterate`` data layer
(``decision_log`` / ``checkpoint`` / ``cost`` / ``config_loader`` /
``html_report``) — it never re-implements business logic.
"""

from __future__ import annotations

__all__ = ["create_app", "serve"]

# Imported lazily inside functions so importing the package never requires
# fastapi to be present (e.g. for the CLI's non-web command paths).
