"""Shared pointer-locator fixture for tests that model a same-host twin pair."""
from __future__ import annotations

from pathlib import Path

import lock_utils


def same_host_locator(repo_name: str, clone: Path) -> dict:
    """``local_locator`` whose clone path is valid on the host running the tests.

    ``windows_default`` is only read on Windows and legacy hosts. On macOS the
    resolver requires an explicit ``macos_default``, so a fixture that points
    at a real local directory has to declare it there as well.
    """
    locator = {"repo_name": repo_name, "windows_default": str(clone)}
    if lock_utils.platform.system().casefold() == "darwin":
        locator["macos_default"] = str(clone)
    return locator
