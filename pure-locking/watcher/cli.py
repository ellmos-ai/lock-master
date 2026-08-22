"""
CLI für den Lock-File-Watcher — für LLMs und Menschen.

Aufruf:
  python cli.py status [--json]
  python cli.py history [--path PFAD] [--type TYPE] [--limit N]
  python cli.py scan [--full] [--update-cache]
  python cli.py stats [--json]
  python cli.py cache
  python cli.py watch [--update-cache]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config
from lock_watcher import _ensure_utf8_stdio
import storage


def _now() -> datetime:
    return datetime.now()


def _calc_remaining(expires_at: str | None) -> str:
    """Berechnet verbleibende Zeit aus absolutem expires_at."""
    if not expires_at:
        return "?"
    try:
        expiry = datetime.fromisoformat(expires_at)
        remaining = expiry - _now()
        total_secs = int(remaining.total_seconds())
        if total_secs < 0:
            return "abgelaufen"
        h_rem, rem = divmod(total_secs, 3600)
        m_rem, _ = divmod(rem, 60)
        return f"{h_rem}h{m_rem:02d}m"
    except (ValueError, TypeError):
        return "?"


def _format_table_row(cols: list[str], widths: list[int]) -> str:
    parts = []
    for i, (col, w) in enumerate(zip(cols, widths)):
        parts.append(col[:w].ljust(w))
    return "  ".join(parts)


def cmd_status(args: argparse.Namespace) -> int:
    db = storage.LockDB(config.DB_PATH)
    try:
        locks = db.get_active_locks()
    finally:
        db.close()

    if args.json:
        print(json.dumps(locks, ensure_ascii=False, indent=2))
        return 0

    if not locks:
        print("Keine aktiven Locks.")
        return 0

    headers = ["Pfad", "Typ", "Scope", "Owner", "Host", "Restzeit"]
    widths = [45, 9, 12, 16, 14, 10]
    print(_format_table_row(headers, widths))
    print("  ".join("-" * w for w in widths))

    for lock in locks:
        remaining = _calc_remaining(lock.get("expires_at"))
        lock_type = lock.get("lock_type", "exclusive")
        row = [
            lock.get("path", ""),
            lock_type,
            lock.get("scope", ""),
            lock.get("owner") or "?",
            lock.get("host") or "?",
            remaining,
        ]
        print(_format_table_row(row, widths))

    return 0


def cmd_history(args: argparse.Namespace) -> int:
    db = storage.LockDB(config.DB_PATH)
    try:
        history = db.get_lock_history(path=args.path, limit=args.limit)
    finally:
        db.close()

    if not history:
        print("Keine Ereignisse gefunden.")
        return 0

    # Optionaler Filter nach Event-Typ
    if args.type:
        history = [e for e in history if e.get("event_type") == args.type]

    headers = ["Zeitpunkt", "Typ", "Pfad", "Details"]
    widths = [19, 10, 45, 30]
    print(_format_table_row(headers, widths))
    print("  ".join("-" * w for w in widths))

    for event in history:
        row = [
            (event.get("timestamp") or "")[:19],
            event.get("event_type") or "",
            event.get("path") or "",
            event.get("details") or "",
        ]
        print(_format_table_row(row, widths))

    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    """Führt einen sofortigen Full-Scan durch."""
    cfg = config.load_scan_config()
    db = storage.LockDB(config.DB_PATH)
    try:
        import lock_watcher as _lw

        stats = _lw._run_full_scan(db, cfg, args.update_cache)
        locks = db.get_active_locks()
        print(
            f"Scan abgeschlossen: {stats['total']} Lock-Dateien gefunden, "
            f"{len(locks)} aktiv."
        )

        for lock in locks:
            legacy = " [LEGACY]" if lock.get("is_legacy") else ""
            owner = lock.get("owner") or "?"
            print(f"  {lock['path']}{legacy}")
            print(f"      scope={lock['scope']}  owner={owner}  erstellt={lock.get('created_at', '?')[:19]}")

        if args.update_cache:
            print("LOCK-CACHE.md aktualisiert.")
    finally:
        db.close()

    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    db = storage.LockDB(config.DB_PATH)
    try:
        stats = db.get_stats()
    finally:
        db.close()

    if args.json:
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        return 0

    print(f"Aktive Locks:    {stats.get('active', 0)}")
    print(f"Abgelaufene:     {stats.get('expired', 0)}")
    print(f"Gelöschte:       {stats.get('deleted', 0)}")
    print(f"Letzter Scan:    {stats.get('last_scan_at') or '(noch keiner)'}")
    print(f"Events gesamt:   {stats.get('total_events', 0)}")
    return 0


def cmd_cache(args: argparse.Namespace) -> int:
    db = storage.LockDB(config.DB_PATH)
    try:
        import cache_writer
        cache_writer.generate_cache(db)
        print("LOCK-CACHE.md wurde regeneriert.")
    finally:
        db.close()
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    """Startet lock_watcher.py im Daemon-Modus."""
    watcher = Path(__file__).resolve().parent / "lock_watcher.py"
    cmd = [sys.executable, str(watcher)]
    if args.update_cache:
        cmd.append("--update-cache")
    print(f"Starte Daemon: {' '.join(cmd)}")
    result = subprocess.run(cmd)
    return result.returncode


def main() -> int:
    _ensure_utf8_stdio()
    parser = argparse.ArgumentParser(
        description="Lock-File-Watcher CLI — Status, History, Scan, Cache."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # status
    p_status = subparsers.add_parser("status", help="Aktive Locks anzeigen")
    p_status.add_argument("--json", action="store_true", help="JSON-Ausgabe")
    p_status.set_defaults(func=cmd_status)

    # history
    p_history = subparsers.add_parser("history", help="Ereignisverlauf anzeigen")
    p_history.add_argument("--path", default=None, help="Filter nach Pfad")
    p_history.add_argument("--type", default=None,
                           help="Filter nach Event-Typ (detected/expired/deleted/modified/renewed)")
    p_history.add_argument("--limit", type=int, default=50, help="Max. Einträge (default: 50)")
    p_history.set_defaults(func=cmd_history)

    # scan
    p_scan = subparsers.add_parser("scan", help="Sofort-Scan auslösen")
    p_scan.add_argument("--full", action="store_true", help="Full-Scan (default)")
    p_scan.add_argument("--update-cache", action="store_true", help="LOCK-CACHE.md aktualisieren")
    p_scan.set_defaults(func=cmd_scan)

    # stats
    p_stats = subparsers.add_parser("stats", help="Zusammenfassung anzeigen")
    p_stats.add_argument("--json", action="store_true", help="JSON-Ausgabe")
    p_stats.set_defaults(func=cmd_stats)

    # cache
    p_cache = subparsers.add_parser("cache", help="LOCK-CACHE.md regenerieren")
    p_cache.set_defaults(func=cmd_cache)

    # watch
    p_watch = subparsers.add_parser("watch", help="Daemon starten")
    p_watch.add_argument("--update-cache", action="store_true", help="Cache nach jedem Scan aktualisieren")
    p_watch.set_defaults(func=cmd_watch)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
