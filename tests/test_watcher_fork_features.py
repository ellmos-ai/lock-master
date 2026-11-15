"""Watcher fork features brought into the repo (T-20261115-353226340 / T-20261115-837915758).

* The overrun warning follows the runtime `full_scan_interval` instead of an import-time constant.
* Ticket form and control-file panel (optional ticket-master helpers) behind the loopback API.
"""
from __future__ import annotations

import http.client
import json
import os
import sys
import tempfile
import threading
import time
import types
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WATCHER = ROOT / "pure-locking" / "watcher"
os.environ.setdefault(
    "LOCK_MASTER_WATCHER_DATA",
    str(Path(tempfile.mkdtemp(prefix="lockmaster-fork-features-"))),
)
sys.path.insert(0, str(WATCHER))

import cache_writer  # noqa: E402
import config  # noqa: E402
import lock_watcher  # noqa: E402
import web_server  # noqa: E402


def _runtime(monkeypatch, tmp_path: Path, **values):
    settings = tmp_path / "runtime_settings.json"
    settings.write_text(json.dumps(values), encoding="utf-8")
    monkeypatch.setattr(config, "RUNTIME_SETTINGS_PATH", settings)


# --- overrun warning follows the runtime interval ---------------------------------------


def test_overrun_threshold_defaults_to_five_intervals(monkeypatch, tmp_path):
    _runtime(monkeypatch, tmp_path)  # empty settings file = defaults
    assert lock_watcher.scan_overrun_warn_seconds() == config.FULL_SCAN_INTERVAL * 5
    assert lock_watcher.scan_overrun_warn_seconds() == lock_watcher.SCAN_OVERRUN_WARN_SECONDS


def test_overrun_threshold_follows_a_changed_interval(monkeypatch, tmp_path):
    _runtime(monkeypatch, tmp_path, full_scan_interval=120)
    assert lock_watcher.scan_overrun_warn_seconds() == 600
    _runtime(monkeypatch, tmp_path, full_scan_interval=30)
    # A shorter interval must lower the threshold: the old max() with the constant kept it at 300.
    assert lock_watcher.scan_overrun_warn_seconds() == 150


def test_stale_threshold_uses_the_runtime_overrun_margin(monkeypatch, tmp_path):
    _runtime(monkeypatch, tmp_path, full_scan_interval=120)
    assert lock_watcher.scan_stale_after_seconds(None) == 600 + 120
    status = {"next_full_scan_gap_s": 500.0, "last_scan_duration_s": 100.0}
    assert lock_watcher.scan_stale_after_seconds(status) == 600 + 500 + 100


def test_api_marks_a_scan_stuck_by_the_runtime_threshold(monkeypatch, tmp_path):
    from datetime import datetime, timedelta

    _runtime(monkeypatch, tmp_path, full_scan_interval=30)  # overrun threshold 150 s
    started = (datetime.now() - timedelta(seconds=200)).isoformat(timespec="seconds")
    status = {"pid": os.getpid(), "scan_in_progress": True, "scan_started_at": started,
              "last_seen": datetime.now().isoformat(timespec="seconds")}
    monkeypatch.setattr(lock_watcher, "load_daemon_status", lambda: status)
    monkeypatch.setattr(lock_watcher, "_daemon_status_is_fresh", lambda _s: True)
    # 200 s > 150 s (runtime) although < 300 s (the old import-time constant)
    assert web_server._daemon_status_for_api(None)["state"] == "degraded"
    _runtime(monkeypatch, tmp_path, full_scan_interval=120)  # threshold 600 s
    assert web_server._daemon_status_for_api(None)["state"] == "running"


def test_cache_writer_fresh_limit_is_the_runtime_stale_setting(monkeypatch, tmp_path):
    _runtime(monkeypatch, tmp_path)
    assert cache_writer._daemon_fresh_limit_seconds() == config.STALE_DAEMON_SECONDS
    _runtime(monkeypatch, tmp_path, stale_daemon_seconds=900)
    assert cache_writer._daemon_fresh_limit_seconds() == 900


def test_daemon_code_no_longer_reads_the_import_time_constants():
    source = (WATCHER / "lock_watcher.py").read_text(encoding="utf-8")
    body = source.split("def run_daemon", 1)[1]
    assert "SCAN_OVERRUN_WARN_SECONDS" not in body
    assert "config.FULL_SCAN_INTERVAL" not in body
    assert "scan_overrun_warn_seconds()" in body
    assert "SCAN_OVERRUN_WARN_SECONDS" not in (WATCHER / "web_server.py").read_text(encoding="utf-8")


# --- optional ticket-master helpers behind the API -------------------------------------


class FakeDocs:
    DOC_KINDS = {"todo": ("TODO.md", ()), "done": ("DONE.md", ())}

    def __init__(self):
        self.calls = []

    def scan_docs(self, root):
        return {"todo": {"exists": False, "path": None, "lines": 0}}

    def ensure_doc(self, root, kind):
        self.calls.append(("ensure", Path(root).name, kind))
        return str(Path(root) / self.DOC_KINDS[kind][0])

    def append_entry(self, path, text):
        self.calls.append(("append", Path(path).name, text))


class FakeWriter:
    def __init__(self):
        self.calls = []

    def create(self, title, body, **kwargs):
        self.calls.append((title, body, kwargs))
        return "INBOX/T-20261115-000000001.txt"


