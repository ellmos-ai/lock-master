"""Room-Mapping: ordnet Lock-Pfade thematischen Räumen zu.

Jeder Scan-Root aus lock_roots.json wird zu einem Raum.
Zuordnung per Longest-Prefix-Match (überlappende Roots korrekt aufgelöst).
Raum-Konfiguration (Name, Farbe, Notizen, Objekte) in rooms.json persistiert.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import config

_SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9_\-. ]+\.md$")

_ROOMS_FILE: Path = config._LOCAL_DATA_DIR / "rooms.json"

_DEFAULT_LABELS: dict[str, str] = {
    ".": "Workspace",
    "scripts": "Scripts",
    "_scripts": "Scripts",
    "docs": "Docs",
    "src": "Source",
}

_DEFAULT_COLORS: dict[str, str] = {
    ".": "#64748b",
    "scripts": "#6b7280",
    "_scripts": "#6b7280",
    "docs": "#0ea5e9",
    "src": "#10b981",
}


def _key_from_relpath(relpath: str) -> str:
    return relpath.replace("\\", "/").strip("/").replace("/", "_").replace(".", "").lower()


def _load_rooms_config() -> dict:
    if _ROOMS_FILE.exists():
        try:
            return json.loads(_ROOMS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_rooms_config(cfg: dict) -> None:
    _ROOMS_FILE.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _workspace_root(paths: list[str]) -> Path:
    """Bestimmt einen neutralen gemeinsamen Root für relative Raumlabels."""
    normalized = [
        os.path.normpath(os.path.expandvars(p))
        for p in paths
        if p
    ]
    if not normalized:
        return Path.home()
    try:
        return Path(os.path.commonpath(normalized))
    except ValueError:
        return Path.home()


def _build_rooms() -> list[dict]:
    """Baut die Raumliste aus lock_roots.json + gespeicherter Config."""
    scan_cfg = config.load_scan_config()
    root_entries = scan_cfg.get("roots", [])
    workspace = _workspace_root([entry.get("path", "") for entry in root_entries])
    user_cfg = _load_rooms_config()

    rooms = []
    for root_entry in root_entries:
        raw_path = root_entry.get("path", "")
        abs_path = os.path.expandvars(raw_path)
        abs_path = os.path.normpath(abs_path)

        try:
            relpath = os.path.relpath(abs_path, str(workspace)).replace("\\", "/")
        except ValueError:
            relpath = os.path.basename(abs_path)
        if relpath == ".":
            relpath = os.path.basename(abs_path)

        key = _key_from_relpath(relpath)
        default_label = _DEFAULT_LABELS.get(relpath, os.path.basename(abs_path))
        default_color = _DEFAULT_COLORS.get(relpath, "#6b7280")

        room_user = user_cfg.get(key, {})
        rooms.append({
            "key": key,
            "dir_name": os.path.basename(abs_path),
            "label": room_user.get("label", default_label),
            "color": room_user.get("color", default_color),
            "notes": room_user.get("notes", ""),
            "goals": room_user.get("goals", ""),
            "notes_target": room_user.get("notes_target", "own_file"),
            "notes_filename": room_user.get("notes_filename", "USER-NOTES.md"),
            "icon": room_user.get("icon", ""),
            "objects": room_user.get("objects", []),
            "abs_path": abs_path,
            "rel_path": relpath,
        })

    rooms.sort(key=lambda r: len(r["abs_path"]), reverse=True)
    return rooms


_rooms_cache: list[dict] | None = None


def _get_rooms() -> list[dict]:
    global _rooms_cache
    if _rooms_cache is None:
        _rooms_cache = _build_rooms()
    return _rooms_cache


def invalidate_cache() -> None:
    global _rooms_cache
    _rooms_cache = None


def get_rooms() -> list[dict]:
    """Gibt alle Raum-Definitionen zurück (ohne abs_path für die API)."""
    return [
        {
            "key": r["key"],
            "dir_name": r["dir_name"],
            "label": r["label"],
            "color": r["color"],
            "notes": r["notes"],
            "goals": r["goals"],
            "notes_target": r["notes_target"],
            "notes_filename": r["notes_filename"],
            "icon": r["icon"],
            "objects": r["objects"],
            "rel_path": r["rel_path"],
        }
        for r in _get_rooms()
    ]


def get_room(key: str) -> dict | None:
    """Gibt einen einzelnen Raum nach key zurück (inkl. abs_path)."""
    for r in _get_rooms():
        if r["key"] == key:
            return dict(r)
    return None


def room_for_path(lock_path: str) -> str:
    """Bestimmt den Raum-Key für einen Lock-Pfad (Longest-Prefix-Match)."""
    normalized = os.path.normpath(lock_path).lower()
    for room in _get_rooms():
        root_lower = os.path.normpath(room["abs_path"]).lower()
        if normalized.startswith(root_lower + os.sep) or normalized == root_lower:
            return room["key"]
    return "other"


def update_room(key: str, **kwargs: str) -> bool:
    """Aktualisiert Raum-Einstellungen (label, color, notes, goals).
    Gibt True zurück wenn erfolgreich."""
    room = get_room(key)
    if room is None:
        return False

    cfg = _load_rooms_config()
    if key not in cfg:
        cfg[key] = {}

    allowed = {"label", "color", "notes", "goals", "notes_target", "notes_filename", "icon"}
    for field, value in kwargs.items():
        if field in allowed:
            cfg[key][field] = value

    _save_rooms_config(cfg)
    invalidate_cache()
    return True


def update_room_objects(key: str, objects: list[dict]) -> bool:
    """Speichert die Objekt-Liste eines Raumes (Möbel, Notizen etc.)."""
    room = get_room(key)
    if room is None:
        return False

    cfg = _load_rooms_config()
    if key not in cfg:
        cfg[key] = {}
    cfg[key]["objects"] = objects

    _save_rooms_config(cfg)
    invalidate_cache()
    return True


def get_room_abs_path(key: str) -> str | None:
    """Gibt den absoluten Pfad eines Raumes zurück (für Pfad-Validierung)."""
    room = get_room(key)
    return room["abs_path"] if room else None


def is_path_in_roots(target_path: str) -> bool:
    """Prüft ob ein Pfad innerhalb eines der Scan-Roots liegt."""
    normalized = os.path.normpath(target_path).lower()
    for room in _get_rooms():
        root_lower = os.path.normpath(room["abs_path"]).lower()
        if normalized.startswith(root_lower + os.sep) or normalized == root_lower:
            return True
    return False


def write_user_notes(key: str) -> None:
    """Schreibt die Notizen und Ziele eines Raumes in eine Datei.

    notes_target bestimmt das Ziel:
      'own_file' → eigene Datei (Name konfigurierbar, Default: USER-NOTES.md)
      'claude_md' → an CLAUDE.md des Projektes anhängen
    """
    room = get_room(key)
    if room is None:
        return

    notes = room.get("notes", "").strip()
    goals = room.get("goals", "").strip()

    obj_notes = []
    for obj in room.get("objects", []):
        if obj.get("note"):
            obj_notes.append(f"- **{obj.get('name', 'Objekt')}:** {obj['note']}")

    if not notes and not goals and not obj_notes:
        return

    block_lines = [f"## {room['label']} — User-Notizen", ""]
    if goals:
        block_lines += ["### Ziele", "", goals, ""]
    if notes:
        block_lines += ["### Notizen", "", notes, ""]
    if obj_notes:
        block_lines += ["### Objekt-Notizen", ""] + obj_notes + [""]

    stamp = __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M")
    block_lines.append(f"*Automatisch generiert vom Lock-Watcher ({stamp})*\n")
    block = "\n".join(block_lines)

    target = room.get("notes_target", "own_file")
    root = Path(room["abs_path"])

    try:
        if target == "claude_md":
            claude_md = root / "CLAUDE.md"
            marker = "<!-- LOCK-WATCHER-NOTES START -->"
            end_marker = "<!-- LOCK-WATCHER-NOTES END -->"
            wrapped = f"\n{marker}\n{block}\n{end_marker}\n"

            if claude_md.exists():
                content = claude_md.read_text(encoding="utf-8")
                if marker in content:
                    import re as _re
                    content = _re.sub(
                        f"{_re.escape(marker)}.*?{_re.escape(end_marker)}",
                        lambda _m: wrapped.strip(),
                        content,
                        flags=_re.DOTALL,
                    )
                else:
                    content = content.rstrip() + "\n" + wrapped
                claude_md.write_text(content, encoding="utf-8")
            else:
                claude_md.write_text(wrapped, encoding="utf-8")
        else:
            filename = room.get("notes_filename", "USER-NOTES.md")
            if not _SAFE_FILENAME_RE.fullmatch(filename):
                return
            notes_path = root / filename
            if notes_path.resolve() != (root / filename).resolve():
                return
            full = f"# {room['label']} — User-Notizen\n\n" + block
            notes_path.write_text(full, encoding="utf-8")
    except (OSError, re.error):
        pass
