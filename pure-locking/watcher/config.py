"""Konfiguration für den lock-master Watcher."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Pfad-Konstanten
WATCHER_DIR: Path = Path(__file__).resolve().parent
REPO_ROOT: Path = WATCHER_DIR.parent
SCRIPTS_DIR: Path = REPO_ROOT

# DB außerhalb synchronisierter Projektordner halten (WAL-Sidecars + Sync = Korruptionsrisiko)
_DATA_ENV = "LOCK_MASTER_WATCHER_DATA"
_LOCAL_DATA_DIR: Path = Path(os.environ.get(_DATA_ENV, Path.home() / ".lock_master_watcher")).expanduser()
_LOCAL_DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH: Path = _LOCAL_DATA_DIR / "watcher.db"
DAEMON_STATUS_PATH: Path = _LOCAL_DATA_DIR / "daemon_status.json"

# Intervalle in Sekunden
FULL_SCAN_INTERVAL: int = 60
CHECK_INTERVAL: int = 20

# Repo-Root zum Python-Path hinzufügen, damit lock_utils und lock_scan importierbar sind
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import lock_scan as _lock_scan  # noqa: E402

ROOTS_FILE_ENV = "LOCK_MASTER_ROOTS_FILE"


def _roots_file_candidates() -> list[Path]:
    """Return user-neutral candidates in explicit-to-conventional order."""
    candidates: list[Path] = []

    explicit = os.environ.get(ROOTS_FILE_ENV)
    if explicit:
        candidates.append(Path(explicit).expanduser())

    # Standalone/default installation next to the portable scanner.
    candidates.append(_lock_scan.DEFAULT_ROOTS_FILE)

    # Windows OneDrive exposes one of these variables depending on account type.
    for env_name in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        base = os.environ.get(env_name)
        if base:
            candidates.append(Path(base).expanduser() / "_scripts" / "lock_roots.json")

    # Cross-version fallback without embedding a user name.
    candidates.append(Path.home() / "OneDrive" / "_scripts" / "lock_roots.json")

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = os.path.normcase(str(candidate))
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def resolve_roots_file() -> Path:
    """Resolve the active roots file or fail with an actionable diagnosis."""
    candidates = _roots_file_candidates()
    for candidate in candidates:
        if candidate.is_file():
            return candidate

    attempted = "\n  - ".join(str(path) for path in candidates)
    raise FileNotFoundError(
        "No lock_roots.json found. Set "
        f"{ROOTS_FILE_ENV} to the canonical file or create a local copy. "
        f"Checked:\n  - {attempted}"
    )


def load_scan_config() -> dict:
    """Load the configured roots through explicit or portable discovery."""
    return _lock_scan.load_config(resolve_roots_file())
