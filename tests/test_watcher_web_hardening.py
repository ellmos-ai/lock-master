"""Request hardening of the loopback API (follow-ups of the ticket-form review) and optional host hooks."""
from __future__ import annotations

import http.client
import json
import os
import sys
import tempfile
import threading
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("LOCK_MASTER_WATCHER_DATA", tempfile.mkdtemp(prefix="lockmaster-web-hardening-"))
sys.path.insert(0, str(ROOT / "pure-locking" / "watcher"))

import config  # noqa: E402
import lock_utils  # noqa: E402
import rooms  # noqa: E402
import web_server  # noqa: E402


class _Writer:
    def __init__(self):
        self.calls = []

    def create(self, title, body, project=None, priority="mittel"):
        self.calls.append(title)
        return "INBOX/T-20000101-000000001.txt"


class _Hook:
    def __init__(self, fail=False):
        self.events, self.routed, self.fail = [], [], fail

    def record_event(self, kind, text):
        if self.fail:
            raise RuntimeError("boom")
        self.events.append((kind, text))

    def route_task(self, text):
        self.routed.append(text)
        return {"gang": "fast", "score": 3}


def _serve(monkeypatch, tmp_path, modules: dict):
    monkeypatch.setattr(web_server.config, "DB_PATH", tmp_path / "watcher.db")
    monkeypatch.setattr(rooms, "is_path_in_roots", lambda p: os.path.normpath(p).startswith(str(tmp_path)))
    monkeypatch.setattr(web_server, "_optional_module", lambda name, env: modules.get(name))
    server = web_server.WatcherServer(0)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(method, path, raw: bytes | None = None, headers: dict | None = None):
        hdrs = {"Host": f"127.0.0.1:{port}", "Origin": f"http://127.0.0.1:{port}", **(headers or {})}
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            for key, value in hdrs.items():
                conn.putheader(key, value)
            if raw is not None and "Content-Length" not in hdrs:
                conn.putheader("Content-Length", str(len(raw)))
            conn.endheaders(raw or b"")
            resp = conn.getresponse()
            data = resp.read()
            is_json = resp.getheader("Content-Type", "").startswith("application/json")
            return resp.status, (json.loads(data) if data and is_json else data), resp
        finally:
            conn.close()

    def stop():
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    return request, stop


def _json(value) -> bytes:
    return json.dumps(value).encode("utf-8")


def test_malformed_bodies_get_a_clean_400(monkeypatch, tmp_path):
    request, stop = _serve(monkeypatch, tmp_path, {"ticket_writer": _Writer()})
    try:
        # b"[" * 5000: nesting beyond the interpreter limit raised RecursionError and dropped the connection
        for raw in (b"[1, 2]", b'"text"', b"42", b"null", b"{not json", b"\xff\xfe", b"[" * 5000):
            status, body, _ = request("POST", "/api/ticket", raw)
            assert status == 400, raw
            assert "error" in body
        assert request("PUT", "/api/settings", b"[1]")[0] == 400
        assert request("PUT", "/api/profile", b"7")[0] == 400
    finally:
        stop()


def test_a_field_of_the_wrong_type_is_a_400_not_a_dropped_connection(monkeypatch, tmp_path):
    request, stop = _serve(monkeypatch, tmp_path, {})
    try:
        for path, payload in (
            ("/api/user-lock", {"project_dir": 5}),
            ("/api/user-lock", {"project_dir": str(tmp_path), "scope": ["x"]}),
            ("/api/lock", {"project_dir": {"a": 1}}),
            ("/api/room-file", {"room_key": 1, "filename": 2}),
        ):
            status, body, _ = request("POST", path, _json(payload))
            assert status == 400 and body["error"], (path, payload)
    finally:
        stop()


def test_content_length_is_validated_and_capped(monkeypatch, tmp_path):
    request, stop = _serve(monkeypatch, tmp_path, {})
    try:
        assert request("POST", "/api/ticket", b"", {"Content-Length": "abc"})[0] == 400
        assert request("POST", "/api/ticket", b"", {"Content-Length": "-5"})[0] == 400
        # declared above the cap: refused before the body is read
        status, body, _ = request("POST", "/api/ticket", b"", {"Content-Length": str(web_server.MAX_BODY_BYTES + 1)})
        assert status == 413 and "too large" in body["error"]
        # a body within the cap still works
        big = _json({"title": "t", "body": "x" * 200_000})
        assert request("POST", "/api/ticket", big)[0] == 501  # no ticket-master: reached the handler
    finally:
        stop()


def test_a_foreign_origin_is_refused_for_every_write_route(monkeypatch, tmp_path):
    request, stop = _serve(monkeypatch, tmp_path, {"ticket_writer": _Writer()})
    try:
        for method, path in (("POST", "/api/ticket"), ("POST", "/api/user-lock"), ("PUT", "/api/settings")):
            status, _body, _ = request(method, path, _json({"title": "x"}), {"Origin": "https://evil.example"})
            assert status == 403, (method, path)
    finally:
        stop()


