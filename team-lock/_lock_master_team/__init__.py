"""Atomic, file-local coordination for ``LOCK.team.*.txt`` files.

The Team Lock remains the source of truth. A persistent sibling ``.guard``
file only supplies an operating-system lock while one process performs a
read/validate/write transaction; it contains no coordination state.
"""

from __future__ import annotations

import argparse
import os
import re
import stat
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path, PureWindowsPath
from typing import Iterator, Sequence

_AGENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@+\-]{0,127}$")
_TOOL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@+\-]{0,255}$")
_TEAM_LOCK_RE = re.compile(r"^LOCK\.team(?:\.[A-Za-z0-9_-]+)+\.txt$")
_EXPLICIT_CLAIM_RE = re.compile(
    r"^\[([^\]]+)\]\s+(EDITING|WAITING|USING)\s+order=(\d+)\s+\|\s+(.+)$"
)
_LEGACY_CLAIM_RE = re.compile(r"^\[?([^\]\s]+)\]?\s+(EDITING|WAITING|USING)\s+(.+)$")
_PRESENCE_RE = re.compile(
    r"^\[?([^\]|]+)\]?\s*\|\s*\[?([^\]|]+)\]?\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*(\S+)\s*$"
)
_HEADER_RE = re.compile(r"^([a-z][a-z0-9_]*):[ \t]*(.*)$")
_MARKERLESS_HEADER_FIELDS = {
    "owner",
    "created",
    "host",
    "expires_after",
    "release_condition",
    "mode",
    "purpose",
    "scope",
}
_LEGACY_SECTION_HEADING_RE = re.compile(
    r"^(?:#{1,3}\s*)?(?:\d+[.)]?\s*)?"
    r"(?:Anwesenheit(?:slog)?|Presence|Datei(?:en)?(?:\s+beansprucht)?|"
    r"Files?\s+claimed|File\s+claims?|Tool(?:s)?(?:\s+claimed)?|"
    r"Nachrichten|Messages?|Notes?|Queue|Warteschlange)\s*:?\s*$",
    re.IGNORECASE,
)

_SECTION_ORDER = ("presence", "file_claims", "tool_claims", "messages")
_SECTION_MARKERS = {
    "presence": "# PRESENCE:",
    "file_claims": "# FILE-CLAIMS:",
    "tool_claims": "# TOOL-CLAIMS:",
    "messages": "# MESSAGES:",
}

TEAM_SECTIONS = """
# ============================================================================
# SECTION 1 -- PRESENCE LOG
# Format: [loop-id] | [agent-name] | role | main task | start time
# ============================================================================
# PRESENCE:

# ============================================================================
# SECTION 2 -- FILE / FOLDER CLAIMS + QUEUE
# One line is one atomic bundle. "order" is a file-local FIFO ordinal.
# Format: [agent-name] EDITING order=000001 | path | second/path
#         [agent-name] WAITING order=000002 | path | second/path
# ============================================================================
# FILE-CLAIMS:

# ============================================================================
# SECTION 3 -- TOOL / SOFTWARE / MCP CLAIMS + QUEUE
# One line is one atomic bundle. Shared read-only tools need no claim.
# Format: [agent-name] USING   order=000001 | tool-id | second-tool-id
#         [agent-name] WAITING order=000002 | tool-id | second-tool-id
# ============================================================================
# TOOL-CLAIMS:

# ============================================================================
# SECTION 4 -- MESSAGES / TIPS / LESSONS LEARNED
# Format: [YYYY-MM-DDTHH:MM:SS] [agent-name]: message
# ============================================================================
# MESSAGES:
""".lstrip("\n")


class TeamLockFormatError(ValueError):
    """The on-disk Team Lock cannot be updated without guessing."""


@dataclass(frozen=True)
class ClaimBundle:
    """One atomic request; ``order`` is local queue order, not a claim ID."""

    order: int
    agent: str
    state: str
    resources: tuple[str, ...]


