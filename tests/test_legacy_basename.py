"""Legacy TEST markers use their exact basename on every host (BACH task2044)."""
from pathlib import Path
from types import SimpleNamespace

import pytest

import lock_utils
from lock_scan import _check_dir


@pytest.mark.parametrize("name", ["test.txt", "tests.txt", "Test.txt", "Tests.txt"])
def test_ordinary_test_documents_are_not_legacy_locks(tmp_path, name):
    (tmp_path / name).write_text("Test command documentation", encoding="utf-8")
    assert lock_utils.find_lock_files(tmp_path) == []
    assert lock_utils.active_locks(tmp_path) == []
    assert _check_dir(tmp_path, as_json=True, strict=False) == 0


@pytest.mark.parametrize("name", ["TEST.txt", "TESTS.txt"])
def test_exact_legacy_markers_still_block_without_expiry(tmp_path, name):
    (tmp_path / name).write_text("", encoding="utf-8")
    expected = [(name, "project", True)]
    assert lock_utils.find_lock_files(tmp_path) == expected
    assert lock_utils.active_locks(tmp_path) == expected
    assert lock_utils.find_lock_files(tmp_path, include_legacy=False) == []
    assert _check_dir(tmp_path, as_json=True, strict=False) == 1


def test_case_insensitive_glob_does_not_promote_lowercase_help(tmp_path, monkeypatch):
    """Reproduce Windows glob matching on Linux/macOS CI as well."""
    help_file = tmp_path / "test.txt"
    help_file.write_text("bach test --help", encoding="utf-8")
    original_glob = Path.glob

    def insensitive_glob(path, pattern, *args, **kwargs):
        if path == tmp_path and pattern == "TEST.txt":
            return iter([help_file])
        return original_glob(path, pattern, *args, **kwargs)

    monkeypatch.setattr(Path, "glob", insensitive_glob)
    assert lock_utils.find_lock_files(tmp_path) == []


def test_pattern_named_glob_alias_does_not_promote_lowercase_help(tmp_path, monkeypatch):
    """Simulate Windows Python 3.10/3.11 literal glob on every CI host."""
    (tmp_path / "test.txt").write_text("bach test --help", encoding="utf-8")
    original_glob = Path.glob
    patterns = []

    def pattern_named_glob(path, pattern, *args, **kwargs):
        if path == tmp_path:
            patterns.append(pattern)
            if pattern == "TEST.txt":
                return iter([SimpleNamespace(name="TEST.txt", is_file=lambda: True)])
        return original_glob(path, pattern, *args, **kwargs)

    monkeypatch.setattr(Path, "glob", pattern_named_glob)
    assert lock_utils.find_lock_files(tmp_path) == []
    assert not set(patterns).intersection(lock_utils.LEGACY_LOCK_NAMES)


@pytest.mark.parametrize("name", [
    "lock.txt", "lock.user.work.txt", "lock.condition.work.txt",
    "LOCK.TXT", "LOCK.USER.work.TXT",
])
def test_modern_lock_names_remain_case_insensitive(tmp_path, name):
    (tmp_path / name).write_text("owner: user\n", encoding="utf-8")
    locks = lock_utils.find_lock_files(tmp_path)
    assert len(locks) == 1 and locks[0][0] == name and not locks[0][2]
    assert len(lock_utils.active_locks(tmp_path)) == 1
    assert _check_dir(tmp_path, as_json=True, strict=False) == 1
