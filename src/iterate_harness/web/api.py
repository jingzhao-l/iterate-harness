"""FastAPI application factory + static frontend hosting (design §17.4).

:func:`create_app` builds the ASGI app for the WebUI: it registers the API
routers under ``/api/v1``, applies loopback-only CORS, and mounts the built
React frontend (``frontend/web/dist`` → ``iterate_harness/_frontend_web``
via hatch force-include) under ``/`` when present.

Security defaults (design §17.4):
- CORS allow-list: loopback origins only (see :func:`.security.is_loopback_origin`).
- Access token: :func:`serve` issues a random token and protects every
  ``/api/v1`` route behind it (``Authorization: Bearer <token>`` header or
  ``?token=<token>`` query parameter, compared in constant time). Static
  frontend assets stay unauthenticated — they carry no secrets.
- Every mutating route enforces ``confirm=true`` + audit logging.
"""

from __future__ import annotations

import logging
import secrets
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
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


def _extract_request_token(request: "Request") -> str:
    """Return the access token from the request, if any.

    Accepts ``Authorization: Bearer <token>`` (used by the frontend fetch
    wrapper) and the ``?token=<token>`` query parameter (required for the
    EventSource stream, which cannot set custom headers).
    """
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return request.query_params.get("token", "")


def _guard_api(token: str, request: "Request") -> JSONResponse | None:
    """Return a 401 response when ``token`` is set and the request lacks it."""
    if not token:
        return None
    provided = _extract_request_token(request)
    if not provided or not secrets.compare_digest(provided, token):
        log.info("WebUI API request rejected: missing or invalid access token (%s %s)", request.method, request.url.path)
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid or missing access token. Start the WebUI again via 'ih web serve'."},
        )
    return None


def create_app(project_root: str | Path | None = None, *, token: str | None = None) -> FastAPI:
    """Build the WebUI ASGI app.

    Args:
        project_root: The iterate project to operate on. When omitted, each
            route falls back to the current working directory. Passing it
            explicitly makes every read/write target a fixed project.
        token: The access token required for every ``/api/v1`` request. When
            ``None``, API authentication is disabled (backwards-compatible
            default for embedding and tests); :func:`serve` always passes a
            generated token.

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
    # Empty string means "authentication disabled"; serve() always sets it.
    app.state.webui_token = token or ""

    # Protect every /api/v1 route behind the access token (when one is set).
    @app.middleware("http")
    async def require_access_token(request: "Request", call_next):
        if request.url.path.startswith(API_PREFIX):
            denied = _guard_api(app.state.webui_token, request)
            if denied is not None:
                return denied
        return await call_next(request)

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

    from .token import get_or_create_webui_token

    # A random per-install access token protects every /api/v1 route from
    # other local users/processes reaching the loopback server.
    token = get_or_create_webui_token()
    app = create_app(project_root, token=token)

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
    # The access token rides on the URL so the opened browser session (and any
    # re-open via this printed link) can authenticate against the API.
    url_with_token = f"{url}/?token={token}"
    print(f"iterate WebUI: {url_with_token}")
    print(f"Access token: {token}")
    print(f"Project root: {Path(project_root).resolve() if project_root else Path.cwd().resolve()}")
    print("Press Ctrl+C to stop the server.")

    if open_browser:
        import webbrowser

        try:
            webbrowser.open(url_with_token)
        except Exception:  # noqa: BLE001 - best-effort browser open
            pass

    try:
        server.run(sockets=[sock])
    except KeyboardInterrupt:
        print("\niterate WebUI stopped.")
    return port


__all__ = ["API_PREFIX", "create_app", "serve"]
