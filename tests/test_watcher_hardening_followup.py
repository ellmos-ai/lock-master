"""Regression tests for watcher follow-up hardening (scan_partial, room validation, framing, int64, atomic writes)."""
from __future__ import annotations

import atexit
import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if "LOCK_MASTER_WATCHER_DATA" not in os.environ:
    _DEFAULT_DATA_DIR = tempfile.mkdtemp(prefix="lockmaster-followup-")
    atexit.register(shutil.rmtree, _DEFAULT_DATA_DIR, True)
    os.environ["LOCK_MASTER_WATCHER_DATA"] = _DEFAULT_DATA_DIR
sys.path.insert(0, str(ROOT / "pure-locking" / "watcher"))
sys.path.insert(0, str(ROOT / "pure-locking"))

import cache_writer  # noqa: E402
import config  # noqa: E402
import lock_scan  # noqa: E402
import lock_watcher  # noqa: E402
import rooms  # noqa: E402
import web_server  # noqa: E402


def _serve(monkeypatch, tmp_path: Path):
    root = tmp_path / "workspace" / "alpha"
    root.mkdir(parents=True, exist_ok=True)
    roots_file = tmp_path / "roots.json"
    roots_file.write_text(
        json.dumps({"roots": [{"path": str(root)}], "caches": []}),
        encoding="utf-8",
    )
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(config, "_LOCAL_DATA_DIR", data_dir)
    monkeypatch.setattr(config, "DB_PATH", data_dir / "watcher.db")
    monkeypatch.setattr(config, "DAEMON_STATUS_PATH", data_dir / "daemon_status.json")
    monkeypatch.setattr(config, "RUNTIME_SETTINGS_PATH", data_dir / "runtime_settings.json")
    monkeypatch.setattr(config, "OVERLAY_FILE", tmp_path / "overlay" / "rooms.overlay.json")
    monkeypatch.setattr(config, "OVERLAY_ICONS_DIR", tmp_path / "overlay" / "icons")
    monkeypatch.setattr(config, "_roots_file_candidates", lambda: [roots_file])
    monkeypatch.setattr(rooms, "_ROOMS_FILE", data_dir / "rooms.json")
    monkeypatch.setattr(web_server, "USER_PROFILE_FILE", data_dir / "user_profile.json")
    rooms.invalidate_cache()

    server = web_server.WatcherServer(0)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(
        method: str,
        path: str,
        raw: bytes | None = None,
        headers: list[tuple[str, str]] | dict[str, str] | None = None,
        auto_clen: bool = True,
    ):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            extra = list(headers.items()) if isinstance(headers, dict) else list(headers or [])
            lower_keys = {k.lower() for k, _ in extra}
            if "host" not in lower_keys:
                conn.putheader("Host", f"127.0.0.1:{port}")
            if method != "GET" and "origin" not in lower_keys:
                conn.putheader("Origin", f"http://127.0.0.1:{port}")
            for k, v in extra:
                conn.putheader(k, v)
            if raw is not None and auto_clen and "content-length" not in lower_keys:
                conn.putheader("Content-Length", str(len(raw)))
            conn.endheaders(raw or b"")
            resp = conn.getresponse()
            data = resp.read()
            is_json = resp.getheader("Content-Type", "").startswith("application/json")
            return resp.status, (json.loads(data) if data and is_json else data)
        finally:
            conn.close()

    def stop():
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
        rooms.invalidate_cache()

    return request, stop, root, data_dir


def _json(obj: object) -> bytes:
    return json.dumps(obj).encode("utf-8")


def test_trigger_scan_reports_partial_and_warnings_when_root_times_out(monkeypatch, tmp_path):
    request, stop, root, _ = _serve(monkeypatch, tmp_path)
    try:
        monkeypatch.setattr(
            lock_watcher,
            "_run_full_scan",
            lambda _db, _cfg, _upd: {
                "total": 2,
                "new": 1,
                "modified": 0,
                "expired": 0,
                "deleted": 0,
                "roots": [{"path": str(root), "dirs": 5, "seconds": 301.2, "timed_out": True}],
                "roots_scanned": 1,
            },
        )
        status, body = request("POST", "/api/scan", _json({}))
        assert status == 200
        assert body["locks_found"] == 2 and body["new"] == 1
        assert body["scan_partial"] is True
        assert isinstance(body["scan_warnings"], list) and len(body["scan_warnings"]) >= 1
        assert str(root) in body["scan_warnings"][0]
        assert body["message"] != "Scan complete" and "incomplete" in body["message"].lower()

        monkeypatch.setattr(
            lock_watcher,
            "_run_full_scan",
            lambda _db, _cfg, _upd: {
                "total": 1,
                "new": 0,
                "modified": 0,
                "expired": 0,
                "deleted": 0,
                "roots": [{"path": str(root), "dirs": 5, "seconds": 0.4, "timed_out": False}],
                "roots_scanned": 1,
            },
        )
        status_ok, body_ok = request("POST", "/api/scan", _json({}))
        assert status_ok == 200
        assert body_ok["scan_partial"] is False
        assert body_ok["scan_warnings"] == []
        assert body_ok["message"] == "Scan complete"
    finally:
        stop()


