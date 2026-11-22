"""End-to-End-Regressionstest für.

Alle bisherigen Tests in test_watcher.py mocken scanner.full_scan/check_paths
und prüfen NUR die Reconciliation-Logik in _run_full_scan — sie rühren nie
den echten Dateisystem-Scanner (scanner.full_scan -> lock_scan.iter_lock_dirs
-> Path.iterdir()) an. Genau das ließ den stillen Ausfall aus
durchrutschen: alle 13 vorhandenen Testdateien mockten die Scan-Ebene weg.

Dieser Test legt eine echte LOCK.txt in einem temporären Verzeichnis an,
lässt _run_full_scan UNGEMOCKT laufen (echter Scanner, echte Dateisystem-
Traversierung über eine minimale lock_roots.json-kompatible Konfiguration)
und prüft die Abnahme aus dem Ticket:
  - ein frisch angelegter Lock erscheint nach einem Scan-Intervall aktiv,
  - nach Löschen der Datei verschwindet er nach dem nächsten Scan wieder.
"""

import atexit
import shutil
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if "LOCK_MASTER_WATCHER_DATA" not in os.environ:
    _WATCHER_TMP = tempfile.mkdtemp(prefix="lockmaster-watcher-scan-roundtrip-")
    atexit.register(shutil.rmtree, _WATCHER_TMP, True)
    os.environ["LOCK_MASTER_WATCHER_DATA"] = _WATCHER_TMP
sys.path.insert(0, str(ROOT / "pure-locking" / "watcher"))
sys.path.insert(0, str(ROOT / "pure-locking"))
sys.path.insert(0, str(ROOT))

import os  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402

import lock_utils  # noqa: E402
import lock_watcher  # noqa: E402
import storage  # noqa: E402


class TestRealScanRoundtrip(unittest.TestCase):
    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_db.close()
        self.db = storage.LockDB(Path(self.tmp_db.name))

        self.tmp_root_ctx = tempfile.TemporaryDirectory()
        self.tmp_root = Path(self.tmp_root_ctx.name)
        self.project_dir = self.tmp_root / "TestProjekt"
        self.project_dir.mkdir(parents=True)

        # Minimalkonfiguration im selben Schema wie _scripts/lock_roots.json,
        # aber auf das temporäre Testverzeichnis begrenzt (kein cloud-sync I/O).
        self.cfg = {
            "default_max_depth": 4,
            "shallow_depth": 2,
            "skip_dirs": [],
            "roots": [{"path": str(self.tmp_root)}],
        }

    def tearDown(self):
        self.db.close()
        for ext in ("", "-wal", "-shm"):
            try:
                os.unlink(self.tmp_db.name + ext)
            except OSError:
                pass
        self.tmp_root_ctx.cleanup()

    def test_real_lock_appears_and_disappears_across_real_scans(self):
        lock_path = self.project_dir / "LOCK.txt"
        lock_path.write_text(
            "owner: Regressionstest\n"
            # A fixed date would be a time bomb: the lock has to be young at test time.
            f"created: {lock_utils.created_utc_iso()}\n"
            "scope: Gegenprobe echter Scanner\n",
            encoding="utf-8",
        )

        # Echter Scan, kein Mock von scanner.full_scan/check_paths.
        stats = lock_watcher._run_full_scan(self.db, self.cfg, update_cache=False)
        self.assertEqual(stats["new"], 1)

        active = self.db.get_active_locks()
        active_paths = {a["path"] for a in active}
        self.assertIn(str(lock_path), active_paths)

        # Lock löschen, nächster Scan muss ihn als gelöscht erkennen.
        lock_path.unlink()
        stats2 = lock_watcher._run_full_scan(self.db, self.cfg, update_cache=False)
        self.assertEqual(stats2["deleted"], 1)

        lock = self.db.get_lock_by_path(str(lock_path))
        self.assertEqual(lock["status"], "deleted")

        active_after = {a["path"] for a in self.db.get_active_locks()}
        self.assertNotIn(str(lock_path), active_after)

    def test_full_scan_records_fresh_last_scan_at(self):
        """Nach einem echten Scan muss last_scan_at aktuell sein — das ist
        genau das Feld, das im Ticket zwei Wochen alt stehenblieb."""
        before = self.db.get_stats().get("last_scan_at")
        lock_watcher._run_full_scan(self.db, self.cfg, update_cache=False)
        after = self.db.get_stats().get("last_scan_at")

        self.assertIsNotNone(after)
        self.assertNotEqual(before, after)


if __name__ == "__main__":
    unittest.main()
