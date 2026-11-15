"""Tests für Daemon-Heartbeat und Singleton-Erkennung."""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("LOCK_MASTER_WATCHER_DATA", tempfile.mkdtemp(prefix="lockmaster-watcher-daemon-status-"))
sys.path.insert(0, str(ROOT / "pure-locking" / "watcher"))
sys.path.insert(0, str(ROOT / "pure-locking"))
sys.path.insert(0, str(ROOT))

import json  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
import unittest  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest.mock import patch  # noqa: E402

import lock_watcher  # noqa: E402


class TestDaemonStatus(unittest.TestCase):
    def _status(self, **overrides):
        status = {
            "pid": 12345,
            "host": lock_watcher.socket.gethostname(),
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "last_seen": datetime.now().isoformat(timespec="seconds"),
            "update_cache": True,
        }
        status.update(overrides)
        return status

    def test_fresh_status_requires_running_pid(self):
        with patch.object(lock_watcher, "_pid_is_running", return_value=True):
            self.assertTrue(lock_watcher._daemon_status_is_fresh(self._status()))

        with patch.object(lock_watcher, "_pid_is_running", return_value=False):
            self.assertFalse(lock_watcher._daemon_status_is_fresh(self._status()))

    def test_stale_status_is_not_fresh(self):
        old = (datetime.now() - timedelta(seconds=lock_watcher.STALE_DAEMON_SECONDS + 1)).isoformat(
            timespec="seconds"
        )
        with patch.object(lock_watcher, "_pid_is_running", return_value=True):
            self.assertFalse(lock_watcher._daemon_status_is_fresh(self._status(last_seen=old)))

    def test_other_host_is_not_fresh(self):
        with patch.object(lock_watcher, "_pid_is_running", return_value=True):
            self.assertFalse(lock_watcher._daemon_status_is_fresh(self._status(host="OTHER-HOST")))

    def test_run_daemon_reuses_existing_daemon(self):
        existing = self._status(pid=999)
        with patch.object(lock_watcher, "get_running_daemon_status", return_value=existing), \
             patch.object(lock_watcher.config, "load_scan_config") as load_scan_config:
            lock_watcher.run_daemon(update_cache=True)
        load_scan_config.assert_not_called()


class TestHeartbeatIndependentOfScan(unittest.TestCase):
    """Regressionstest für.

    URSACHE: Vor dem Fix lief der Heartbeat im selben Thread/Loop wie der
    Full-Scan. Ein Full-Scan über alle synced roots dauert empirisch ~50s
    und kann bei cloud-sync stalls unbegrenzt blockieren (Path.iterdir()/
    read_text() kennen kein Timeout) — der Heartbeat fror dann für die
    gesamte Scan-Dauer ein, im schlimmsten Fall dauerhaft, ohne dass der
    Prozess abstürzte. Ein Test der nur prüft "Prozess/Thread lebt" haette
    diesen stillen Ausfall NICHT gefangen; dieser Test prüft stattdessen,
    dass der Heartbeat mehrfach VORANSCHREITET, während der Scan-Status
    durchgehend "in_progress" bleibt (simulierter hängender Scan).
    """

    def test_heartbeat_keeps_advancing_while_scan_is_stuck(self):
        counter = {"n": 0}

        def fake_now_iso() -> str:
            counter["n"] += 1
            return f"2026-07-14T04:{counter['n']:02d}:00"

        stop_event = threading.Event()
        state_lock = threading.Lock()
        scan_state = {
            "scan_in_progress": True,
            "scan_started_at": "2026-07-14T04:00:00",
            "last_scan_finished_at": None,
            "last_scan_duration_s": None,
        }

        with tempfile.TemporaryDirectory() as tmp:
            status_path = Path(tmp) / "daemon_status.json"
            with patch.object(lock_watcher.config, "DAEMON_STATUS_PATH", status_path), \
                 patch.object(lock_watcher, "_now_iso", side_effect=fake_now_iso):
                thread = threading.Thread(
                    target=lock_watcher._heartbeat_loop,
                    args=(stop_event, True, "2026-07-14T04:00:00", scan_state, state_lock, 0.02),
                    daemon=True,
                )
                thread.start()
                time.sleep(0.3)
                stop_event.set()
                thread.join(timeout=2)

            status = json.loads(status_path.read_text(encoding="utf-8"))

        # Der Heartbeat muss mehrfach geschrieben worden sein, nicht nur einmal
        # beim Start (das ist genau das Muster aus dem Ticket: started_at ==
        # last_seen bei jedem Neustart, weil der Loop danach nie wieder ankam).
        self.assertGreater(counter["n"], 3, "Heartbeat wurde nicht wiederholt geschrieben")
        self.assertNotEqual(status["last_seen"], "2026-07-14T04:00:00")
        # Ehrlichkeit statt Vortäuschung: der Scan-Zustand wird mitgeschrieben,
        # nicht verschwiegen — ein Beobachter sieht "Daemon lebt, Scan hängt seit X".
        self.assertTrue(status["scan_in_progress"])
        self.assertEqual(status["scan_started_at"], "2026-07-14T04:00:00")


if __name__ == "__main__":
    unittest.main()