def _validate_text(value: str, name: str, *, max_length: int = 512, forbidden: str = "") -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} muss Text sein")
    value = value.strip()
    if not value:
        raise ValueError(f"{name} darf nicht leer sein")
    if len(value) > max_length:
        raise ValueError(f"{name} ist zu lang (maximal {max_length} Zeichen)")
    if re.search(r"[\x00-\x1f\x7f]", value):
        raise ValueError(f"Ungültige Steuerzeichen in {name}")
    if any(char in value for char in forbidden):
        raise ValueError(f"Ungültiges Trennzeichen in {name}")
    return value


def check_invalid_chars(value: str, name: str) -> None:
    """Compatibility helper retained for callers of the first implementation."""
    _validate_text(value, name)


def normalize_agent_name(name: str) -> str:
    name = _validate_text(name, "Agent-Name", max_length=128)
    if not _AGENT_RE.fullmatch(name):
        raise ValueError(
            "Ungültiger Agent-Name; erlaubt sind Buchstaben, Ziffern sowie . _ @ + -"
        )
    return name


def normalize_path(project_dir: Path, value: str) -> str:
    """Return a contained, project-relative resource path."""
    value = _validate_text(value, "Ressourcenpfad", max_length=1024, forbidden="|;*?[]")
    portable = value.replace("\\", "/")
    windows_path = PureWindowsPath(value)
    if portable.startswith("/") or windows_path.is_absolute() or windows_path.drive:
        raise ValueError(f"Absolute Pfade sind nicht erlaubt: {value}")
    parts = portable.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"Ungültiger oder nicht normalisierter Ressourcenpfad: {value}")

    root = Path(project_dir).resolve(strict=True)
    candidate = (root / Path(*parts)).resolve(strict=False)
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Pfad außerhalb des Projektverzeichnisses: {value}") from exc
    normalized = relative.as_posix()
    if normalized in ("", "."):
        raise ValueError("Der Projektroot selbst ist kein gültiger Ressourcenpfad")
    return normalized


def normalize_tool_id(tool_id: str) -> str:
    tool_id = _validate_text(tool_id, "Tool-ID", max_length=256, forbidden="|")
    if not _TOOL_RE.fullmatch(tool_id):
        raise ValueError(
            "Ungültige Tool-ID; erlaubt sind Buchstaben, Ziffern sowie . _ : / @ + -"
        )
    return tool_id.casefold()


def paths_overlap(path1: str, path2: str) -> bool:
    """Return whether two normalized paths are identical or ancestors."""
    left = path1.replace("\\", "/").strip("/").casefold()
    right = path2.replace("\\", "/").strip("/").casefold()
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


def _resources_overlap(left: Sequence[str], right: Sequence[str], section: str) -> bool:
    if section == "file_claims":
        return any(paths_overlap(a, b) for a in left for b in right)
    return bool({item.casefold() for item in left} & {item.casefold() for item in right})


def _validate_lock_path(lock_path: Path, project_dir: Path | None) -> tuple[Path, Path]:
    lock_path = Path(lock_path)
    project = Path(project_dir) if project_dir is not None else lock_path.parent
    try:
        project = project.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"Projektverzeichnis ist nicht lesbar: {project}") from exc
    if not project.is_dir():
        raise ValueError(f"Projektpfad ist kein Verzeichnis: {project}")

    if not _TEAM_LOCK_RE.fullmatch(lock_path.name):
        raise ValueError(f"Ungültiger Team-Lock-Name: {lock_path.name}")
    try:
        parent = lock_path.parent.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"Team-Lock-Verzeichnis ist nicht lesbar: {lock_path.parent}") from exc
    if parent != project:
        raise ValueError("Team-Lock muss direkt im angegebenen Projektverzeichnis liegen")
    if lock_path.is_symlink():
        raise ValueError("Symbolische Links sind für Team-Locks nicht erlaubt")
    if lock_path.exists() and lock_path.resolve(strict=True).parent != project:
        raise ValueError("Team-Lock verweist außerhalb des Projektverzeichnisses")
    return lock_path, project


