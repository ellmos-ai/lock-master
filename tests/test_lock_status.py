"""Tests for lock_status.py (per-project lock status checker)."""
import tempfile
import unittest
from pathlib import Path

import lock_create
import lock_status


class LockStatusCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_unlocked_project_returns_exit_code_0(self):
        locks = lock_status.check_project_status(self.project)
        self.assertEqual(len(locks), 0)

    def test_locked_project_returns_exit_code_1_and_lock_details(self):
        # Stamp a lock using lock_create
        lock_create.main([str(self.project), "--owner", "test-agent", "--purpose", "test lock"])
        locks = lock_status.check_project_status(self.project)
        self.assertEqual(len(locks), 1)
        self.assertEqual(locks[0]["owner"], "test-agent")
        self.assertEqual(locks[0]["scope"], "project")

    def test_invalid_path_raises_error(self):
        nonexistent = Path(self.temp.name) / "nonexistent"
        with self.assertRaises(ValueError):
            lock_status.check_project_status(nonexistent)


if __name__ == "__main__":
    unittest.main()
