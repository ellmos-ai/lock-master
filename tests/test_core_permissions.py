# -*- coding: utf-8 -*-
"""Tests: agent-neutrales LOCK.permissions-Rechte-Schema (Syntax von .claude)."""
import atexit
import shutil
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if "LOCK_MASTER_WATCHER_DATA" not in os.environ:
    _WATCHER_TMP = tempfile.mkdtemp(prefix="lockmaster-core-permissions-")
    atexit.register(shutil.rmtree, _WATCHER_TMP, True)
    os.environ["LOCK_MASTER_WATCHER_DATA"] = _WATCHER_TMP
sys.path.insert(0, str(ROOT / "pure-locking" / "watcher"))
sys.path.insert(0, str(ROOT / "pure-locking"))
sys.path.insert(0, str(ROOT))

import sys  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402


import permissions  # noqa: E402

PERM = {
    "format": "lock-permissions-v1",
    "scope": "project",
    "owner": "user",
    "default": "allow",
    "rules": {
        "allow": ["Read(**)", "Bash(python:*)"],
        "deny": ["Bash(rm:*)", "Write(**/CREDENTIALS/**)", "mcp__*__delete*"],
        "ask": ["Write(**)"],
    },
    "applies_to_agents": ["claude", "codex", "gemini", "kimi", "*"],
}


class TestEvaluate(unittest.TestCase):
    def test_deny_wins(self):
        self.assertEqual(permissions.evaluate(PERM, "codex", "Bash(rm -rf x)"), "deny")

    def test_allow_read(self):
        self.assertEqual(permissions.evaluate(PERM, "claude", "Read(a.txt)"), "allow")

    def test_allow_python(self):
        self.assertEqual(permissions.evaluate(PERM, "claude", "Bash(python script.py)"), "allow")

    def test_ask_write(self):
        self.assertEqual(permissions.evaluate(PERM, "gemini", "Write(notes.md)"), "ask")

    def test_deny_over_ask_for_credentials(self):
        # Write(**) wäre ask, aber Write(**/CREDENTIALS/**) ist deny -> deny gewinnt.
        self.assertEqual(
            permissions.evaluate(PERM, "kimi", "Write(/x/CREDENTIALS/token.json)"), "deny")

    def test_mcp_wildcard_deny(self):
        self.assertEqual(permissions.evaluate(PERM, "codex", "mcp__filecommander__delete_file"), "deny")

    def test_default_when_no_rule(self):
        self.assertEqual(permissions.evaluate(PERM, "claude", "WebSearch"), "allow")

    def test_default_deny_object(self):
        obj = {"default": "deny", "rules": {"allow": ["Read(**)"]}, "applies_to_agents": ["*"]}
        self.assertEqual(permissions.evaluate(obj, "claude", "Bash(ls)"), "deny")
        self.assertEqual(permissions.evaluate(obj, "claude", "Read(x)"), "allow")


class TestAgentScoping(unittest.TestCase):
    def test_applies_to_specific_agent(self):
        obj = {"default": "deny", "rules": {"allow": ["Bash(*)"]}, "applies_to_agents": ["codex"]}
        self.assertTrue(permissions.applies_to(obj, "codex"))
        self.assertFalse(permissions.applies_to(obj, "claude"))

    def test_wildcard_agent(self):
        self.assertTrue(permissions.applies_to(PERM, "irgendwer"))

    def test_evaluate_returns_default_for_unscoped_agent(self):
        obj = {"default": "allow", "rules": {"deny": ["Bash(*)"]}, "applies_to_agents": ["codex"]}
        # claude ist nicht abgedeckt -> Objekt gilt nicht -> default.
        self.assertEqual(permissions.evaluate(obj, "claude", "Bash(rm)"), "allow")


if __name__ == "__main__":
    unittest.main()
