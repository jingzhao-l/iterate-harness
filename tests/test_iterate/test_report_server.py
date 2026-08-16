"""Tests for the static report server (iterate_harness.iterate.report_server)."""

from __future__ import annotations

import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from iterate_harness.iterate import report_server


def _write_sample_files(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "report.html").write_text("<!DOCTYPE html><html>report</html>", encoding="utf-8")
    (tmp_path / "replay.html").write_text("<!DOCTYPE html><html>replay</html>", encoding="utf-8")
    return tmp_path


def _serve_in_thread(root: Path, port: int = 0) -> tuple[object, threading.Thread]:
    """Start a background server; returns (server, thread). Caller shuts down."""
    import http.server

    handler_class = report_server._ReportHandler
    handler_class._report_directory = str(root)
    server = http.server.HTTPServer(("127.0.0.1", port), handler_class)
    server.timeout = 5.0
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


class TestReportHandler:
    def test_serves_report_html_at_root(self, tmp_path):
        root = _write_sample_files(tmp_path)
        server, thread = _serve_in_thread(root)
        try:
            port = server.server_port  # type: ignore[attr-defined]
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
                assert resp.status == 200
                body = resp.read().decode("utf-8")
                assert "report</html>" in body
        finally:
            server.shutdown()  # type: ignore[attr-defined]
            thread.join(timeout=3)

    def test_serves_replay_page_and_alias(self, tmp_path):
        root = _write_sample_files(tmp_path)
        server, thread = _serve_in_thread(root)
        try:
            port = server.server_port  # type: ignore[attr-defined]
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/replay.html", timeout=5) as resp:
                assert resp.status == 200
                assert "replay</html>" in resp.read().decode("utf-8")
            # /replay alias points to replay.html
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/replay", timeout=5) as resp:
                assert resp.status == 200
                assert "replay</html>" in resp.read().decode("utf-8")
        finally:
            server.shutdown()  # type: ignore[attr-defined]
            thread.join(timeout=3)

    def test_missing_report_returns_404(self, tmp_path):
        server, thread = _serve_in_thread(tmp_path)
        try:
            port = server.server_port  # type: ignore[attr-defined]
            with pytest.raises(urllib.error.HTTPError) as exc:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/report.html", timeout=5)
            assert exc.value.code == 404
        finally:
            server.shutdown()  # type: ignore[attr-defined]
            thread.join(timeout=3)


class TestServeReport:
    def test_missing_directory_raises(self, tmp_path):
        with pytest.raises(NotADirectoryError):
            report_server.serve_report(tmp_path / "does-not-exist", open_browser=False)

    def test_oneshot_serves_one_request_and_returns_port(self, tmp_path, monkeypatch):
        root = _write_sample_files(tmp_path)
        import http.server as http_server_module

        created: dict[str, object] = {}
        original = http_server_module.HTTPServer

        class RecordingServer(original):  # type: ignore[misc, valid-type]
            def __init__(self, *args: object, **kwargs: object) -> None:
                super().__init__(*args, **kwargs)
                created["server"] = self

        monkeypatch.setattr(report_server.http.server, "HTTPServer", RecordingServer)
        result: dict[str, object] = {}

        def _run() -> None:
            result["port"] = report_server.serve_report(
                root, port=0, oneshot=True, open_browser=False
            )

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        for _ in range(100):
            if "server" in created:
                break
            time.sleep(0.02)
        server = created["server"]
        port = server.server_port  # type: ignore[attr-defined]

        # The oneshot request that lets serve_report finish.
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/report.html", timeout=5) as resp:
            assert resp.status == 200
            assert "report</html>" in resp.read().decode("utf-8")

        thread.join(timeout=5)
        assert not thread.is_alive()
        assert result["port"] == port
