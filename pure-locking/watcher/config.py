"""Konfiguration für den lock-master Watcher."""

from __future__ import annotations

import json
import os
import re
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
RUNTIME_SETTINGS_PATH: Path = _LOCAL_DATA_DIR / "runtime_settings.json"


def _dir_from_env(name: str, default: Path) -> Path:
    """Directory named by an environment variable, else ``default`` (blank counts as unset)."""
    raw = os.environ.get(name, "").strip()
    return Path(os.path.expandvars(raw)).expanduser() if raw else default


# Ziel der Cache-Dateien, die der Watcher selbst schreibt (Detail-Cache LOCK-CACHE.<host>.md).
# Default bleibt das Watcher-Verzeichnis (abwaertskompatibel). Wird der Watcher aus einem Git-Klon
# gestartet, zeigt LOCK_MASTER_WATCHER_CACHE_DIR auf einen Ordner ausserhalb des Klons.
# Die Standard-Caches aus lock_scan.write_caches() haben eigene Ziele im "caches"-Block der roots-Datei.
CACHE_DIR_ENV = "LOCK_MASTER_WATCHER_CACHE_DIR"
CACHE_DIR: Path = _dir_from_env(CACHE_DIR_ENV, WATCHER_DIR)

# Host-Overlay: Raumnamen, -farben, Icons und Workspace-Root eines Hosts. Liegt ausserhalb des Repos
# (Default <Datenverzeichnis>/overlay), damit das oeffentliche Repo keine privaten Projektnamen
# kennt. Beispiel: pure-locking/watcher/rooms.overlay.example.json.
OVERLAY_DIR_ENV = "LOCK_MASTER_WATCHER_OVERLAY"
OVERLAY_DIR: Path = _dir_from_env(OVERLAY_DIR_ENV, _LOCAL_DATA_DIR / "overlay")
OVERLAY_FILE: Path = OVERLAY_DIR / "rooms.overlay.json"
OVERLAY_ICONS_DIR: Path = OVERLAY_DIR / "icons"

_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{3,8}$")


def _norm_rel(key: object) -> str:
    return str(key).replace("\\", "/").strip().strip("/")


