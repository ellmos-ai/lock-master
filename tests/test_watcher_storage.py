"""Tests für storage.py — LockDB."""

import atexit
import shutil
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if "LOCK_MASTER_WATCHER_DATA" not in os.environ:
    _WATCHER_TMP = tempfile.mkdtemp(prefix="lockmaster-watcher-storage-")
    atexit.register(shutil.rmtree, _WATCHER_TMP, True)
    os.environ["LOCK_MASTER_WATCHER_DATA"] = _WATCHER_TMP
sys.path.insert(0, str(ROOT / "pure-locking" / "watcher"))
sys.path.insert(0, str(ROOT / "pure-locking"))
sys.path.insert(0, str(ROOT))

import json  # noqa: E402
import os  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import unittest  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402
from pathlib import Path  # noqa: E402

from storage import LockDB  # noqa: E402


class TestLockDB(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = LockDB(Path(self.tmp.name))

    def tearDown(self):
        self.db.close()
        try:
            os.unlink(self.tmp.name)
        except OSError:
            pass
        for ext in ("-wal", "-shm"):
            try:
                os.unlink(self.tmp.name + ext)
            except OSError:
                pass

    def _make_lock(self, **overrides):
        base = {
            "path": r"C:\test\LOCK.txt",
            "filename": "LOCK.txt",
            "project_dir": r"C:\test",
            "scope": "project",
            "lock_type": "exclusive",
            "is_legacy": False,
            "owner": "alice",
            "host": "HOST1",
            "purpose": "testing",
            "mode": "hard",
            "created_at": "2026-06-20T10:00:00",
            "created_source": "header",
            "expires_after": "24h",
            "expires_at": (datetime.now() + timedelta(hours=12)).isoformat(timespec="seconds"),
            "team_data": None,
            "raw_content": "owner: alice\n",
        }
        base.update(overrides)
        return base

    # ── Insert / Update ──────────────────────────────

    def test_insert_new_lock(self):
        lock_id = self.db.upsert_lock(self._make_lock())
        self.assertIsNotNone(lock_id)
        self.assertGreater(lock_id, 0)

    def test_upsert_updates_existing(self):
        lock_id1 = self.db.upsert_lock(self._make_lock(owner="alice"))
        lock_id2 = self.db.upsert_lock(self._make_lock(owner="bob"))
        self.assertEqual(lock_id1, lock_id2)
        lock = self.db.get_lock_by_id(lock_id1)
        self.assertEqual(lock["owner"], "bob")

    def test_upsert_preserves_status_on_update(self):
        lock_id = self.db.upsert_lock(self._make_lock())
        self.db.mark_expired(lock_id, datetime.now().isoformat())
        self.db.upsert_lock(self._make_lock(owner="charlie"))
        lock = self.db.get_lock_by_id(lock_id)
        self.assertEqual(lock["status"], "expired")

    def test_upsert_can_override_status(self):
        lock_id = self.db.upsert_lock(self._make_lock())
        self.db.mark_deleted(lock_id, datetime.now().isoformat())
        self.db.upsert_lock(self._make_lock(status="active"))
        lock = self.db.get_lock_by_id(lock_id)
        self.assertEqual(lock["status"], "active")

    # ── Retrieval ────────────────────────────────────

    def test_get_lock_by_id(self):
        lock_id = self.db.upsert_lock(self._make_lock())
        lock = self.db.get_lock_by_id(lock_id)
        self.assertEqual(lock["owner"], "alice")
        self.assertEqual(lock["path"], r"C:\test\LOCK.txt")

    def test_get_lock_by_id_not_found(self):
        self.assertIsNone(self.db.get_lock_by_id(9999))

    def test_get_lock_by_path(self):
        self.db.upsert_lock(self._make_lock())
        lock = self.db.get_lock_by_path(r"C:\test\LOCK.txt")
        self.assertIsNotNone(lock)

    def test_get_lock_by_path_not_found(self):
        self.assertIsNone(self.db.get_lock_by_path(r"C:\nonexistent\LOCK.txt"))

    def test_get_active_locks(self):
        self.db.upsert_lock(self._make_lock(path=r"C:\a\LOCK.txt"))
        lid2 = self.db.upsert_lock(self._make_lock(path=r"C:\b\LOCK.txt"))
        self.db.mark_expired(lid2, datetime.now().isoformat())
        active = self.db.get_active_locks()
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["path"], r"C:\a\LOCK.txt")

    def test_get_all_locks_unfiltered(self):
        self.db.upsert_lock(self._make_lock(path=r"C:\a\LOCK.txt"))
        lid2 = self.db.upsert_lock(self._make_lock(path=r"C:\b\LOCK.txt"))
        self.db.mark_expired(lid2, datetime.now().isoformat())
        all_locks = self.db.get_all_locks()
        self.assertEqual(len(all_locks), 2)

    def test_get_all_locks_filtered(self):
        self.db.upsert_lock(self._make_lock(path=r"C:\a\LOCK.txt"))
        lid2 = self.db.upsert_lock(self._make_lock(path=r"C:\b\LOCK.txt"))
        self.db.mark_expired(lid2, datetime.now().isoformat())
        expired = self.db.get_all_locks("expired")
        self.assertEqual(len(expired), 1)

    # ── Status Changes ───────────────────────────────

    def test_mark_expired(self):
        lock_id = self.db.upsert_lock(self._make_lock())
        ts = datetime.now().isoformat()
        self.db.mark_expired(lock_id, ts)
        lock = self.db.get_lock_by_id(lock_id)
        self.assertEqual(lock["status"], "expired")

    def test_mark_deleted(self):
        lock_id = self.db.upsert_lock(self._make_lock())
        ts = datetime.now().isoformat()
        self.db.mark_deleted(lock_id, ts)
        lock = self.db.get_lock_by_id(lock_id)
        self.assertEqual(lock["status"], "deleted")
        self.assertIsNotNone(lock["removed_at"])

    # ── Events ───────────────────────────────────────

    def test_record_event(self):
        lock_id = self.db.upsert_lock(self._make_lock())
        self.db.record_event(lock_id, "detected", "initial scan")
        events = self.db.get_lock_history(r"C:\test\LOCK.txt")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "detected")
        self.assertEqual(events[0]["details"], "initial scan")

    def test_mark_expired_creates_event(self):
        lock_id = self.db.upsert_lock(self._make_lock())
        self.db.mark_expired(lock_id, datetime.now().isoformat())
        events = self.db.get_lock_history(r"C:\test\LOCK.txt")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "expired")

    def test_mark_deleted_creates_event(self):
        lock_id = self.db.upsert_lock(self._make_lock())
        self.db.mark_deleted(lock_id, datetime.now().isoformat())
        events = self.db.get_lock_history(r"C:\test\LOCK.txt")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "deleted")

    def test_get_lock_history_limit(self):
        lock_id = self.db.upsert_lock(self._make_lock())
        for i in range(10):
            self.db.record_event(lock_id, "modified", f"change {i}")
        events = self.db.get_lock_history(limit=5)
        self.assertEqual(len(events), 5)

    def test_get_events_by_prefix(self):
        self.db.upsert_lock(self._make_lock(path=r"C:\proj\a\LOCK.txt"))
        lid1 = self.db.get_lock_by_path(r"C:\proj\a\LOCK.txt")["id"]
        self.db.record_event(lid1, "detected")

        self.db.upsert_lock(self._make_lock(path=r"C:\other\LOCK.txt"))
        lid2 = self.db.get_lock_by_path(r"C:\other\LOCK.txt")["id"]
        self.db.record_event(lid2, "detected")

        events = self.db.get_events_by_prefix(r"C:\proj")
        self.assertEqual(len(events), 1)

    # ── Display Name ─────────────────────────────────

    def test_update_display_name(self):
        lock_id = self.db.upsert_lock(self._make_lock())
        ok = self.db.update_display_name(lock_id, "Mein Lock")
        self.assertTrue(ok)
        lock = self.db.get_lock_by_id(lock_id)
        self.assertEqual(lock["display_name"], "Mein Lock")

    # ── Scans ────────────────────────────────────────

    def test_record_scan(self):
        scan_id = self.db.record_scan(
            "full",
            "2026-06-20T10:00:00",
            "2026-06-20T10:00:01",
            {"locks_found": 5, "locks_new": 2},
        )
        self.assertGreater(scan_id, 0)

    def test_get_stats_includes_last_scan(self):
        self.db.record_scan("full", "2026-06-20T10:00:00", "2026-06-20T10:00:01", {})
        stats = self.db.get_stats()
        self.assertEqual(stats["last_scan_at"], "2026-06-20T10:00:00")
        self.assertEqual(stats["last_scan_type"], "full")

    def test_get_stats_empty_db(self):
        stats = self.db.get_stats()
        self.assertEqual(stats["active"], 0)
        self.assertEqual(stats["expired"], 0)
        self.assertEqual(stats["deleted"], 0)
        self.assertIsNone(stats["last_scan_at"])

    def test_get_stats_refreshes_expired_active_locks(self):
        past = (datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds")
        lock_id = self.db.upsert_lock(self._make_lock(expires_at=past))
        stats = self.db.get_stats()
        self.assertEqual(stats["active"], 0)
        self.assertEqual(stats["expired"], 1)
        lock = self.db.get_lock_by_id(lock_id)
        self.assertEqual(lock["status"], "expired")

    # ── Team Data ────────────────────────────────────

    def test_team_data_stored_as_json(self):
        td = {"presence": ["agent1", "agent2"], "queue": ["task1"]}
        lock_id = self.db.upsert_lock(self._make_lock(team_data=td, lock_type="team"))
        lock = self.db.get_lock_by_id(lock_id)
        parsed = json.loads(lock["team_data"])
        self.assertEqual(parsed["presence"], ["agent1", "agent2"])

    # ── Known Active Paths ───────────────────────────

    def test_get_known_active_paths(self):
        self.db.upsert_lock(self._make_lock(path=r"C:\a\LOCK.txt"))
        lid2 = self.db.upsert_lock(self._make_lock(path=r"C:\b\LOCK.txt"))
        self.db.mark_deleted(lid2, datetime.now().isoformat())
        paths = self.db.get_known_active_paths()
        self.assertEqual(paths, [r"C:\a\LOCK.txt"])

    # ── Migration ────────────────────────────────────

    def test_migration_adds_missing_columns(self):
        db2 = LockDB(Path(self.tmp.name))
        lock_id = db2.upsert_lock(self._make_lock())
        lock = db2.get_lock_by_id(lock_id)
        self.assertIn("display_name", lock)
        self.assertIn("lock_type", lock)
        db2.close()

    # ── Bugfix: update_display_name rowcount ────────

    def test_update_display_name_nonexistent_returns_false(self):
        self.db.upsert_lock(self._make_lock())
        ok = self.db.update_display_name(9999, "Ghost")
        self.assertFalse(ok)

    # ── Bugfix: LIKE wildcard escaping ──────────────

    def test_events_by_prefix_underscore_not_wildcard(self):
        self.db.upsert_lock(self._make_lock(path=r"C:\test\_scripts\LOCK.txt",
                                             project_dir=r"C:\test\_scripts"))
        lid1 = self.db.get_lock_by_path(r"C:\test\_scripts\LOCK.txt")["id"]
        self.db.record_event(lid1, "detected")

        self.db.upsert_lock(self._make_lock(path=r"C:\test\Xscripts\LOCK.txt",
                                             project_dir=r"C:\test\Xscripts"))
        lid2 = self.db.get_lock_by_path(r"C:\test\Xscripts\LOCK.txt")["id"]
        self.db.record_event(lid2, "detected")

        events = self.db.get_events_by_prefix(r"C:\test\_scripts")
        self.assertEqual(len(events), 1)
        self.assertIn("_scripts", events[0]["path"])

    # ── Bugfix: geschützte Locks verfallen nie automatisch ──

    def test_refresh_expired_locks_never_expires_user_lock(self):
        """User-Locks duerfen NIE automatisch verfallen — auch nicht wenn
        (z.B. durch einen frueheren Bug in compute_expires_at) ein laengst
        vergangener expires_at in der DB steht. refresh_expired_locks() muss
        das anhand von lock_type abfangen, unabhaengig vom gespeicherten
        Zeitstempel."""
        past = (datetime.now() - timedelta(days=6)).isoformat(timespec="seconds")
        lock_id = self.db.upsert_lock(self._make_lock(
            path=r"C:\test\LOCK.user.zenodo-upload.txt",
            lock_type="user",
            expires_at=past,
        ))
        self.db.refresh_expired_locks()
        lock = self.db.get_lock_by_id(lock_id)
        self.assertEqual(lock["status"], "active")

    def test_refresh_expired_locks_never_expires_condition_lock(self):
        past = (datetime.now() - timedelta(days=6)).isoformat(timespec="seconds")
        lock_id = self.db.upsert_lock(self._make_lock(
            path=r"C:\test\LOCK.condition.release.txt",
            lock_type="condition",
            expires_at=past,
        ))
        self.db.refresh_expired_locks()
        lock = self.db.get_lock_by_id(lock_id)
        self.assertEqual(lock["status"], "active")

    def test_refresh_expired_locks_still_expires_exclusive_lock(self):
        """Gegenprobe: normale Locks sind von der Schutzausnahme NICHT betroffen."""
        past = (datetime.now() - timedelta(days=6)).isoformat(timespec="seconds")
        lock_id = self.db.upsert_lock(self._make_lock(
            path=r"C:\test\LOCK.txt", lock_type="exclusive", expires_at=past,
        ))
        self.db.refresh_expired_locks()
        lock = self.db.get_lock_by_id(lock_id)
        self.assertEqual(lock["status"], "expired")

    def test_upsert_until_lock(self):
        lock_id = self.db.upsert_lock(self._make_lock(
            path=r"C:\test\LOCK.until.winners-announcement.txt",
            filename="LOCK.until.winners-announcement.txt",
            lock_type="until",
        ))
        self.assertIsNotNone(lock_id)
        lock = self.db.get_lock_by_id(lock_id)
        self.assertEqual(lock["lock_type"], "until")


if __name__ == "__main__":
    unittest.main()
