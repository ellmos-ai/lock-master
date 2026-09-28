# -*- coding: utf-8 -*-
"""Verifikation der zurueckgespiegelten Lock-/Rechte-Funktionen im lock-master-Modul:
user-locks, prune-Schutz, LOCK.permissions, bulk-lock Schutzinvarianten."""
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MODULE_DIR))

import lock_utils  # noqa: E402
import permissions  # noqa: E402
import bulk_lock  # noqa: E402


def _old(days):
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M")


class TestUserLocks(unittest.TestCase):
    def test_detection(self):
        self.assertTrue(lock_utils.is_user_lock("LOCK.user.txt"))
        self.assertTrue(lock_utils.is_user_lock("LOCK.user.api.txt"))
        self.assertFalse(lock_utils.is_user_lock("LOCK.txt"))
        self.assertFalse(lock_utils.is_user_lock("LOCK.team.LAPTOP.txt"))
        self.assertEqual(lock_utils.scope_from_name("LOCK.user.txt"), "project")
        self.assertEqual(lock_utils.scope_from_name("LOCK.user.api.txt"), "api")

    def test_prune_protection(self):
        with tempfile.TemporaryDirectory() as tmp:
            ul = Path(tmp) / "LOCK.user.txt"
            ul.write_text(f"owner: user\ncreated: {_old(400)}\nexpires_after: 24h\n", encoding="utf-8")
            # v1.4.0: protected locks never expire by time (spec: a user lock
            # holds until the user removes it, even past its nominal expiry).
            self.assertFalse(lock_utils.is_expired(ul))
            self.assertFalse(lock_utils.is_prunable(ul))
            nl = Path(tmp) / "LOCK.txt"
            nl.write_text(f"owner: x\ncreated: {_old(2)}\nexpires_after: 24h\n", encoding="utf-8")
            self.assertTrue(lock_utils.is_prunable(nl))

    def test_content_aware_user_lock_assistant_core_case(self):
        """T-20260926-592074763: LOCK.assets.txt with LOCK.user header must be recognized as user lock."""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "LOCK.assets.txt"
            p.write_text(
                "LOCK.user.claude-code@ASUS-GEI\n"
                "Grund: Banner-Entwurf (zwei Motiv-Varianten, agy-generiert) fuer README/README_de, Review vor Commit\n"
                "Gesetzt: 2026-09-20\n",
                encoding="utf-8",
            )
            self.assertTrue(lock_utils.is_user_lock(p))
            self.assertTrue(lock_utils.is_protected_lock(p))
            self.assertFalse(lock_utils.is_expired(p))
            self.assertFalse(lock_utils.is_prunable(p))
            active = lock_utils.active_locks(Path(tmp))
            self.assertEqual(len(active), 1)
            self.assertEqual(active[0], ("LOCK.assets.txt", "assets", False))

    def test_content_aware_user_lock_accounts_core_case(self):
        """T-20260926-592074763: LOCK.txt with LOCK.user line must be recognized as user lock."""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "LOCK.txt"
            p.write_text(
                "LOCK.user.claude-code\n"
                "created: 2026-09-20T10:00\n",
                encoding="utf-8",
            )
            self.assertTrue(lock_utils.is_user_lock(p))
            self.assertTrue(lock_utils.is_protected_lock(p))
            self.assertFalse(lock_utils.is_expired(p))
            self.assertFalse(lock_utils.is_prunable(p))
            active = lock_utils.active_locks(Path(tmp))
            self.assertEqual(len(active), 1)
            self.assertEqual(active[0], ("LOCK.txt", "project", False))

    def test_content_aware_user_lock_fields_and_bulk_exclusion(self):
        """Verify field-based user lock recognition and bulk lock exclusion."""
        with tempfile.TemporaryDirectory() as tmp:
            p1 = Path(tmp) / "LOCK.txt"
            p1.write_text("owner: claude-code\ntype: user\ncreated: 2026-09-20T10:00\n", encoding="utf-8")
            self.assertTrue(lock_utils.is_user_lock(p1))

            p2 = Path(tmp) / "LOCK.txt"
            p2.write_text("owner: claude-code\nremovable_by: user\ncreated: 2026-09-20T10:00\n", encoding="utf-8")
            self.assertTrue(lock_utils.is_user_lock(p2))

            p3 = Path(tmp) / "LOCK.txt"
            p3.write_text("owner: user\ncreated_by: bulk\ncreated: 2026-09-20T10:00\n", encoding="utf-8")
            self.assertFalse(lock_utils.is_user_lock(p3))


class TestPermissions(unittest.TestCase):
    PERM = {"default": "allow", "applies_to_agents": ["*"],
            "rules": {"deny": ["Bash(rm:*)"], "ask": ["Write(**)"], "allow": ["Read(**)"]}}

    def test_eval(self):
        self.assertEqual(permissions.evaluate(self.PERM, "codex", "Bash(rm -rf x)"), "deny")
        self.assertEqual(permissions.evaluate(self.PERM, "claude", "Read(a)"), "allow")
        self.assertEqual(permissions.evaluate(self.PERM, "gemini", "Write(x)"), "ask")


class TestBulkLock(unittest.TestCase):
    def test_user_lock_root_untouched_and_reversible(self):
        with tempfile.TemporaryDirectory() as tmp:
            roots = [Path(tmp) / f"p{i}" for i in range(3)]
            for r in roots:
                r.mkdir()
            (roots[0] / "LOCK.user.txt").write_text(
                "owner: user\ncreated: 2026-06-01T10:00\n", encoding="utf-8")
            res = bulk_lock.bulk_lock(roots, created="2026-06-27T14:00", commit=True)
            self.assertEqual(res["locked"], 2)
            self.assertFalse((roots[0] / "LOCK.txt").exists())
            un = bulk_lock.bulk_unlock(roots, commit=True)
            self.assertEqual(un["unlocked"], 2)
            self.assertTrue((roots[0] / "LOCK.user.txt").exists())


if __name__ == "__main__":
    unittest.main()
