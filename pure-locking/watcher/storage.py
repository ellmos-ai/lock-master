"""SQLite-Storage-Layer für den Lock-File-Watcher."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path


class LockDB:
    """Persistenz-Layer für erkannte Lock-Dateien, Events und Scan-Metadaten."""

    def __init__(self, db_path: Path) -> None:
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA encoding='UTF-8'")
        self.init_db()
        self._migrate()

    def init_db(self) -> None:
        """Legt die drei Tabellen an, falls sie noch nicht existieren."""
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS locks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL UNIQUE,
                filename TEXT NOT NULL,
                project_dir TEXT NOT NULL,
                scope TEXT,
                lock_type TEXT DEFAULT 'exclusive' CHECK(lock_type IN ('exclusive', 'team', 'user', 'condition', 'until', 'legacy')),
                is_legacy INTEGER DEFAULT 0,
                status TEXT DEFAULT 'active' CHECK(status IN ('active', 'expired', 'deleted')),
                owner TEXT,
                host TEXT,
                purpose TEXT,
                mode TEXT,
                created_at TEXT,
                created_source TEXT,
                expires_after TEXT,
                expires_at TEXT,
                team_data TEXT,
                raw_content TEXT,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                removed_at TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lock_id INTEGER NOT NULL,
                event_type TEXT NOT NULL CHECK(event_type IN ('detected', 'expired', 'deleted', 'modified', 'renewed')),
                timestamp TEXT NOT NULL,
                details TEXT,
                FOREIGN KEY (lock_id) REFERENCES locks(id)
            );

            CREATE TABLE IF NOT EXISTS scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_type TEXT NOT NULL CHECK(scan_type IN ('full', 'check')),
                started_at TEXT NOT NULL,
                finished_at TEXT,
                locks_found INTEGER DEFAULT 0,
                locks_new INTEGER DEFAULT 0,
                locks_expired INTEGER DEFAULT 0,
                locks_deleted INTEGER DEFAULT 0,
                roots_scanned INTEGER DEFAULT 0,
                duration_ms INTEGER
            );

            CREATE TABLE IF NOT EXISTS room_stats (
                room_key TEXT PRIMARY KEY,
                file_count INTEGER DEFAULT 0,
                folder_count INTEGER DEFAULT 0,
                total_bytes INTEGER DEFAULT 0,
                type_code INTEGER DEFAULT 0,
                type_human INTEGER DEFAULT 0,
                type_llm INTEGER DEFAULT 0,
                type_media INTEGER DEFAULT 0,
                type_other INTEGER DEFAULT 0,
                scanned_at TEXT NOT NULL
            );
        """)
        self._conn.commit()

    def _migrate(self) -> None:
        """Ergänzt Spalten die in älteren DB-Versionen fehlen."""
        self._rebuild_locks_if_check_outdated()
        existing = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(locks)").fetchall()
        }
        migrations = [
            ("lock_type", "TEXT DEFAULT 'exclusive'"),
            ("expires_at", "TEXT"),
            ("team_data", "TEXT"),
            ("display_name", "TEXT"),
        ]
        for col, typedef in migrations:
            if col not in existing:
                self._conn.execute(f"ALTER TABLE locks ADD COLUMN {col} {typedef}")
        self._conn.commit()

    def _rebuild_locks_if_check_outdated(self) -> None:
        """Baut die locks-Tabelle neu, wenn der lock_type-CHECK veraltet ist.

        Aeltere DBs (bis v1.4.0) kennen nur exclusive/team/legacy — User- und
        Condition-Locks liefen dort in einen IntegrityError. SQLite kann
        CHECK-Constraints nicht per ALTER aendern, daher Rename + Copy.
        """
        row = self._conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='locks'"
        ).fetchone()
        if row is None or ("'condition'" in (row[0] or "") and "'until'" in (row[0] or "")):
            return

        cols = [
            r[1] for r in self._conn.execute("PRAGMA table_info(locks)").fetchall()
        ]
        # legacy_alter_table: RENAME soll die FK-Referenz in events NICHT auf
        # locks_old umschreiben.
        self._conn.execute("PRAGMA legacy_alter_table=ON")
        self._conn.execute("ALTER TABLE locks RENAME TO locks_old")
        self._conn.execute("PRAGMA legacy_alter_table=OFF")
        self.init_db()
        new_cols = {
            r[1] for r in self._conn.execute("PRAGMA table_info(locks)").fetchall()
        }
        for col in cols:
            if col not in new_cols:
                self._conn.execute(f"ALTER TABLE locks ADD COLUMN {col} TEXT")
        col_list = ", ".join(cols)
        self._conn.execute(
            f"INSERT INTO locks ({col_list}) SELECT {col_list} FROM locks_old"
        )
        self._conn.execute("DROP TABLE locks_old")
        self._conn.commit()

    def upsert_lock(self, lock_data: dict) -> int:
        """Insert oder Update eines Lock-Eintrags. Gibt die lock_id zurück.

        Status wird nur gesetzt wenn 'status' in lock_data enthalten ist;
        sonst bleibt bei Updates der bestehende Status erhalten (Insert
        default: 'active'). Events werden NICHT automatisch geloggt.
        """
        now = datetime.now().isoformat(timespec="seconds")
        path = lock_data["path"]

        team_data_raw = lock_data.get("team_data")
        team_data_json = (
            json.dumps(team_data_raw, ensure_ascii=False)
            if team_data_raw is not None
            else None
        )

        existing = self.get_lock_by_path(path)
        if existing is None:
            status = lock_data.get("status", "active")
            try:
                cur = self._conn.execute(
                    """INSERT INTO locks
                       (path, filename, project_dir, scope, lock_type, is_legacy,
                        status, owner, host, purpose, mode, created_at,
                        created_source, expires_after, expires_at, team_data,
                        raw_content, first_seen, last_seen, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        path,
                        lock_data.get("filename", ""),
                        lock_data.get("project_dir", ""),
                        lock_data.get("scope"),
                        lock_data.get("lock_type", "exclusive"),
                        1 if lock_data.get("is_legacy") else 0,
                        status,
                        lock_data.get("owner"),
                        lock_data.get("host"),
                        lock_data.get("purpose"),
                        lock_data.get("mode"),
                        lock_data.get("created_at"),
                        lock_data.get("created_source"),
                        lock_data.get("expires_after"),
                        lock_data.get("expires_at"),
                        team_data_json,
                        lock_data.get("raw_content"),
                        now,
                        now,
                        now,
                    ),
                )
                self._conn.commit()
                return cur.lastrowid
            except sqlite3.IntegrityError:
                self._conn.rollback()
                existing = self.get_lock_by_path(path)
                if existing is None:
                    raise

        lock_id = existing["id"]
        new_status = lock_data.get("status", existing["status"])

        self._conn.execute(
            """UPDATE locks SET
               filename=?, project_dir=?, scope=?, lock_type=?, is_legacy=?,
               status=?, owner=?, host=?, purpose=?, mode=?,
               created_at=?, created_source=?, expires_after=?, expires_at=?,
               team_data=?, raw_content=?, last_seen=?, updated_at=?
               WHERE id=?""",
            (
                lock_data.get("filename", existing["filename"]),
                lock_data.get("project_dir", existing["project_dir"]),
                lock_data.get("scope", existing["scope"]),
                lock_data.get("lock_type", existing.get("lock_type", "exclusive")),
                1 if lock_data.get("is_legacy") else 0,
                new_status,
                lock_data.get("owner"),
                lock_data.get("host"),
                lock_data.get("purpose"),
                lock_data.get("mode"),
                lock_data.get("created_at"),
                lock_data.get("created_source"),
                lock_data.get("expires_after"),
                lock_data.get("expires_at"),
                team_data_json,
                lock_data.get("raw_content"),
                now,
                now,
                lock_id,
            ),
        )
        self._conn.commit()
        return lock_id

    def record_event(self, lock_id: int, event_type: str, details: str | None = None) -> None:
        """Schreibt einen Event-Eintrag für eine Lock-Datei."""
        now = datetime.now().isoformat(timespec="seconds")
        self._conn.execute(
            "INSERT INTO events (lock_id, event_type, timestamp, details) VALUES (?,?,?,?)",
            (lock_id, event_type, now, details),
        )
        self._conn.commit()

    def record_scan(
        self,
        scan_type: str,
        started_at: str,
        finished_at: str,
        stats: dict,
    ) -> int:
        """Speichert Scan-Metadaten. Gibt die scan_id zurück."""
        started = datetime.fromisoformat(started_at)
        finished = datetime.fromisoformat(finished_at)
        duration_ms = int((finished - started).total_seconds() * 1000)

        cur = self._conn.execute(
            """INSERT INTO scans
               (scan_type, started_at, finished_at, locks_found, locks_new,
                locks_expired, locks_deleted, roots_scanned, duration_ms)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                scan_type,
                started_at,
                finished_at,
                stats.get("locks_found", stats.get("total", 0)),
                stats.get("locks_new", stats.get("new", 0)),
                stats.get("locks_expired", stats.get("expired", 0)),
                stats.get("locks_deleted", stats.get("deleted", 0)),
                stats.get("roots_scanned", 0),
                duration_ms,
            ),
        )
        self._conn.commit()
        return cur.lastrowid

    def refresh_expired_locks(self) -> int:
        """Mark active ordinary locks with elapsed ``expires_at`` as expired.

        User and condition locks are protected by policy and therefore excluded
        here as a second defence even if an older scanner persisted a nominal
        expiry timestamp.
        """
        now_dt = datetime.now()
        now_iso = now_dt.isoformat(timespec="seconds")
        rows = self._conn.execute(
            "SELECT id, expires_at FROM locks "
            "WHERE status='active' AND expires_at IS NOT NULL "
            "AND lock_type NOT IN ('user', 'condition')"
        ).fetchall()

        expired_ids: list[int] = []
        for row in rows:
            try:
                expires_at = datetime.fromisoformat(row["expires_at"])
            except (TypeError, ValueError):
                continue
            if expires_at <= now_dt:
                expired_ids.append(row["id"])

        for lock_id in expired_ids:
            self.mark_expired(lock_id, now_iso)

        return len(expired_ids)

    def get_active_locks(self) -> list[dict]:
        """Liefert alle Locks mit status='active'."""
        self.refresh_expired_locks()
        cur = self._conn.execute("SELECT * FROM locks WHERE status='active' ORDER BY path")
        return [dict(row) for row in cur.fetchall()]

    def get_lock_history(self, path: str | None = None, limit: int = 50) -> list[dict]:
        """Liefert Events, optional gefiltert nach Lock-Pfad."""
        if path is not None:
            cur = self._conn.execute(
                """SELECT e.*, l.path FROM events e
                   JOIN locks l ON e.lock_id = l.id
                   WHERE l.path = ?
                   ORDER BY e.timestamp DESC LIMIT ?""",
                (path, limit),
            )
        else:
            cur = self._conn.execute(
                """SELECT e.*, l.path FROM events e
                   JOIN locks l ON e.lock_id = l.id
                   ORDER BY e.timestamp DESC LIMIT ?""",
                (limit,),
            )
        return [dict(row) for row in cur.fetchall()]

    def mark_expired(self, lock_id: int, timestamp: str) -> None:
        """Setzt status auf 'expired' und loggt einen Event."""
        self._conn.execute(
            "UPDATE locks SET status='expired', updated_at=? WHERE id=?",
            (timestamp, lock_id),
        )
        self._conn.commit()
        self.record_event(lock_id, "expired")

    def mark_deleted(self, lock_id: int, timestamp: str) -> None:
        """Setzt status auf 'deleted', setzt removed_at und loggt einen Event."""
        self._conn.execute(
            "UPDATE locks SET status='deleted', removed_at=?, updated_at=? WHERE id=?",
            (timestamp, timestamp, lock_id),
        )
        self._conn.commit()
        self.record_event(lock_id, "deleted")

    def get_known_active_paths(self) -> list[str]:
        """Liefert alle Pfade mit status='active'."""
        cur = self._conn.execute("SELECT path FROM locks WHERE status='active'")
        return [row["path"] for row in cur.fetchall()]

    def get_lock_by_path(self, path: str) -> dict | None:
        """Lädt einen einzelnen Lock per Pfad."""
        cur = self._conn.execute("SELECT * FROM locks WHERE path=?", (path,))
        row = cur.fetchone()
        return dict(row) if row is not None else None

    def get_stats(self) -> dict:
        """Zusammenfassung: Anzahl aktiv/expired/deleted, letzter Scan, Events."""
        self.refresh_expired_locks()
        counts = {}
        for status in ("active", "expired", "deleted"):
            cur = self._conn.execute(
                "SELECT COUNT(*) FROM locks WHERE status=?", (status,)
            )
            counts[status] = cur.fetchone()[0]

        cur = self._conn.execute(
            "SELECT started_at, scan_type FROM scans ORDER BY id DESC LIMIT 1"
        )
        last_scan = cur.fetchone()

        cur = self._conn.execute("SELECT COUNT(*) FROM events")
        total_events = cur.fetchone()[0]

        return {
            "active": counts["active"],
            "expired": counts["expired"],
            "deleted": counts["deleted"],
            "last_scan_at": last_scan["started_at"] if last_scan else None,
            "last_scan_type": last_scan["scan_type"] if last_scan else None,
            "total_events": total_events,
        }

    def get_lock_by_id(self, lock_id: int) -> dict | None:
        """Lädt einen einzelnen Lock per ID."""
        self.refresh_expired_locks()
        cur = self._conn.execute("SELECT * FROM locks WHERE id=?", (lock_id,))
        row = cur.fetchone()
        return dict(row) if row is not None else None

    def get_all_locks(self, status_filter: str | None = None) -> list[dict]:
        """Liefert alle Locks, optional nach Status gefiltert."""
        self.refresh_expired_locks()
        if status_filter:
            cur = self._conn.execute(
                "SELECT * FROM locks WHERE status=? ORDER BY path", (status_filter,)
            )
        else:
            cur = self._conn.execute("SELECT * FROM locks ORDER BY path")
        return [dict(row) for row in cur.fetchall()]

    def get_events_by_prefix(self, path_prefix: str, limit: int = 100) -> list[dict]:
        """Events für Locks deren Pfad mit prefix beginnt (= Raum-History)."""
        escaped = path_prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        cur = self._conn.execute(
            """SELECT e.*, l.path, l.filename, l.display_name FROM events e
               JOIN locks l ON e.lock_id = l.id
               WHERE l.path LIKE ? || '%' ESCAPE '\\'
               ORDER BY e.timestamp DESC LIMIT ?""",
            (escaped, limit),
        )
        return [dict(row) for row in cur.fetchall()]

    def update_display_name(self, lock_id: int, name: str) -> bool:
        """Setzt den Anzeigenamen eines Locks."""
        now = datetime.now().isoformat(timespec="seconds")
        cur = self._conn.execute(
            "UPDATE locks SET display_name=?, updated_at=? WHERE id=?",
            (name, now, lock_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def upsert_room_stats(self, room_key: str, stats: dict) -> None:
        """Insert oder Update der Statistiken eines Raumes."""
        self._conn.execute(
            """INSERT INTO room_stats
               (room_key, file_count, folder_count, total_bytes,
                type_code, type_human, type_llm, type_media, type_other, scanned_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(room_key) DO UPDATE SET
                 file_count=excluded.file_count,
                 folder_count=excluded.folder_count,
                 total_bytes=excluded.total_bytes,
                 type_code=excluded.type_code,
                 type_human=excluded.type_human,
                 type_llm=excluded.type_llm,
                 type_media=excluded.type_media,
                 type_other=excluded.type_other,
                 scanned_at=excluded.scanned_at""",
            (
                room_key,
                stats.get("file_count", 0),
                stats.get("folder_count", 0),
                stats.get("total_bytes", 0),
                stats.get("type_code", 0),
                stats.get("type_human", 0),
                stats.get("type_llm", 0),
                stats.get("type_media", 0),
                stats.get("type_other", 0),
                stats.get("scanned_at", ""),
            ),
        )

    def upsert_room_stats_batch(self, stats_map: dict[str, dict]) -> None:
        """Batch-Insert/-Update aller Room-Stats in einer Transaktion."""
        for room_key, stats in stats_map.items():
            self.upsert_room_stats(room_key, stats)
        self._conn.commit()

    def get_all_room_stats(self) -> list[dict]:
        """Liefert alle Room-Stats als Liste von Dicts."""
        cur = self._conn.execute("SELECT * FROM room_stats ORDER BY room_key")
        return [dict(row) for row in cur.fetchall()]

    def close(self) -> None:
        """Schließt die Datenbankverbindung."""
        self._conn.close()
