"""Tests für lock_watcher.py — Reconciliation-Logik."""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("LOCK_MASTER_WATCHER_DATA", tempfile.mkdtemp(prefix="lockmaster-watcher-daemon-"))
sys.path.insert(0, str(ROOT / "pure-locking" / "watcher"))
sys.path.insert(0, str(ROOT / "pure-locking"))
sys.path.insert(0, str(ROOT))

import os  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import unittest  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest.mock import patch  # noqa: E402

import config  # noqa: E402
import storage  # noqa: E402
import lock_watcher  # noqa: E402


class TestFullScanReconciliation(unittest.TestCase):
    """Testet _run_full_scan Reconciliation: neue/modified/expired/deleted Locks."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = storage.LockDB(Path(self.tmp.name))
        self.cfg = config.load_scan_config()

    def tearDown(self):
        self.db.close()
        for ext in ("", "-wal", "-shm"):
            try:
                os.unlink(self.tmp.name + ext)
            except OSError:
                pass

    def _make_scan_result(self, path, owner="alice", is_expired=False, **kw):
        defaults = {
            "path": path,
            "filename": "LOCK.txt",
            "project_dir": str(Path(path).parent),
            "scope": "project",
            "lock_type": "exclusive",
            "is_legacy": False,
            "owner": owner,
            "host": "HOST1",
            "purpose": "test",
            "mode": "hard",
            "created_at": "2026-06-20T10:00:00",
            "created_source": "header",
            "expires_after": "24h",
            "expires_at": (datetime.now() + timedelta(hours=12)).isoformat(timespec="seconds"),
            "team_data": None,
            "raw_content": f"owner: {owner}\n",
            "is_expired": is_expired,
        }
        defaults.update(kw)
        return defaults

    @patch("lock_watcher.scanner.full_scan")
    def test_new_locks_detected(self, mock_scan):
        mock_scan.return_value = [
            self._make_scan_result(r"C:\a\LOCK.txt"),
            self._make_scan_result(r"C:\b\LOCK.txt"),
        ]
        stats = lock_watcher._run_full_scan(self.db, self.cfg, False)
        self.assertEqual(stats["new"], 2)
        self.assertEqual(stats["total"], 2)
        self.assertEqual(len(self.db.get_active_locks()), 2)

    @patch("lock_watcher.scanner.full_scan")
    def test_existing_lock_not_counted_as_new(self, mock_scan):
        self.db.upsert_lock(self._make_scan_result(r"C:\a\LOCK.txt"))
        mock_scan.return_value = [self._make_scan_result(r"C:\a\LOCK.txt")]
        stats = lock_watcher._run_full_scan(self.db, self.cfg, False)
        self.assertEqual(stats["new"], 0)

    @patch("lock_watcher.scanner.full_scan")
    def test_modified_lock_detected(self, mock_scan):
        self.db.upsert_lock(self._make_scan_result(r"C:\a\LOCK.txt", owner="alice"))
        mock_scan.return_value = [self._make_scan_result(r"C:\a\LOCK.txt", owner="bob")]
        stats = lock_watcher._run_full_scan(self.db, self.cfg, False)
        self.assertEqual(stats["modified"], 1)
        lock = self.db.get_lock_by_path(r"C:\a\LOCK.txt")
        self.assertEqual(lock["owner"], "bob")

    @patch("lock_watcher.scanner.full_scan")
    def test_expired_lock_marked(self, mock_scan):
        self.db.upsert_lock(self._make_scan_result(r"C:\a\LOCK.txt"))
        mock_scan.return_value = [self._make_scan_result(r"C:\a\LOCK.txt", is_expired=True)]
        stats = lock_watcher._run_full_scan(self.db, self.cfg, False)
        self.assertEqual(stats["expired"], 1)
        lock = self.db.get_lock_by_path(r"C:\a\LOCK.txt")
        self.assertEqual(lock["status"], "expired")

    @patch("lock_watcher.scanner.full_scan")
    def test_deleted_lock_detected(self, mock_scan):
        self.db.upsert_lock(self._make_scan_result(r"C:\a\LOCK.txt"))
        self.db.record_event(self.db.get_lock_by_path(r"C:\a\LOCK.txt")["id"], "detected")
        with patch("lock_watcher.scanner.check_paths", return_value={r"C:\a\LOCK.txt": None}):
            mock_scan.return_value = []
            stats = lock_watcher._run_full_scan(self.db, self.cfg, False)
        self.assertEqual(stats["deleted"], 1)
        lock = self.db.get_lock_by_path(r"C:\a\LOCK.txt")
        self.assertEqual(lock["status"], "deleted")

    @patch("lock_watcher.scanner.full_scan")
    def test_missing_scan_result_does_not_delete_existing_file(self, mock_scan):
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = Path(tmpdir) / "LOCK.txt"
            lock_path.write_text(
                "owner: alice\ncreated: 2026-06-20T10:00\nexpires_after: 365d\n",
                encoding="utf-8",
            )
            self.db.upsert_lock(self._make_scan_result(str(lock_path)))
            mock_scan.return_value = []
            stats = lock_watcher._run_full_scan(self.db, self.cfg, False)

        self.assertEqual(stats["deleted"], 0)
        lock = self.db.get_lock_by_path(str(lock_path))
        self.assertEqual(lock["status"], "active")

    @patch("lock_watcher.scanner.full_scan")
    def test_scan_counts_are_persisted(self, mock_scan):
        mock_scan.return_value = [self._make_scan_result(r"C:\a\LOCK.txt")]
        lock_watcher._run_full_scan(self.db, self.cfg, False)
        row = self.db._conn.execute(
            "SELECT locks_found, locks_new FROM scans ORDER BY id DESC LIMIT 1"
        ).fetchone()
        self.assertEqual(row["locks_found"], 1)
        self.assertEqual(row["locks_new"], 1)

    @patch("lock_watcher.scanner.full_scan")
    def test_renewed_lock(self, mock_scan):
        """Ein gelöschter Lock der wieder auftaucht = renewed."""
        lock_id = self.db.upsert_lock(self._make_scan_result(r"C:\a\LOCK.txt"))
        self.db.mark_deleted(lock_id, datetime.now().isoformat())
        mock_scan.return_value = [self._make_scan_result(r"C:\a\LOCK.txt")]
        stats = lock_watcher._run_full_scan(self.db, self.cfg, False)
        self.assertEqual(stats["new"], 1)
        lock = self.db.get_lock_by_path(r"C:\a\LOCK.txt")
        self.assertEqual(lock["status"], "active")

    @patch("lock_watcher.scanner.full_scan")
    def test_expired_lock_revived_when_no_longer_expired(self, mock_scan):
        """Ein expired Lock der wieder gültig ist = renewed."""
        lock_id = self.db.upsert_lock(self._make_scan_result(r"C:\a\LOCK.txt"))
        self.db.mark_expired(lock_id, datetime.now().isoformat())
        mock_scan.return_value = [self._make_scan_result(r"C:\a\LOCK.txt", is_expired=False)]
        stats = lock_watcher._run_full_scan(self.db, self.cfg, False)
        self.assertEqual(stats["new"], 1)
        lock = self.db.get_lock_by_path(r"C:\a\LOCK.txt")
        self.assertEqual(lock["status"], "active")

    @patch("lock_watcher.scanner.full_scan")
    def test_expired_lock_stays_expired_when_still_expired(self, mock_scan):
        """Ein expired Lock der weiterhin expired ist, bleibt expired."""
        lock_id = self.db.upsert_lock(self._make_scan_result(r"C:\a\LOCK.txt"))
        self.db.mark_expired(lock_id, datetime.now().isoformat())
        mock_scan.return_value = [self._make_scan_result(r"C:\a\LOCK.txt", is_expired=True)]
        stats = lock_watcher._run_full_scan(self.db, self.cfg, False)
        self.assertEqual(stats["new"], 0)
        lock = self.db.get_lock_by_path(r"C:\a\LOCK.txt")
        self.assertEqual(lock["status"], "expired")

    @patch("lock_watcher.scanner.full_scan")
    def test_scan_recorded(self, mock_scan):
        mock_scan.return_value = []
        lock_watcher._run_full_scan(self.db, self.cfg, False)
        stats = self.db.get_stats()
        self.assertIsNotNone(stats["last_scan_at"])
        self.assertEqual(stats["last_scan_type"], "full")


class TestQuickCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = storage.LockDB(Path(self.tmp.name))

    def tearDown(self):
        self.db.close()
        for ext in ("", "-wal", "-shm"):
            try:
                os.unlink(self.tmp.name + ext)
            except OSError:
                pass

    @patch("lock_watcher.scanner.check_paths")
    def test_deleted_file_marks_deleted(self, mock_check):
        lock_id = self.db.upsert_lock({
            "path": r"C:\test\LOCK.txt", "filename": "LOCK.txt",
            "project_dir": r"C:\test", "scope": "project",
        })
        mock_check.return_value = {r"C:\test\LOCK.txt": None}
        lock_watcher._run_quick_check(self.db)
        lock = self.db.get_lock_by_id(lock_id)
        self.assertEqual(lock["status"], "deleted")

    @patch("lock_watcher.scanner.check_paths")
    def test_expired_file_marks_expired(self, mock_check):
        lock_id = self.db.upsert_lock({
            "path": r"C:\test\LOCK.txt", "filename": "LOCK.txt",
            "project_dir": r"C:\test", "scope": "project",
        })
        mock_check.return_value = {r"C:\test\LOCK.txt": {"is_expired": True}}
        lock_watcher._run_quick_check(self.db)
        lock = self.db.get_lock_by_id(lock_id)
        self.assertEqual(lock["status"], "expired")

    @patch("lock_watcher.scanner.check_paths")
    def test_no_active_locks_noop(self, mock_check):
        lock_watcher._run_quick_check(self.db)
        mock_check.assert_not_called()



class TestFullScanScheduling(unittest.TestCase):
    """Portiert aus lock-master tests/test_watcher_scheduling.py (2026-10-17)."""

    def test_gap_never_drops_below_the_interval(self):
        self.assertEqual(lock_watcher.next_full_scan_gap(5, interval=60, idle_factor=4), 60)
        self.assertEqual(lock_watcher.next_full_scan_gap(-3, interval=60, idle_factor=4), 60)

    def test_gap_scales_with_a_slow_scan(self):
        self.assertEqual(lock_watcher.next_full_scan_gap(127, interval=60, idle_factor=4), 508)
        self.assertEqual(lock_watcher.next_full_scan_gap(617, interval=60, idle_factor=0), 60)

    def test_defaults_come_from_config(self):
        self.assertEqual(config.FULL_SCAN_IDLE_FACTOR, 4)
        self.assertEqual(lock_watcher.next_full_scan_gap(100), max(config.FULL_SCAN_INTERVAL, 400))

    def test_stale_threshold_follows_the_announced_gap(self):
        base = lock_watcher.SCAN_OVERRUN_WARN_SECONDS
        self.assertEqual(lock_watcher.scan_stale_after_seconds(None), base + config.FULL_SCAN_INTERVAL)
        self.assertEqual(lock_watcher.scan_stale_after_seconds(
            {"next_full_scan_gap_s": 508.0, "last_scan_duration_s": 127.0}), base + 635.0)
        self.assertEqual(config._idle_factor_from_env("abc"), 4.0)
        self.assertEqual(config._idle_factor_from_env("inf"), 4.0)

    def test_daemon_uses_the_adaptive_gap(self):
        source = Path(lock_watcher.__file__).read_text(encoding="utf-8")
        self.assertIn("now - last_full_scan >= full_scan_gap", source)
        self.assertNotIn("now - last_full_scan >= config.FULL_SCAN_INTERVAL", source)


if __name__ == "__main__":
    unittest.main()
