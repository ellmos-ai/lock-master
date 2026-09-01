#!/usr/bin/env python3
"""Source-checkout entry point for the installable Team-Lock package."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from _lock_master_team import *  # noqa: F403,E402
from _lock_master_team import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
