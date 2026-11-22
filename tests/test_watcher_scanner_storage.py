"""Regression tests for the watcher scan pipeline and storage layer.

Covers the 2026-07-04 review findings: scanner.lock_to_record() used
lock_utils helpers that did not exist in the portable mirror (daemon crash on
the very first scan), and the locks table CHECK constraint rejected the
'user' and 'condition' lock types introduced in v1.3.0/v1.4.0.
"""

from __future__ import annotations

import atexit
import shutil
import os
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# The watcher keeps runtime data outside the repo; point it at a temp dir
# BEFORE importing watcher modules (config creates the dir on import).
if "LOCK_MASTER_WATCHER_DATA" not in os.environ:
    _WATCHER_TMP = tempfile.mkdtemp(prefix="lockmaster-test-")
    atexit.register(shutil.rmtree, _WATCHER_TMP, True)
    os.environ["LOCK_MASTER_WATCHER_DATA"] = _WATCHER_TMP
sys.path.insert(0, str(ROOT / "pure-locking" / "watcher"))

import lock_utils  # noqa: E402
import scanner  # noqa: E402
import storage  # noqa: E402


def _write_lock(path: Path, extra: str = "") -> Path:
    path.write_text(
        "owner: test-agent\n"
        f"created: {datetime.now().strftime('%Y-%m-%dT%H:%M')}\n"
        "host: TESTHOST\n"
        "purpose: regression test\n"
        f"{extra}",
        encoding="utf-8",
    )
    return path


def test_lock_to_record_handles_all_lock_types(tmp_path: Path):
    cases = {
        "LOCK.txt": "exclusive",
        "LOCK.docs.txt": "exclusive",
        "LOCK.team.LAPTOP.txt": "team",
        "LOCK.user.txt": "user",
        "LOCK.condition.publish.txt": "condition",
    }
    for name, expected_type in cases.items():
        extra = "release_condition: done\n" if expected_type == "condition" else ""
        lock_path = _write_lock(tmp_path / name, extra)
        scope = lock_utils.scope_from_name(name) or "project"
        record = scanner.lock_to_record(lock_path, name, scope, False)
        assert record["lock_type"] == expected_type, name
        assert record["owner"] == "test-agent"
        assert record["path"] == str(lock_path)


def test_lock_to_record_parses_team_sections(tmp_path: Path):
    lock_path = tmp_path / "LOCK.team.LAPTOP.txt"
    _write_lock(lock_path, "\n## Anwesenheit\n- agent-a seit 10:00\n")
    record = scanner.lock_to_record(lock_path, lock_path.name, "project", False)
    assert record["team_data"] is not None
    assert record["team_data"]["presence"] == ["agent-a seit 10:00"]


def test_storage_accepts_user_and_condition_locks(tmp_path: Path):
    db = storage.LockDB(tmp_path / "test.db")
    for lock_type in ("exclusive", "team", "user", "condition", "legacy"):
        lock_id = db.upsert_lock({
            "path": str(tmp_path / f"LOCK.{lock_type}.txt"),
            "filename": f"LOCK.{lock_type}.txt",
            "project_dir": str(tmp_path),
            "scope": "project",
            "lock_type": lock_type,
        })
        assert lock_id > 0, lock_type


def test_storage_migrates_old_check_constraint(tmp_path: Path):
    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE locks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT NOT NULL UNIQUE,
            filename TEXT NOT NULL,
            project_dir TEXT NOT NULL,
            scope TEXT,
            lock_type TEXT DEFAULT 'exclusive' CHECK(lock_type IN ('exclusive', 'team', 'legacy')),
            is_legacy INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active' CHECK(status IN ('active', 'expired', 'deleted')),
            owner TEXT, host TEXT, purpose TEXT, mode TEXT,
            created_at TEXT, created_source TEXT,
            expires_after TEXT, expires_at TEXT,
            team_data TEXT, raw_content TEXT,
            first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
            removed_at TEXT, updated_at TEXT NOT NULL
        );
    """)
    conn.execute(
        "INSERT INTO locks (path, filename, project_dir, lock_type,"
        " first_seen, last_seen, updated_at)"
        " VALUES ('x', 'LOCK.txt', 'p', 'exclusive', 't', 't', 't')"
    )
    conn.commit()
    conn.close()

    db = storage.LockDB(db_path)
    # old row survived the rebuild
    assert db.get_lock_by_path("x") is not None
    # new lock types are now accepted
    lock_id = db.upsert_lock({
        "path": "y", "filename": "LOCK.user.txt", "project_dir": "p",
        "lock_type": "user",
    })
    assert lock_id > 0
