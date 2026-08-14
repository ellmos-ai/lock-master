"""
Generiert LOCK-CACHE.md aus der Watcher-Datenbank.

Zwei Cache-Formate:
  1. Standard-Cache via lock_scan.write_caches() (kompatibel mit bestehenden Konsumenten)
  2. Watcher-Detail-Cache (LOCK-CACHE.md im Watcher-Ordner, inkl. Team-Lock-Daten)

Aufruf (standalone):
  PYTHONIOENCODING=utf-8 python cache_writer.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Eigene Module
sys.path.insert(0, str(Path(__file__).resolve().parent))
import config
import storage

# lock_scan aus dem lock-master Repo-Root importieren
sys.path.insert(0, str(config.SCRIPTS_DIR))


def _format_remaining(expires_at: str | None) -> str:
    """Berechnet Restzeit aus absolutem expires_at."""
    if not expires_at:
        return "?"
    try:
        expiry = datetime.fromisoformat(expires_at)
        remaining = expiry - datetime.now()
        total_secs = int(remaining.total_seconds())
        if total_secs < 0:
            return "abgelaufen"
        h, rem = divmod(total_secs, 3600)
        m, _ = divmod(rem, 60)
        return f"{h}h{m:02d}m"
    except (ValueError, TypeError):
        return "?"


def _db_lock_to_scan_format(lock: dict) -> dict:
    """Konvertiert einen DB-Lock-Eintrag in das Format von lock_scan.collect_locks()."""
    return {
        "path": lock.get("path", ""),
        "scope": lock.get("scope", ""),
        "legacy": bool(lock.get("is_legacy", False)),
        "owner": lock.get("owner") or "",
        "created": (lock.get("created_at") or "")[:16],
        "created_source": lock.get("created_source") or "mtime",
        "expires_after": str(lock.get("expires_after") or ""),
        "remaining": _format_remaining(lock.get("expires_at")),
    }


def _load_daemon_status() -> dict | None:
    try:
        return json.loads(config.DAEMON_STATUS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _pid_is_running(pid: int) -> bool:
    """Laeuft die PID noch?

    Bewusst ueber ctypes/OpenProcess statt ueber einen `tasklist`-Subprozess:
    Unter pythonw.exe gibt es keine Standardausgabe, `subprocess.run(...).stdout`
    ist dann None, und der frueher hier stehende `.splitlines()`-Aufruf warf
    AttributeError. Folge war, dass sich der Daemon fensterlos nicht mehr
    starten liess, sobald noch ein frischer Heartbeat in daemon_status.json
    stand (beobachtet 2026-08-01). OpenProcess braucht keine Konsole, oeffnet
    kein Fenster und ist deutlich schneller.
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        try:
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
            )
        except Exception:  # noqa: BLE001 -- im Zweifel "laeuft nicht"
            return False
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _format_daemon_status(scanned_at: datetime) -> list[str]:
    status = _load_daemon_status()
    if not status:
        return ["Daemon: nicht bekannt"]

    pid = status.get("pid")
    last_seen_raw = status.get("last_seen")
    try:
        last_seen = datetime.fromisoformat(str(last_seen_raw))
        age = int((scanned_at - last_seen).total_seconds())
    except (TypeError, ValueError):
        age = None

    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        pid_int = 0

    fresh = age is not None and age <= max(180, config.FULL_SCAN_INTERVAL * 3)
    running = _pid_is_running(pid_int)
    state = "läuft" if fresh and running else "nicht aktuell"
    age_text = f"{age}s" if age is not None else "unbekannt"
    return [
        f"Daemon: {state}",
        f"Daemon-PID: {pid or '?'}",
        f"Daemon-Heartbeat: {last_seen_raw or '?'} (Alter: {age_text})",
    ]


