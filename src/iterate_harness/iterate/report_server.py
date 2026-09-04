"""Self-contained static HTTP server for the HTML report and replay page.

Design §14.4 finding #6 ("HTML 报告服务化"): the existing ``--html`` flag
produces a single-file report; this module adds a zero-dependency static
file server that serves the report + replay page on a local port, then
opens the user's browser.

Typical usage (CLI)::

    ih iterate report --html --serve
    ih iterate report --html --serve --port 8080

Typical usage (slash command)::

    /iterate report --html --serve

The server is intentionally single-threaded and synchronous (Python stdlib
``http.server``) — it serves one local user, not a production workload.
The process exits after the first request by default (``oneshot=True``) so
the user's terminal is not permanently blocked; pass ``oneshot=False`` for
a persistent server.
"""

from __future__ import annotations

import http.server
import logging
import os
import webbrowser
from pathlib import Path

log = logging.getLogger(__name__)

#: Default port for the report server.
DEFAULT_PORT = 0  # 0 = OS-assigned ephemeral port

#: Timeout for the server's ``handle_timeout`` (seconds).
_SERVER_TIMEOUT = 30.0


class _ReportHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP handler that serves files from the report directory.

    The served directory is resolved from ``_report_directory`` — either the
    class attribute (set by :func:`serve_report`) or an instance override.
    Inject a small index so ``/`` shows ``report.html`` when present, and
    alias ``/report`` → ``report.html`` and ``/replay`` → ``replay.html``.
    """

    #: Directory served by this handler (class-level default; overridden by
    #: :func:`serve_report` and by tests via per-class subclasses).
    _report_directory: str = ""

    def __init__(
        self,
        *args: object,
        directory: str | None = None,
        **kwargs: object,
    ) -> None:
        served = directory or self._report_directory or os.getcwd()
        self._report_directory = served
        super().__init__(*args, directory=served, **kwargs)  # type: ignore[arg-type]

    def do_GET(self) -> None:
        # Serve /report.html and /replay.html as the default pages.
        if self.path in ("", "/"):
            report_path = Path(self._report_directory) / "report.html"
            self.path = "/report.html" if report_path.exists() else "/"
        elif self.path == "/replay":
            self.path = "/replay.html"
        elif self.path == "/report":
            self.path = "/report.html"
        return super().do_GET()

    def log_message(self, fmt: str, *args: object) -> None:
        log.info("report-server: %s", fmt % args)


def serve_report(
    report_dir: str | Path,
    *,
    port: int = DEFAULT_PORT,
    oneshot: bool = True,
    open_browser: bool = True,
) -> int:
    """Start a local HTTP server serving the report directory.

    Args:
        report_dir: Directory containing ``report.html`` / ``replay.html``.
        port: TCP port (0 = OS-assigned ephemeral).
        oneshot: If True, exit after serving one request.
        open_browser: If True, open the browser automatically.

    Returns:
        The actual port the server is listening on.
    """
    report_dir = Path(report_dir).resolve()
    if not report_dir.is_dir():
        raise NotADirectoryError(f"Report directory not found: {report_dir}")

    class _BoundReportHandler(_ReportHandler):
        pass
    _BoundReportHandler._report_directory = str(report_dir)
    handler_class = _BoundReportHandler
    handler_class.extensions_map.update(
        {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".svg": "image/svg+xml",
            ".json": "application/json; charset=utf-8",
        }
    )

    server = http.server.HTTPServer(
        ("127.0.0.1", port),
        handler_class,
    )
    server.timeout = _SERVER_TIMEOUT
    actual_port = server.server_port
    report_url = f"http://127.0.0.1:{actual_port}/report.html"
    replay_url = f"http://127.0.0.1:{actual_port}/replay.html"

    log.info(
        "report-server: serving %s on port %d",
        report_dir,
        actual_port,
    )

    if open_browser:
        _try_open_browser(report_url)

    print(f"Report server: {report_url}")
    print(f"Replay page:  {replay_url}")
    print("Press Ctrl+C to stop the server.")

    try:
        if oneshot:
            server.handle_request()
            print("Request served; stopping (pass --serve-persist for a persistent server).")
        else:
            server.serve_forever()
    except KeyboardInterrupt:
        print("\nReport server stopped.")
    finally:
        server.server_close()

    return actual_port


def _try_open_browser(url: str) -> None:
    """Open the browser; best-effort in headless environments."""
    try:
        webbrowser.open(url)
    except Exception as exc:  # noqa: BLE001 - best-effort browser open
        log.debug("Could not open browser at %s: %s", url, exc)


__all__ = [
    "DEFAULT_PORT",
    "_ReportHandler",
    "serve_report",
]