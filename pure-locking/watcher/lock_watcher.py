"""
Lock-File-Watcher: Daemon mit Dual-Scan-Rhythmus.

Aufruf:
  PYTHONIOENCODING=utf-8 python lock_watcher.py
  PYTHONIOENCODING=utf-8 python lock_watcher.py --once
  PYTHONIOENCODING=utf-8 python lock_watcher.py --update-cache

Ohne Argumente: Daemon-Modus (Endlosloop).
--once: genau ein Full-Scan, dann beenden.
--update-cache: LOCK-CACHE.md nach jedem Full-Scan aktualisieren.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

# Eigene Module (gleicher Ordner)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import config
import dir_stats
import rooms as rooms_mod
import scanner
import storage

# lock_scan aus dem lock-master Repo-Root für optionale Cache-Updates
sys.path.insert(0, str(config.SCRIPTS_DIR))

STATS_SCAN_INTERVAL: int = 900  # 15 Minuten
HEARTBEAT_INTERVAL: int = 5
STALE_DAEMON_SECONDS: int = max(180, config.FULL_SCAN_INTERVAL * 3)
SCAN_OVERRUN_WARN_SECONDS: int = config.FULL_SCAN_INTERVAL * 5


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _ensure_utf8_stdio_for_stream(stream) -> None:
    """Reconfigure a single stream to tolerant UTF-8, wenn moeglich."""
    if stream is not None and hasattr(stream, "reconfigure"):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def _ensure_utf8_stdio() -> None:
    """Haertet stdout/stderr gegen die System-Codepage des Aufrufers.

    Fensterlose Starter (z. B. der VBS-Wrapper des Scheduled Tasks) setzen
    kein PYTHONIOENCODING; unter Windows faellt Python dann auf die lokale
    Codepage zurueck (meist cp1252), die Zeichen wie "→" nicht kodieren kann.
    Bislang beendete ein einzelner solcher print() (_run_quick_check bei
    einem geloeschten/abgelaufenen Lock) den gesamten Daemon mit
    UnicodeEncodeError — beobachtet ASUS-GEI 2026-08-02, ca. alle 10-40 Min.
    reconfigure() macht die Streams robust, unabhaengig davon, ob der
    Aufrufer die Umgebungsvariable setzt.
    """
    for stream in (sys.stdout, sys.stderr):
        _ensure_utf8_stdio_for_stream(stream)


def load_daemon_status() -> dict | None:
    """Lädt den letzten Daemon-Heartbeat."""
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


def _status_age_seconds(status: dict, now: datetime | None = None) -> int | None:
    last_seen = status.get("last_seen")
    if not last_seen:
        return None
    try:
        seen = datetime.fromisoformat(str(last_seen))
    except ValueError:
        return None
    now = now or datetime.now()
    return int((now - seen).total_seconds())


def _daemon_status_is_fresh(status: dict | None) -> bool:
    if not status:
        return False
    if status.get("host") != socket.gethostname():
        return False
    try:
        pid = int(status.get("pid", 0))
    except (TypeError, ValueError):
        return False
    age = _status_age_seconds(status)
    if age is None or age > STALE_DAEMON_SECONDS:
        return False
    return _pid_is_running(pid)


def get_running_daemon_status() -> dict | None:
    status = load_daemon_status()
    return status if _daemon_status_is_fresh(status) else None


def _write_daemon_status(
    update_cache: bool,
    started_at: str,
    scan_info: dict | None = None,
) -> None:
    status = {
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "started_at": started_at,
        "last_seen": _now_iso(),
        "update_cache": update_cache,
        "db_path": str(config.DB_PATH),
    }
    if scan_info:
        status.update(scan_info)
    tmp_path = config.DAEMON_STATUS_PATH.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(config.DAEMON_STATUS_PATH)


def _clear_daemon_status(pid: int) -> None:
    status = load_daemon_status()
    if not status:
        return
    try:
        current_pid = int(status.get("pid", 0))
    except (TypeError, ValueError):
        current_pid = 0
    if current_pid == pid:
        try:
            config.DAEMON_STATUS_PATH.unlink()
        except OSError:
            pass


def _run_full_scan(db: storage.LockDB, cfg: dict, update_cache: bool) -> dict:
    """Führt einen Full-Scan durch, reconciled Ergebnisse mit DB.
    Gibt Stats-Dict zurück: {new, modified, expired, deleted, total}."""
    started = _now_iso()
    scan_results = scanner.full_scan(cfg)
    scanned_paths = {r["path"] for r in scan_results}

    stats = {"new": 0, "modified": 0, "expired": 0, "deleted": 0, "total": len(scan_results)}

    for lock_data in scan_results:
        path = lock_data["path"]
        existing = db.get_lock_by_path(path)

        if existing is None:
            lock_id = db.upsert_lock(lock_data)
            db.record_event(lock_id, "detected")
            stats["new"] += 1
        elif existing.get("status") == "deleted":
            lock_data["status"] = "active"
            lock_id = db.upsert_lock(lock_data)
            db.record_event(lock_id, "renewed")
            stats["new"] += 1
        elif existing.get("status") == "expired" and not lock_data.get("is_expired"):
            lock_data["status"] = "active"
            lock_id = db.upsert_lock(lock_data)
            db.record_event(lock_id, "renewed")
            stats["new"] += 1
        else:
            lock_id = existing["id"]
            changed = (
                existing.get("owner") != lock_data.get("owner")
                or existing.get("purpose") != lock_data.get("purpose")
                or existing.get("raw_content") != lock_data.get("raw_content")
            )
            if changed:
                db.upsert_lock(lock_data)
                db.record_event(lock_id, "modified")
                stats["modified"] += 1
            else:
                db.upsert_lock(lock_data)

            if lock_data.get("is_expired") and existing.get("status") == "active":
                db.mark_expired(lock_id, _now_iso())
                stats["expired"] += 1

    # Aktive DB-Locks die im Scan nicht auftauchen → gelöscht
    known_active = db.get_known_active_paths()
    for known_path in known_active:
        if known_path not in scanned_paths:
            lock_entry = db.get_lock_by_path(known_path)
            if lock_entry and lock_entry.get("status") == "active":
                # Full-scans may miss a subtree temporarily (cloud-sync/FS errors).
                # Verify the specific file before turning an active lock into deleted.
                check_result = scanner.check_paths([known_path]).get(known_path)
                if check_result is not None:
                    lock_id = db.upsert_lock(check_result)
                    if check_result.get("is_expired"):
                        db.mark_expired(lock_id, _now_iso())
                        stats["expired"] += 1
                    continue
                db.mark_deleted(lock_entry["id"], _now_iso())
                stats["deleted"] += 1

    finished = _now_iso()
    db.record_scan("full", started, finished, stats)

    if update_cache:
        try:
            import cache_writer
            cache_writer.generate_cache(db)
        except Exception as exc:
            print(f"[{_now_iso()}] Cache-Update fehlgeschlagen: {exc}", file=sys.stderr)

    return stats


def _run_quick_check(db: storage.LockDB) -> None:
    """Prüft nur bekannte aktive Locks auf Änderungen/Löschungen."""
    known_active = db.get_known_active_paths()
    if not known_active:
        return

    results = scanner.check_paths(known_active)
    changed = False

    for path, data in results.items():
        lock_entry = db.get_lock_by_path(path)
        if lock_entry is None:
            continue
        lock_id = lock_entry["id"]

        if data is None:
            if lock_entry.get("status") == "active":
                db.mark_deleted(lock_id, _now_iso())
                print(f"[{_now_iso()}] Quick-Check: gelöscht → {path}")
                changed = True
        elif data.get("is_expired") and lock_entry.get("status") == "active":
            db.mark_expired(lock_id, _now_iso())
            print(f"[{_now_iso()}] Quick-Check: abgelaufen → {path}")
            changed = True

    _ = changed  # Logging erfolgt inline oben


def _run_stats_scan(db: storage.LockDB) -> None:
    """Scannt Verzeichnis-Statistiken für alle Räume."""
    all_rooms = rooms_mod._get_rooms()
    cfg = config.load_scan_config()
    skipped = set(cfg.get("skip_dirs", []))
    stats_map = dir_stats.scan_all_rooms(all_rooms, skipped)
    db.upsert_room_stats_batch(stats_map)


def _heartbeat_loop(
    stop_event: threading.Event,
    update_cache: bool,
    started_at: str,
    scan_state: dict,
    state_lock: threading.Lock,
    interval: float = HEARTBEAT_INTERVAL,
) -> None:
    """Advance daemon health independently from potentially blocking scans."""
    while not stop_event.is_set():
        with state_lock:
            info = dict(scan_state)
        _write_daemon_status(update_cache, started_at, scan_info=info)
        stop_event.wait(interval)


def run_daemon(update_cache: bool) -> None:
    """Endlosloop mit Dual-Scan-Rhythmus."""
    existing = get_running_daemon_status()
    if existing is not None:
        print(
            f"[{_now_iso()}] Lock-Watcher läuft bereits "
            f"(PID {existing.get('pid')}, Host {existing.get('host')}, "
            f"letzter Heartbeat {existing.get('last_seen')})."
        )
        return

    started_at = _now_iso()
    cfg = config.load_scan_config()
    db = storage.LockDB(config.DB_PATH)

    shutdown_requested = False
    stop_heartbeat = threading.Event()
    state_lock = threading.Lock()
    scan_state: dict = {
        "scan_in_progress": False,
        "scan_started_at": None,
        "last_scan_finished_at": None,
        "last_scan_duration_s": None,
    }

    def _handle_signal(signum, frame):
        nonlocal shutdown_requested
        shutdown_requested = True

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    _write_daemon_status(update_cache, started_at, scan_info=scan_state)
    heartbeat_thread = threading.Thread(
        target=_heartbeat_loop,
        args=(stop_heartbeat, update_cache, started_at, scan_state, state_lock),
        name="lock-watcher-heartbeat",
        daemon=True,
    )
    heartbeat_thread.start()

    print(
        f"[{_now_iso()}] Lock-Watcher gestartet. "
        f"Full-Scan alle {config.FULL_SCAN_INTERVAL}s, "
        f"Quick-Check alle {config.CHECK_INTERVAL}s."
    )

    last_full_scan: float = 0.0
    last_check: float = 0.0
    last_stats_scan: float = 0.0
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="lock-watcher-scan")
    pending_scan: Future | None = None
    pending_scan_started_mono: float = 0.0
    last_overrun_warning: float = 0.0

    def _submit_full_scan() -> Future:
        nonlocal pending_scan_started_mono
        with state_lock:
            scan_state["scan_in_progress"] = True
            scan_state["scan_started_at"] = _now_iso()
        pending_scan_started_mono = time.monotonic()
        return executor.submit(_run_full_scan, db, cfg, update_cache)

    try:
        while not shutdown_requested:
            now = time.monotonic()

            if pending_scan is not None and pending_scan.done():
                with state_lock:
                    scan_state["scan_in_progress"] = False
                    scan_state["last_scan_finished_at"] = _now_iso()
                    scan_state["last_scan_duration_s"] = round(
                        time.monotonic() - pending_scan_started_mono, 1
                    )
                try:
                    stats = pending_scan.result()
                    print(
                        f"[{_now_iso()}] Full-Scan: {stats['total']} aktiv, "
                        f"{stats['new']} neu, {stats['modified']} geändert, "
                        f"{stats['expired']} abgelaufen, {stats['deleted']} gelöscht"
                    )
                except Exception as exc:
                    print(
                        f"[{_now_iso()}] Full-Scan fehlgeschlagen: {exc}",
                        file=sys.stderr,
                    )
                pending_scan = None
                last_full_scan = time.monotonic()
                last_check = time.monotonic()

            elif pending_scan is None and now - last_full_scan >= config.FULL_SCAN_INTERVAL:
                pending_scan = _submit_full_scan()

            elif pending_scan is None and now - last_check >= config.CHECK_INTERVAL:
                try:
                    _run_quick_check(db)
                except Exception as exc:
                    print(
                        f"[{_now_iso()}] Quick-Check fehlgeschlagen: {exc}",
                        file=sys.stderr,
                    )
                last_check = time.monotonic()

            if pending_scan is not None:
                running_for = time.monotonic() - pending_scan_started_mono
                if (
                    running_for >= SCAN_OVERRUN_WARN_SECONDS
                    and now - last_overrun_warning >= SCAN_OVERRUN_WARN_SECONDS
                ):
                    print(
                        f"[{_now_iso()}] WARNING: full scan has run for "
                        f"{running_for:.0f}s; heartbeat remains independent.",
                        file=sys.stderr,
                    )
                    last_overrun_warning = now

            now2 = time.monotonic()
            if pending_scan is None and now2 - last_stats_scan >= STATS_SCAN_INTERVAL:
                try:
                    _run_stats_scan(db)
                    last_stats_scan = time.monotonic()
                    print(f"[{_now_iso()}] Stats-Scan abgeschlossen.")
                except Exception as exc:
                    print(
                        f"[{_now_iso()}] Stats-Scan fehlgeschlagen: {exc}",
                        file=sys.stderr,
                    )

            time.sleep(1)

    finally:
        stop_heartbeat.set()
        heartbeat_thread.join(timeout=2)
        executor.shutdown(wait=False, cancel_futures=True)
        db.close()
        _clear_daemon_status(os.getpid())
        print(f"[{_now_iso()}] Lock-Watcher beendet.")


def run_once(update_cache: bool) -> None:
    """Genau ein Full-Scan, dann beenden."""
    cfg = config.load_scan_config()
    db = storage.LockDB(config.DB_PATH)
    try:
        print(f"[{_now_iso()}] Lock-Watcher --once: starte Full-Scan.")
        stats = _run_full_scan(db, cfg, update_cache)
        print(
            f"[{_now_iso()}] Full-Scan abgeschlossen: {stats['total']} aktiv, "
            f"{stats['new']} neu, {stats['modified']} geändert, "
            f"{stats['expired']} abgelaufen, {stats['deleted']} gelöscht"
        )
    finally:
        db.close()


def main() -> int:
    _ensure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Lock-File-Watcher Daemon")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Genau einen Full-Scan ausführen und beenden.",
    )
    parser.add_argument(
        "--update-cache",
        action="store_true",
        help="LOCK-CACHE.md nach jedem Full-Scan aktualisieren.",
    )
    args = parser.parse_args()

    if args.once:
        run_once(args.update_cache)
    else:
        run_daemon(args.update_cache)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