def acquire_os_lock(lock_path: Path, timeout: float = 5.0) -> tuple[int, Path]:
    """Acquire a process lock on a persistent sibling guard file."""
    lock_path = Path(lock_path)
    if not _TEAM_LOCK_RE.fullmatch(lock_path.name) or not lock_path.is_file() or lock_path.is_symlink():
        raise ValueError("OS-Guard benötigt eine vorhandene reguläre Team-Lock-Datei")
    guard_path = lock_path.with_name(lock_path.name + ".guard")
    if guard_path.is_symlink():
        raise ValueError("Symbolische Links sind für Team-Lock-Guards nicht erlaubt")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(guard_path), flags, 0o666)
    try:
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"\0")
            os.fsync(fd)
        deadline = time.monotonic() + timeout
        while True:
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                if sys.platform == "win32":
                    import msvcrt

                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fd, guard_path
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"Betriebssystem-Guard für {lock_path.name} konnte nicht erworben werden"
                    )
                time.sleep(0.025)
    except BaseException:
        os.close(fd)
        raise


def release_os_lock(fd: int, guard_path: Path | None = None) -> None:
    """Release a guard. The guard file intentionally remains in place."""
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


@contextmanager
def _guarded(lock_path: Path) -> Iterator[None]:
    fd, guard_path = acquire_os_lock(lock_path)
    try:
        yield
    finally:
        release_os_lock(fd, guard_path)


def _atomic_write(lock_path: Path, content: str) -> None:
    """Replace ``lock_path`` after a flushed, synced unique temporary write."""
    target_mode = stat.S_IMODE(lock_path.stat().st_mode)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{lock_path.name}.",
            suffix=".tmp",
            dir=lock_path.parent,
            delete=False,
        ) as handle:
            temp_name = handle.name
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, target_mode)
        os.replace(temp_name, lock_path)
        temp_name = None

        if hasattr(os, "O_DIRECTORY"):
            directory_fd: int | None = None
            try:
                directory_fd = os.open(str(lock_path.parent), os.O_RDONLY | os.O_DIRECTORY)
                os.fsync(directory_fd)
            except OSError:
                pass
            finally:
                if directory_fd is not None:
                    os.close(directory_fd)
    finally:
        if temp_name is not None:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass


def _section_layout(lines: list[str]) -> dict[str, tuple[int, int]]:
    marker_indices: dict[str, int] = {}
    for section, marker in _SECTION_MARKERS.items():
        matches = [index for index, line in enumerate(lines) if line.strip() == marker]
        if len(matches) != 1:
            raise TeamLockFormatError(
                "Team-Lock benötigt jeden kanonischen Abschnittsmarker genau einmal"
            )
        marker_indices[section] = matches[0]

    ordered = [marker_indices[section] for section in _SECTION_ORDER]
    if ordered != sorted(ordered) or len(set(ordered)) != len(ordered):
        raise TeamLockFormatError("Team-Lock-Abschnitte stehen nicht in kanonischer Reihenfolge")

    layout: dict[str, tuple[int, int]] = {}
    divider = "# " + "=" * 76
    for position, section in enumerate(_SECTION_ORDER):
        start = marker_indices[section] + 1
        if position + 1 == len(_SECTION_ORDER):
            end = len(lines)
        else:
            next_marker = marker_indices[_SECTION_ORDER[position + 1]]
            next_heading = next_marker
            for index in range(next_marker - 1, start - 1, -1):
                if lines[index].strip().startswith("# SECTION "):
                    next_heading = index
                    if index > 0 and lines[index - 1].strip() == divider:
                        next_heading = index - 1
                    break
            end = next_heading
        layout[section] = (start, end)
    return layout


def _team_host_from_name(lock_name: str) -> str:
    """Return the authoritative host (last segment before ``.txt``)."""
    return lock_name[:-4].split(".")[-1]


