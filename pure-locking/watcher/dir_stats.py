"""Directory-Statistiken für Raum-Kacheln. Sammelt Datei-/Ordner-Counts und Größen."""

from __future__ import annotations

import fnmatch
import os
import time
from collections.abc import Iterable
from datetime import datetime


_CODE_EXT = frozenset({
    '.py', '.js', '.ts', '.tsx', '.jsx', '.lua', '.luau', '.rs', '.go', '.java',
    '.c', '.cpp', '.h', '.hpp', '.cs', '.css', '.scss', '.html', '.htm',
    '.sh', '.ps1', '.bat', '.cmd', '.sql', '.rb', '.php', '.swift', '.kt',
    '.r', '.m', '.vue', '.svelte',
})

_HUMAN_EXT = frozenset({
    '.doc', '.docx', '.pdf', '.odt', '.rtf', '.pptx', '.ppt',
    '.xlsx', '.xls', '.pages', '.numbers', '.key',
})

_LLM_EXT = frozenset({
    '.txt', '.md', '.json', '.yaml', '.yml', '.toml', '.ini', '.cfg',
    '.csv', '.xml', '.log', '.rst', '.tex', '.bib', '.jsonl',
})

_MEDIA_EXT = frozenset({
    '.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.bmp', '.ico', '.tif', '.tiff',
    '.mp4', '.mp3', '.wav', '.webm', '.avi', '.mov', '.mkv', '.flac', '.ogg',
    '.obj', '.fbx', '.glb', '.gltf',
})


def classify(ext: str) -> str:
    ext = ext.lower()
    if ext in _CODE_EXT:
        return 'code'
    if ext in _HUMAN_EXT:
        return 'human'
    if ext in _LLM_EXT:
        return 'llm'
    if ext in _MEDIA_EXT:
        return 'media'
    return 'other'


def _split_skips(skipped_dirs: Iterable[str] | None) -> tuple[set[str], tuple[str, ...]]:
    """Split configured skip entries into case-folded literals and glob patterns."""
    if not skipped_dirs:
        return set(), ()
    folded = [str(entry).casefold() for entry in skipped_dirs]
    literals = {entry for entry in folded if not any(ch in entry for ch in "*?[")}
    globs = tuple(entry for entry in folded if any(ch in entry for ch in "*?["))
    return literals, globs


def _is_skipped_dir(name: str, literal_skips: set[str], glob_skips: tuple[str, ...]) -> bool:
    name_low = name.casefold()
    if name_low in literal_skips:
        return True
    return bool(glob_skips and any(fnmatch.fnmatchcase(name_low, pat) for pat in glob_skips))


def scan_room(abs_path: str, skipped_dirs: Iterable[str] | None = None,
              timeout: float = 30.0) -> dict:
    """Scannt ein Verzeichnis rekursiv via os.scandir und liefert Statistiken."""
    literal_skips, glob_skips = _split_skips(skipped_dirs)

    result = {
        'file_count': 0,
        'folder_count': 0,
        'total_bytes': 0,
        'type_code': 0,
        'type_human': 0,
        'type_llm': 0,
        'type_media': 0,
        'type_other': 0,
        'scanned_at': datetime.now().isoformat(timespec='seconds'),
    }

    deadline = time.monotonic() + timeout
    stack = [abs_path]

    while stack:
        if time.monotonic() > deadline:
            break
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    if time.monotonic() > deadline:
                        break
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if not _is_skipped_dir(entry.name, literal_skips, glob_skips):
                                result['folder_count'] += 1
                                stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            result['file_count'] += 1
                            try:
                                st = entry.stat(follow_symlinks=False)
                                attrs = getattr(st, 'st_file_attributes', 0)
                                if not (attrs & 0x00400000):
                                    result['total_bytes'] += st.st_size
                            except OSError:
                                pass
                            ext = os.path.splitext(entry.name)[1]
                            cat = classify(ext)
                            result[f'type_{cat}'] += 1
                    except OSError:
                        continue
        except OSError:
            continue

    return result


def scan_all_rooms(rooms: list[dict], skipped_dirs: Iterable[str] | None = None,
                   timeout_per_room: float = 30.0) -> dict[str, dict]:
    """Scannt alle Räume und gibt ein Dict room_key → stats zurück."""
    results = {}
    for room in rooms:
        key = room.get('key', '')
        abs_path = room.get('abs_path', '')
        if not abs_path or not os.path.isdir(abs_path):
            continue
        try:
            results[key] = scan_room(abs_path, skipped_dirs, timeout_per_room)
        except Exception:
            continue
    return results