def generate_cache(db: storage.LockDB) -> None:
    """Schreibt Standard-Caches via lock_scan.write_caches() + Watcher-Detail-Cache."""
    import lock_scan

    active_locks = db.get_active_locks()
    scan_format_locks = [_db_lock_to_scan_format(lock) for lock in active_locks]
    scanned_at = datetime.now()

    # Signatur-tolerant: write_caches() nimmt in dieser Modulfassung zusaetzlich
    # die Konfiguration entgegen (Cache-Ziele stehen im "caches"-Block der
    # lock_roots.json). Aeltere Fassungen haben die Ziele hartkodiert und
    # erwarten nur zwei Argumente. Ohne diese Weiche bricht der Cache-Schreibpfad
    # still, sobald die Code-Quelle wechselt.
    import inspect
    if "config" in inspect.signature(lock_scan.write_caches).parameters:
        written = lock_scan.write_caches(
            scan_format_locks, scanned_at, config.load_scan_config()
        )
    else:
        written = lock_scan.write_caches(scan_format_locks, scanned_at)
    for path, count in written:
        print(f"cache_writer: {path} ({count} aktive Locks geschrieben)")

    _write_detail_cache(active_locks, scanned_at)


def _write_detail_cache(locks: list[dict], scanned_at: datetime) -> None:
    """Schreibt einen Detail-Cache mit Team-Lock-Daten ins Watcher-Verzeichnis."""
    now_iso = scanned_at.isoformat(timespec="seconds")
    cache_path = config.WATCHER_DIR / "LOCK-CACHE.md"

    lines: list[str] = [
        "# LOCK-CACHE (Lock-File-Watcher, Detail-Ansicht)",
        "",
        f"Stand: {now_iso}",
        *_format_daemon_status(scanned_at),
        "",
    ]

    if not locks:
        lines.append("Keine aktiven Locks.")
    else:
        exclusive = [lock_item for lock_item in locks if lock_item.get("lock_type", "exclusive") != "team"]
        team = [lock_item for lock_item in locks if lock_item.get("lock_type") == "team"]

        if exclusive:
            lines.append(f"## Exclusive Locks ({len(exclusive)})")
            lines.append("")
            lines.append("| Pfad | Scope | Owner | Host | Restzeit |")
            lines.append("|---|---|---|---|---|")
            for lock in exclusive:
                remaining = _format_remaining(lock.get("expires_at"))
                lines.append(
                    f"| {lock.get('path', '')} "
                    f"| {lock.get('scope', '')} "
                    f"| {lock.get('owner') or '?'} "
                    f"| {lock.get('host') or '?'} "
                    f"| {remaining} |"
                )
            lines.append("")

        if team:
            lines.append(f"## Team Locks ({len(team)})")
            lines.append("")
            for lock in team:
                remaining = _format_remaining(lock.get("expires_at"))
                lines.append(
                    f"### {lock.get('filename', '?')} — {lock.get('scope', '?')}"
                )
                lines.append(f"- **Pfad:** {lock.get('path', '')}")
                lines.append(
                    f"- **Owner:** {lock.get('owner') or '?'} | "
                    f"**Host:** {lock.get('host') or '?'} | "
                    f"**Restzeit:** {remaining}"
                )
                _render_team_data(lock.get("team_data"), lines)
                lines.append("")

    lines.append("---")
    lines.append(f"*Generiert: {now_iso} | DB: {config.DB_PATH}*")
    lines.append("")

    cache_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"cache_writer: {cache_path} (Detail-Cache)")


def _render_team_data(team_data_raw: str | None, lines: list[str]) -> None:
    """Rendert team_data JSON als Markdown-Liste in lines."""
    if not team_data_raw:
        return
    try:
        td = json.loads(team_data_raw) if isinstance(team_data_raw, str) else team_data_raw
    except (json.JSONDecodeError, TypeError):
        return
    if not td:
        return

    section_labels = {
        "presence": "Anwesend",
        "file_claims": "Datei-Claims",
        "tool_claims": "Tool-Claims",
        "queue": "Warteschlange",
        "messages": "Nachrichten",
    }
    for key, label in section_labels.items():
        entries = td.get(key)
        if entries:
            lines.append(f"- **{label}:**")
            for entry in entries:
                lines.append(f"  - {entry}")


def main() -> int:
    db = storage.LockDB(config.DB_PATH)
    try:
        generate_cache(db)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
