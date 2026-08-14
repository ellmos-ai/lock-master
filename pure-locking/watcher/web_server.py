"""Lock-Watcher Web-Server: REST-API + Static-File-Serving.

Nur an 127.0.0.1 gebunden (kein Netzwerkzugriff).
Zero-Dependency: stdlib http.server.

Aufruf:
  PYTHONIOENCODING=utf-8 python web_server.py [--port 8095]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import urllib.parse
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config
import rooms
import storage
import lock_utils  # noqa: E402
import bulk_lock  # noqa: E402

STATIC_DIR = Path(__file__).resolve().parent / "static"
DEFAULT_PORT = 8095
USER_PROFILE_FILE = config._LOCAL_DATA_DIR / "user_profile.json"

_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_\-. ]+$")
_SAFE_MD_FILENAME_RE = re.compile(r"^[A-Za-z0-9_\-. ]+\.md$")
_SCOPE_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


def _resolve_within(root: Path, name: str, *, allow_subdirs: bool = False) -> Path | None:
    """Resolved *name* innerhalb von *root*. Gibt None zurück bei Traversal."""
    resolved_root = root.resolve()
    normalized_name = name.replace("\\", "/").strip()
    if not normalized_name:
        return None
    relative = PurePosixPath(normalized_name)
    if relative.is_absolute():
        return None
    parts = relative.parts
    if any(part in {"", ".", ".."} for part in parts):
        return None
    if not allow_subdirs and len(parts) != 1:
        return None
    resolved = root.joinpath(*parts).resolve()
    if resolved == resolved_root:
        return resolved
    try:
        resolved.relative_to(resolved_root)
    except ValueError:
        return None
    return resolved


def _safe_room_key(value: str) -> str | None:
    value = value.strip()
    return value if _SCOPE_RE.fullmatch(value) else None


def _safe_md_filename(value: str) -> str | None:
    name = value.strip()
    if not name:
        return None
    if not name.endswith(".md"):
        name += ".md"
    return name if _SAFE_MD_FILENAME_RE.fullmatch(name) else None


def _safe_header_value(value: str | None) -> str | None:
    if not value or "\r" in value or "\n" in value:
        return None
    return value


def _canonical_allowed_origin(port: int, origin: str | None) -> str | None:
    if not origin:
        return None
    if origin == f"http://127.0.0.1:{port}":
        return f"http://127.0.0.1:{port}"
    if origin == f"http://localhost:{port}":
        return f"http://localhost:{port}"
    if origin == f"http://[::1]:{port}":
        return f"http://[::1]:{port}"
    return None


def _load_user_profile() -> dict:
    if USER_PROFILE_FILE.exists():
        try:
            return json.loads(USER_PROFILE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"name": os.environ.get("USERNAME", "User"), "host": socket.gethostname()}


def _save_user_profile(profile: dict) -> None:
    USER_PROFILE_FILE.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _iso_age_seconds(value: str | None) -> int | None:
    if not value:
        return None
    try:
        then = datetime.fromisoformat(value)
    except ValueError:
        return None
    return int((datetime.now() - then).total_seconds())


def _daemon_status_for_api(db: storage.LockDB | None = None) -> dict:
    """Combine process heartbeat and scan freshness into one health state."""
    import lock_watcher as _lw

    status = _lw.load_daemon_status()
    if not status:
        return {"state": "unknown"}

    age = _lw._status_age_seconds(status)
    heartbeat_fresh = _lw._daemon_status_is_fresh(status)
    scan_in_progress = bool(status.get("scan_in_progress"))
    scan_started_at = status.get("scan_started_at")
    last_scan_age_seconds = None
    if db is not None:
        try:
            last_scan_age_seconds = _iso_age_seconds(db.get_stats().get("last_scan_at"))
        except Exception:
            last_scan_age_seconds = None

    scan_started_age = _iso_age_seconds(scan_started_at) if scan_in_progress else None
    scan_stuck = (
        scan_started_age is not None
        and scan_started_age > _lw.SCAN_OVERRUN_WARN_SECONDS
    )
    scan_stale = (
        last_scan_age_seconds is not None
        and last_scan_age_seconds > _lw.SCAN_OVERRUN_WARN_SECONDS
    )
    if not heartbeat_fresh:
        state = "stale"
    elif scan_stuck or scan_stale:
        state = "degraded"
    else:
        state = "running"

    return {
        "state": state,
        "pid": status.get("pid"),
        "host": status.get("host"),
        "started_at": status.get("started_at"),
        "last_seen": status.get("last_seen"),
        "age_seconds": age,
        "update_cache": bool(status.get("update_cache")),
        "scan_in_progress": scan_in_progress,
        "scan_started_at": scan_started_at,
        "last_scan_finished_at": status.get("last_scan_finished_at"),
        "last_scan_duration_s": status.get("last_scan_duration_s"),
        "last_scan_age_seconds": last_scan_age_seconds,
    }


class WatcherHandler(BaseHTTPRequestHandler):
    """HTTP-Handler mit JSON-API und Static-File-Serving."""

    db: storage.LockDB

    def log_message(self, format, *args):
        pass

    # ── Routing ────────────────────────────────────────────────

    def _host_allowed(self) -> bool:
        """Validate the Host header against the loopback addresses we serve.

        Defence against DNS rebinding: a malicious page can point its own
        hostname at 127.0.0.1 and then read the API same-origin — the only
        reliable tell is the Host header, which then carries the attacker's
        hostname instead of a loopback address.
        """
        host = _safe_header_value(self.headers.get("Host"))
        if not host:
            return False
        port = self.server.server_port
        return host in (
            f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}",
            "127.0.0.1", "localhost", "[::1]",
        )

    def _ensure_host_allowed(self) -> bool:
        if self._host_allowed():
            return True
        self._json_error(403, "Forbidden host")
        return False

    def do_GET(self):
        if not self._ensure_host_allowed():
            return
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = dict(urllib.parse.parse_qsl(parsed.query))

        routes = {
            "/api/rooms": self._get_rooms,
            "/api/locks": self._get_locks,
            "/api/stats": self._get_stats,
            "/api/settings": self._get_settings,
            "/api/profile": self._get_profile,
            "/api/central-files": self._get_central_files,
            "/api/icons": self._get_icons,
            "/api/room-stats": self._get_room_stats,
        }

        if path in routes:
            routes[path](qs)
        elif path.startswith("/api/lock/"):
            parts = path.split("/")
            if len(parts) == 4:
                self._get_lock_detail(parts[3], qs)
            else:
                self._json_error(404, "Not found")
        elif path.startswith("/api/room/"):
            parts = path.split("/")
            if len(parts) == 5 and parts[4] == "history":
                self._get_room_history(parts[3], qs)
            elif len(parts) == 4:
                self._get_room_detail(parts[3], qs)
            else:
                self._json_error(404, "Not found")
        elif path.startswith("/api/central-file/"):
            name = urllib.parse.unquote(path[len("/api/central-file/"):])
            self._read_central_file(name, qs)
        elif path.startswith("/api/room-files/"):
            room_key = path.split("/")[3] if len(path.split("/")) > 3 else ""
            self._list_room_files(room_key, qs)
        elif path.startswith("/api/room-file/"):
            parts = path.split("/", 4)
            if len(parts) >= 5:
                room_key, fname = parts[3], urllib.parse.unquote(parts[4])
                self._read_room_file(room_key, fname, qs)
            else:
                self._json_error(400, "room_key and filename required")
        elif path.startswith("/api/"):
            self._json_error(404, "Unknown API endpoint")
        else:
            self._serve_static(path)

    def do_POST(self):
        if not self._ensure_host_allowed():
            return
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")
        body = self._read_body()
        if not self._ensure_write_origin_allowed():
            return

        routes = {
            "/api/lock": self._create_lock,
            "/api/scan": self._trigger_scan,
            "/api/prune": self._trigger_prune,
            "/api/notes": self._write_notes,
            "/api/central-file": self._write_central_file,
            "/api/room-file": self._write_room_file,
            "/api/swap-central-file": self._swap_central_file,
            "/api/room-stats/refresh": self._refresh_room_stats,
            "/api/user-lock": self._create_user_lock,
            "/api/user-lock/remove": self._remove_user_lock,
            "/api/bulk-lock": self._bulk_lock,
            "/api/bulk-unlock": self._bulk_unlock,
        }

        if path in routes:
            routes[path](body)
        else:
            self._json_error(404, "Unknown API endpoint")

    def do_PUT(self):
        if not self._ensure_host_allowed():
            return
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")
        body = self._read_body()
        if not self._ensure_write_origin_allowed():
            return

        if path.startswith("/api/lock/") and path.endswith("/name"):
            parts = path.split("/")
            lock_id = parts[3]
            self._set_lock_name(lock_id, body)
        elif path.startswith("/api/room/") and len(path.split("/")) == 4:
            room_key = path.split("/")[3]
            self._update_room(room_key, body)
        elif path == "/api/settings":
            self._update_settings(body)
        elif path == "/api/profile":
            self._update_profile(body)
        else:
            self._json_error(404, "Unknown API endpoint")

    def do_OPTIONS(self):
        if not self._origin_allowed(self.headers.get("Origin")):
            self.send_response(403)
            self.end_headers()
            return
        self.send_response(200)
        self._cors_headers()
        self.end_headers()

    # ── API: GET ───────────────────────────────────────────────

    def _get_rooms(self, qs: dict):
        room_list = rooms.get_rooms()
        db = self.server.db
        active_locks = db.get_active_locks()

        for r in room_list:
            r["lock_count"] = sum(
                1 for lock_item in active_locks if rooms.room_for_path(lock_item["path"]) == r["key"]
            )

        self._json_response(room_list)

    def _get_locks(self, qs: dict):
        db = self.server.db
        room_key = qs.get("room")
        status = qs.get("status", "active")

        if status == "all":
            all_locks = db.get_all_locks()
        else:
            all_locks = db.get_all_locks(status)

        result = []
        for lock_item in all_locks:
            lock_item["room"] = rooms.room_for_path(lock_item["path"])
            if room_key and lock_item["room"] != room_key:
                continue
            if lock_item.get("team_data") and isinstance(lock_item["team_data"], str):
                try:
                    lock_item["team_data"] = json.loads(lock_item["team_data"])
                except json.JSONDecodeError:
                    pass
            lock_item["remaining"] = self._calc_remaining(lock_item.get("expires_at"))
            result.append(lock_item)

        self._json_response(result)

    def _get_lock_detail(self, lock_id_str: str, qs: dict):
        try:
            lock_id = int(lock_id_str)
        except ValueError:
            self._json_error(400, "Invalid lock ID")
            return

        db = self.server.db
        lock = db.get_lock_by_id(lock_id)
        if lock is None:
            self._json_error(404, "Lock not found")
            return

        lock["room"] = rooms.room_for_path(lock["path"])
        if lock.get("team_data") and isinstance(lock["team_data"], str):
            try:
                lock["team_data"] = json.loads(lock["team_data"])
            except json.JSONDecodeError:
                pass
        lock["remaining"] = self._calc_remaining(lock.get("expires_at"))

        events = db.get_lock_history(lock["path"], limit=20)
        lock["events"] = events

        self._json_response(lock)

    def _get_room_detail(self, room_key: str, qs: dict):
        room = rooms.get_room(room_key)
        if room is None:
            self._json_error(404, "Room not found")
            return

        db = self.server.db
        active_locks = db.get_active_locks()
        room["locks"] = [
            {**lock_item, "room": room_key, "remaining": self._calc_remaining(lock_item.get("expires_at"))}
            for lock_item in active_locks
            if rooms.room_for_path(lock_item["path"]) == room_key
        ]
        self._json_response(room)

    def _get_room_history(self, room_key: str, qs: dict):
        room = rooms.get_room(room_key)
        if room is None:
            self._json_error(404, "Room not found")
            return

        try:
            limit = int(qs.get("limit", "50"))
        except ValueError:
            self._json_error(400, "Invalid limit")
            return
        events = self.server.db.get_events_by_prefix(room["abs_path"], limit)
        self._json_response(events)

    def _get_stats(self, qs: dict):
        import lock_watcher as _lw

        stats = self.server.db.get_stats()
        age = _iso_age_seconds(stats.get("last_scan_at"))
        stats["last_scan_age_seconds"] = age
        stats["scan_stale"] = (
            age is not None and age > _lw.SCAN_OVERRUN_WARN_SECONDS
        )
        self._json_response(stats)

    def _get_profile(self, qs: dict):
        self._json_response(_load_user_profile())

    def _get_settings(self, qs: dict):
        self._json_response({
            "full_scan_interval": config.FULL_SCAN_INTERVAL,
            "check_interval": config.CHECK_INTERVAL,
            "db_path": str(config.DB_PATH),
            "roots_file": str(config.resolve_roots_file()),
            "daemon": _daemon_status_for_api(self.server.db),
        })

    # ── API: POST ──────────────────────────────────────────────

    def _create_lock(self, body: dict):
        project_dir = body.get("project_dir", "").strip()
        if not project_dir:
            self._json_error(400, "project_dir required")
            return

        project_path = os.path.normpath(project_dir)
        if not rooms.is_path_in_roots(project_path):
            self._json_error(403, "Path outside allowed roots")
            return

        if not os.path.isdir(project_path):
            self._json_error(404, "Directory not found")
            return

        scope = body.get("scope", "").strip()
        if scope and scope != "project":
            if not _SCOPE_RE.match(scope):
                self._json_error(400, "Invalid scope (only A-Z, a-z, 0-9, _, -)")
                return
            filename = f"LOCK.{scope}.txt"
        else:
            filename = "LOCK.txt"

        lock_path = os.path.join(project_path, filename)
        if os.path.exists(lock_path):
            self._json_error(409, f"{filename} already exists")
            return

        profile = _load_user_profile()
        owner = body.get("owner", profile.get("name", "User (Web UI)")).strip()
        purpose = body.get("purpose", "").strip()
        mode = body.get("mode", "hard").strip()
        expires = body.get("expires_after", "24h").strip()
        host = socket.gethostname()
        now = datetime.now().strftime("%Y-%m-%dT%H:%M")

        lines = [
            f"owner: {owner}",
            f"created: {now}",
            f"host: {host}",
            f"expires_after: {expires}",
            f"mode: {mode}",
        ]
        if purpose:
            lines.append(f"purpose: {purpose}")
        if scope and scope != "project":
            lines.append(f"scope: {scope}")

        try:
            with Path(lock_path).open("x", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except FileExistsError:
            self._json_error(409, f"{filename} already exists")
            return
        except OSError as exc:
            self._json_error(500, f"Failed to write lock: {exc}")
            return

        self._json_response({"created": lock_path, "filename": filename}, status=201)

    def _resolve_project_dir(self, value: str) -> str | None:
        """Normalize + validate a project path (must live under a configured root)."""
        project_path = os.path.normpath(value.strip())
        if not rooms.is_path_in_roots(project_path):
            return None
        return project_path

    def _create_user_lock(self, body: dict):
        """Create a user lock (LOCK.user(.<scope>).txt) -- only the user removes it."""
        project_dir = body.get("project_dir", "").strip()
        if not project_dir:
            self._json_error(400, "project_dir required")
            return
        project_path = self._resolve_project_dir(project_dir)
        if project_path is None:
            self._json_error(403, "Path outside allowed roots")
            return
        if not os.path.isdir(project_path):
            self._json_error(404, "Directory not found")
            return

        scope = body.get("scope", "").strip()
        if scope and scope != "project":
            if not _SCOPE_RE.match(scope):
                self._json_error(400, "Invalid scope (only A-Z, a-z, 0-9, _, -)")
                return
            filename = f"LOCK.user.{scope}.txt"
        else:
            filename = "LOCK.user.txt"

        lock_path = os.path.join(project_path, filename)
        if os.path.exists(lock_path):
            self._json_error(409, f"{filename} already exists")
            return

        profile = _load_user_profile()
        owner = body.get("owner", profile.get("name", "User (Web UI)")).strip()
        purpose = body.get("purpose", "").strip()
        host = socket.gethostname()
        now = datetime.now().strftime("%Y-%m-%dT%H:%M")

        lines = [f"owner: {owner}", f"created: {now}", f"host: {host}"]
        if purpose:
            lines.append(f"purpose: {purpose}")
        if scope and scope != "project":
            lines.append(f"scope: {scope}")

        try:
            with Path(lock_path).open("x", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except FileExistsError:
            self._json_error(409, f"{filename} already exists")
            return
        except OSError as exc:
            self._json_error(500, f"Failed to write user-lock: {exc}")
            return

        self._json_response({"created": lock_path, "filename": filename}, status=201)

    def _remove_user_lock(self, body: dict):
        """Remove a user lock. Via the GUI this counts as the 'user' channel (allowed)."""
        project_dir = body.get("project_dir", "").strip()
        project_path = self._resolve_project_dir(project_dir) if project_dir else None
        if project_path is None:
            self._json_error(403, "Path outside allowed roots")
            return
        scope = body.get("scope", "").strip()
        filename = f"LOCK.user.{scope}.txt" if scope and scope != "project" else "LOCK.user.txt"
        if not lock_utils.is_user_lock(filename):
            self._json_error(400, "Not a user-lock filename")
            return
        target = Path(project_path) / filename
        if not target.exists():
            self._json_error(404, f"{filename} not found")
            return
        try:
            target.unlink()
        except OSError as exc:
            self._json_error(500, f"Failed to remove user-lock: {exc}")
            return
        self._json_response({"removed": str(target)})

    def _bulk_lock(self, body: dict):
        """Immediately lock all connected roots. 'commit' must be explicitly true."""
        commit = bool(body.get("commit", False))
        owner = body.get("owner", _load_user_profile().get("name", "User")).strip()
        reason = body.get("reason", "Bulk lockdown").strip()
        try:
            roots = bulk_lock._load_roots_from_config()
            res = bulk_lock.bulk_lock(roots, owner=owner, reason=reason, commit=commit)
        except Exception as exc:
            self._json_error(500, f"Bulk-lock failed: {exc}")
            return
        self._json_response(res)

    def _bulk_unlock(self, body: dict):
        """Lift a bulk lockdown (only bulk-created locks, never user-locks)."""
        commit = bool(body.get("commit", False))
        try:
            roots = bulk_lock._load_roots_from_config()
            res = bulk_lock.bulk_unlock(roots, commit=commit)
        except Exception as exc:
            self._json_error(500, f"Bulk-unlock failed: {exc}")
            return
        self._json_response(res)

    def _trigger_scan(self, body: dict):
        try:
            import lock_watcher as _lw
            cfg = config.load_scan_config()
            stats = _lw._run_full_scan(self.server.db, cfg, False)
            self._json_response({
                "locks_found": stats.get("total", 0),
                "new": stats.get("new", 0),
                "modified": stats.get("modified", 0),
                "expired": stats.get("expired", 0),
                "deleted": stats.get("deleted", 0),
                "message": "Scan complete",
            })
        except Exception as exc:
            self._json_error(500, f"Scan failed: {exc}")

    def _trigger_prune(self, body: dict):
        prune_script = config.SCRIPTS_DIR / "prune_stale_locks.py"
        if not prune_script.exists():
            self._json_error(404, "prune_stale_locks.py not found")
            return

        try:
            result = subprocess.run(
                [sys.executable, str(prune_script)],
                capture_output=True,
                text=True,
                timeout=30,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
            self._json_response({
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
            })
        except subprocess.TimeoutExpired:
            self._json_error(504, "Prune timed out")
        except Exception as exc:
            self._json_error(500, f"Prune failed: {exc}")

    def _write_notes(self, body: dict):
        room_key = body.get("room_key", "").strip()
        if not room_key:
            self._json_error(400, "room_key required")
            return
        rooms.write_user_notes(room_key)
        self._json_response({"written": True, "room": room_key})

    # ── API: PUT ───────────────────────────────────────────────

    def _set_lock_name(self, lock_id_str: str, body: dict):
        try:
            lock_id = int(lock_id_str)
        except ValueError:
            self._json_error(400, "Invalid lock ID")
            return

        name = body.get("name", "").strip()
        if not name:
            self._json_error(400, "name required")
            return

        ok = self.server.db.update_display_name(lock_id, name)
        if ok:
            self._json_response({"updated": True})
        else:
            self._json_error(404, "Lock not found")

    def _update_room(self, room_key: str, body: dict):
        allowed = {"label", "color", "notes", "goals", "notes_target", "notes_filename", "icon"}
        updates = {k: v for k, v in body.items() if k in allowed}
        if not updates:
            self._json_error(400, "No valid fields")
            return

        ok = rooms.update_room(room_key, **updates)
        if ok:
            if "notes" in updates or "goals" in updates:
                rooms.write_user_notes(room_key)
            self._json_response({"updated": True})
        else:
            self._json_error(404, "Room not found")

    # ── API: Central File Management ──────────────────────────

    def _central_files_dir(self) -> Path:
        return config._LOCAL_DATA_DIR / "central_files"

    def _get_central_files(self, qs: dict):
        lib_dir = self._central_files_dir()
        lib_dir.mkdir(exist_ok=True)
        files = []
        for f in sorted(lib_dir.glob("*.md")):
            files.append({
                "name": f.stem,
                "filename": f.name,
                "size": f.stat().st_size,
                "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds"),
            })
        self._json_response(files)

    def _read_central_file(self, name: str, qs: dict):
        name = _safe_md_filename(name)
        if name is None:
            self._json_error(400, "Invalid filename")
            return
        safe = _resolve_within(self._central_files_dir(), name)
        if safe is None:
            self._json_error(403, "Forbidden")
            return
        if not safe.exists():
            self._json_error(404, "File not found")
            return
        content = safe.read_text(encoding="utf-8")
        self._json_response({"name": safe.stem, "filename": safe.name, "content": content})

    def _write_central_file(self, body: dict):
        name = _safe_md_filename(body.get("name", ""))
        content = body.get("content", "")
        if not name:
            self._json_error(400, "name required")
            return
        lib_dir = self._central_files_dir()
        lib_dir.mkdir(exist_ok=True)
        safe = _resolve_within(lib_dir, name)
        if safe is None:
            self._json_error(403, "Forbidden")
            return
        safe.write_text(content, encoding="utf-8")
        self._json_response({"saved": name})

    def _swap_central_file(self, body: dict):
        """Tauscht eine zentrale Datei (z.B. CLAUDE.md) eines Raumes gegen eine aus der Bibliothek."""
        room_key = body.get("room_key", "").strip()
        target_name = body.get("target_name", "CLAUDE.md").strip()
        source_name = _safe_md_filename(body.get("source_name", ""))
        if not room_key or not source_name:
            self._json_error(400, "room_key and source_name required")
            return

        room_key = _safe_room_key(room_key)
        target_name = _safe_md_filename(target_name)
        if room_key is None:
            self._json_error(400, "Invalid room_key")
            return
        if target_name is None:
            self._json_error(400, "Invalid target_name")
            return

        room = rooms.get_room(room_key)
        if room is None:
            self._json_error(404, "Room not found")
            return

        target_path = _resolve_within(Path(room["abs_path"]), target_name)
        if target_path is None:
            self._json_error(403, "target_name traversal blocked")
            return

        source_path = _resolve_within(self._central_files_dir(), source_name)
        if source_path is None:
            self._json_error(403, "source_name traversal blocked")
            return

        if not source_path.exists():
            self._json_error(404, f"Library file {source_name} not found")
            return

        if target_path.exists():
            backup_name = f"{target_path.stem}_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}{target_path.suffix}"
            backup_path = _resolve_within(self._central_files_dir(), backup_name)
            if backup_path is None:
                self._json_error(500, "Backup name invalid")
                return
            try:
                backup_path.write_text(target_path.read_text(encoding="utf-8"), encoding="utf-8")
            except OSError as exc:
                self._json_error(500, f"Backup failed: {exc}")
                return

        try:
            content = source_path.read_text(encoding="utf-8")
            target_path.write_text(content, encoding="utf-8")
        except OSError as exc:
            self._json_error(500, f"Swap failed: {exc}")
            return

        self._json_response({"swapped": True, "target": str(target_path), "source": source_name})

    def _list_room_files(self, room_key: str, qs: dict):
        room_key = _safe_room_key(room_key)
        if room_key is None:
            self._json_error(400, "Invalid room_key")
            return
        room = rooms.get_room(room_key)
        if room is None:
            self._json_error(404, "Room not found")
            return
        root = Path(room["abs_path"])
        if not root.is_dir():
            self._json_response([])
            return
        md_files = []
        for f in sorted(root.glob("*.md")):
            if f.is_file():
                md_files.append({
                    "name": f.name,
                    "size": f.stat().st_size,
                    "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds"),
                })
        self._json_response(md_files)

    def _read_room_file(self, room_key: str, filename: str, qs: dict):
        room_key = _safe_room_key(room_key)
        filename = _safe_md_filename(filename)
        if room_key is None:
            self._json_error(400, "Invalid room_key")
            return
        room = rooms.get_room(room_key)
        if room is None:
            self._json_error(404, "Room not found")
            return
        if filename is None:
            self._json_error(400, "Invalid filename")
            return
        safe = _resolve_within(Path(room["abs_path"]), filename)
        if safe is None:
            self._json_error(403, "Forbidden")
            return
        if not safe.is_file():
            self._json_error(404, "File not found")
            return
        try:
            content = safe.read_text(encoding="utf-8")
            self._json_response({"name": safe.name, "content": content})
        except OSError:
            self._json_error(500, "Read error")

    def _write_room_file(self, body: dict):
        room_key = _safe_room_key(body.get("room_key", ""))
        filename = _safe_md_filename(body.get("filename", ""))
        content = body.get("content", "")
        if not room_key or not filename:
            self._json_error(400, "room_key and filename required")
            return
        room = rooms.get_room(room_key)
        if room is None:
            self._json_error(404, "Room not found")
            return
        safe = _resolve_within(Path(room["abs_path"]), filename)
        if safe is None:
            self._json_error(403, "Forbidden")
            return
        try:
            safe.write_text(content, encoding="utf-8")
            self._json_response({"saved": filename})
        except OSError as exc:
            self._json_error(500, f"Write failed: {exc}")

    def _update_profile(self, body: dict):
        profile = _load_user_profile()
        if "name" in body:
            profile["name"] = body["name"].strip()
        _save_user_profile(profile)
        self._json_response({"updated": True, "profile": profile})

    def _update_settings(self, body: dict):
        self._json_response({
            "message": "Settings are read-only at runtime. Edit config.py or restart daemon.",
            "current": {
                "full_scan_interval": config.FULL_SCAN_INTERVAL,
                "check_interval": config.CHECK_INTERVAL,
            },
        })

    # ── Static Files ───────────────────────────────────────────

    def _get_icons(self, qs: dict):
        icons_dir = STATIC_DIR / "icons"
        if not icons_dir.is_dir():
            self._json_response([])
            return
        icons = sorted(
            f.name for f in icons_dir.iterdir()
            if f.is_file() and f.suffix.lower() in (".svg", ".png")
        )
        self._json_response(icons)

    def _get_room_stats(self, qs: dict):
        """GET /api/room-stats — liefert gecachte Verzeichnis-Statistiken."""
        stats = self.server.db.get_all_room_stats()
        self._json_response(stats)

    def _refresh_room_stats(self, body: dict):
        """POST /api/room-stats/refresh — führt Stats-Scan direkt im Webserver aus."""
        import dir_stats
        import rooms as rooms_mod
        try:
            all_rooms = rooms_mod._get_rooms()
            cfg = config.load_scan_config()
            skipped = set(cfg.get("skip_dirs", []))
            stats_map = dir_stats.scan_all_rooms(all_rooms, skipped)
            self.server.db.upsert_room_stats_batch(stats_map)
            self._json_response({"refreshed": True, "rooms": len(stats_map)})
        except Exception as exc:
            self._json_error(500, f"Stats-Scan fehlgeschlagen: {exc}")

    def _serve_static(self, path: str):
        if path == "/":
            path = "/index.html"

        static_name = urllib.parse.unquote(path.lstrip("/"))
        safe_path = _resolve_within(STATIC_DIR, static_name, allow_subdirs=True)
        if safe_path is None:
            self._json_error(403, "Forbidden")
            return

        if not safe_path.is_file():
            safe_path = STATIC_DIR / "index.html"
            if not safe_path.is_file():
                self._json_error(404, "Not found")
                return

        content_types = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".ico": "image/x-icon",
        }
        ext = safe_path.suffix.lower()
        ct = content_types.get(ext, "application/octet-stream")

        try:
            data = safe_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(data)))
            self._cors_headers()
            self.end_headers()
            self.wfile.write(data)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass
        except OSError:
            self._json_error(500, "File read error")

    # ── Helpers ────────────────────────────────────────────────

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def _json_response(self, data, status: int = 200):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors_headers()
        self.end_headers()
        try:
            self.wfile.write(body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass

    def _json_error(self, status: int, message: str):
        self._json_response({"error": message}, status)

    def _origin_allowed(self, origin: str | None) -> bool:
        if not origin:
            return True
        return _canonical_allowed_origin(self.server.server_address[1], origin) is not None

    def _ensure_write_origin_allowed(self) -> bool:
        if self._origin_allowed(self.headers.get("Origin")):
            return True
        self._json_error(403, "Forbidden origin")
        return False

    def _cors_headers(self):
        origin = _canonical_allowed_origin(
            self.server.server_address[1], self.headers.get("Origin")
        )
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    @staticmethod
    def _calc_remaining(expires_at: str | None) -> str | None:
        if not expires_at:
            return None
        try:
            exp = datetime.fromisoformat(expires_at)
            delta = exp - datetime.now()
            if delta.total_seconds() <= 0:
                return "abgelaufen"
            hours, rem = divmod(int(delta.total_seconds()), 3600)
            minutes = rem // 60
            if hours > 0:
                return f"{hours}h {minutes}m"
            return f"{minutes}m"
        except ValueError:
            return None


class WatcherServer(HTTPServer):
    def __init__(self, port: int):
        super().__init__(("127.0.0.1", port), WatcherHandler)
        self.db = storage.LockDB(config.DB_PATH)

    def server_close(self):
        self.db.close()
        super().server_close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Lock-Watcher Web-Server")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    STATIC_DIR.mkdir(parents=True, exist_ok=True)

    server = WatcherServer(args.port)
    print(f"Lock-Watcher Web-UI: http://127.0.0.1:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