def _validate_header(content: str, lock_name: str, *, strict_fields: bool) -> None:
    """Validate header syntax and the filename-authoritative Team-Lock host."""
    lines = content.splitlines()
    marker_positions = [
        index
        for index, line in enumerate(lines)
        if line.strip() in set(_SECTION_MARKERS.values())
    ]
    header_end = min(marker_positions, default=len(lines))
    fields: dict[str, str] = {}
    for raw_line in lines[:header_end]:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _HEADER_RE.fullmatch(stripped)
        if not match:
            raise TeamLockFormatError(
                f"Unbekannte Zeile außerhalb kanonischer Abschnitte: {stripped!r}"
            )
        key, value = match.groups()
        if strict_fields and key not in _MARKERLESS_HEADER_FIELDS:
            raise TeamLockFormatError(f"Unbekanntes Team-Lock-Headerfeld: {key}")
        if key in fields:
            raise TeamLockFormatError(f"Doppeltes Team-Lock-Headerfeld: {key}")
        if re.search(r"[\x00-\x1f\x7f]", value):
            raise TeamLockFormatError(f"Ungültige Steuerzeichen im Headerfeld {key}")
        fields[key] = value.strip()

    for required in ("owner", "host"):
        if not fields.get(required):
            raise TeamLockFormatError(f"Erforderliches Team-Lock-Headerfeld fehlt: {required}")
    if "mode" in fields and fields["mode"] not in ("hard", "soft"):
        raise TeamLockFormatError("Team-Lock-Headerfeld mode muss hard oder soft sein")

    expected_host = _team_host_from_name(lock_name)
    if fields["host"].casefold() != expected_host.casefold():
        raise TeamLockFormatError(
            f"Header-Host {fields['host']!r} widerspricht dem Dateinamen-Host {expected_host!r}"
        )


def _ensure_sections(content: str, lock_name: str) -> str:
    lines = content.splitlines()
    markers = set(_SECTION_MARKERS.values())
    marker_count = sum(1 for line in lines if line.strip() in markers)
    if marker_count == 0:
        legacy_heading = next(
            (line.strip() for line in lines if _LEGACY_SECTION_HEADING_RE.fullmatch(line.strip())),
            None,
        )
        if legacy_heading is not None:
            raise TeamLockFormatError(
                f"Alter Team-Lock-Abschnitt {legacy_heading!r} ist nicht eindeutig migrierbar"
            )
        _validate_header(content, lock_name, strict_fields=True)
        return content.rstrip("\r\n") + "\n\n" + TEAM_SECTIONS
    if marker_count != len(_SECTION_MARKERS):
        raise TeamLockFormatError(
            "Team-Lock enthält nur einen Teil der kanonischen Abschnitte"
        )
    _validate_header(content, lock_name, strict_fields=False)
    _section_layout(lines)
    return content


def _normalize_resources(section: str, resources: Sequence[str], project: Path) -> tuple[str, ...]:
    if not resources:
        raise ValueError("Mindestens eine Ressource ist erforderlich")
    if section == "file_claims":
        normalized = tuple(normalize_path(project, item) for item in resources)
    else:
        normalized = tuple(normalize_tool_id(item) for item in resources)
    keys = [item.casefold() for item in normalized]
    if len(keys) != len(set(keys)):
        raise ValueError("Eine Ressource darf innerhalb eines Bundles nur einmal vorkommen")
    return normalized


