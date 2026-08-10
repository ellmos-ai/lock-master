"""Security-focused tests for watcher.web_server helpers."""

from __future__ import annotations

import http.client
import json
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pure-locking" / "watcher"))

import web_server  # noqa: E402


def test_resolve_within_blocks_traversal(tmp_path: Path):
    assert web_server._resolve_within(tmp_path, "../secret.md") is None
    assert web_server._resolve_within(tmp_path, "/tmp/secret.md") is None


def test_resolve_within_allows_safe_child(tmp_path: Path):
    assert web_server._resolve_within(tmp_path, "notes.md") == (tmp_path / "notes.md").resolve()


def test_safe_md_filename_rejects_paths_and_empty_values():
    assert web_server._safe_md_filename("") is None
    assert web_server._safe_md_filename("../notes.md") is None
    assert web_server._safe_md_filename("notes") == "notes.md"


def test_safe_header_value_blocks_response_splitting():
    assert web_server._safe_header_value("http://127.0.0.1:8095") == "http://127.0.0.1:8095"
    assert web_server._safe_header_value("http://127.0.0.1:8095\r\nX-Bad: 1") is None


def _handler_stub(host_header: str | None, port: int = 8095):
    import types

    handler = object.__new__(web_server.WatcherHandler)
    handler.headers = {} if host_header is None else {"Host": host_header}
    handler.server = types.SimpleNamespace(server_port=port)
    return handler


def test_host_allowed_accepts_loopback_only():
    for host in ("127.0.0.1:8095", "localhost:8095", "[::1]:8095",
                 "127.0.0.1", "localhost"):
        assert _handler_stub(host)._host_allowed(), host


def test_host_allowed_blocks_dns_rebinding_hosts():
    for host in ("evil.test:8095", "evil.test", "127.0.0.1.evil.test:8095",
                 "localhost:9999", None, "127.0.0.1:8095\r\nX-Bad: 1"):
        assert not _handler_stub(host)._host_allowed(), host


def test_canonical_allowed_origin_never_reflects_untrusted_header():
    assert web_server._canonical_allowed_origin(8095, "http://127.0.0.1:8095") == (
        "http://127.0.0.1:8095"
    )
    assert web_server._canonical_allowed_origin(8095, "http://localhost:8095") == (
        "http://localhost:8095"
    )
    assert web_server._canonical_allowed_origin(8095, "http://[::1]:8095") == (
        "http://[::1]:8095"
    )
    assert web_server._canonical_allowed_origin(8095, "http://evil.test:8095") is None
    assert web_server._canonical_allowed_origin(8095, "http://127.0.0.1:8095\r\nX-Bad: 1") is None


def test_loopback_http_server_enforces_host_and_origin_gates(monkeypatch, tmp_path: Path):
    """Exercise actual HTTP request handling instead of only handler helpers."""
    monkeypatch.setattr(web_server.config, "DB_PATH", tmp_path / "watcher.db")
    server = web_server.WatcherServer(0)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(method: str, path: str, **headers: str):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        try:
            connection.request(method, path, body="{}", headers=headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    try:
        allowed_host = f"127.0.0.1:{port}"
        origin = f"http://127.0.0.1:{port}"

        status, headers, body = request("GET", "/api/stats", Host=allowed_host)
        assert status == 200
        assert headers["Content-Type"] == "application/json; charset=utf-8"
        assert "scan_stale" in json.loads(body)

        status, headers, body = request(
            "POST",
            "/api/lock",
            Host=allowed_host,
            Origin=origin,
            **{"Content-Type": "application/json"},
        )
        assert status == 400
        assert json.loads(body) == {"error": "project_dir required"}
        assert headers["Access-Control-Allow-Origin"] == origin

        status, _, body = request("GET", "/api/stats", Host="evil.invalid")
        assert status == 403
        assert json.loads(body) == {"error": "Forbidden host"}
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