def _serve(monkeypatch, tmp_path, project: Path, modules: dict):
    monkeypatch.setattr(web_server.config, "DB_PATH", tmp_path / "watcher.db")
    monkeypatch.setattr(web_server.rooms, "is_path_in_roots",
                        lambda p: os.path.normpath(p).lower().startswith(str(project).lower()))
    monkeypatch.setattr(web_server, "_ticket_master_module", lambda name: modules.get(name))
    server = web_server.WatcherServer(0)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(method, path, body=None):
        headers = {"Host": f"127.0.0.1:{port}", "Origin": f"http://127.0.0.1:{port}",
                   "Content-Type": "application/json"}
        for attempt in range(3):
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
                try:
                    conn.request(method, path, body=json.dumps(body or {}), headers=headers)
                    resp = conn.getresponse()
                    return resp.status, json.loads(resp.read() or b"{}")
                finally:
                    conn.close()
            except (ConnectionError, OSError):
                if attempt == 2:
                    raise
                time.sleep(0.05)

    def stop():
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    return request, stop


def test_ticket_form_creates_a_ticket_and_sanitises_single_line_fields(monkeypatch, tmp_path):
    writer = FakeWriter()
    request, stop = _serve(monkeypatch, tmp_path, tmp_path, {"ticket_writer": writer})
    try:
        status, body = request("POST", "/api/ticket", {
            "title": "  Titel\nSTATUS: DONE  ", "body": "Text", "project": "p\r\nX: y", "priority": "HOCH"})
        assert status == 201 and body["created"].endswith(".txt")
        title, text, kwargs = writer.calls[0]
        assert title == "Titel STATUS: DONE" and "\n" not in kwargs["project"]
        assert kwargs["priority"] == "hoch" and "tickets_dir" not in kwargs
        assert request("POST", "/api/ticket", {"title": "  "})[0] == 400
        assert request("POST", "/api/ticket", {"title": "x", "priority": "x\nSTATUS: y"})[0] == 400
        assert len(writer.calls) == 1
    finally:
        stop()


def test_ticket_form_and_docs_answer_501_without_ticket_master(monkeypatch, tmp_path):
    request, stop = _serve(monkeypatch, tmp_path, tmp_path, {})
    try:
        assert request("POST", "/api/ticket", {"title": "x"})[0] == 501
        assert request("GET", "/api/project-docs?project_dir=" + urllib.parse.quote(str(tmp_path), safe=""))[0] == 501
        assert request("POST", "/api/project-docs", {"project_dir": str(tmp_path), "kind": "todo"})[0] == 501
    finally:
        stop()


def test_project_docs_are_confined_to_the_configured_roots(monkeypatch, tmp_path):
    docs = FakeDocs()
    project = tmp_path / "proj"
    project.mkdir()
    request, stop = _serve(monkeypatch, tmp_path, project, {"doc_scanner": docs})
    try:
        status, body = request("GET", "/api/project-docs?project_dir=" + urllib.parse.quote(str(project), safe=""))
        assert status == 200 and body["todo"]["exists"] is False
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        assert request("GET", "/api/project-docs?project_dir=" + urllib.parse.quote(str(outside), safe=""))[0] == 403
        assert request("POST", "/api/project-docs", {"project_dir": str(outside), "kind": "todo"})[0] == 403
        assert request("POST", "/api/project-docs", {"project_dir": str(project / "missing"), "kind": "todo"})[0] == 403
        assert docs.calls == []
    finally:
        stop()


def test_project_docs_actions_validate_kind_action_and_text(monkeypatch, tmp_path):
    docs = FakeDocs()
    project = tmp_path / "proj"
    project.mkdir()
    request, stop = _serve(monkeypatch, tmp_path, project, {"doc_scanner": docs})
    try:
        base = {"project_dir": str(project)}
        assert request("POST", "/api/project-docs", {**base, "kind": "../x"})[0] == 400
        assert request("POST", "/api/project-docs", {**base, "kind": "todo", "action": "delete"})[0] == 400
        assert request("POST", "/api/project-docs", {**base, "kind": "todo", "action": "append"})[0] == 400
        assert docs.calls == []
        status, body = request("POST", "/api/project-docs", {**base, "kind": "todo", "action": "ensure"})
        assert status == 200 and body["path"].endswith("TODO.md")
        status, _ = request("POST", "/api/project-docs", {**base, "kind": "done", "action": "append", "text": "- x"})
        assert status == 200 and docs.calls[-1] == ("append", "DONE.md", "- x")
    finally:
        stop()


def test_ticket_master_module_lookup_uses_the_lib_env_and_degrades_to_none(monkeypatch, tmp_path):
    monkeypatch.delenv(web_server.TICKET_MASTER_LIB_ENV, raising=False)
    assert web_server._ticket_master_module("tm_probe_module_that_does_not_exist") is None
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "tm_probe_module.py").write_text("VALUE = 42\n", encoding="utf-8")
    monkeypatch.setenv(web_server.TICKET_MASTER_LIB_ENV, str(lib))
    monkeypatch.setattr(sys, "path", list(sys.path))
    module = web_server._ticket_master_module("tm_probe_module")
    assert isinstance(module, types.ModuleType) and module.VALUE == 42
    sys.modules.pop("tm_probe_module", None)
