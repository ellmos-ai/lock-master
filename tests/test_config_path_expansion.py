"""Regression test: configured paths must expand `~` and environment variables.

Background (2026-08-01): a consumer switched its code source to this module while
its `lock_roots.json` referenced roots via `%USERPROFILE%`. `load_config()` did
not expand them, so every one of those roots stayed a literal string,
`Path.exists()` returned False and `iter_lock_dirs()` skipped it silently. The
scan reported 2 instead of 12 active locks -- a lock watcher that misses locks is
worse than none at all, because it reports false safety.

The failure was silent in both directions: no exception, no warning, just fewer
results. These tests pin the expansion down.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import lock_scan


def _write_config(tmp_path: Path, config: dict) -> Path:
    roots_file = tmp_path / "lock_roots.json"
    roots_file.write_text(json.dumps(config), encoding="utf-8")
    return roots_file


def test_root_paths_expand_environment_variables(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LOCKMASTER_TEST_HOME", str(tmp_path))
    var = "%LOCKMASTER_TEST_HOME%" if os.name == "nt" else "$LOCKMASTER_TEST_HOME"

    roots_file = _write_config(tmp_path, {"roots": [{"path": var + os.sep + "work"}]})
    config = lock_scan.load_config(roots_file)

    expanded = config["roots"][0]["path"]
    assert str(tmp_path) in expanded
    assert "LOCKMASTER_TEST_HOME" not in expanded


def test_root_paths_expand_user_home(tmp_path: Path):
    roots_file = _write_config(tmp_path, {"roots": [{"path": os.path.join("~", "work")}]})
    config = lock_scan.load_config(roots_file)

    expanded = config["roots"][0]["path"]
    assert not expanded.startswith("~")
    assert str(Path.home()) in expanded


def test_cache_paths_and_filter_prefix_expand(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LOCKMASTER_TEST_HOME", str(tmp_path))
    var = "%LOCKMASTER_TEST_HOME%" if os.name == "nt" else "$LOCKMASTER_TEST_HOME"

    roots_file = _write_config(tmp_path, {
        "caches": [{
            "name": "scoped",
            "path": var + os.sep + "LOCK-CACHE.md",
            "filter_prefix": var + os.sep + "projects",
        }]
    })
    config = lock_scan.load_config(roots_file)

    entry = config["caches"][0]
    assert "LOCKMASTER_TEST_HOME" not in entry["path"]
    assert "LOCKMASTER_TEST_HOME" not in entry["filter_prefix"]
    assert str(tmp_path) in entry["path"]
    assert str(tmp_path) in entry["filter_prefix"]


def test_unexpandable_root_is_skipped_not_crashed(tmp_path: Path):
    """An unresolvable path must not raise -- it simply yields no directories."""
    roots_file = _write_config(tmp_path, {
        "roots": [{"path": os.path.join(str(tmp_path), "does-not-exist")}]
    })
    config = lock_scan.load_config(roots_file)

    assert list(lock_scan.iter_lock_dirs(config)) == []


def test_expansion_makes_an_existing_root_scannable(tmp_path: Path, monkeypatch):
    """End-to-end: the variable-based root must actually be walked."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("LOCKMASTER_TEST_HOME", str(tmp_path))
    var = "%LOCKMASTER_TEST_HOME%" if os.name == "nt" else "$LOCKMASTER_TEST_HOME"

    roots_file = _write_config(tmp_path, {"roots": [{"path": var + os.sep + "workspace"}]})
    config = lock_scan.load_config(roots_file)

    dirs = list(lock_scan.iter_lock_dirs(config))
    assert workspace in dirs, "expanded root must be scanned, not skipped"


def test_missing_roots_file_uses_example_or_defaults(tmp_path: Path):
    """When lock_roots.json is absent, load_config falls back to example or safe defaults."""
    non_existent = tmp_path / "does_not_exist_lock_roots.json"
    config = lock_scan.load_config(non_existent)
    assert isinstance(config, dict)
    assert "default_max_depth" in config
    assert "roots" in config
