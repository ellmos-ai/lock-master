# -*- coding: utf-8 -*-
"""Integrationstest: prune_stale_locks lässt User-Locks unangetastet."""
import atexit
import shutil
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if "LOCK_MASTER_WATCHER_DATA" not in os.environ:
    _WATCHER_TMP = tempfile.mkdtemp(prefix="lockmaster-core-prune-user-locks-")
    atexit.register(shutil.rmtree, _WATCHER_TMP, True)
    os.environ["LOCK_MASTER_WATCHER_DATA"] = _WATCHER_TMP
sys.path.insert(0, str(ROOT / "pure-locking" / "watcher"))
sys.path.insert(0, str(ROOT / "pure-locking"))
sys.path.insert(0, str(ROOT))

import sys  # noqa: E402
import tempfile  # noqa: E402
import unittest  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402
from pathlib import Path  # noqa: E402


import prune_stale_locks  # noqa: E402


def _old(days):
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M")


class TestPruneRespectsUserLocks(unittest.TestCase):
    def test_prune_removes_expired_but_keeps_user_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "LOCK.txt").write_text(
                f"owner: claude\ncreated: {_old(2)}\nexpires_after: 24h\n", encoding="utf-8")
            (d / "LOCK.user.txt").write_text(
                f"owner: user\ncreated: {_old(400)}\nexpires_after: 24h\n", encoding="utf-8")

            # iter_lock_dirs durch unser temp-Verzeichnis ersetzen (kein echtes roots.json).
            original = prune_stale_locks.iter_lock_dirs
            prune_stale_locks.iter_lock_dirs = lambda config: iter([d])
            try:
                removed = prune_stale_locks.prune({}, dry_run=False)
            finally:
                prune_stale_locks.iter_lock_dirs = original

            self.assertEqual(removed, 1, "Nur der normale abgelaufene Lock soll entfernt werden")
            self.assertFalse((d / "LOCK.txt").exists(), "Abgelaufener LOCK.txt muss weg sein")
            self.assertTrue((d / "LOCK.user.txt").exists(), "User-Lock muss erhalten bleiben")


if __name__ == "__main__":
    unittest.main()
