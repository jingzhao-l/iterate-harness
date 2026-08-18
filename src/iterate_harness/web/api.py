"""FastAPI application factory + static frontend hosting (design §17.4).

:func:`create_app` builds the ASGI app for the WebUI: it registers the API
routers under ``/api/v1``, applies loopback-only CORS, and mounts the built
React frontend (``frontend/web/dist`` → ``iterate_harness/_frontend_web``
via hatch force-include) under ``/`` when present.

Security defaults (design §17.4):
- CORS allow-list: loopback origins only (see :func:`.security.is_loopback_origin`).
- No auth token: this is a local single-user console bound to 127.0.0.1.
- Every mutating route enforces ``confirm=true`` + audit logging.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import routes as route_modules
from . import events as events_module
from iterate_harness import __version__

log = logging.getLogger(__name__)

API_PREFIX = "/api/v1"

#: Where the built React frontend lives inside the installed package
#: (populated by hatch force-include from ``frontend/web/dist`` into
#: ``iterate_harness/_frontend_web`` — a sibling of ``web/``, not a child).
_FRONTEND_BUNDLE_DIR = Path(__file__).resolve().parent.parent / "_frontend_web"

#: Source-tree dev path used when running from a checkout without a rebuild.
_FRONTEND_SOURCE_DIR = Path(__file__).resolve().parents[3] / "frontend" / "web" / "dist"


def _frontend_dir() -> Path | None:
    """Return the bundled frontend directory, or ``None`` when absent.

    Prefers the package-bundled copy (installed wheel). Falls back to the
    source-tree dev path so ``ih web`` works from a checkout without a
    rebuild: ``harness/iterate-harness/frontend/web/dist``.
    """
    if _FRONTEND_BUNDLE_DIR.is_dir():
        return _FRONTEND_BUNDLE_DIR
    if _FRONTEND_SOURCE_DIR.is_dir():
        return _FRONTEND_SOURCE_DIR
    return None


def create_app(project_root: str | Path | None = None) -> FastAPI:
    """Build the WebUI ASGI app.

    Args:
        project_root: The iterate project to operate on. When omitted, each
            route falls back to the current working directory. Passing it
            explicitly makes every read/write target a fixed project.

    Returns:
        A configured :class:`FastAPI` instance.
    """
    app = FastAPI(
        title="iterate-harness WebUI",
        description="Local management console for iterate-harness (design §17).",
        version=__version__,
    )

    # Expose the resolved project root to routes via app state.
    resolved_root = str(Path(project_root).resolve()) if project_root else ""
    app.state.project_root = resolved_root

    # Loopback-only CORS.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1", "http://localhost", "http://[::1]"],
        allow_origin_regex=r"^http://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$",
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    # Register API routers under /api/v1.
    app.include_router(route_modules.status.router, prefix=API_PREFIX)
    app.include_router(route_modules.runs.router, prefix=API_PREFIX)
    app.include_router(route_modules.checkpoints.router, prefix=API_PREFIX)
    app.include_router(route_modules.config.router, prefix=API_PREFIX)
    app.include_router(route_modules.reports.router, prefix=API_PREFIX)
    app.include_router(route_modules.workspaces.router, prefix=API_PREFIX)
    app.include_router(route_modules.chat.router, prefix=API_PREFIX)
    app.include_router(events_module.router, prefix=API_PREFIX)

    # Mount the built frontend (SPA) at / when available.
    frontend = _frontend_dir()
    if frontend is not None:
        app.mount(
            "/",
            StaticFiles(directory=str(frontend), html=True),
            name="frontend",
        )

    return app


def serve(
    project_root: str | Path | None = None,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    open_browser: bool = True,
    log_level: str = "info",
) -> int:
    """Run the WebUI server via uvicorn; returns the bound port.

    A listening socket is bound here and handed to uvicorn (``sock=``), which
    removes the TOCTOU race of the old "probe then unbind" flow: the selected
    port cannot be re-acquired between binding and server startup. Port ``0``
    selects an OS-assigned ephemeral port (the actual port is returned). If the
    requested port is already taken, an ephemeral port is used instead of
    crashing.
    """
    import socket

    import uvicorn

    app = create_app(project_root)

    # Bind the socket ourselves and pass it to uvicorn so it never re-binds
    # (re-binding is what opened the TOCTOU window). This both selects an
    # ephemeral port for ``port == 0`` and lets us fall back cleanly when the
    # requested port is already in use.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, port))
        except OSError as exc:
            if port == 0:
                # Binding an ephemeral port cannot reasonably fail; surface the
                # real error instead of masking it with a fallback retry.
                raise
            log.warning(
                "requested port %s is unavailable (%s); falling back to an ephemeral port",
                port,
                exc,
            )
            sock.close()
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, 0))
        port = sock.getsockname()[1]
    except OSError:
        sock.close()
        raise

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level=log_level,
        access_log=False,
    )
    server = uvicorn.Server(config)

    url = f"http://{host}:{port}"
    print(f"iterate WebUI: {url}")
    print(f"Project root: {Path(project_root).resolve() if project_root else Path.cwd().resolve()}")
    print("Press Ctrl+C to stop the server.")

    if open_browser:
        import webbrowser

        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001 - best-effort browser open
            pass

    try:
        server.run(sockets=[sock])
    except KeyboardInterrupt:
        print("\niterate WebUI stopped.")
    return port


__all__ = ["API_PREFIX", "create_app", "serve"]
