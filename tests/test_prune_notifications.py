"""Tests for the opt-in stale-lock notification hook."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import threading

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pure-locking"))

import prune_stale_locks  # noqa: E402


class _WebhookHandler(BaseHTTPRequestHandler):
    payload: dict | None = None

    def do_POST(self):  # noqa: N802
        length = int(self.headers["Content-Length"])
        self.__class__.payload = json.loads(self.rfile.read(length))
        self.send_response(204)
        self.end_headers()

    def log_message(self, *_args):
        return


def _expired_lock(path: Path) -> None:
    old = (datetime.now() - timedelta(hours=2)).isoformat(timespec="minutes")
    path.write_text(
        f"owner: test\ncreated: {old}\nexpires_after: 1h\n", encoding="utf-8"
    )


def test_prune_notifies_after_real_removals(monkeypatch, tmp_path: Path):
    expired = tmp_path / "LOCK.txt"
    _expired_lock(expired)
    monkeypatch.setattr(
        prune_stale_locks,
        "iter_lock_dirs",
        lambda _config: iter([tmp_path]),
    )

    server = HTTPServer(("127.0.0.1", 0), _WebhookHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/hook"
        removed = prune_stale_locks.prune(
            {}, webhook_url=url
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert removed == 1
    assert not expired.exists()
    assert _WebhookHandler.payload == {
        "event": "lock_expired",
        "removed": 1,
        "paths": [str(expired)],
        "timestamp": _WebhookHandler.payload["timestamp"],
    }


def test_prune_notifies_paths_and_skips_dry_run(monkeypatch, tmp_path: Path):
    expired = tmp_path / "LOCK.txt"
    _expired_lock(expired)
    notifications: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(
        prune_stale_locks,
        "iter_lock_dirs",
        lambda _config: iter([tmp_path]),
    )
    monkeypatch.setattr(
        prune_stale_locks,
        "notify_expired_locks",
        lambda url, paths: notifications.append((url, paths)) or True,
    )

    assert prune_stale_locks.prune({}, webhook_url="https://example.test/hook") == 1
    assert notifications == [("https://example.test/hook", [str(expired)])]
    assert not expired.exists()

    expired = tmp_path / "LOCK.again.txt"
    _expired_lock(expired)
    notifications.clear()
    assert prune_stale_locks.prune(
        {}, dry_run=True, webhook_url="https://example.test/hook"
    ) == 1
    assert notifications == []
    assert expired.exists()


def test_invalid_webhook_url_is_fail_closed_without_network(capsys):
    assert not prune_stale_locks.notify_expired_locks(
        "file:///tmp/notification", ["C:/project/LOCK.txt"]
    )
    assert "must use http(s)" in capsys.readouterr().out
