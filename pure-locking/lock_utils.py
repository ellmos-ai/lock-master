r"""
lock_utils.py -- Canonical logic for project locks (LOCK*.txt)

Single source of truth for the LOCK file format and the scope/expiry logic
across all configured roots (see lock_roots.json).

Canonical spec (lifecycle, tiers, scripts): LOCK-SYSTEM.md (same directory).

Convention:
  - LOCK.txt            = entire project locked      (scope = "project")
  - LOCK.<scope>.txt    = only this component locked (scope = "<scope>")
                          free scope name (sub-area/sub-folder),
                          e.g. LOCK.frontend.txt, LOCK.web.txt, LOCK.api.txt
  - Detection regex: ^LOCK(\.[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*)?\.txt$
    Matches: LOCK.txt, LOCK.api.txt, LOCK.team.LAPTOP.txt,
             LOCK.team.frontend.LAPTOP.txt
  - Legacy (deprecated, do not create): TEST.txt / TESTS.txt

File format (one setting per line, stdlib parser, no extra dependency):
  - Lines starting with '#' = comment, blank lines = ignored.
  - Otherwise split on the FIRST ':'; trim key/value; key lowercased.
  Fields:
    owner             (required)  Who holds the lock.
    created           (required)  ISO YYYY-MM-DDTHH:MM (base for expiry).
    expires_after     (optional)  e.g. "24h" / "48h" / "90m". Default = 24h.
    release_condition (optional)  Free-text release condition.
    mode              (optional)  "hard" (default) | "soft".
    purpose           (optional)  Free-text description.
    scope             (optional)  Informational only; AUTHORITATIVE is the filename.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

# Current lock files: LOCK.txt, LOCK.<scope>.txt, LOCK.team.<host>.txt,
# LOCK.team.<scope>.<host>.txt (multi-segment names allowed)
LOCK_RE = re.compile(r"^LOCK(?:\.([A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*))?\.txt$", re.IGNORECASE)
# Legacy locks (still recognised, but marked as deprecated)
LEGACY_LOCK_NAMES = ("TEST.txt", "TESTS.txt")

DEFAULT_EXPIRES = timedelta(hours=24)

# Duration strings: "24h", "48h", "90m", "30s", "2d"
_DURATION_RE = re.compile(r"^\s*(\d+)\s*([smhd])\s*$", re.IGNORECASE)
_DURATION_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}


TYPE_MARKERS = frozenset({"user", "team", "community", "condition", "until"})
BOOLEAN_WORDS = frozenset({"and", "or", "not"})


def _has_reserved_leftovers(segments: list[str]) -> bool:
    """True if scope segments still contain a reserved marker or a boolean word.

    A lock name carries exactly ONE type marker, in the first segment. A second
    one, or a word like 'and'/'or'/'not-...', means the name was written as if
    lock types could be combined in the filename — they cannot. Such a name is
    ambiguous and must not be read as the weaker of the two readings.

    Only whole segments count: a scope like 'publication-and-claim-edits' is one
    segment and stays perfectly valid.
    """
    for segment in segments:
        low = segment.lower()
        if low in TYPE_MARKERS or low in BOOLEAN_WORDS:
            return True
        if low.startswith("not-") and low[4:] in TYPE_MARKERS:
            return True
    return False


def lock_name_parts(name: str) -> dict[str, str | None | bool] | None:
    """Split a lock filename into type, scope and host.

    The file location determines the project/room. Scope only means the
    intra-project component; the markers
    'team'/'community'/'user'/'condition'/'until' and the host segment are
    not part of the scope.

    Returns None if the name is not a lock filename at all.
    """
    if name in LEGACY_LOCK_NAMES:
        return {"lock_type": "legacy", "scope": "project", "host": None,
                "is_legacy": True, "ambiguous": False}

    m = LOCK_RE.match(name)
    if not m:
        return None

    raw_scope = m.group(1)
    if not raw_scope:
        return {"lock_type": "exclusive", "scope": "project", "host": None,
                "is_legacy": False, "ambiguous": False}

    segments = raw_scope.split(".")
    marker = segments[0].lower()
    if marker in {"team", "community"}:
        host = segments[-1] if len(segments) >= 2 else None
        scope_segments = segments[1:-1] if len(segments) >= 3 else []
        scope = ".".join(scope_segments) if scope_segments else "project"
        return {"lock_type": "team", "scope": scope, "host": host,
                "is_legacy": False,
                "ambiguous": _has_reserved_leftovers(scope_segments)}
    if marker in {"user", "condition", "until"}:
        scope_segments = segments[1:]
        scope = ".".join(scope_segments) if scope_segments else "project"
        return {"lock_type": marker, "scope": scope, "host": None,
                "is_legacy": False,
                "ambiguous": _has_reserved_leftovers(scope_segments)}
    return {"lock_type": "exclusive", "scope": raw_scope, "host": None,
            "is_legacy": False,
            "ambiguous": _has_reserved_leftovers(segments[1:])}


def lock_type_from_name(name: str) -> str:
    """Determine the lock type from the filename.

    'user' for LOCK.user.*, 'condition' for LOCK.condition.*, 'until' for
    LOCK.until.*, 'team' for LOCK.team.* and LOCK.community.* (deprecated),
    'legacy' for TEST.txt/TESTS.txt, 'exclusive' for all other LOCK*.txt."""
    parts = lock_name_parts(name)
    if parts is None:
        return "exclusive"
    return str(parts["lock_type"])


def scope_from_name(name: str) -> str | None:
    """Derive scope from filename.

    LOCK.txt                      -> 'project'
    LOCK.api.txt                  -> 'api'
    LOCK.team.LAPTOP.txt          -> 'project'  (team lock, whole project)
    LOCK.team.frontend.LAPTOP.txt -> 'frontend' (team lock, scoped)

    Returns None if not a lock filename.
    Team locks are identified by a 'team.' prefix in the segment string;
    use is_team_lock() to distinguish them from exclusive locks.
    """
    m = LOCK_RE.match(name)
    if not m:
        return None
    segments = m.group(1)
    if not segments:
        return "project"
    parts = segments.split(".")
    # Team lock: LOCK.team.<host>.txt  or  LOCK.team.<scope>.<host>.txt
    if parts[0].lower() == "team":
        if len(parts) == 2:
            return "project"
        # middle parts are the scope; last part is host
        return ".".join(parts[1:-1])
    # User lock: LOCK.user.txt (project) or LOCK.user.<scope>.txt (component).
    if parts[0].lower() == "user":
        scope_segments = parts[1:]
        return ".".join(scope_segments) if scope_segments else "project"
    # Condition lock: LOCK.condition.txt / LOCK.condition.<scope>.txt.
    if parts[0].lower() == "condition":
        scope_segments = parts[1:]
        return ".".join(scope_segments) if scope_segments else "project"
    return segments


def is_team_lock(name: str) -> bool:
    """Return True if filename is a Team Lock (LOCK.team.*.txt)."""
    m = LOCK_RE.match(name)
    if not m or not m.group(1):
        return False
    return m.group(1).lower().startswith("team.")


def is_user_lock(name: str) -> bool:
    """Return True for LOCK.user(.<scope>).txt — a user-owned full lock.

    User locks are removed ONLY by the user (manually or via the watcher GUI);
    agents and the stale-cleanup (prune) never touch them, even when nominally
    expired."""
    m = LOCK_RE.match(name)
    if not m or not m.group(1):
        return False
    return m.group(1).split(".")[0].lower() == "user"


def is_condition_lock(name: str) -> bool:
    """Return True for LOCK.condition(.<scope>).txt — a condition-based lock.

    Condition locks (since v1.4.0) do NOT expire by time; they remain active
    until the condition described in the 'release_condition' field is met.
    The stale-cleanup (prune) and bulk-unlock never touch them. Unlike user
    locks, ANY agent may remove a condition lock once it has verifiably
    fulfilled the release condition (and documents that fulfilment when
    removing the lock). Typical use: operation-scoped locks via the
    'operations:' field (e.g. 'operations: publish-release'), leaving all
    other work on the project unrestricted."""
    m = LOCK_RE.match(name)
    if not m or not m.group(1):
        return False
    return m.group(1).split(".")[0].lower() == "condition"


def is_ambiguous_lock(name: str) -> bool:
    """True if the filename reads as if lock types were combined.

    Lock types cannot be combined in the filename: a name carries exactly one
    type marker, in its first segment. Names like
    'LOCK.until.and.condition.<scope>.txt' were previously read as a plain
    until lock with the odd scope 'and.condition.<scope>' — the second marker
    silently ignored. Whoever wrote such a name intending "both conditions"
    got the WEAKER of the two locks, without any warning.

    Ambiguous locks are fail-closed: is_expired() never releases them, so the
    mistake can only lock too long, never too little. Combine a deadline with
    a condition through the FIELDS instead ('not_before' plus
    'release_condition'), not through the name.
    """
    parts = lock_name_parts(name)
    return bool(parts and parts.get("ambiguous"))


def is_until_lock(name: str) -> bool:
    """Return True for LOCK.until(.<scope>).txt — a deadline lock.

    An until lock holds until an ABSOLUTE point in time that the file states
    in its mandatory 'not_before' field, rather than for a relative duration
    ('expires_after', default 24h) or until a free-text condition is met.

    Motivating case: competition judging holds. A submission must stay frozen
    until the winners are announced — a fixed calendar moment weeks away, not
    a duration anyone wants to express as '900h'. Before this type existed the
    need was improvised twice with ad-hoc fields ('LOCK_FALLS_NOT_BEFORE',
    'REQUIRED_EVENT') that no tool ever evaluated.

    Two properties that no other type combines:
      * it DOES expire by time — but at an absolute moment, so a guard stops
        watching that project by itself once the moment has passed;
      * it is still protected from automatic deletion, so the file survives
        for the human decision and the evidence obligation recorded in
        'release_condition'.
    """
    m = LOCK_RE.match(name)
    if not m or not m.group(1):
        return False
    return m.group(1).split(".")[0].lower() == "until"


def lock_not_before(lock_path: Path) -> datetime | None:
    """Absolute release moment from the 'not_before' field of an until lock.

    Accepts an ISO timestamp with or without a UTC offset, e.g.
    '2026-10-08T12:00-07:00' or '2026-10-08 12:00'. An offset-aware value is
    converted to local naive time so it can be compared with datetime.now().
    A value without an offset is read as local time.

    Returns None when the field is missing or unparsable — callers MUST treat
    that as 'never expires' (fail-closed), never as 'expired'.
    """
    raw = parse_lock_file(lock_path).get("not_before")
    if not raw:
        return None
    candidate = raw.strip()
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        parsed = _parse_created(candidate)
    if parsed is None:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def is_protected_lock(name: str) -> bool:
    """True if the lock is protected from automatic removal.

    Protected are user locks (removed only by the user), condition locks
    (released by condition, not by time) and until locks (released by an
    absolute moment). Protected locks are never deleted by prune or
    bulk-unlock actions.

    NOTE: protection from deletion and expiry by time are two different
    things, and until locks are the first type to separate them. A user or
    condition lock never expires by time; an until lock DOES expire, at the
    moment stated in its 'not_before' field — but its file still survives,
    because the human decision and the evidence obligation in
    'release_condition' outlive the deadline. See is_expired."""
    return (is_user_lock(name) or is_condition_lock(name)
            or is_until_lock(name) or is_ambiguous_lock(name))


def locked_operations(lock_path: Path) -> list[str]:
    """Read the 'operations:' field as a list of locked operations.

    Empty list = the lock is not operation-scoped (it locks the whole area
    according to its mode/type). Non-empty list = ONLY these operations are
    locked; all other work on the project remains allowed."""
    raw = parse_lock_file(lock_path).get("operations", "")
    return [op.strip() for op in raw.split(",") if op.strip()]


def is_prunable(lock_path: Path, now: datetime | None = None) -> bool:
    """True if the stale-cleanup may remove this lock.
    Condition: expired AND not protected (no user lock) AND not legacy."""
    name = lock_path.name
    if name in LEGACY_LOCK_NAMES:
        return False
    if is_protected_lock(name):
        return False
    return is_expired(lock_path, now)


def is_lock_file(name: str) -> bool:
    return LOCK_RE.match(name) is not None


def parse_lock_file(lock_path: Path) -> dict[str, str]:
    """Parse a LOCK file into a key:value dict (keys lowercased)."""
    data: dict[str, str] = {}
    try:
        text = lock_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return data
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        data[key.strip().lower()] = value.strip()
    return data


def normalize_lock_fields(data: dict[str, str]) -> dict[str, str]:
    """Map alternative field names onto canonical names.

    Only safe 1:1 mappings. Started/Expires fields are NOT mapped because
    third-party formats may use timezone suffixes and absolute timestamps
    that _parse_created/parse_duration cannot handle. Returns a copy;
    original keys are preserved."""
    result = dict(data)
    field_map = {
        "task": "purpose",
    }
    for old_key, new_key in field_map.items():
        if old_key in result and new_key not in result:
            result[new_key] = result[old_key]
    return result


def parse_duration(value: str | None) -> timedelta:
    """'24h'/'90m'/... -> timedelta. Defaults to 24h if missing or unparseable."""
    if not value:
        return DEFAULT_EXPIRES
    m = _DURATION_RE.match(value)
    if not m:
        return DEFAULT_EXPIRES
    amount = int(m.group(1))
    unit = _DURATION_UNITS[m.group(2).lower()]
    return timedelta(**{unit: amount})


def _parse_created(value: str | None) -> datetime | None:
    """Parse ISO timestamp from 'created' field (T or space separator,
    seconds optional)."""
    if not value:
        return None
    candidate = value.strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(candidate, fmt)
        except ValueError:
            continue
    return None


def lock_created_and_expiry(lock_path: Path) -> tuple[datetime, timedelta, str]:
    """Return (created, expires_after, source).
    source = 'header' if created came from the file, else 'mtime' (fallback)."""
    data = parse_lock_file(lock_path)
    created = _parse_created(data.get("created"))
    expires = parse_duration(data.get("expires_after"))
    if created is not None:
        return created, expires, "header"
    mtime = datetime.fromtimestamp(lock_path.stat().st_mtime)
    return mtime, expires, "mtime"


def is_expired(lock_path: Path, now: datetime | None = None) -> bool:
    """Time-based expiry.

    Until locks expire at the ABSOLUTE moment in their 'not_before' field.
    A missing or unparsable value is fail-closed: the lock never expires, so
    a typo can only lock too long, never release too early.

    User locks and condition locks NEVER expire by time: user locks hold
    until the user removes them, condition locks hold until their
    release_condition is fulfilled and the lock is removed. (Fix in v1.4.0:
    previously a nominally expired user lock could drop out of active_locks()
    even though the spec defines it as still valid.)

    Ambiguous names (see is_ambiguous_lock) never expire either: a name that
    reads as if two lock types were combined must not be resolved to the
    weaker of its readings.

    All other locks expire after the relative 'expires_after' duration
    (default 24h) counted from 'created'."""
    now = now or datetime.now()
    if is_ambiguous_lock(lock_path.name):
        return False
    if is_until_lock(lock_path.name):
        moment = lock_not_before(lock_path)
        if moment is None:
            return False
        return now > moment
    if is_protected_lock(lock_path.name):
        return False
    created, expires, _ = lock_created_and_expiry(lock_path)
    return now > created + expires


def lock_host(lock_path: Path) -> str | None:
    """Machine/hostname from the 'host' field of the LOCK file.

    Identifies which system currently holds the lock (cross-system
    coordination). Returns None when the field is absent (backwards
    compatible)."""
    return parse_lock_file(lock_path).get("host") or None


def find_lock_files(project_dir: Path, include_legacy: bool = True):
    """Find all lock files in a project root directory.
    Returns: list of (name, scope, is_legacy)."""
    results = []
    for hit in sorted(project_dir.glob("*.txt")):
        if not hit.is_file():
            continue
        scope = scope_from_name(hit.name)
        if scope is not None:
            results.append((hit.name, scope, False))
    if include_legacy:
        for legacy in LEGACY_LOCK_NAMES:
            for hit in project_dir.glob(legacy):
                if hit.is_file():
                    results.append((hit.name, "project", True))
    return sorted(set(results))


def active_locks(project_dir: Path, now: datetime | None = None):
    """Non-expired lock files. Returns list of (name, scope, is_legacy).
    Legacy locks (TEST.txt/TESTS.txt) have no expiry format -> always treated
    as active (stale cleanup only applies to LOCK*.txt)."""
    now = now or datetime.now()
    out = []
    for name, scope, is_legacy in find_lock_files(project_dir):
        lock_path = project_dir / name
        if is_legacy:
            out.append((name, scope, is_legacy))
            continue
        if not is_expired(lock_path, now):
            out.append((name, scope, is_legacy))
    return out


# ---------------------------------------------------------------------------
# Team lock section parsing and absolute expiry (used by the watcher)
# ---------------------------------------------------------------------------

_SECTION_RE = {
    "presence": re.compile(
        r"^(?:#{1,3}\s*)?(?:\d+\.?\s*)?(?:Anwesenheit(?:slog)?|Presence|PRESENCE:?)",
        re.IGNORECASE,
    ),
    "file_claims": re.compile(
        r"^(?:#{1,3}\s*)?(?:\d+\.?\s*)?(?:Datei|Files?\s+claimed|FILE-CLAIMS:?)", re.IGNORECASE
    ),
    "tool_claims": re.compile(
        r"^(?:#{1,3}\s*)?(?:\d+\.?\s*)?(?:Tool|Tools?\s+claimed|TOOL-CLAIMS:?)", re.IGNORECASE
    ),
    "messages": re.compile(
        r"^(?:#{1,3}\s*)?(?:\d+\.?\s*)?(?:Nachrichten|Notes|Messages|MESSAGES:?)",
        re.IGNORECASE,
    ),
    "queue": re.compile(
        r"^(?:#{1,3}\s*)?(?:\d+\.?\s*)?(?:Queue|Warteschlange)", re.IGNORECASE
    ),
}


def parse_team_lock_sections(raw_content: str) -> dict | None:
    """Parse the structured sections of a team/community lock.

    Returns a dict with the sections, or None when the content has no
    recognisable team sections. Each section is a list of strings
    (bullet points / entries)."""
    if not raw_content:
        return None

    sections: dict[str, list[str]] = {
        "presence": [],
        "file_claims": [],
        "tool_claims": [],
        "messages": [],
        "queue": [],
    }

    current_section: str | None = None

    for line in raw_content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        new_section = None
        for sec_name, pattern in _SECTION_RE.items():
            if pattern.match(stripped):
                new_section = sec_name
                break

        if new_section is not None:
            current_section = new_section
            continue

        if current_section is None:
            continue

        if stripped.startswith("#"):
            continue

        entry = stripped.lstrip("- ").strip()
        if entry:
            sections[current_section].append(entry)

    has_content = any(entries for entries in sections.values())
    return sections if has_content else None


def compute_expires_at(lock_path: Path) -> str | None:
    """Absolute expiry time as an ISO string, or None if not determinable."""
    if is_protected_lock(lock_path.name):
        return None
    try:
        created, expires, _ = lock_created_and_expiry(lock_path)
        return (created + expires).isoformat(timespec="seconds")
    except (OSError, ValueError):
        return None