def test_user_locks_carry_the_protection_fields_and_are_recognised(monkeypatch, tmp_path):
    request, stop = _serve(monkeypatch, tmp_path, {})
    try:
        status, body, _ = request("POST", "/api/user-lock", _json(
            {"project_dir": str(tmp_path), "scope": "hold", "purpose": "keep out"}))
        assert status == 201 and body["filename"] == "LOCK.user.hold.txt"
        text = (tmp_path / "LOCK.user.hold.txt").read_text(encoding="utf-8")
        assert "removable_by: user" in text and "mode: hard" in text and "purpose: keep out" in text
        assert "expires_after" not in text  # a user lock never expires by time
        data = lock_utils.parse_lock_file(tmp_path / "LOCK.user.hold.txt")
        assert lock_utils.is_user_lock(tmp_path / "LOCK.user.hold.txt", data)
        assert request("POST", "/api/user-lock", _json({"project_dir": str(tmp_path), "scope": "hold"}))[0] == 409
    finally:
        stop()


# --- optional host hook (cc_integrations) ------------------------------------------------------


def test_the_integration_hook_records_the_ticket_and_adds_a_routing_hint(monkeypatch, tmp_path):
    hook = _Hook()
    request, stop = _serve(monkeypatch, tmp_path, {"ticket_writer": _Writer(), "cc_integrations": hook})
    try:
        status, body, _ = request("POST", "/api/ticket", _json({"title": "Fix", "body": "Details"}))
        assert status == 201 and body["routing"] == {"gang": "fast", "score": 3}
        assert hook.events and hook.events[0][0] == "ticket" and "Fix" in hook.events[0][1]
        assert hook.routed == ["Fix\nDetails"]
    finally:
        stop()


def test_a_failing_or_missing_hook_never_fails_the_request(monkeypatch, tmp_path):
    writer = _Writer()
    request, stop = _serve(monkeypatch, tmp_path, {"ticket_writer": writer, "cc_integrations": _Hook(fail=True)})
    try:
        status, body, _ = request("POST", "/api/ticket", _json({"title": "A"}))
        assert status == 201 and "routing" not in body
    finally:
        stop()
    request, stop = _serve(monkeypatch, tmp_path, {"ticket_writer": writer})
    try:
        status, body, _ = request("POST", "/api/ticket", _json({"title": "B"}))
        assert status == 201 and "routing" not in body and writer.calls == ["A", "B"]
    finally:
        stop()


# --- icons from the host overlay -----------------------------------------------------------------


def test_overlay_icons_are_listed_and_served(monkeypatch, tmp_path):
    icons = tmp_path / "overlay" / "icons"
    icons.mkdir(parents=True)
    (icons / "room_a.svg").write_text("<svg id='a'/>", encoding="utf-8")
    monkeypatch.setattr(config, "OVERLAY_ICONS_DIR", icons)
    request, stop = _serve(monkeypatch, tmp_path, {})
    try:
        status, body, _ = request("GET", "/api/icons")
        assert status == 200 and "room_a.svg" in body
        status, data, resp = request("GET", "/icons/room_a.svg")
        assert status == 200 and data == b"<svg id='a'/>" and resp.getheader("Content-Type") == "image/svg+xml"
        # not in the overlay and not shipped: falls back to the index page, never to a path outside
        assert request("GET", "/icons/..%2F..%2Fapp.py")[0] in (200, 403, 404)
        assert request("GET", "/icons/room_a.svg%00.txt")[0] in (200, 403, 404)  # NUL: answered, not dropped
    finally:
        stop()


# --- helpers that had no test --------------------------------------------------------------------


def test_a_nul_in_a_path_is_refused_not_a_crash(tmp_path):
    assert web_server._resolve_within(tmp_path, "a\x00b.md") is None
    assert web_server._resolve_within(tmp_path, "icons/ok.svg\x00.txt", allow_subdirs=True) is None


def test_calc_remaining():
    calc = web_server.WatcherHandler._calc_remaining
    assert calc(None) is None
    assert calc((datetime.now() - timedelta(hours=1)).isoformat()) == "abgelaufen"
    assert "5h" in calc((datetime.now() + timedelta(hours=5, minutes=30)).isoformat())
    assert calc("not-a-date") is None


def test_scope_and_name_regexes_reject_traversal():
    assert all(web_server._SCOPE_RE.match(s) for s in ["desktop", "my-component", "v2_beta", "TEST"])
    assert not any(web_server._SCOPE_RE.match(s) for s in ["../evil", "a/b", "a\\b", "a b", ".."])
    assert not any(web_server._SAFE_NAME_RE.match(s) for s in ["../evil.md", "C:\\Windows\\x", "a/b.md"])
