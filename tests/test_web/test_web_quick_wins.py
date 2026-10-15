"""Regression tests for the WebUI quick-wins pass.

Covers:

- ``create_app`` keeps the unauthenticated ``/docs`` / ``/redoc`` /
  ``/openapi.json`` endpoints disabled by default and gated behind an
  explicit flag (fix 12).
- ``serve`` prints an explicit "Port <requested> busy; bound to <actual>"
  line when the requested port is unavailable and an ephemeral one is
  bound instead, and prints the access token exactly once (fix 12).
- The dead ``ErrorResponse`` / ``ErrorStatus`` schema contracts are gone
  (fix 13).
"""

from __future__ import annotations

import socket

from fastapi.testclient import TestClient

from iterate_harness.web.api import create_app


class TestDocsGated:
    def test_docs_disabled_by_default(self, tmp_path):
        client = TestClient(create_app(tmp_path))
        assert client.get("/docs").status_code == 404
        assert client.get("/redoc").status_code == 404
        assert client.get("/openapi.json").status_code == 404

    def test_docs_enableable(self, tmp_path):
        client = TestClient(create_app(tmp_path, enable_docs=True))
        assert client.get("/openapi.json").status_code == 200
        assert client.get("/docs").status_code == 200


class TestServePortFallback:
    def test_busy_port_prints_explicit_fallback_line(
        self, tmp_path, monkeypatch, capsys
    ):
        busy = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        busy.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        requested = busy.getsockname()[1]
        try:
            monkeypatch.setenv("ITERATE_CONFIG_DIR", str(tmp_path))
            monkeypatch.setenv("ITERATE_DATA_DIR", str(tmp_path / "data"))
            import uvicorn

            monkeypatch.setattr(uvicorn.Server, "run", lambda self, *a, **k: None)
            from iterate_harness.web.api import serve

            bound = serve(
                tmp_path,
                host="127.0.0.1",
                port=requested,
                open_browser=False,
                log_level="error",
            )
        finally:
            busy.close()
        out = capsys.readouterr().out
        assert bound != requested
        assert f"Port {requested} busy; bound to {bound} instead." in out
        # The access token is printed exactly once, and the URL carrying it
        # exactly once — no duplicated banner lines.
        assert out.count("Access token:") == 1
        assert out.count("iterate WebUI: http://") == 1


class TestDeadSchemaCleanup:
    def test_error_response_model_removed(self):
        from iterate_harness.web import schemas

        assert not hasattr(schemas, "ErrorResponse")
        assert not hasattr(schemas, "ErrorStatus")
        assert "ErrorResponse" not in schemas.__all__

    def test_operation_result_contract_intact(self):
        from iterate_harness.web import schemas

        assert set(schemas.OperationResult.model_fields) == {
            "status",
            "message",
            "target",
            "detail",
        }