def _parse_claim_bundles(
    lines: list[str], section: str, project: Path
) -> tuple[list[ClaimBundle], set[int]]:
    expected_active = "EDITING" if section == "file_claims" else "USING"
    parsed: list[tuple[ClaimBundle, bool]] = []
    entry_indices: set[int] = set()

    for index, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        explicit = _EXPLICIT_CLAIM_RE.fullmatch(stripped)
        legacy = None if explicit else _LEGACY_CLAIM_RE.fullmatch(stripped)
        if explicit:
            agent_raw, state, order_raw, resource_raw = explicit.groups()
            resource_parts = tuple(part.strip() for part in resource_raw.split(" | "))
            order = int(order_raw)
            is_legacy = False
        elif legacy:
            agent_raw, state, resource_raw = legacy.groups()
            resource_parts = (resource_raw.strip(),)
            order = len(parsed) + 1
            is_legacy = True
        else:
            raise TeamLockFormatError(
                f"Beschädigte Claim-Zeile in {section}: {stripped!r}"
            )
        if state not in (expected_active, "WAITING"):
            raise TeamLockFormatError(f"Ungültiger Claim-Status {state!r} in {section}")
        if order <= 0:
            raise TeamLockFormatError("Claim-Reihenfolge muss größer als null sein")
        try:
            agent = normalize_agent_name(agent_raw)
            normalized = _normalize_resources(section, resource_parts, project)
        except ValueError as exc:
            raise TeamLockFormatError(f"Ungültige bestehende Claim-Zeile: {exc}") from exc
        parsed.append((ClaimBundle(order, agent, state, normalized), is_legacy))
        entry_indices.add(index)

    if parsed and any(is_legacy for _, is_legacy in parsed):
        if not all(is_legacy for _, is_legacy in parsed):
            raise TeamLockFormatError("Explizite und alte Claim-Zeilen dürfen nicht gemischt werden")
        agents = [bundle.agent.casefold() for bundle, _ in parsed]
        if len(agents) != len(set(agents)):
            raise TeamLockFormatError(
                "Mehrere alte Claim-Zeilen desselben Agenten sind als Bundle mehrdeutig"
            )

    bundles = [bundle for bundle, _ in parsed]
    orders = [bundle.order for bundle in bundles]
    if len(orders) != len(set(orders)):
        raise TeamLockFormatError("Claim-Reihenfolge ist nicht eindeutig")

    active = [bundle for bundle in bundles if bundle.state == expected_active]
    for index, left in enumerate(active):
        for right in active[index + 1 :]:
            if left.agent.casefold() != right.agent.casefold() and _resources_overlap(
                left.resources, right.resources, section
            ):
                raise TeamLockFormatError("Bestehende aktive Claims überlappen sich")
    for index, left in enumerate(bundles):
        for right in bundles[index + 1 :]:
            if left.agent.casefold() == right.agent.casefold() and _resources_overlap(
                left.resources, right.resources, section
            ):
                raise TeamLockFormatError(
                    "Derselbe Agent besitzt überlappende Bundles; Zuordnung ist mehrdeutig"
                )
    return bundles, entry_indices


def _format_bundle(bundle: ClaimBundle) -> str:
    spacing = "   " if bundle.state == "USING" else "  " if bundle.state == "EDITING" else " "
    return (
        f"[{bundle.agent}] {bundle.state}{spacing}order={bundle.order:06d} | "
        + " | ".join(bundle.resources)
    )


def _replace_entries(lines: list[str], entry_indices: set[int], entries: list[str]) -> list[str]:
    if entry_indices:
        insert_at = min(entry_indices)
    else:
        insert_at = len(lines)
        while insert_at > 0 and not lines[insert_at - 1].strip():
            insert_at -= 1

    result: list[str] = []
    inserted = False
    for index, line in enumerate(lines):
        if index == insert_at:
            result.extend(entries)
            inserted = True
        if index not in entry_indices:
            result.append(line)
    if not inserted:
        result.extend(entries)
    return result


def _promote_waiters(bundles: list[ClaimBundle], section: str) -> tuple[list[ClaimBundle], list[str]]:
    active_state = "EDITING" if section == "file_claims" else "USING"
    active = [bundle for bundle in bundles if bundle.state == active_state]
    waiting = sorted((bundle for bundle in bundles if bundle.state == "WAITING"), key=lambda item: item.order)
    promoted: set[int] = set()

    for index, candidate in enumerate(waiting):
        older_waiters = waiting[:index]
        blocked_by_active = any(
            _resources_overlap(candidate.resources, holder.resources, section)
            for holder in active
        )
        blocked_by_older = any(
            older.order not in promoted
            and _resources_overlap(candidate.resources, older.resources, section)
            for older in older_waiters
        )
        if not blocked_by_active and not blocked_by_older:
            promoted.add(candidate.order)
            active.append(replace(candidate, state=active_state))

    updated = [
        replace(bundle, state=active_state)
        if bundle.state == "WAITING" and bundle.order in promoted
        else bundle
        for bundle in bundles
    ]
    agents = [bundle.agent for bundle in waiting if bundle.order in promoted]
    return updated, agents


