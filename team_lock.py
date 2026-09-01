"""Stable root shim for the Team-Lock CLI and public Python API."""

from __future__ import annotations

import sys
from pathlib import Path

_SOURCE_PACKAGE = Path(__file__).resolve().parent / "team-lock"
if _SOURCE_PACKAGE.is_dir() and str(_SOURCE_PACKAGE) not in sys.path:
    sys.path.insert(0, str(_SOURCE_PACKAGE))

from _lock_master_team import *  # noqa: F403,E402
from _lock_master_team import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