def load_rooms_overlay() -> dict:
    """Read the host overlay; a missing or unusable file means "no overlay", never a failed start.

    Returns ``{"workspace_root": Path | None, "labels": {relpath: str}, "colors": {relpath: "#rrggbb"}}``.
    Keys are paths relative to the workspace root, with forward slashes. Values are validated
    because they end up in the UI.
    """
    result: dict = {"workspace_root": None, "labels": {}, "colors": {}}
    try:
        raw = json.loads(OVERLAY_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return result
    if not isinstance(raw, dict):
        return result
    root = raw.get("workspace_root")
    if isinstance(root, str) and root.strip():
        result["workspace_root"] = Path(os.path.expandvars(root.strip())).expanduser()
    labels, colors = raw.get("labels"), raw.get("colors")
    if isinstance(labels, dict):
        for key, value in labels.items():
            if isinstance(value, str) and value.strip():
                result["labels"][_norm_rel(key)] = value.strip()[:80]
    if isinstance(colors, dict):
        for key, value in colors.items():
            if isinstance(value, str) and _COLOR_RE.match(value.strip()):
                result["colors"][_norm_rel(key)] = value.strip()
    return result

# Intervalle in Sekunden
FULL_SCAN_INTERVAL: int = 60
CHECK_INTERVAL: int = 20
HEARTBEAT_INTERVAL: int = 5
# Pause nach einem Full-Scan: mindestens FULL_SCAN_INTERVAL, sonst das Vielfache der
# gemessenen Scandauer. Ohne diese Bremse lief der Daemon pausenlos, weil ein Scan
# über viele Roots 55-617 s dauert (LANGLAUF-PROTOKOLL) und das Intervall 60 s ist:
# ASUS-GEI 2026-10-17 dauerhaft ~45 % eines Kerns und ~3.900 stat/s. Faktor 4 hält
# den Scan-Anteil unter 20 %. Übersteuerbar per LOCK_MASTER_FULL_SCAN_IDLE_FACTOR.
DEFAULT_FULL_SCAN_IDLE_FACTOR: float = 4.0


def _idle_factor_from_env(raw: str | None) -> float:
    """Parse the override; anything unusable falls back to the default instead of failing the start."""
    if raw is None or not raw.strip():
        return DEFAULT_FULL_SCAN_IDLE_FACTOR
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_FULL_SCAN_IDLE_FACTOR
    if value != value or value in (float("inf"), float("-inf")):  # NaN or infinite
        return DEFAULT_FULL_SCAN_IDLE_FACTOR
    return max(0.0, value)


FULL_SCAN_IDLE_FACTOR: float = _idle_factor_from_env(os.environ.get("LOCK_MASTER_FULL_SCAN_IDLE_FACTOR"))
STALE_DAEMON_SECONDS: int = max(180, FULL_SCAN_INTERVAL * 3)
# Schutz gegen ausufernde Scans (Messung 2026-11-15 auf einem Host: ein Full-Scan dauerte 5608 s
# statt ~60 s, mit Idle-Faktor 4 begann der naechste erst nach ~6 h):
# - Zeitbudget je Root: ueberschreitet ein Root es, wird er abgebrochen, der Scan gilt als
#   unvollstaendig und der Daemon meldet das sichtbar (scan_warnings, Zustand "degraded").
# - Obergrenze fuer die Pause nach einem Scan, damit eine einzelne langsame Messung die
#   Aktualitaet der Lock-Daten nicht auf Stunden streckt.
SCAN_ROOT_BUDGET_SECONDS: int = 300
FULL_SCAN_MAX_GAP_SECONDS: int = 1800

# Laufzeit-konfigurierbare Heartbeat- und Scan-Optionen (T-20260913-477382785)
RUNTIME_SETTINGS_BOUNDS: dict[str, tuple[type, float, float]] = {
    "heartbeat_interval": (int, 2, 60),
    "check_interval": (int, 5, 300),
    "full_scan_interval": (int, 30, 900),
    "full_scan_idle_factor": (float, 0.0, 10.0),
    "stale_daemon_seconds": (int, 30, 1800),
    "scan_root_budget_seconds": (int, 10, 3600),
    "full_scan_max_gap_seconds": (int, 60, 86400),
}


def default_runtime_settings() -> dict[str, int | float]:
    """Standardwerte für Heartbeat- und Scan-Intervalle."""
    return {
        "heartbeat_interval": HEARTBEAT_INTERVAL,
        "check_interval": CHECK_INTERVAL,
        "full_scan_interval": FULL_SCAN_INTERVAL,
        "full_scan_idle_factor": FULL_SCAN_IDLE_FACTOR,
        "stale_daemon_seconds": STALE_DAEMON_SECONDS,
        "scan_root_budget_seconds": SCAN_ROOT_BUDGET_SECONDS,
        "full_scan_max_gap_seconds": FULL_SCAN_MAX_GAP_SECONDS,
    }


def _coerce_setting(key: str, raw_val: object, default_val: int | float) -> int | float:
    spec = RUNTIME_SETTINGS_BOUNDS.get(key)
    if spec is None or isinstance(raw_val, bool):
        return default_val
    kind, low, high = spec
    try:
        num = float(raw_val)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default_val
    if num != num or num in (float("inf"), float("-inf")):
        return default_val
    clamped = max(low, min(high, num))
    return int(round(clamped)) if kind is int else round(float(clamped), 2)


def load_runtime_settings() -> dict[str, int | float]:
    """Liest Laufzeit-Einstellungen aus runtime_settings.json (mit Fallback auf Standardwerte)."""
    defaults = default_runtime_settings()
    if not RUNTIME_SETTINGS_PATH.is_file():
        return defaults
    try:
        raw = json.loads(RUNTIME_SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return defaults
    if not isinstance(raw, dict):
        return defaults
    result = dict(defaults)
    for key, default_val in defaults.items():
        if key in raw:
            result[key] = _coerce_setting(key, raw[key], default_val)
    return result


def save_runtime_settings(updates: dict[str, object]) -> dict[str, int | float]:
    """Speichert validierte Laufzeit-Einstellungen in runtime_settings.json.

    Wird ``{"reset": True}`` übergeben, werden die Standardwerte wiederhergestellt.
    """
    if isinstance(updates, dict) and updates.get("reset") is True:
        try:
            RUNTIME_SETTINGS_PATH.unlink()
        except OSError:
            pass
        return default_runtime_settings()
    current = load_runtime_settings()
    defaults = default_runtime_settings()
    if isinstance(updates, dict):
        for key in defaults:
            if key in updates:
                current[key] = _coerce_setting(key, updates[key], defaults[key])
    _LOCAL_DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = RUNTIME_SETTINGS_PATH.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(RUNTIME_SETTINGS_PATH)
    return current

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