def _process_claims(
    lines: list[str],
    section: str,
    action: str,
    agent: str,
    resources: tuple[str, ...],
    queue: bool,
    project: Path,
) -> tuple[list[str], bool, str, bool]:
    bundles, entry_indices = _parse_claim_bundles(lines, section, project)
    active_state = "EDITING" if section == "file_claims" else "USING"
    agent_key = agent.casefold()

    if action == "claim":
        for bundle in bundles:
            if bundle.agent.casefold() != agent_key:
                continue
            if tuple(item.casefold() for item in bundle.resources) == tuple(
                item.casefold() for item in resources
            ):
                return lines, True, "Bundle war bereits registriert", False
            if _resources_overlap(bundle.resources, resources, section):
                return lines, False, "Konflikt: eigener überlappender Claim ist bereits vorhanden", False

        active_conflicts = [
            bundle
            for bundle in bundles
            if bundle.state == active_state
            and bundle.agent.casefold() != agent_key
            and _resources_overlap(bundle.resources, resources, section)
        ]
        older_waiters = [
            bundle
            for bundle in bundles
            if bundle.state == "WAITING"
            and _resources_overlap(bundle.resources, resources, section)
        ]
        blockers = active_conflicts + older_waiters
        if blockers and not queue:
            blocker = min(blockers, key=lambda item: item.order)
            return (
                lines,
                False,
                f"Konflikt: älteres Bundle von {blocker.agent} hat Vorrang ({', '.join(blocker.resources)})",
                False,
            )

        state = "WAITING" if blockers else active_state
        next_order = max((bundle.order for bundle in bundles), default=0) + 1
        bundles.append(ClaimBundle(next_order, agent, state, resources))
        rendered = [_format_bundle(bundle) for bundle in sorted(bundles, key=lambda item: item.order)]
        return (
            _replace_entries(lines, entry_indices, rendered),
            True,
            f"Bundle atomar registriert ({state}): {', '.join(resources)}",
            True,
        )

    if action == "release":
        requested = {item.casefold() for item in resources}
        found = False
        remaining: list[ClaimBundle] = []
        for bundle in bundles:
            if bundle.agent.casefold() != agent_key:
                remaining.append(bundle)
                continue
            kept = tuple(item for item in bundle.resources if item.casefold() not in requested)
            if len(kept) != len(bundle.resources):
                found = True
            if kept:
                remaining.append(replace(bundle, resources=kept))

        promoted: list[str] = []
        if found:
            remaining, promoted = _promote_waiters(remaining, section)
        rendered = [_format_bundle(bundle) for bundle in sorted(remaining, key=lambda item: item.order)]
        if not found:
            return lines, True, "Keine passenden eigenen Claims gefunden", False
        message = f"Eigene Claims freigegeben: {', '.join(resources)}"
        if promoted:
            message += "; FIFO-Promotion: " + ", ".join(promoted)
        return _replace_entries(lines, entry_indices, rendered), True, message, True

    raise ValueError(f"Unbekannte Claim-Aktion: {action}")


def _parse_presence(lines: list[str]) -> tuple[list[tuple[int, str]], set[int]]:
    entries: list[tuple[int, str]] = []
    indices: set[int] = set()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _PRESENCE_RE.fullmatch(stripped)
        if not match:
            raise TeamLockFormatError(f"Beschädigte Präsenzzeile: {stripped!r}")
        try:
            agent = normalize_agent_name(match.group(2).strip())
        except ValueError as exc:
            raise TeamLockFormatError(f"Ungültige bestehende Präsenzzeile: {exc}") from exc
        entries.append((index, agent))
        indices.add(index)
    return entries, indices


