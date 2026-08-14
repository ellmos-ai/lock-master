"""
tests/test_smoke.py -- Smoke tests for lock-master

Tests:
  - Scope detection from filename (lock_utils.scope_from_name)
  - Expiry logic (lock_utils.is_expired)
  - find_lock_files / active_locks in a temp directory
  - prune dry-run: reports expired locks, deletes nothing
  - lock_scan.collect_locks: returns results for a temp config
  - lock_scan.render_cache: produces valid Markdown table

Run:
  python -m pytest tests/test_smoke.py -v
"""

from datetime import datetime, timedelta
from pathlib import Path

import lock_utils
from lock_scan import collect_locks, render_cache
from prune_stale_locks import prune


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def write_lock(path: Path, owner: str = "test-agent", created: datetime | None = None,
               expires_after: str = "24h", scope: str = "project") -> None:
    """Write a minimal LOCK.txt file to *path*."""
    ts = (created or datetime.now()).strftime("%Y-%m-%dT%H:%M")
    path.write_text(
        f"owner: {owner}\ncreated: {ts}\nexpires_after: {expires_after}\nscope: {scope}\n",
        encoding="utf-8",
    )


def make_config(roots: list[Path]) -> dict:
    return {
        "default_max_depth": 2,
        "shallow_depth": 1,
        "skip_dirs": ["__pycache__", ".git"],
        "roots": [{"path": str(r)} for r in roots],
    }


# ---------------------------------------------------------------------------
# scope_from_name
# ---------------------------------------------------------------------------

class TestScopeFromName:
    def test_lock_txt_is_project(self):
        assert lock_utils.scope_from_name("LOCK.txt") == "project"

    def test_lock_txt_case_insensitive(self):
        assert lock_utils.scope_from_name("lock.txt") == "project"

    def test_scoped_lock(self):
        assert lock_utils.scope_from_name("LOCK.frontend.txt") == "frontend"

    def test_scoped_lock_underscore(self):
        assert lock_utils.scope_from_name("LOCK.my_scope.txt") == "my_scope"

    def test_non_lock_file_returns_none(self):
        assert lock_utils.scope_from_name("README.md") is None

    def test_non_lock_txt_returns_none(self):
        assert lock_utils.scope_from_name("LOCK.txt.bak") is None

    def test_legacy_test_txt_not_a_lock(self):
        # TEST.txt is handled separately as legacy, not via scope_from_name
        assert lock_utils.scope_from_name("TEST.txt") is None


# ---------------------------------------------------------------------------
# Expiry logic
# ---------------------------------------------------------------------------

class TestExpiry:
    def test_fresh_lock_is_not_expired(self, tmp_path: Path):
        lock = tmp_path / "LOCK.txt"
        write_lock(lock, created=datetime.now(), expires_after="24h")
        assert not lock_utils.is_expired(lock)

    def test_old_lock_is_expired(self, tmp_path: Path):
        lock = tmp_path / "LOCK.txt"
        created = datetime.now() - timedelta(hours=25)
        write_lock(lock, created=created, expires_after="24h")
        assert lock_utils.is_expired(lock)

    def test_custom_expiry_90m(self, tmp_path: Path):
        lock = tmp_path / "LOCK.txt"
        created = datetime.now() - timedelta(minutes=91)
        write_lock(lock, created=created, expires_after="90m")
        assert lock_utils.is_expired(lock)

    def test_custom_expiry_90m_not_yet(self, tmp_path: Path):
        lock = tmp_path / "LOCK.txt"
        created = datetime.now() - timedelta(minutes=89)
        write_lock(lock, created=created, expires_after="90m")
        assert not lock_utils.is_expired(lock)


# ---------------------------------------------------------------------------
# find_lock_files / active_locks
# ---------------------------------------------------------------------------

class TestFindAndActiveLocks:
    def test_finds_project_lock(self, tmp_path: Path):
        write_lock(tmp_path / "LOCK.txt")
        results = lock_utils.find_lock_files(tmp_path)
        names = [r[0] for r in results]
        assert "LOCK.txt" in names

    def test_finds_scoped_lock(self, tmp_path: Path):
        write_lock(tmp_path / "LOCK.api.txt")
        results = lock_utils.find_lock_files(tmp_path)
        scopes = [r[1] for r in results]
        assert "api" in scopes

    def test_active_locks_excludes_expired(self, tmp_path: Path):
        old_created = datetime.now() - timedelta(hours=48)
        write_lock(tmp_path / "LOCK.txt", created=old_created)
        assert lock_utils.active_locks(tmp_path) == []

    def test_active_locks_includes_fresh(self, tmp_path: Path):
        write_lock(tmp_path / "LOCK.txt")
        active = lock_utils.active_locks(tmp_path)
        assert len(active) == 1
        assert active[0][1] == "project"


# ---------------------------------------------------------------------------
# prune dry-run
# ---------------------------------------------------------------------------

class TestPruneDryRun:
    def test_dry_run_does_not_delete(self, tmp_path: Path, capsys):
        old_created = datetime.now() - timedelta(hours=48)
        lock = tmp_path / "LOCK.txt"
        write_lock(lock, created=old_created)

        config = make_config([tmp_path])
        count = prune(config, dry_run=True)

        assert lock.exists(), "dry-run must not delete the file"
        assert count == 1
        captured = capsys.readouterr()
        assert "would remove" in captured.out

    def test_prune_removes_expired(self, tmp_path: Path):
        old_created = datetime.now() - timedelta(hours=48)
        lock = tmp_path / "LOCK.txt"
        write_lock(lock, created=old_created)

        config = make_config([tmp_path])
        count = prune(config, dry_run=False)

        assert not lock.exists(), "expired lock should have been deleted"
        assert count == 1

    def test_prune_keeps_active(self, tmp_path: Path):
        lock = tmp_path / "LOCK.txt"
        write_lock(lock)

        config = make_config([tmp_path])
        count = prune(config, dry_run=False)

        assert lock.exists(), "active lock must not be deleted"
        assert count == 0


# ---------------------------------------------------------------------------
# collect_locks (lock_scan)
# ---------------------------------------------------------------------------

class TestCollectLocks:
    def test_collect_finds_active_lock(self, tmp_path: Path):
        write_lock(tmp_path / "LOCK.txt", owner="ci-agent")
        config = make_config([tmp_path])
        locks = collect_locks(config)
        assert len(locks) == 1
        assert locks[0]["owner"] == "ci-agent"
        assert locks[0]["scope"] == "project"

    def test_collect_excludes_expired(self, tmp_path: Path):
        old_created = datetime.now() - timedelta(hours=48)
        write_lock(tmp_path / "LOCK.txt", created=old_created)
        config = make_config([tmp_path])
        locks = collect_locks(config)
        assert locks == []

    def test_collect_scoped_lock(self, tmp_path: Path):
        write_lock(tmp_path / "LOCK.frontend.txt", owner="design-agent")
        config = make_config([tmp_path])
        locks = collect_locks(config)
        assert len(locks) == 1
        assert locks[0]["scope"] == "frontend"


# ---------------------------------------------------------------------------
# render_cache (lock_scan)
# ---------------------------------------------------------------------------

class TestRenderCache:
    def test_render_produces_markdown_table(self, tmp_path: Path):
        write_lock(tmp_path / "LOCK.txt", owner="render-test")
        config = make_config([tmp_path])
        locks = collect_locks(config)
        md = render_cache(locks, datetime.now(), "Test Cache")
        assert "| Path |" in md
        assert "render-test" in md

    def test_render_empty_list(self):
        md = render_cache([], datetime.now(), "Empty")
        assert "no active locks" in md
