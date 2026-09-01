"""Tests for lock_create.py (convenience lock stamper)."""
import tempfile
import unittest
from pathlib import Path

import lock_create
import lock_utils


class LockCreateCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _run(self, *extra: str) -> int:
        return lock_create.main([str(self.project), "--owner", "test-agent",
                                 *extra])

    def test_default_creates_project_lock(self):
        self.assertEqual(self._run("--purpose", "unit test"), 0)
        lock_path = self.project / "LOCK.txt"
        self.assertTrue(lock_path.exists())
        data = lock_utils.parse_lock_file(lock_path)
        self.assertEqual(data["owner"], "test-agent")
        self.assertEqual(data["scope"], "project")
        self.assertEqual(data["expires_after"], "24h")
        self.assertFalse(lock_utils.is_expired(lock_path))

    def test_scoped_lock_name(self):
        self._run("--scope", "docs")
        self.assertTrue((self.project / "LOCK.docs.txt").exists())

    def test_team_lock_names(self):
        self._run("--team", "LAPTOP")
        project_lock = self.project / "LOCK.team.LAPTOP.txt"
        self.assertTrue(project_lock.exists())
        self.assertEqual(lock_utils.parse_lock_file(project_lock)["host"], "LAPTOP")
        self._run("--team", "LAPTOP", "--scope", "assets")
        team_scoped = self.project / "LOCK.team.assets.LAPTOP.txt"
        self.assertTrue(team_scoped.exists())
        self.assertTrue(lock_utils.is_team_lock(team_scoped.name))

    def test_team_lock_rejects_mismatched_explicit_host(self):
        with self.assertRaises(SystemExit):
            self._run("--team", "LAPTOP", "--host", "OTHER")

    def test_user_lock(self):
        self._run("--user")
        lock_path = self.project / "LOCK.user.txt"
        self.assertTrue(lock_utils.is_user_lock(lock_path.name))
        data = lock_utils.parse_lock_file(lock_path)
        self.assertEqual(data["removable_by"], "user")
        self.assertNotIn("expires_after", data)

    def test_condition_lock_requires_release_condition(self):
        with self.assertRaises(SystemExit):
            self._run("--condition")

    def test_condition_lock(self):
        self._run("--condition", "--scope", "publish",
                  "--release-condition", "PR merged")
        lock_path = self.project / "LOCK.condition.publish.txt"
        self.assertTrue(lock_utils.is_condition_lock(lock_path.name))
        data = lock_utils.parse_lock_file(lock_path)
        self.assertEqual(data["release_condition"], "PR merged")
        self.assertNotIn("expires_after", data)

    def test_refuses_overwrite_without_force(self):
        self._run()
        with self.assertRaises(SystemExit):
            self._run()
        self.assertEqual(self._run("--force"), 0)

    def test_rejects_path_like_scope(self):
        for bad in ("../evil", "a/b", "a\\b", "a.b"):
            with self.assertRaises(SystemExit, msg=bad):
                self._run("--scope", bad)

    def test_mutually_exclusive_kinds(self):
        with self.assertRaises(SystemExit):
            self._run("--team", "LAPTOP", "--user")


if __name__ == "__main__":
    unittest.main()