def _process_presence(lines: list[str], action: str, entry: str) -> tuple[list[str], bool, str, bool]:
    entries, _ = _parse_presence(lines)
    if action == "claim":
        parts = [part.strip() for part in entry.split("|")]
        if len(parts) != 4:
            raise ValueError("Präsenz benötigt Loop-ID, Agent, Rolle und Aufgabe")
        loop_id = _validate_text(parts[0].strip("[] "), "Loop-ID", max_length=128, forbidden="|[]")
        agent = normalize_agent_name(parts[1].strip("[] "))
        role = _validate_text(parts[2], "Rolle", max_length=256, forbidden="|")
        task = _validate_text(parts[3], "Aufgabe", max_length=1024, forbidden="|")
        remove = {index for index, existing in entries if existing.casefold() == agent.casefold()}
        retained = [line for index, line in enumerate(lines) if index not in remove]
        insert_at = len(retained)
        while insert_at > 0 and not retained[insert_at - 1].strip():
            insert_at -= 1
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        retained.insert(insert_at, f"[{loop_id}] | [{agent}] | {role} | {task} | {now}")
        return retained, True, "Präsenz registriert", True
    if action == "release":
        agent = normalize_agent_name(entry)
        remove = {index for index, existing in entries if existing.casefold() == agent.casefold()}
        if not remove:
            return lines, True, "Keine passende Präsenz gefunden", False
        return [line for index, line in enumerate(lines) if index not in remove], True, "Präsenz abgemeldet", True
    raise ValueError(f"Unbekannte Präsenzaktion: {action}")


def _process_message(lines: list[str], action: str, entry: str) -> tuple[list[str], bool, str, bool]:
    if action != "claim":
        raise ValueError(f"Unbekannte Nachrichtenaktion: {action}")
    agent_raw, separator, message_raw = entry.partition(":")
    if not separator:
        raise ValueError("Nachricht benötigt das Format 'agent: text'")
    agent = normalize_agent_name(agent_raw)
    message = _validate_text(message_raw, "Nachricht", max_length=4096)
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    insertion = len(lines)
    while insertion > 0 and not lines[insertion - 1].strip():
        insertion -= 1
    updated = list(lines)
    updated.insert(insertion, f"[{now}] [{agent}]: {message}")
    return updated, True, "Nachricht hinzugefügt", True


