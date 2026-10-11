"""Scanner für den Lock-File-Watcher. Importiert lock_utils.py und lock_scan.py."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

# config importieren stellt sicher, dass der Repo-Root im sys.path ist
import config  # noqa: F401 (Seiteneffekt: sys.path-Setup)

import lock_scan
import lock_utils


def lock_to_record(lock_path: Path, name: str, scope: str, is_legacy: bool) -> dict:
    """Konvertiert eine Lock-Datei in ein Dict für die Datenbank.

    Liest Rohdaten über lock_utils-APIs aus. is_expired wird separat übergeben
    damit der Aufrufer den Zeitpunkt kontrollieren kann.
    """
    data = lock_utils.parse_lock_file(lock_path)
    data = lock_utils.normalize_lock_fields(data)

    try:
        raw_content = lock_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        raw_content = None

    try:
        created, expires, created_source = lock_utils.lock_created_and_expiry(lock_path)
        created_at = (
            None if created_source == "invalid"
            else lock_utils.format_utc_iso(created)
        )
        expires_after = str(expires)
    except (OSError, ValueError):
        created_at = None
        expires_after = None
        created_source = None

    name_parts = lock_utils.lock_name_parts(name) or {}
    lock_type = str(name_parts.get("lock_type") or lock_utils.lock_type_from_name(name))
    scope = str(name_parts.get("scope") or scope)
    filename_host = name_parts.get("host")
    expires_at = lock_utils.compute_expires_at(lock_path)

    team_data = None
    if lock_type == "team" and raw_content:
        team_data = lock_utils.parse_team_lock_sections(raw_content)

    return {
        "path": str(lock_path),
        "filename": name,
        "project_dir": str(lock_path.parent),
        "scope": scope,
        "lock_type": lock_type,
        "is_legacy": is_legacy,
        "owner": data.get("owner"),
        "host": data.get("host") or filename_host,
        "purpose": data.get("purpose"),
        "mode": data.get("mode"),
        "created_at": created_at,
        "created_source": created_source,
        "expires_after": expires_after,
        "expires_at": expires_at,
        "team_data": team_data,
        "raw_content": raw_content,
    }


def full_scan(config_data: dict, now: datetime | None = None) -> list[dict]:
    """Traversiert alle konfigurierten Roots und liefert ALLE Lock-Dateien.

    Im Gegensatz zu lock_scan.collect_locks() werden hier auch abgelaufene
    Locks zurückgegeben. is_expired wird pro Lock separat geprüft.
    """
    now = datetime.now(timezone.utc) if now is None else now
    seen: set[Path] = set()
    results: list[dict] = []

    for project_dir in lock_scan.iter_lock_dirs(config_data):
        if project_dir in seen:
            continue
        seen.add(project_dir)

        # find_lock_files liefert alle Lock-Dateien unabhängig vom Ablaufstatus
        for name, scope, is_legacy in lock_utils.find_lock_files(project_dir):
            lock_path = project_dir / name

            record = lock_to_record(lock_path, name, scope, is_legacy)

            # is_expired separat prüfen (Legacy-Locks kennen kein Format → nie expired)
            if is_legacy:
                expired = False
            else:
                try:
                    expired = lock_utils.is_expired(lock_path, now)
                except (OSError, ValueError):
                    expired = False

            record["is_expired"] = expired
            results.append(record)

    return results


def check_paths(paths: list[str], now: datetime | None = None) -> dict[str, dict | None]:
    """Prüft ob gegebene Lock-Pfade noch existieren und parst sie neu.

    Gibt ein Dict zurück: path → lock_data (aktualisiert) oder None (gelöscht).
    """
    now = datetime.now(timezone.utc) if now is None else now
    result: dict[str, dict | None] = {}

    for path_str in paths:
        lock_path = Path(path_str)
        if not lock_path.exists() or not lock_path.is_file():
            result[path_str] = None
            continue

        name = lock_path.name
        scope = lock_utils.scope_from_name(name)
        is_legacy = name in lock_utils.LEGACY_LOCK_NAMES

        if scope is None and not is_legacy:
            # Datei ist kein gültiger Lock mehr (umbenannt?)
            result[path_str] = None
            continue

        if scope is None:
            scope = "project"

        record = lock_to_record(lock_path, name, scope, is_legacy)

        if is_legacy:
            expired = False
        else:
            try:
                expired = lock_utils.is_expired(lock_path, now)
            except (OSError, ValueError):
                expired = False

        record["is_expired"] = expired
        result[path_str] = record

    return result
