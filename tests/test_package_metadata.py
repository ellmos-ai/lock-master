"""Tests for package metadata, PEP-621/639 compliance, gitignore rules and stack integrity."""

from __future__ import annotations

from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent.parent


def test_pyproject_pep621_pep639_compliance():
    pyproject_file = ROOT / "pyproject.toml"
    assert pyproject_file.is_file(), "pyproject.toml must exist"

    with open(pyproject_file, "rb") as f:
        data = tomllib.load(f)

    project = data.get("project", {})
    assert project.get("name") == "lock-master"
    assert project.get("license") == "MIT", "License must be standard SPDX string 'MIT'"

    classifiers = project.get("classifiers", [])
    for c in classifiers:
        assert not c.startswith("License ::"), f"Deprecated license classifier found: {c}"

    setuptools_cfg = data.get("tool", {}).get("setuptools", {})
    assert "py-modules" in setuptools_cfg, "py-modules must be explicitly configured"
    expected_modules = [
        "bulk_lock",
        "lock_create",
        "lock_scan",
        "lock_status",
        "lock_utils",
        "permissions",
        "prune_stale_locks",
        "team_lock",
    ]
    for mod in expected_modules:
        assert mod in setuptools_cfg["py-modules"], f"Module {mod} missing in py-modules"
    assert "_lock_master_team" in setuptools_cfg.get("packages", [])
    assert setuptools_cfg.get("package-dir", {}).get("_lock_master_team") == "team-lock/_lock_master_team"
    assert project.get("scripts", {}).get("lock-master-team") == "team_lock:main"


def test_manifest_in_exists_and_grafts_stack():
    manifest_file = ROOT / "MANIFEST.in"
    assert manifest_file.is_file(), "MANIFEST.in must exist for sdist completeness"

    content = manifest_file.read_text(encoding="utf-8")
    assert "graft pure-locking" in content
    assert "graft permission-control" in content
    assert "graft team-lock" in content
    assert "graft assets" in content


def test_gitignore_contains_security_and_build_patterns():
    gitignore_file = ROOT / ".gitignore"
    assert gitignore_file.is_file(), ".gitignore must exist"

    content = gitignore_file.read_text(encoding="utf-8")
    lines = [line.strip() for line in content.splitlines() if line.strip() and not line.startswith("#")]

    assert "lock_roots.json" in lines
    assert "LOCK-CACHE.md" in lines
    assert ".env" in lines
    assert "mobile_icons/" in lines or "mobile_icons" in lines
    assert "__pycache__/" in lines or "__pycache__" in lines


def test_root_shims_resolve_to_target_implementations():
    shims = [
        ("bulk_lock.py", ROOT / "pure-locking" / "bulk_lock.py"),
        ("lock_create.py", ROOT / "pure-locking" / "lock_create.py"),
        ("lock_scan.py", ROOT / "pure-locking" / "lock_scan.py"),
        ("lock_status.py", ROOT / "pure-locking" / "lock_status.py"),
        ("lock_utils.py", ROOT / "pure-locking" / "lock_utils.py"),
        ("permissions.py", ROOT / "permission-control" / "permissions.py"),
        ("prune_stale_locks.py", ROOT / "pure-locking" / "prune_stale_locks.py"),
        ("team_lock.py", ROOT / "team-lock" / "team_lock.py"),
    ]
    for shim_name, real_path in shims:
        shim_file = ROOT / shim_name
        assert shim_file.is_file(), f"Root shim {shim_name} must exist"
        assert real_path.is_file(), f"Real target {real_path} must exist"