def update_team_lock(
    lock_path: Path,
    section_type: str,
    action: str,
    entry_str: str,
    resources: Sequence[str] | None = None,
    queue: bool = False,
    project_dir: Path | None = None,
) -> tuple[bool, str]:
    """Validate and atomically update one Team Lock section.

    Invalid API input raises ``ValueError``. An on-disk ambiguity or a normal
    claim conflict returns ``(False, message)`` without modifying the file.
    """
    lock_path, project = _validate_lock_path(lock_path, project_dir)
    if section_type not in _SECTION_ORDER:
        raise ValueError(f"Unbekannter Team-Lock-Abschnitt: {section_type}")
    if action not in ("claim", "release"):
        raise ValueError(f"Unbekannte Aktion: {action}")
    if not lock_path.is_file():
        return False, f"Team-Lock {lock_path.name} existiert nicht"

    normalized_entry = entry_str
    normalized_resources: tuple[str, ...] = ()
    if section_type in ("file_claims", "tool_claims"):
        normalized_entry = normalize_agent_name(entry_str)
        normalized_resources = _normalize_resources(section_type, resources or (), project)
        if queue and action != "claim":
            raise ValueError("--queue ist nur beim Beanspruchen erlaubt")
    elif resources:
        raise ValueError("Ressourcen sind für diesen Abschnitt nicht erlaubt")

    with _guarded(lock_path):
        try:
            original = lock_path.read_text(encoding="utf-8")
            content = _ensure_sections(original, lock_path.name)
            lines = content.splitlines()
            layout = _section_layout(lines)
            start, end = layout[section_type]
            section_lines = lines[start:end]

            if section_type in ("file_claims", "tool_claims"):
                updated, success, message, changed = _process_claims(
                    section_lines,
                    section_type,
                    action,
                    normalized_entry,
                    normalized_resources,
                    queue,
                    project,
                )
            elif section_type == "presence":
                updated, success, message, changed = _process_presence(
                    section_lines, action, normalized_entry
                )
            else:
                updated, success, message, changed = _process_message(
                    section_lines, action, normalized_entry
                )
        except TeamLockFormatError as exc:
            return False, f"Konflikt: {exc} (fail-closed)"

        if success and (changed or content != original):
            lines[start:end] = updated
            rendered = "\n".join(lines).rstrip("\n") + "\n"
            _atomic_write(lock_path, rendered)
        return success, message


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Atomare Claim-Vergabe für Team-Locks")
    parser.add_argument(
        "action",
        choices=(
            "claim-presence",
            "release-presence",
            "claim-file",
            "release-file",
            "claim-tool",
            "release-tool",
            "add-message",
        ),
    )
    parser.add_argument("project_dir", type=Path)
    parser.add_argument("--lock-name", required=True, help="Team-Lock-Dateiname")
    parser.add_argument("--agent", required=True, help="Agent-Name ohne Leerzeichen")
    parser.add_argument("--role", help="Rolle für claim-presence")
    parser.add_argument("--task", help="Aufgabe für claim-presence")
    parser.add_argument("--loop-id", default="loop-01", help="Loop-ID für claim-presence")
    parser.add_argument("--resource", action="append", help="Pfad oder Tool-ID; mehrfach möglich")
    parser.add_argument("--queue", action="store_true", help="Bei Konflikt atomar als WAITING einreihen")
    parser.add_argument("--msg", help="Nachricht für add-message")
    args = parser.parse_args(argv)

    try:
        project = args.project_dir.resolve(strict=True)
        lock_path = project / args.lock_name
        agent = normalize_agent_name(args.agent)

        if args.action == "claim-presence":
            if args.role is None or args.task is None:
                parser.error("--role und --task werden für claim-presence benötigt")
            entry = f"{args.loop_id} | {agent} | {args.role} | {args.task}"
            success, message = update_team_lock(lock_path, "presence", "claim", entry, project_dir=project)
        elif args.action == "release-presence":
            success, message = update_team_lock(lock_path, "presence", "release", agent, project_dir=project)
        elif args.action in ("claim-file", "release-file"):
            if not args.resource:
                parser.error("--resource wird für Datei-Claims benötigt")
            operation = "claim" if args.action == "claim-file" else "release"
            success, message = update_team_lock(
                lock_path,
                "file_claims",
                operation,
                agent,
                resources=args.resource,
                queue=args.queue,
                project_dir=project,
            )
        elif args.action in ("claim-tool", "release-tool"):
            if not args.resource:
                parser.error("--resource wird für Tool-Claims benötigt")
            operation = "claim" if args.action == "claim-tool" else "release"
            success, message = update_team_lock(
                lock_path,
                "tool_claims",
                operation,
                agent,
                resources=args.resource,
                queue=args.queue,
                project_dir=project,
            )
        else:
            if args.msg is None:
                parser.error("--msg wird für add-message benötigt")
            success, message = update_team_lock(
                lock_path,
                "messages",
                "claim",
                f"{agent}: {args.msg}",
                project_dir=project,
            )
        print(message)
        return 0 if success else 1
    except (OSError, TimeoutError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


__all__ = [
    "TEAM_SECTIONS",
    "TeamLockFormatError",
    "acquire_os_lock",
    "check_invalid_chars",
    "main",
    "normalize_agent_name",
    "normalize_path",
    "normalize_tool_id",
    "paths_overlap",
    "release_os_lock",
    "update_team_lock",
]
