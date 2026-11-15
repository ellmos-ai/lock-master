# -*- coding: utf-8 -*-
"""Tests: User-Locks (LOCK.user(.<scope>).txt) — nur vom User entfernbar.

Importiert die kanonische, laufende Lock-Logik aus _scripts/ (die der Daemon nutzt).
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("LOCK_MASTER_WATCHER_DATA", tempfile.mkdtemp(prefix="lockmaster-core-user-locks-"))
sys.path.insert(0, str(ROOT / "pure-locking" / "watcher"))
sys.path.insert(0, str(ROOT / "pure-locking"))
sys.path.insert(0, str(ROOT))

import sys  # noqa: E402
import tempfile  # noqa: E402
import unittest  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402
from pathlib import Path  # noqa: E402


import lock_utils  # noqa: E402


class TestUserLockDetection(unittest.TestCase):
    def test_user_lock_project_scope(self):
        parts = lock_utils.lock_name_parts("LOCK.user.txt")
        self.assertIsNotNone(parts)
        self.assertEqual(parts["lock_type"], "user")
        self.assertEqual(parts["scope"], "project")

    def test_user_lock_scoped(self):
        parts = lock_utils.lock_name_parts("LOCK.user.frontend.txt")
        self.assertEqual(parts["lock_type"], "user")
        self.assertEqual(parts["scope"], "frontend")

    def test_is_user_lock_helper(self):
        self.assertTrue(lock_utils.is_user_lock("LOCK.user.txt"))
        self.assertTrue(lock_utils.is_user_lock("LOCK.user.api.txt"))
        self.assertFalse(lock_utils.is_user_lock("LOCK.txt"))
        self.assertFalse(lock_utils.is_user_lock("LOCK.team.LAPTOP.txt"))
        self.assertFalse(lock_utils.is_user_lock("LOCK.frontend.txt"))

    def test_lock_type_from_name(self):
        self.assertEqual(lock_utils.lock_type_from_name("LOCK.user.txt"), "user")
        self.assertEqual(lock_utils.lock_type_from_name("LOCK.txt"), "exclusive")
        self.assertEqual(lock_utils.lock_type_from_name("LOCK.team.LAPTOP.txt"), "team")

    def test_user_lock_not_confused_with_exclusive_component(self):
        # Eine normale Komponente "users" (Plural) bleibt exclusive, nur exaktes
        # Marker-Segment "user" triggert den user-Lock-Typ.
        self.assertEqual(lock_utils.lock_type_from_name("LOCK.users.txt"), "exclusive")


class TestUserLockProtection(unittest.TestCase):
    """User-Locks dürfen von prune/Agenten nie als entfernbar gelten."""

    def test_is_protected_user_lock(self):
        self.assertTrue(lock_utils.is_protected_lock("LOCK.user.txt"))
        self.assertFalse(lock_utils.is_protected_lock("LOCK.txt"))

    def test_old_user_lock_is_not_prunable_even_if_expired(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            ul = d / "LOCK.user.txt"
            old = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%dT%H:%M")
            ul.write_text(f"owner: user\ncreated: {old}\nexpires_after: 24h\n", encoding="utf-8")
            # Geschützte Locks laufen zeitbasiert NIE ab (kanonischer Fix
            # lock-master v1.4.0: nominell abgelaufene User-Locks fielen
            # fälschlich aus active_locks) ...
            self.assertFalse(lock_utils.is_expired(ul))
            # ... und sind erst recht nicht prunebar.
            self.assertFalse(lock_utils.is_prunable(ul))

    def test_expired_normal_lock_is_prunable(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            nl = d / "LOCK.txt"
            old = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")
            nl.write_text(f"owner: claude\ncreated: {old}\nexpires_after: 24h\n", encoding="utf-8")
            self.assertTrue(lock_utils.is_prunable(nl))


if __name__ == "__main__":
    unittest.main()
