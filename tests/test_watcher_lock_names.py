"""Tests für kanonische Lock-Dateinamen-Semantik aus _scripts/lock_utils.py."""

import atexit
import shutil
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if "LOCK_MASTER_WATCHER_DATA" not in os.environ:
    _WATCHER_TMP = tempfile.mkdtemp(prefix="lockmaster-watcher-lock-names-")
    atexit.register(shutil.rmtree, _WATCHER_TMP, True)
    os.environ["LOCK_MASTER_WATCHER_DATA"] = _WATCHER_TMP
sys.path.insert(0, str(ROOT / "pure-locking" / "watcher"))
sys.path.insert(0, str(ROOT / "pure-locking"))
sys.path.insert(0, str(ROOT))

import sys  # noqa: E402
import tempfile  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402

import lock_utils  # noqa: E402

import scanner  # noqa: E402


class TestLockNameParts(unittest.TestCase):
    def test_plain_lock_whole_folder(self):
        parts = lock_utils.lock_name_parts("LOCK.txt")
        self.assertEqual(parts["lock_type"], "exclusive")
        self.assertEqual(parts["scope"], "project")
        self.assertIsNone(parts["host"])

    def test_exclusive_component_scope(self):
        parts = lock_utils.lock_name_parts("LOCK.desktop-app.txt")
        self.assertEqual(parts["lock_type"], "exclusive")
        self.assertEqual(parts["scope"], "desktop-app")
        self.assertIsNone(parts["host"])

    def test_team_whole_folder_uses_host_not_scope(self):
        parts = lock_utils.lock_name_parts("LOCK.team.HOST-A.txt")
        self.assertEqual(parts["lock_type"], "team")
        self.assertEqual(parts["scope"], "project")
        self.assertEqual(parts["host"], "HOST-A")

    def test_team_component_scope_and_host(self):
        parts = lock_utils.lock_name_parts("LOCK.team.desktop-app.HOST-B.txt")
        self.assertEqual(parts["lock_type"], "team")
        self.assertEqual(parts["scope"], "desktop-app")
        self.assertEqual(parts["host"], "HOST-B")

    def test_deprecated_community_keeps_team_semantics(self):
        parts = lock_utils.lock_name_parts("LOCK.community.loop.codex.HOST-C.txt")
        self.assertEqual(parts["lock_type"], "team")
        self.assertEqual(parts["scope"], "loop.codex")
        self.assertEqual(parts["host"], "HOST-C")

    def test_legacy_lock_is_not_returned_twice(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            legacy_path = Path(tmpdir) / "TEST.txt"
            legacy_path.write_text("legacy\n", encoding="utf-8")
            found = lock_utils.find_lock_files(Path(tmpdir))

        self.assertEqual(found, [("TEST.txt", "project", True)])


class TestComputeExpiresAtProtectedLocks(unittest.TestCase):
    """Regressionstest für (Advisor-Nachschärfung).

    Fund: compute_expires_at() ignorierte is_protected_lock() und berechnete
    für User-/Condition-Locks trotzdem ein nominelles expires_at (created +
    24h). Der Lock-Watcher persistiert diesen Wert und markierte damit
    reale, aktive User-Locks (z.B. LOCK.user.zenodo-upload.txt, mehrere Tage
    alt) als "expired" — obwohl is_expired() sie korrekt als nie
    zeitbasiert ablaufend behandelt. Das ist genau die vom Ticket
    beschriebene Gefahr: der Watcher zeigt einen realen, aktiven Lock als
    inaktiv/abgelaufen an.
    """

    def _write_lock(self, tmpdir: str, name: str, days_old: int = 6) -> Path:
        import os
        import time

        lock_path = Path(tmpdir) / name
        lock_path.write_text("owner: test\n", encoding="utf-8")
        old_time = time.time() - days_old * 86400
        os.utime(lock_path, (old_time, old_time))
        return lock_path

    def test_user_lock_has_no_computed_expiry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = self._write_lock(tmpdir, "LOCK.user.zenodo-upload.txt")
            self.assertIsNone(lock_utils.compute_expires_at(lock_path))
            self.assertFalse(lock_utils.is_expired(lock_path))

    def test_condition_lock_has_no_computed_expiry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = self._write_lock(tmpdir, "LOCK.condition.release.txt")
            self.assertIsNone(lock_utils.compute_expires_at(lock_path))
            self.assertFalse(lock_utils.is_expired(lock_path))

    def test_normal_exclusive_lock_still_gets_computed_expiry(self):
        """Gegenprobe: normale Locks sind von der Schutzausnahme NICHT betroffen."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = self._write_lock(tmpdir, "LOCK.txt", days_old=6)
            expires_at = lock_utils.compute_expires_at(lock_path)
            self.assertIsNotNone(expires_at)
            self.assertTrue(lock_utils.is_expired(lock_path))


class TestScannerTeamLock(unittest.TestCase):
    def test_scanner_uses_filename_scope_and_host_for_team_lock(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = Path(tmpdir) / "LOCK.team.desktop-app.HOST-A.txt"
            lock_path.write_text(
                "owner: alice\ncreated: 2026-06-20T10:00\nexpires_after: 365d\n",
                encoding="utf-8",
            )

            record = scanner.lock_to_record(
                lock_path,
                lock_path.name,
                "team.desktop-app.HOST-A",
                False,
            )

        self.assertEqual(record["lock_type"], "team")
        self.assertEqual(record["scope"], "desktop-app")
        self.assertEqual(record["host"], "HOST-A")


if __name__ == "__main__":
    unittest.main()
