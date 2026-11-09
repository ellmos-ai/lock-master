"""Pytest configuration and session lifecycle fixtures for lock-master.

Provides:
- OneDrive and CloudStorage basetemp guard to protect cloud sync trees (Windows / macOS).
- Automatic cleanup of temporary lock fixtures (LOCK*.txt) and basetemp directory at session end
  so that lingering test files never trigger false-positive lock alerts during lock scans.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest


def _is_cloud_or_onedrive(path: Path | str) -> bool:
    """True if path is inside a OneDrive or CloudStorage sync tree (Windows / macOS)."""
    norm = str(path).replace("\\", "/").lower()
    return "onedrive" in norm or "cloudstorage" in norm


def pytest_configure(config: pytest.Config) -> None:
    """Enforce safe basetemp locations outside cloud storage sync roots."""
    root = Path(config.rootpath).resolve()

    # Fail closed in git-less mirror trees that link to canonical clones
    if (root / "REPO.pointer.json").is_file() and not (root / ".git").exists():
        raise pytest.UsageError(
            f"{root} is a git-less mirror (REPO.pointer.json, no .git). "
            "Run the tests in the canonical clone instead (see REPO.pointer.json)."
        )

    basetemp_opt = getattr(config.option, "basetemp", None)
    if not basetemp_opt:
        basetemp_opt = config.getoption("basetemp") if hasattr(config, "getoption") else None

    # Anchor relative basetemp to repo root
    if basetemp_opt and not Path(basetemp_opt).is_absolute():
        config.option.basetemp = str((root / basetemp_opt).resolve())

    # If the project or basetemp is inside OneDrive / CloudStorage, redirect to safe local temp
    current_basetemp = getattr(config.option, "basetemp", None) or basetemp_opt
    target_path = Path(current_basetemp).resolve() if current_basetemp else root
    if _is_cloud_or_onedrive(root) or (current_basetemp and _is_cloud_or_onedrive(target_path)):
        safe_temp = Path(tempfile.gettempdir()) / "lock-master-pytest-temp"
        safe_temp.mkdir(parents=True, exist_ok=True)
        config.option.basetemp = str(safe_temp)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Clean up basetemp and guarantee no LOCK* test fixture files remain on disk."""
    try:
        basetemp_val = getattr(session.config.option, "basetemp", None)
        if not basetemp_val:
            return
        root = Path(session.config.rootpath).resolve()
        bt = (root / basetemp_val).resolve()
        if not bt.is_dir():
            return

        # 1. First explicitly unlink all LOCK* files to prevent scan false positives
        for lock_file in bt.rglob("LOCK*"):
            try:
                if lock_file.is_file() or lock_file.is_symlink():
                    lock_file.unlink()
            except OSError:
                pass

        # 2. Only remove the entire basetemp folder if it is an expected dedicated test temp dir
        if bt.name in (".pytest_temp", "lock-master-pytest-temp"):
            shutil.rmtree(bt, ignore_errors=True)
    except Exception:
        pass