def test_cache_writer_daemon_status_reflects_scan_partial_and_warnings(monkeypatch, tmp_path):
    cache_dir = tmp_path / "cache"
    status_file = tmp_path / "daemon_status.json"
    monkeypatch.setattr(config, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(config, "DAEMON_STATUS_PATH", status_file)
    monkeypatch.setattr(cache_writer, "_pid_is_running", lambda _pid: True)

    now = datetime(2026, 11, 15, 10, 0, 0, tzinfo=timezone.utc)
    status_file.write_text(
        json.dumps({
            "pid": 4321,
            "host": "TEST-HOST",
            "last_seen": "2026-11-15T10:00:00+00:00",
            "scan_partial": True,
            "scan_warnings": ["Root /slow nach 301s abgebrochen (Budget 300s): Scan unvollstaendig"],
        }),
        encoding="utf-8",
    )
    cache_writer._write_detail_cache([], now)
    written = list(cache_dir.glob("LOCK-CACHE*.md"))
    assert len(written) == 1
    text = written[0].read_text(encoding="utf-8")
    lines = text.splitlines()
    assert "Daemon: läuft" not in lines
    assert any("eingeschränkt" in ln and "scan_partial" in ln for ln in lines)
    assert any("Root /slow nach 301s abgebrochen" in ln for ln in lines)

    # Slow but complete scan keeps "läuft" while still surfacing scan_warnings.
    lines_warn = cache_writer._format_daemon_status(now)
    assert any("scan_partial" in ln for ln in lines_warn)
    status_file.write_text(
        json.dumps({
            "pid": 4321,
            "host": "TEST-HOST",
            "last_seen": "2026-11-15T10:00:00+00:00",
            "scan_partial": False,
            "scan_warnings": ["Full-Scan dauerte 400s (Warnschwelle 300s)"],
        }),
        encoding="utf-8",
    )
    lines_slow = cache_writer._format_daemon_status(now)
    assert "Daemon: läuft" in lines_slow
    assert any("Full-Scan dauerte 400s" in ln for ln in lines_slow)

    # Healthy scan emits the classic 3-line block without warnings.
    status_file.write_text(
        json.dumps({
            "pid": 4321,
            "host": "TEST-HOST",
            "last_seen": "2026-11-15T10:00:00+00:00",
            "scan_partial": False,
            "scan_warnings": [],
        }),
        encoding="utf-8",
    )
    lines_clean = cache_writer._format_daemon_status(now)
    assert lines_clean[0] == "Daemon: läuft"
    assert len(lines_clean) == 3


def test_frontend_esc_escapes_quotes_and_avoids_inline_string_handlers():
    app_js = (ROOT / "pure-locking" / "watcher" / "static" / "app.js").read_text(encoding="utf-8")
    assert "&quot;" in app_js and "&#39;" in app_js
    assert "div.innerHTML" not in app_js
    assert "style=\"background:${room.color}\"" not in app_js
    assert 'value="${room.color}"' not in app_js
    assert "selectIcon(this, '${" not in app_js
    assert "showRoomDetail('${" not in app_js
    assert "result.scan_partial" in app_js

    node = shutil.which("node")
    if node:
        snippet = (
            "const fs = require('fs');"
            f"const src = fs.readFileSync({json.dumps(str(ROOT / 'pure-locking' / 'watcher' / 'static' / 'app.js'))}, 'utf8');"
            "const m = src.match(/function esc\\(str\\)\\s*\\{[\\s\\S]*?\\n\\}/);"
            "if (!m) process.exit(2);"
            "const fn = new Function(m[0] + '; return esc;')();"
            "const out = fn('<img src=\"x\" onclick=\\'y\\'>&');"
            "if (out !== '&lt;img src=&quot;x&quot; onclick=&#39;y&#39;&gt;&amp;') {"
            "  console.error(out); process.exit(1);"
            "}"
        )
        proc = subprocess.run([node, "-e", snippet], capture_output=True, text=True, check=False)
        assert proc.returncode == 0, proc.stderr


def test_update_room_validates_color_label_and_icon(monkeypatch, tmp_path):
    request, stop, _, data_dir = _serve(monkeypatch, tmp_path)
    try:
        bad_payloads = [
            {"color": "red"},
            {"color": "#12345g"},
            {"color": "#12"},
            {"color": "#112233445"},
            {"color": "black;background:url(x)"},
            {"color": "#ff0000\nevil"},
            {"color": ""},
            {"color": 123},
            {"label": ""},
            {"label": "   "},
            {"label": "line1\nline2"},
            {"label": "nul\x00byte"},
            {"label": "x" * 200},
            {"label": 42},
            {"icon": "../app.js"},
            {"icon": "icons/ok.svg"},
            {"icon": "sub\\ok.svg"},
            {"icon": "bad'quote.svg"},
            {"icon": 'bad"quote.png'},
            {"icon": "notes.txt"},
            {"icon": "ok\x00.svg"},
            {"icon": ".."},
            {"icon": 99},
            {"notes_target": "invalid_target"},
            {"notes_filename": "../outside.md"},
            {"notes": 123},
            {"goals": ["not-a-str"]},
        ]
        for payload in bad_payloads:
            status, body = request("PUT", "/api/room/alpha", _json(payload))
            assert status == 400 and "error" in body, payload

        assert not (data_dir / "rooms.json").exists()

        status_ok, _ = request(
            "PUT",
            "/api/room/alpha",
            _json({"label": "Alpha & 'Friends'", "color": "#1a2b3c", "icon": "room_a.svg"}),
        )
        assert status_ok == 200
        status_clear_icon, _ = request("PUT", "/api/room/alpha", _json({"icon": ""}))
        assert status_clear_icon == 200
        saved = json.loads((data_dir / "rooms.json").read_text(encoding="utf-8"))
        assert saved["alpha"]["color"] == "#1a2b3c"
        assert saved["alpha"]["icon"] == ""
    finally:
        stop()


def test_read_body_rejects_transfer_encoding_and_duplicate_content_length(monkeypatch, tmp_path):
    request, stop, _, _ = _serve(monkeypatch, tmp_path)
    scan_calls = []
    monkeypatch.setattr(lock_watcher, "_run_full_scan", lambda *a, **k: scan_calls.append(1) or {"total": 0})
    try:
        status_te, body_te = request(
            "POST",
            "/api/scan",
            raw=b"2\r\n{}\r\n0\r\n\r\n",
            headers=[("Transfer-Encoding", "chunked")],
            auto_clen=False,
        )
        assert status_te == 400 and "error" in body_te

        status_dup_same, body_dup_same = request(
            "POST",
            "/api/scan",
            raw=b"{}",
            headers=[("Content-Length", "2"), ("Content-Length", "2")],
            auto_clen=False,
        )
        assert status_dup_same == 400 and "error" in body_dup_same

        status_dup_diff, body_dup_diff = request(
            "POST",
            "/api/scan",
            raw=b"{}",
            headers=[("Content-Length", "2"), ("Content-Length", "10")],
            auto_clen=False,
        )
        assert status_dup_diff == 400 and "error" in body_dup_diff
        assert scan_calls == []
    finally:
        stop()


def test_int64_bounds_and_get_error_guards_never_drop_connection(monkeypatch, tmp_path):
    request, stop, root, data_dir = _serve(monkeypatch, tmp_path)
    try:
        for bad_id in ("9223372036854775808", "-9223372036854775809", "0", "-1", "1_0"):
            st_get, body_get = request("GET", f"/api/lock/{bad_id}")
            assert st_get == 400 and "error" in body_get, bad_id
            st_put, body_put = request("PUT", f"/api/lock/{bad_id}/name", _json({"name": "x"}))
            assert st_put == 400 and "error" in body_put, bad_id

        st_max, _ = request("GET", "/api/lock/9223372036854775807")
        assert st_max == 404

        for bad_limit in ("9223372036854775808", "-9223372036854775809", "-1", "1_0", "abc"):
            st_hist, body_hist = request("GET", f"/api/room/alpha/history?limit={bad_limit}")
            assert st_hist == 400 and "error" in body_hist, bad_limit

        st_hist_ok, body_hist_ok = request("GET", "/api/room/alpha/history?limit=0")
        assert st_hist_ok == 200 and body_hist_ok == []

        central_dir = data_dir / "central_files"
        central_dir.mkdir(parents=True, exist_ok=True)
        (central_dir / "corrupt.md").write_bytes(b"\xff\xfe\x80invalid-utf8")
        (root / "corrupt.md").write_bytes(b"\xff\xfe\x80invalid-utf8")

        st_central, body_central = request("GET", "/api/central-file/corrupt.md")
        assert st_central in (400, 500) and "error" in body_central

        st_room_file, body_room_file = request("GET", "/api/room-file/alpha/corrupt.md")
        assert st_room_file in (400, 500) and "error" in body_room_file
    finally:
        stop()


def test_atomic_writes_replace_via_temp_file_and_clean_up_on_failure(monkeypatch, tmp_path):
    cache_target = tmp_path / "cache" / "LOCK-CACHE.TEST.md"
    rooms_target = tmp_path / "data" / "rooms.json"
    profile_target = tmp_path / "data" / "user_profile.json"
    cache_target.parent.mkdir(parents=True, exist_ok=True)
    rooms_target.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(rooms, "_ROOMS_FILE", rooms_target)
    monkeypatch.setattr(web_server, "USER_PROFILE_FILE", profile_target)

    replaced: list[tuple[Path, Path]] = []
    real_replace = os.replace

    def tracking_replace(src, dst):
        s_path, d_path = Path(src), Path(dst)
        replaced.append((s_path, d_path))
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", tracking_replace)

    assert lock_scan.write_if_changed(cache_target, "# Cache v1\n") is True
    rooms._save_rooms_config({"alpha": {"label": "Alpha"}})
    web_server._save_user_profile({"name": "Tester", "host": "TEST"})

    targets = [dst for _, dst in replaced]
    assert cache_target in targets
    assert rooms_target in targets
    assert profile_target in targets
    for src, dst in replaced:
        assert src.parent == dst.parent
        assert src != dst

    # Simulate os.replace failure: target contents must remain intact and temp files must be cleaned up.
    cache_before = cache_target.read_text(encoding="utf-8")
    rooms_before = rooms_target.read_text(encoding="utf-8")
    profile_before = profile_target.read_text(encoding="utf-8")

    def failing_replace(_src, _dst):
        raise PermissionError("simulated replace error")

    monkeypatch.setattr(os, "replace", failing_replace)

    with pytest.raises(PermissionError):
        lock_scan.write_if_changed(cache_target, "# Cache v2\n")
    with pytest.raises(PermissionError):
        rooms._save_rooms_config({"alpha": {"label": "Broken"}})
    with pytest.raises(PermissionError):
        web_server._save_user_profile({"name": "Broken", "host": "TEST"})

    assert cache_target.read_text(encoding="utf-8") == cache_before
    assert rooms_target.read_text(encoding="utf-8") == rooms_before
    assert profile_target.read_text(encoding="utf-8") == profile_before
    assert list(cache_target.parent.glob("*.tmp")) == []
    assert list(rooms_target.parent.glob("*.tmp")) == []


def test_early_rejected_bodies_do_not_reset_connection(monkeypatch, tmp_path):
    request, stop, _, _ = _serve(monkeypatch, tmp_path)
    payload = b'{"padding":"' + b"x" * 512 + b'"}'
    routes = [
        ("POST", "/api/lock"),
        ("POST", "/api/scan"),
        ("POST", "/api/user-lock"),
        ("POST", "/api/ticket"),
        ("PUT", "/api/settings"),
        ("PUT", "/api/profile"),
        ("PUT", "/api/room/alpha"),
        ("PUT", "/api/lock/1/name"),
    ]
    try:
        for _ in range(5):
            for method, path in routes:
                st, body = request(
                    method,
                    path,
                    raw=payload,
                    headers=[("Content-Length", "-5")],
                    auto_clen=False,
                )
                assert st == 400 and "error" in body, (method, path)
    finally:
        stop()


def test_symlink_targets_refused_for_atomic_writes_and_user_notes(monkeypatch, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("do-not-overwrite\n", encoding="utf-8")
    link_cache = tmp_path / "cache" / "LOCK-CACHE.TEST.md"
    link_cache.parent.mkdir(parents=True, exist_ok=True)
    try:
        link_cache.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted in this environment")

    with pytest.raises(OSError):
        lock_scan.write_if_changed(link_cache, "# Overwritten\n")
    assert outside.read_text(encoding="utf-8") == "do-not-overwrite\n"

    root = tmp_path / "room_root"
    root.mkdir()
    notes_link = root / "USER-NOTES.md"
    notes_link.symlink_to(outside)
    roots_file = tmp_path / "roots.json"
    roots_file.write_text(json.dumps({"roots": [{"path": str(root)}], "caches": []}), encoding="utf-8")
    monkeypatch.setattr(config, "_roots_file_candidates", lambda: [roots_file])
    monkeypatch.setattr(rooms, "_ROOMS_FILE", tmp_path / "rooms.json")
    rooms.invalidate_cache()
    key = rooms.get_rooms()[0]["key"]
    rooms.update_room(key, notes="hello", notes_target="own_file", notes_filename="USER-NOTES.md")
    rooms.write_user_notes(key)
    assert outside.read_text(encoding="utf-8") == "do-not-overwrite\n"
