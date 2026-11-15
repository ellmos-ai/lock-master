# -*- coding: utf-8 -*-
"""Tests: Sofortsperrung (bulk_lock/bulk_unlock) — guard, idempotent, reversibel,
User-Locks unantastbar."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("LOCK_MASTER_WATCHER_DATA", tempfile.mkdtemp(prefix="lockmaster-core-bulk-lock-"))
sys.path.insert(0, str(ROOT / "pure-locking" / "watcher"))
sys.path.insert(0, str(ROOT / "pure-locking"))
sys.path.insert(0, str(ROOT))

import sys  # noqa: E402
import tempfile  # noqa: E402
import unittest  # noqa: E402
from datetime import datetime  # noqa: E402
from pathlib import Path  # noqa: E402


import bulk_lock  # noqa: E402

# Fester Timestamp faellt nach genug verstrichener Zeit unter die 24h-Default-
# expiry und macht Locks faelschlich "abgelaufen" -> dynamisch statt hardcoded,
# ausser dort wo Ablauf bewusst Testgegenstand ist (User-Lock-Tests unten).
FRESH_TS = datetime.now().strftime("%Y-%m-%dT%H:%M")


class TestBulkLock(unittest.TestCase):
    def _roots(self, tmp, n=3):
        roots = []
        for i in range(n):
            r = Path(tmp) / f"proj{i}"
            r.mkdir()
            roots.append(r)
        return roots

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            roots = self._roots(tmp)
            res = bulk_lock.bulk_lock(roots, owner="user", reason="t", commit=False)
            self.assertEqual(res["would_lock"], 3)
            for r in roots:
                self.assertFalse((r / "LOCK.txt").exists())

    def test_commit_locks_all_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            roots = self._roots(tmp)
            res = bulk_lock.bulk_lock(roots, owner="user", reason="wartung",
                                      created=FRESH_TS, commit=True)
            self.assertEqual(res["locked"], 3)
            for r in roots:
                p = r / "LOCK.txt"
                self.assertTrue(p.exists())
                content = p.read_text(encoding="utf-8")
                self.assertIn("created_by: bulk", content)
                self.assertIn("wartung", content)

    def test_idempotent_skips_already_locked(self):
        with tempfile.TemporaryDirectory() as tmp:
            roots = self._roots(tmp)
            bulk_lock.bulk_lock(roots, created=FRESH_TS, commit=True)
            res = bulk_lock.bulk_lock(roots, created=FRESH_TS, commit=True)
            self.assertEqual(res["locked"], 0, "Bereits gesperrte Roots nicht erneut sperren")
            self.assertEqual(res["skipped"], 3)

    def test_user_lock_root_is_left_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            roots = self._roots(tmp)
            # proj0 hat einen NOMINELL ABGELAUFENEN User-Lock -> bulk darf dort
            # trotzdem KEIN LOCK.txt anlegen (Schutzinvariante, unabh. vom Verfall).
            (roots[0] / "LOCK.user.txt").write_text(
                "owner: user\ncreated: 2026-06-01T10:00\nexpires_after: 24h\n", encoding="utf-8")
            res = bulk_lock.bulk_lock(roots, created="2026-06-27T14:00", commit=True)
            self.assertFalse((roots[0] / "LOCK.txt").exists())
            self.assertEqual(res["locked"], 2)

    def test_bulk_unlock_removes_only_bulk_locks(self):
        with tempfile.TemporaryDirectory() as tmp:
            roots = self._roots(tmp)
            # Ein manueller (nicht-bulk) LOCK.txt in proj2 -> bleibt erhalten.
            (roots[2] / "LOCK.txt").write_text(
                f"owner: claude\ncreated: {FRESH_TS}\n", encoding="utf-8")
            bulk_lock.bulk_lock(roots, created=FRESH_TS, commit=True)  # proj0,proj1 bulk
            res = bulk_lock.bulk_unlock(roots, commit=True)
            self.assertEqual(res["unlocked"], 2)
            self.assertFalse((roots[0] / "LOCK.txt").exists())
            self.assertFalse((roots[1] / "LOCK.txt").exists())
            self.assertTrue((roots[2] / "LOCK.txt").exists(), "Manueller Lock darf nicht entfernt werden")

    def test_bulk_unlock_never_removes_user_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            roots = self._roots(tmp, n=1)
            (roots[0] / "LOCK.user.txt").write_text(
                "owner: user\ncreated: 2026-06-01T10:00\ncreated_by: bulk\n", encoding="utf-8")
            # Selbst wenn fälschlich created_by: bulk drinsteht: user-lock ist geschützt.
            res = bulk_lock.bulk_unlock(roots, commit=True)
            self.assertEqual(res["unlocked"], 0)
            self.assertTrue((roots[0] / "LOCK.user.txt").exists())


if __name__ == "__main__":
    unittest.main()
