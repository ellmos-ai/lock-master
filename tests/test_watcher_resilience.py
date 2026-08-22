"""Regression coverage for the portable watcher backalignment.

These cases protect the failure modes from T-20260715-01: missing external
roots configuration, a heartbeat blocked by a long scan, stale scan data shown
as healthy, and protected locks expiring in the derived watcher database.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WATCHER = ROOT / "pure-locking" / "watcher"
os.environ.setdefault(
    "LOCK_MASTER_WATCHER_DATA",
    str(Path(tempfile.mkdtemp(prefix="lockmaster-resilience-"))),
)
sys.path.insert(0, str(WATCHER))

import config  # noqa: E402
import lock_utils  # noqa: E402
import lock_watcher  # noqa: E402
import storage  # noqa: E402
import web_server  # noqa: E402


def test_cli_status_json_uses_utf8_without_pythonioencoding(tmp_path: Path):
    """The real CLI must emit UTF-8 even when its parent stream is CP1252."""
    project = tmp_path / "projekt-äöü"
    project.mkdir()
    lock_path = project / "LOCK.txt"
    lock_path.write_text(
        "owner: κäöü\n"
        f"created: {datetime.now().isoformat(timespec='seconds')}\n"
        "expires_after: 24h\n"
        "purpose: κ und äöü\n",
        encoding="utf-8",
    )
    roots_file = tmp_path / "lock_roots.json"
    roots_file.write_text(
        json.dumps(
            {
                "default_max_depth": 4,
                "shallow_depth": 2,
                "skip_dirs": [],
                "roots": [{"path": str(project)}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    data_dir = tmp_path / "watcher-data"

    env = os.environ.copy()
    env.pop("PYTHONIOENCODING", None)
    env["PYTHONUTF8"] = "0"
    env["LOCK_MASTER_ROOTS_FILE"] = str(roots_file)
    env["LOCK_MASTER_WATCHER_DATA"] = str(data_dir)

    cli_path = WATCHER / "cli.py"
    if sys.platform == "win32":
        probe = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8=0",
                "-c",
                "import sys; print(sys.stdout.encoding)",
            ],
            env=env,
            capture_output=True,
            check=False,
        )
        assert probe.returncode == 0
        assert probe.stdout.strip().lower() == b"cp1252"

        def cli_command(*args: str) -> list[str]:
            return [sys.executable, "-X", "utf8=0", str(cli_path), *args]
    else:
        # Keep the same regression active in non-Windows CI by giving the real
        # CLI process the strict CP1252 streams that Windows supplies natively.
        def cli_command(*args: str) -> list[str]:
            argv = [str(cli_path), *args]
            bootstrap = (
                "import runpy,sys; "
                "sys.stdout.reconfigure(encoding='cp1252', errors='strict'); "
                "sys.stderr.reconfigure(encoding='cp1252', errors='strict'); "
                f"sys.argv={argv!r}; "
                f"runpy.run_path({str(cli_path)!r}, run_name='__main__')"
            )
            return [sys.executable, "-c", bootstrap]

    scan = subprocess.run(
        cli_command("scan"), env=env, capture_output=True, check=False
    )
    assert scan.returncode == 0, scan.stderr.decode("utf-8", errors="replace")

    result = subprocess.run(
        cli_command("status", "--json"),
        env=env,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    payload = json.loads(result.stdout.decode("utf-8"))
    assert len(payload) == 1
    assert payload[0]["path"] == str(lock_path)
    assert payload[0]["owner"] == "κäöü"
    assert payload[0]["purpose"] == "κ und äöü"
    assert "κ und äöü" in payload[0]["raw_content"]


def test_external_roots_file_is_resolved(monkeypatch, tmp_path: Path):
    roots_file = tmp_path / "lock_roots.json"
    roots_file.write_text(
        json.dumps({"roots": [{"path": str(tmp_path)}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "_roots_file_candidates", lambda: [roots_file])

    assert config.resolve_roots_file() == roots_file
    assert config.load_scan_config()["roots"][0]["path"] == str(tmp_path)


def test_missing_roots_file_has_actionable_error(monkeypatch, tmp_path: Path):
    missing = tmp_path / "missing.json"
    monkeypatch.setattr(config, "_roots_file_candidates", lambda: [missing])

    try:
        config.resolve_roots_file()
    except FileNotFoundError as exc:
        message = str(exc)
    else:
        raise AssertionError("missing roots file did not fail")

    assert config.ROOTS_FILE_ENV in message
    assert str(missing) in message


def test_protected_locks_never_get_nominal_expiry(tmp_path: Path):
    user_lock = tmp_path / "LOCK.user.txt"
    user_lock.write_text(
        "owner: user\ncreated: 2020-01-01T00:00\nexpires_after: 1h\n",
        encoding="utf-8",
    )
    condition_lock = tmp_path / "LOCK.condition.publish.txt"
    condition_lock.write_text(
        "owner: agent\ncreated: 2020-01-01T00:00\nexpires_after: 1h\n"
        "release_condition: approved\n",
        encoding="utf-8",
    )

    assert lock_utils.compute_expires_at(user_lock) is None
    assert lock_utils.compute_expires_at(condition_lock) is None


def test_storage_does_not_expire_protected_rows(tmp_path: Path):
    db = storage.LockDB(tmp_path / "watcher.db")
    old = (datetime.now() - timedelta(days=10)).isoformat(timespec="seconds")
    try:
        for kind in ("user", "condition"):
            db.upsert_lock(
                {
                    "path": str(tmp_path / f"LOCK.{kind}.txt"),
                    "filename": f"LOCK.{kind}.txt",
                    "project_dir": str(tmp_path),
                    "scope": "project",
                    "lock_type": kind,
                    "expires_at": old,
                }
            )

        assert db.refresh_expired_locks() == 0
        assert {row["lock_type"] for row in db.get_active_locks()} == {
            "user",
            "condition",
        }
    finally:
        db.close()


def test_heartbeat_advances_while_scan_is_in_progress(monkeypatch, tmp_path: Path):
    counter = {"n": 0}

    def fake_now_iso() -> str:
        counter["n"] += 1
        return f"2026-07-27T21:{counter['n']:02d}:00"

    status_path = tmp_path / "daemon_status.json"
    monkeypatch.setattr(lock_watcher.config, "DAEMON_STATUS_PATH", status_path)
    monkeypatch.setattr(lock_watcher, "_now_iso", fake_now_iso)

    stop_event = threading.Event()
    scan_state = {
        "scan_in_progress": True,
        "scan_started_at": "2026-07-27T21:00:00",
        "last_scan_finished_at": None,
        "last_scan_duration_s": None,
    }
    thread = threading.Thread(
        target=lock_watcher._heartbeat_loop,
        args=(
            stop_event,
            True,
            "2026-07-27T21:00:00",
            scan_state,
            threading.Lock(),
            0.02,
        ),
        daemon=True,
    )
    thread.start()
    time.sleep(0.15)
    stop_event.set()
    thread.join(timeout=2)

    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert counter["n"] > 3
    assert status["scan_in_progress"] is True
    assert status["last_seen"] != status["started_at"]


def test_real_lock_appears_and_disappears_across_scans(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    lock_path = project / "LOCK.txt"
    now_str = datetime.now().isoformat(timespec="seconds")
    lock_path.write_text(
        f"owner: regression\ncreated: {now_str}\n",
        encoding="utf-8",
    )
    db = storage.LockDB(tmp_path / "scan.db")
    cfg = {
        "default_max_depth": 4,
        "shallow_depth": 2,
        "skip_dirs": [],
        "roots": [{"path": str(tmp_path)}],
    }
    try:
        first = lock_watcher._run_full_scan(db, cfg, update_cache=False)
        assert first["new"] == 1
        assert str(lock_path) in {row["path"] for row in db.get_active_locks()}

        lock_path.unlink()
        second = lock_watcher._run_full_scan(db, cfg, update_cache=False)
        assert second["deleted"] == 1
        assert str(lock_path) not in {row["path"] for row in db.get_active_locks()}
    finally:
        db.close()


def test_stale_scan_with_fresh_heartbeat_is_degraded(monkeypatch, tmp_path: Path):
    db = storage.LockDB(tmp_path / "health.db")
    old = (datetime.now() - timedelta(seconds=10_000)).isoformat(timespec="seconds")
    now = datetime.now().isoformat(timespec="seconds")
    db.record_scan(
        "full",
        old,
        old,
        {"total": 1, "new": 1, "expired": 0, "deleted": 0},
    )
    status = {
        "pid": os.getpid(),
        "host": lock_watcher.socket.gethostname(),
        "started_at": now,
        "last_seen": now,
        "update_cache": True,
        "scan_in_progress": False,
        "scan_started_at": None,
    }
    monkeypatch.setattr(lock_watcher, "load_daemon_status", lambda: status)
    monkeypatch.setattr(lock_watcher, "_daemon_status_is_fresh", lambda value: True)
    try:
        assert web_server._daemon_status_for_api(db)["state"] == "degraded"
    finally:
        db.close()


def test_windows_launcher_does_not_depend_on_timeout_stdin():
    launcher = (WATCHER / "START.bat").read_text(encoding="utf-8")
    assert "timeout /t" not in launcher.lower()
    assert "ping -n 3 127.0.0.1" in launcher.lower()


def test_quick_check_survives_non_utf8_stdout(tmp_path: Path):
    """Regression fuer den ASUS-GEI-Dauerabsturz 2026-08-02.

    Ein fensterloser Start (VBS-Wrapper des Scheduled Task) setzt kein
    PYTHONIOENCODING. Ohne _ensure_utf8_stdio() wirft _run_quick_check()
    dann UnicodeEncodeError, sobald ein Lock als geloescht/abgelaufen
    erkannt wird (print(f"... -> {path}") mit U+2192 auf einem strikten
    cp1252-Stream) und reisst den ganzen Daemon mit sich. Bewiesen: derselbe
    Stream crasht ohne den Fix, ist danach robust.
    """
    project = tmp_path / "project"
    project.mkdir()
    lock_path = project / "LOCK.txt"
    now_str = datetime.now().isoformat(timespec="seconds")
    lock_path.write_text(
        f"owner: regression\ncreated: {now_str}\n",
        encoding="utf-8",
    )
    db = storage.LockDB(tmp_path / "quickcheck.db")
    cfg = {
        "default_max_depth": 4,
        "shallow_depth": 2,
        "skip_dirs": [],
        "roots": [{"path": str(tmp_path)}],
    }
    try:
        lock_watcher._run_full_scan(db, cfg, update_cache=False)
        assert str(lock_path) in {row["path"] for row in db.get_active_locks()}
        lock_path.unlink()

        buffer = io.BytesIO()
        strict_cp1252_stream = io.TextIOWrapper(
            buffer, encoding="cp1252", errors="strict"
        )

        # Ohne den Fix: exakt der Absturz, der auf ASUS-GEI beobachtet wurde.
        with pytest.raises(UnicodeEncodeError):
            with contextlib.redirect_stdout(strict_cp1252_stream):
                lock_watcher._run_quick_check(db)
            strict_cp1252_stream.flush()

        # Lock wurde beim gescheiterten Versuch bereits als geloescht
        # markiert (print() schlaegt NACH dem DB-Update fehl) — Datenbank
        # neu aufsetzen, damit der zweite Durchlauf denselben Ausgangspunkt hat.
        db.close()
        db = storage.LockDB(tmp_path / "quickcheck2.db")
        lock_path.write_text(
            f"owner: regression\ncreated: {now_str}\n", encoding="utf-8"
        )
        lock_watcher._run_full_scan(db, cfg, update_cache=False)
        lock_path.unlink()

        buffer2 = io.BytesIO()
        fixed_stream = io.TextIOWrapper(buffer2, encoding="cp1252", errors="strict")
        lock_watcher._ensure_utf8_stdio_for_stream(fixed_stream)

        with contextlib.redirect_stdout(fixed_stream):
            lock_watcher._run_quick_check(db)
        fixed_stream.flush()

        buffer2.seek(0)
        output = buffer2.read().decode("utf-8")
        assert "gelöscht" in output
        assert str(lock_path) in output
    finally:
        db.close()
