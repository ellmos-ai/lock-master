r"""
lock_utils.py -- Canonical logic for project locks (LOCK*.txt)

Single source of truth for the LOCK file format and the scope/expiry logic
across all configured roots (see lock_roots.json).

Canonical spec (lifecycle, tiers, scripts): LOCK-SYSTEM.md (same directory).

Convention:
  - LOCK.txt            = entire project locked      (scope = "project")
  - LOCK.<scope>.txt    = named component (scope = "<scope>"); the free
                          name alone does not establish a filesystem path.
                          New scoped locks carry explicit protected_paths,
                          old ones conservatively cover their directory,
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
    created           (required)  ISO-8601 timestamp with UTC offset; writers
                                  use UTC Z (base for relative expiry). Legacy
                                  naive values are local wall time and fail
                                  closed when ambiguous or nonexistent.
    expires_after     (optional)  e.g. "24h" / "48h" / "90m". Default = 24h.
    release_condition (optional)  Free-text release condition.
    release_mode      (optional, only meaningful on an until lock that ALSO
                                  sets release_condition)  "all" (default,
                                  backward-compatible) | "any". "all" =
                                  deadline AND condition must both be met --
                                  since the condition is free text the tool
                                  cannot check it, so it does not auto-release
                                  on the deadline alone (fail-closed). "any" =
                                  the deadline alone releases it; the
                                  condition side is then left to a human/agent.
                                  An invalid value falls back to "all".
                                  T-20260903-476807738.
    mode              (optional)  "hard" (default) | "soft".
    purpose           (optional)  Free-text description.
    scope             (optional)  Informational only; AUTHORITATIVE is the filename.
    paths_version     (optional)  "1" for explicit relative protected_paths.
    protected_paths   (optional)  |-separated paths inside the lock directory.
    fence             (optional)  Grant number (epoch microseconds) written by
                                  lock_create.py for cooperative detection. The
                                  holder records it at acquisition and
                                  re-checks it before every write, so a holder
                                  that stalled past its TTL finds out that the
                                  lease moved on instead of writing blind --
                                  see fence_status(). Absent = a lock from
                                  before fencing; handled exactly as before.
"""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
from datetime import datetime, timedelta, timezone
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


def is_user_lock(target: str | Path, data: dict[str, str] | None = None) -> bool:
    """Return True for LOCK.user(.<scope>).txt — a user-owned full lock.

    Recognized by:
      - Filename: LOCK.user(.<scope>).txt
      - Content / Fields (fail-closed, T-20260926-592074763):
        * A header or line matching LOCK.user (e.g. LOCK.user.claude-code@ASUS-GEI)
        * 'type' / 'lock_type' field is 'user'
        * 'removable_by' field is 'user'
        * 'owner' field begins with 'LOCK.user' (e.g. 'owner: LOCK.user.claude-code')

    User locks are removed ONLY by the user (manually or via the watcher GUI);
    agents and the stale-cleanup (prune) never touch them, even when nominally
    expired."""
    name = target.name if isinstance(target, Path) else Path(target).name
    m = LOCK_RE.match(name)
    if m and m.group(1) and m.group(1).split(".")[0].lower() == "user":
        return True

    if data is None:
        lock_file = None
        if isinstance(target, Path):
            lock_file = target
        elif isinstance(target, str) and (os.path.sep in target or "/" in target or Path(target).is_file()):
            p = Path(target)
            if p.is_file():
                lock_file = p
        if lock_file is not None and lock_file.is_file():
            data = parse_lock_file(lock_file)

    if data is not None:
        if (data.get("created_by") or "").strip().lower() == "bulk":
            return False
        ltype = (data.get("type") or data.get("lock_type") or data.get("lock-type") or "").strip().lower()
        if ltype == "user":
            return True
        if (data.get("removable_by") or "").strip().lower() == "user":
            return True
        owner = (data.get("owner") or "").strip().lower()
        if owner.startswith("lock.user"):
            return True

    return False


def is_condition_lock(target: str | Path, data: dict[str, str] | None = None) -> bool:
    """Return True for LOCK.condition(.<scope>).txt — a condition-based lock.

    Condition locks (since v1.4.0) do NOT expire by time; they remain active
    until the condition described in the 'release_condition' field is met.
    The stale-cleanup (prune) and bulk-unlock never touch them. Unlike user
    locks, ANY agent may remove a condition lock once it has verifiably
    fulfilled the release condition (and documents that fulfilment when
    removing the lock). Typical use: operation-scoped locks via the
    'operations:' field (e.g. 'operations: publish-release'), leaving all
    other work on the project unrestricted."""
    name = target.name if isinstance(target, Path) else Path(target).name
    m = LOCK_RE.match(name)
    if m and m.group(1) and m.group(1).split(".")[0].lower() == "condition":
        return True
    if data is not None:
        ltype = (data.get("type") or data.get("lock_type") or "").strip().lower()
        if ltype == "condition":
            return True
    return False


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


def is_until_lock(target: str | Path, data: dict[str, str] | None = None) -> bool:
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
    name = target.name if isinstance(target, Path) else Path(target).name
    m = LOCK_RE.match(name)
    if m and m.group(1) and m.group(1).split(".")[0].lower() == "until":
        return True
    if data is not None:
        ltype = (data.get("type") or data.get("lock_type") or "").strip().lower()
        if ltype == "until":
            return True
    return False


def lock_not_before(lock_path: Path,
                    data: dict[str, str] | None = None) -> datetime | None:
    """Absolute release moment from the 'not_before' field of an until lock.

    Accepts an ISO timestamp with or without a UTC offset, e.g.
    '2026-10-08T12:00-07:00' or '2026-10-08 12:00'. An offset-aware value is
    converted to local naive time so it can be compared with datetime.now().
    A value without an offset is read as local time.

    Returns None when the field is missing or unparsable — callers MUST treat
    that as 'never expires' (fail-closed), never as 'expired'.
    """
    fields = parse_lock_file(lock_path) if data is None else data
    raw = fields.get("not_before")
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


def is_protected_lock(target: str | Path, data: dict[str, str] | None = None) -> bool:
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
    name = target.name if isinstance(target, Path) else Path(target).name
    return (is_user_lock(target, data=data) or is_condition_lock(target, data=data)
            or is_until_lock(target, data=data) or is_ambiguous_lock(name))


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
    if is_protected_lock(lock_path):
        return False
    return is_expired(lock_path, now)


def is_lock_file(name: str) -> bool:
    return LOCK_RE.match(name) is not None


def normalize_lock_fields(data: dict[str, str]) -> dict[str, str]:
    """Map alternative field names onto canonical names.

    Only safe 1:1 mappings. Started/Expires fields are NOT mapped because
    third-party formats may use timezone suffixes and absolute timestamps
    that _parse_created/parse_duration cannot handle. Returns a copy;
    original keys are preserved."""
    result = dict(data)
    field_map = {
        "task": "purpose",
        "reason": "purpose",
        "grund": "purpose",
        "begruendung": "purpose",
        "begründung": "purpose",
        "gesetzt": "created",
        "datum": "created",
        "date": "created",
        "lock_type": "type",
        "lock-type": "type",
    }
    for old_key, new_key in field_map.items():
        if old_key in result and new_key not in result:
            result[new_key] = result[old_key]
    return result


def _parse_lock_text(text: str) -> dict[str, str]:
    """The parser behind parse_lock_file, on text that was already read.

    Last value wins on duplicate keys. The push guard mirrors this; a regex
    taking the FIRST match made the two disagree about the same file."""
    data: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            # Check for header/directive line without colon, e.g. "LOCK.user.claude-code@ASUS-GEI" or "LOCK.user"
            m_user = re.match(r"^LOCK\.user(?:\.([A-Za-z0-9_@.-]+))?$", stripped, re.IGNORECASE)
            if m_user:
                data.setdefault("type", "user")
                if m_user.group(1):
                    data.setdefault("owner", m_user.group(1))
            elif stripped.lower().startswith("lock.user"):
                data.setdefault("type", "user")
            continue
        key, value = stripped.split(":", 1)
        data[key.strip().lower()] = value.strip()
    return normalize_lock_fields(data)


def parse_lock_file(lock_path: Path) -> dict[str, str]:
    """Parse a LOCK file into a key:value dict (keys lowercased).

    An unreadable file yields {} -- indistinguishable from an empty one. Any
    caller that must not treat a read failure as "no fields" reads the text
    itself and hands it to _parse_lock_text (see fence_status)."""
    try:
        text = lock_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    return _parse_lock_text(text)


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


def _local_wall_to_utc(value: datetime) -> datetime | None:
    """Resolve naive local wall time only when it maps to one instant.

    ``timestamp()`` uses the host's historical local timezone rules. Testing
    both folds distinguishes a repeated DST hour from an ordinary hour; a
    round trip distinguishes a nonexistent spring-forward hour. Either
    ambiguity fails closed because the original writer timezone is unknown.
    """
    naive = value.replace(tzinfo=None, fold=0)
    timestamps: list[float] = []
    try:
        for fold in (0, 1):
            timestamp = naive.replace(fold=fold).timestamp()
            round_trip = datetime.fromtimestamp(timestamp).replace(fold=0)
            if round_trip != naive:
                return None
            timestamps.append(timestamp)
    except (OSError, OverflowError, ValueError):
        return None
    if not timestamps or timestamps[0] != timestamps[1]:
        return None
    try:
        return datetime.fromtimestamp(timestamps[0], timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None


def _as_utc(value: datetime) -> datetime | None:
    """Convert an instant or an unambiguous local-naive value to UTC."""
    try:
        if value.tzinfo is None or value.utcoffset() is None:
            return _local_wall_to_utc(value)
        return value.astimezone(timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def format_utc_iso(value: datetime, *, timespec: str = "seconds") -> str:
    """Format a timestamp as UTC ISO-8601 with an explicit ``Z`` suffix."""
    utc_value = _as_utc(value)
    if utc_value is None:
        raise ValueError("timestamp is not an unambiguous instant")
    return utc_value.isoformat(timespec=timespec).replace("+00:00", "Z")


def created_utc_iso(value: str | datetime | None = None) -> str:
    """Create a canonical UTC ``created`` value, rejecting invalid wall times."""
    if value is None:
        parsed = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        parsed = _as_utc(value)
    else:
        parsed = _parse_created(value)
    if parsed is None:
        raise ValueError("created timestamp is invalid, ambiguous, or nonexistent")
    return format_utc_iso(parsed)


def _parse_created(value: str | None) -> datetime | None:
    """Parse ``created`` and return an aware UTC instant.

    ISO offsets and ``Z`` are absolute. Historical naive values are interpreted
    as local wall time using the reader host's timezone history; ambiguous,
    nonexistent, and malformed values return ``None``.
    """
    if value is None or not value.strip():
        return None
    candidate = value.strip()
    if candidate[-1:] in {"Z", "z"}:
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    return _as_utc(parsed)


def lock_created_and_expiry(lock_path: Path,
                            data: dict[str, str] | None = None
                            ) -> tuple[datetime, timedelta, str]:
    """Return (created_utc, expires_after, source).

    source is ``header`` for a valid timestamp, ``mtime`` only when the field
    is absent, or ``invalid`` when a present timestamp cannot be resolved. For
    ``invalid``, the returned mtime is diagnostic only and never an expiry base.

    `data` lets a caller pass fields it has already parsed, so a decision can
    be made from ONE read of the file instead of several. Re-reading looks
    harmless and is not: between two reads the file can be replaced, and the
    caller then judges a state that never existed.
    """
    data = parse_lock_file(lock_path) if data is None else data
    has_created = "created" in data
    created = _parse_created(data.get("created"))
    try:
        expires = parse_duration(data.get("expires_after"))
    except (OverflowError, ValueError):
        expires = timedelta(0)
    if created is not None:
        return created, expires, "header"
    try:
        mtime = datetime.fromtimestamp(lock_path.stat().st_mtime, timezone.utc)
    except FileNotFoundError:
        raise
    except OSError:
        return datetime.now(timezone.utc), expires, "invalid"
    return mtime, expires, "invalid" if has_created else "mtime"

def is_expired(lock_path: Path, now: datetime | None = None,
               data: dict[str, str] | None = None) -> bool:
    """Return whether a time-based lock has expired.

    Offset-aware ``created`` values are compared as UTC instants. Historical
    naive values mean local wall time on the reading host and are accepted only
    when that wall time is unique under the host's historical DST rules. A
    missing field retains the mtime fallback; a present invalid or ambiguous
    field is visible and keeps the lock active (fail-closed).

    Until locks still use their absolute ``not_before`` contract. User,
    condition, and ambiguous-name locks remain non-expiring. Relative TTLs are
    elapsed UTC durations. Overflow in a valid lock's ``expires_after`` makes
    the claim expired, as before.
    """
    if is_ambiguous_lock(lock_path.name):
        return False
    now_utc = datetime.now(timezone.utc) if now is None else _as_utc(now)
    if is_until_lock(lock_path, data=data):
        moment = lock_not_before(lock_path, data)
        if moment is None or now_utc is None:
            return False
        now_local = now_utc.astimezone().replace(tzinfo=None)
        if now_local <= moment:
            return False
        fields = parse_lock_file(lock_path) if data is None else data
        if not fields.get("release_condition"):
            return True
        return (fields.get("release_mode") or "all").strip().lower() == "any"
    if is_protected_lock(lock_path, data=data):
        return False

    try:
        created, expires, source = lock_created_and_expiry(lock_path, data)
    except (OSError, ValueError):
        return True
    if source == "invalid" or now_utc is None:
        return False

    raw_exp = (data.get("expires_after") if data is not None
               else parse_lock_file(lock_path).get("expires_after"))
    if raw_exp:
        m = _DURATION_RE.match(raw_exp)
        if m:
            try:
                amount = int(m.group(1))
                unit = _DURATION_UNITS[m.group(2).lower()]
                _ = timedelta(**{unit: amount})
            except (OverflowError, ValueError):
                return True

    try:
        return now_utc > created + expires
    except (OverflowError, ValueError):
        return True

def lock_host(lock_path: Path) -> str | None:
    """Machine/hostname from the 'host' field of the LOCK file.

    Identifies which system currently holds the lock (cross-system
    coordination). Returns None when the field is absent (backwards
    compatible)."""
    return parse_lock_file(lock_path).get("host") or None


# ---------------------------------------------------------------------------
# Fencing tokens -- a grant number that survives a lost lease
# ---------------------------------------------------------------------------
# A TTL lock alone cannot stop the classic failure (Kleppmann, "How to do
# distributed locking"): holder A stalls past its TTL, prune or holder B takes
# the lock, A wakes up and keeps writing. A never learns it was displaced.
#
# The mitigation is a grant number handed out with every acquisition for
# cooperative detection. The holder remembers it, and checks it against the
# lock on disk before writing. A different number means: the lease moved on.
#
# WHY WALL-CLOCK MICROSECONDS AND NOT A COUNTER FILE.
# A counter needs an atomic increment shared by all writers. Over a cloud
# folder with 30 s to 5 min sync latency there is none: two hosts both read 5
# and both write 6. A rename-created sequence file has the same hole -- the
# rename is atomic on each host separately, which is exactly not what a
# counter needs. Wall-clock microseconds avoid a shared sequence file, but
# monotonicity between hosts depends on clock agreement: `new_fence(previous=...)`
# steps past a known displaced grant on takeover, but clock skew or steps
# backwards can still invert ordering if a host acquires without previous context.
#
# This borrows no new assumption: `expires_after` is ALREADY compared against
# each host's local clock, and contested.py ALREADY orders claims by `created`
# with a host tiebreak. If clocks drift far enough to break fencing, expiry
# itself was broken first.
#
# WHAT THIS DOES AND DOES NOT PROMISE -- read this before relying on it.
#
# This is COOPERATIVE DETECTION, not enforcement. It is deliberately the
# smaller of the two claims, because the larger one is not deliverable on this
# medium and an earlier version of this file made it anyway.
#
# It detects, for a writer that asks:
#   the lease was handed to someone else    (higher number)
#   the lease ran out under it              (own grant expired)
#   the lock file went away                 (pruned or deleted)
#
# It does NOT prevent a write by a holder that has already lost the lock.
# Kleppmann's point is precisely the pause BETWEEN the last check and the
# write: A checks, stalls, loses the lease, B acquires and writes, A's
# already-approved write lands on top. Every writer checked; the data is still
# corrupted. Closing that needs the STORAGE to validate the token as part of
# the write -- an atomic compare-and-set on the target. A directory synced
# through a cloud folder offers no such operation, so no amount of care on
# this side produces the guarantee. A check-then-write wrapper would shrink
# the window, not close it: its own re-check and rollback have the same gap.
#
# Practical consequence for callers -- this is how you keep the window small:
#   check immediately before the write, not at the top of the function
#   re-check after anything that can block (network, sync, subprocess, sleep)
#   keep TTLs close to the real work, so a stall expires rather than lingers
#   where a true guarantee is required, use a store that offers a conditional
#     write -- a database row, not a file in a synced folder
#
# Second limit: the numbers are wall-clock based, so ordering between hosts is
# only as good as clock agreement. That is not a new assumption -- expiry
# already compares `created` against each host's local clock -- but it is a
# real one, and a clock stepped backwards can produce a number that does not
# rise. `new_fence(previous=...)` covers the takeover case; it cannot cover a
# host that was never told what it is replacing.
#
# Verified by adversarial review (Codex, 2026-09-20, PR #8): the reproduction
# `pause_between_check_and_write` shows the scenario above end to end. The
# earlier claim here -- that a writer which checks "can no longer be wrong
# about holding the lock" -- was false and has been removed rather than
# softened.

_FENCE_RE = re.compile(r"^\d{1,19}$")

FENCE_HELD = "held"
FENCE_LOST = "lost"
FENCE_UNKNOWN = "unknown"


# Last number handed out in THIS process. The system clock is not fine-grained
# enough to rely on alone: Windows ticks at roughly 15.6 ms, so two grants a few
# lines apart read the same microsecond value. Measured in CI on windows-latest
# 3.11/3.12, where --force right after an acquisition produced an IDENTICAL
# number -- which would have left the displaced holder reading "held".
_last_fence = 0


def new_fence(previous: int | None = None, now: datetime | None = None) -> int:
    """Grant number for a NEW acquisition: epoch microseconds, forced upward.

    `previous` is the number the area carries right now, if any. Passing it is
    what makes the guarantee hold ACROSS processes: a takeover (--force) reads
    the number it displaces and steps past it, instead of trusting that the
    clock has moved on since. Without that, the one case fencing exists for --
    replacing a stalled holder -- is the case where it can silently fail.

    The wall clock still sets the value whenever it is ahead, so numbers stay
    comparable between hosts (see the block above for why time and not a
    counter). The floor only ever nudges, never resets.
    """
    global _last_fence
    candidate = int((now or datetime.now()).timestamp() * 1_000_000)
    floor = max(_last_fence, previous or 0) + 1
    fence = max(candidate, floor)
    _last_fence = fence
    return fence


def lock_fence(lock_path: Path) -> int | None:
    """Grant number from the 'fence' field, or None when absent/unparsable.

    None means "this file predates fencing" -- NOT "fence 0". Callers must
    keep the two apart; that distinction is what makes old locks keep working.
    """
    return _fence_from(parse_lock_file(lock_path))


def _fence_from(data: dict[str, str]) -> int | None:
    """lock_fence() on already-parsed fields."""
    raw = (data.get("fence") or "").strip()
    if not _FENCE_RE.match(raw):
        return None
    return int(raw)


def fence_status(lock_path: Path, my_fence: int | None,
                 now: datetime | None = None) -> tuple[str, str]:
    """Do I still hold the grant I acquired? Returns (status, reason).

    This is the single gate every writer should pass before touching the
    locked area -- one guard where all callers route through, rather than one
    per caller.

    'unknown' only when the caller never recorded a fence (pre-fencing
    holder); then the old, unchecked behaviour applies and nothing changes.
    Everything else is fail-closed: anything that is not provably still my
    grant is reported as 'lost'.
    """
    if my_fence is None:
        return FENCE_UNKNOWN, "caller recorded no fence (pre-fencing holder)"

    # ONE read for the whole verdict. Reading the number and the expiry
    # separately meant judging two different states: replacing the file
    # between the two reads produced "held" for a holder that a single
    # consistent read reports as lost. An unreadable file is 'lost', never a
    # silent fallback -- parse_lock_file() swallows OSError and returns {},
    # which the expiry path would have turned into a 24h default and thus
    # into a POSITIVE ownership claim built out of a failure.
    try:
        data = _parse_lock_text(lock_path.read_text(encoding="utf-8", errors="replace"))
    except OSError as exc:
        return FENCE_LOST, f"lock file is gone or unreadable: {lock_path.name} ({exc.__class__.__name__})"

    current = _fence_from(data)
    if current is None:
        return FENCE_LOST, (
            f"{lock_path.name} carries no fence -- it was re-created by a "
            "writer that does not know about fencing"
        )
    if current > my_fence:
        return FENCE_LOST, f"lease moved on: fence {current} > own fence {my_fence}"
    if current < my_fence:
        return FENCE_LOST, (
            f"fence went backwards: {current} < own fence {my_fence} -- "
            "clock jump or a restored copy; refusing either way"
        )
    try:
        expired = is_expired(lock_path, now, data=data)
    except (OverflowError, ValueError):
        expired = True
    if expired:
        return FENCE_LOST, (
            "own grant expired (TTL passed) -- the file is still here but the "
            "lease is not; renew before continuing"
        )
    return FENCE_HELD, f"fence {my_fence} still current"


def find_lock_files(project_dir: Path, include_legacy: bool = True):
    """Find all lock files in a project root directory.
    Returns: list of (name, scope, is_legacy)."""
    results = []
    # Enumerate real entry names: literal globs on Windows Python 3.10/3.11
    # can construct the returned path from the pattern's case instead.
    for hit in sorted(project_dir.glob("*")):
        scope = scope_from_name(hit.name)
        is_legacy_name = include_legacy and hit.name in LEGACY_LOCK_NAMES
        if scope is None and not is_legacy_name:
            continue
        try:
            is_file = hit.is_file()
        except OSError:
            is_file = True
        if not is_file:
            continue
        if scope is not None:
            results.append((hit.name, scope, False))
        elif is_legacy_name:
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
    """Absolute expiry as UTC ISO-8601, or None when it is not determinable."""
    if is_protected_lock(lock_path):
        return None
    try:
        created, expires, source = lock_created_and_expiry(lock_path)
        if source == "invalid":
            return None
        return format_utc_iso(created + expires)
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Worktree-aware single-directory check (T-20260903-592302105)
# ---------------------------------------------------------------------------
# A lock can live in the repo clone (C:\_Local_DEV\repos\<repo>\LOCK.*.txt)
# while the actual work happens in a `git worktree` (C:\_Local_DEV\worktrees\
# <name>) -- its own directory with no LOCK file of its own. Anyone who only
# checks `LOCK*.txt` locally (stage 1, LOCK-SYSTEM.md) finds nothing and
# wrongly considers the area free, even though the lock applies to the same
# underlying work. `git rev-parse --git-common-dir` reliably leads from the
# worktree to the shared .git and thus to the main clone.

class GitPathResolutionError(OSError):
    """A linked worktree's Git paths cannot be trusted for a lock decision."""


def _linked_worktree_marker(path: Path) -> bool:
    for directory in (path, *path.parents):
        marker = directory / ".git"
        if marker.is_file():
            return True
        if marker.is_dir():
            return False
    return False


def _git_directory(path: Path, *args: str) -> Path | None:
    """Decode Git's path bytes with filesystem semantics, not the console codepage."""
    cwd = Path(path)
    while not cwd.is_dir() and cwd != cwd.parent:
        cwd = cwd.parent
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", *args],
            capture_output=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        if _linked_worktree_marker(Path(path)):
            raise GitPathResolutionError(f"cannot resolve linked worktree: {path}") from exc
        return None
    if result.returncode != 0:
        if _linked_worktree_marker(Path(path)):
            raise GitPathResolutionError(f"cannot resolve linked worktree: {path}")
        return None
    if not result.stdout.strip():
        raise GitPathResolutionError(f"Git returned an empty path for {path}")
    try:
        directory = Path(os.fsdecode(result.stdout.strip()))
        if not directory.is_absolute():
            directory = cwd / directory
        directory = directory.resolve()
        if not directory.is_dir():
            raise GitPathResolutionError(
                f"Git path does not exist: {ascii(str(directory))}")
        return directory
    except (OSError, UnicodeError) as exc:
        if isinstance(exc, GitPathResolutionError):
            raise
        raise GitPathResolutionError(f"cannot decode or resolve Git path for {path}") from exc


def git_common_dir(path: Path) -> Path | None:
    """`git rev-parse --git-common-dir`, preserving Unicode filesystem paths.

    None means the path is not a Git tree. A broken linked worktree raises
    GitPathResolutionError so callers cannot mistake it for a free path.
    """
    return _git_directory(path, "--git-common-dir")


def git_worktree_top_dir(path: Path) -> Path | None:
    """Return Git's existing worktree root, or None outside a Git tree."""
    return _git_directory(path, "--show-toplevel")


def worktree_main_dir(path: Path) -> Path | None:
    """Main-clone directory for a git worktree.

    A linked worktree has no `.git` directory of its own, only a `.git`
    file pointing at the shared `.git` in the main clone; that file's
    parent directory is the main clone. Returns None if `path` is not a
    git directory OR `path` already IS the main clone itself (then there
    is nothing additional to check)."""
    top = git_worktree_top_dir(path)
    if top is None:
        return None
    if (top / ".git").is_dir():
        return None
    if not (top / ".git").is_file():
        raise GitPathResolutionError(f"Git worktree marker is missing: {top}")
    common = git_common_dir(path)
    if common is None:
        raise GitPathResolutionError(f"Git common directory is missing: {path}")
    main_dir = common.parent
    if not (main_dir / ".git").is_dir():
        raise GitPathResolutionError(f"Git main clone is not verifiable: {main_dir}")
    try:
        if main_dir.resolve() == Path(path).resolve():
            return None
    except OSError:
        pass
    return main_dir


def protected_relative_paths(lock_path: Path) -> list[Path] | None:
    """Versioned, explicit write areas; None means conservative whole-root lock.

    Older locks and protected user/condition/until locks retain their existing
    broad effect. Malformed metadata also fails closed.
    """
    data = parse_lock_file(lock_path)
    if is_protected_lock(lock_path, data=data):
        return None
    if data.get("paths_version") != "1" or not data.get("protected_paths"):
        return None
    # The general key:value parser strips value whitespace. For path metadata
    # that would silently change a real filename (" leading" -> "leading").
    # Require the writer's unambiguous one-space field prefix and no spaces
    # around the path separator; any other spelling remains a broad lock.
    try:
        fields = [line.split(":", 1)[1] for line in lock_path.read_text(encoding="utf-8").splitlines()
                  if ":" in line and line.split(":", 1)[0].strip().lower() == "protected_paths"]
    except OSError:
        return None
    if fields != [" " + data["protected_paths"]]:
        return None
    paths = []
    for raw in data["protected_paths"].split("|"):
        if not raw or raw != raw.strip() or "\\" in raw or ":" in raw or "\x00" in raw:
            return None
        path = Path(raw)
        if path.is_absolute() or any(part in ("", ".", "..") for part in raw.split("/")):
            return None
        try:
            resolved = (lock_path.parent / path).resolve()
            resolved.relative_to(lock_path.parent.resolve())
        except (OSError, ValueError):
            return None
        paths.append(path)
    return paths or None


def lock_applies_to_target(lock_path: Path, target: Path) -> bool:
    """Check actual directory overlap, including a root query of a subarea."""
    paths = protected_relative_paths(lock_path)
    if paths is None:
        return True
    root = lock_path.parent.resolve()
    target = target.resolve()
    for path in paths:
        area = (root / path).resolve()
        # normcase handles Windows case-insensitive names; Path equality alone
        # is not portable for synthetic Windows paths tested on POSIX.
        a, b = os.path.normcase(str(area)), os.path.normcase(str(target))
        if a == b or a.startswith(b + os.sep) or b.startswith(a + os.sep):
            return True
        # macOS may use a case-insensitive volume despite os.name == 'posix'.
        # Existing paths can be compared by filesystem identity rather than
        # guessing the volume's case rules from the operating system.
        try:
            if area.exists() and any(os.path.samefile(area, p) for p in (target, *target.parents)
                                     if p.exists()):
                return True
            if target.exists() and any(os.path.samefile(target, p) for p in (area, *area.parents)
                                       if p.exists()):
                return True
        except OSError:
            pass
    return False


def active_locks_affecting_target(source_dir: Path, target: Path,
                                  now: datetime | None = None):
    """Active locks in one directory that overlap a mapped target directory."""
    return [(name, scope, legacy, source_dir)
            for name, scope, legacy in active_locks(source_dir, now)
            if legacy or lock_applies_to_target(source_dir / name, target)]


def active_locks_for_path(path: Path, now: datetime | None = None):
    """Worktree-aware lock check for one directory -- the stage-1
    check ("OBSERVE") before starting work at exactly this location.

    Returns (name, scope, is_legacy, source_dir) for overlapping locks in
    `path` and its ancestors, plus mapped locks of a worktree's main clone.
    A caller that only checks `active_locks(path)` locally never sees a
    worktree's main-clone locks -- that is the exact gap from
    T-20260903-592302105.

    Covers the reported case (one worktree, one main clone); nested
    worktrees/submodules are not handled separately."""
    path = Path(path).resolve()
    out = []
    for ancestor in (path, *path.parents):
        out.extend(active_locks_affecting_target(ancestor, path, now))
    main_dir = worktree_main_dir(path)
    if main_dir is not None and main_dir.is_dir():
        try:
            top = git_worktree_top_dir(path)
            if top is None:
                raise GitPathResolutionError(f"Git worktree root is missing: {path}")
            mapped = main_dir / path.relative_to(top)
        except (OSError, ValueError):
            mapped = main_dir  # uncertainty must not hide a main-clone lock
        for ancestor in (mapped, *mapped.parents):
            out.extend(active_locks_affecting_target(ancestor, mapped, now))
    return out


# ---------------------------------------------------------------------------
# Git hook guards (T-20260906-910508487)
# ---------------------------------------------------------------------------
# lock_scan --check-dir only ever looked at LOCK*.txt files. A directory can
# report "free" there and still be blocked at push time by a git hook the
# lock system never sees -- found three times in the same OneDrive-shared
# BACH .git instance (stale Build-Week-Judging pre-push embargo hooks left
# behind after the 2026-08-26 lift: WORKSTATION-LG 2026-08-31, ASUS-GEI's
# OneDrive mirror 2026-09-06). "Free" means "no LOCK file", not "pushable".

KNOWN_GUARD_HOOKS = ("pre-push", "pre-commit", "pre-receive")

# Case-insensitive substrings identifying the Build-Week-Judging embargo
# hook lineage (archived at
# .SYNC/_archive/2026-08-26-build-week-hold-released/pre-push-hooks/).
EMBARGO_HOOK_SIGNATURES = (
    "build week judging",
    "buildweek-no-push",
    "operator competition embargo",
)


def _first_hint_line(content: str) -> str:
    """First non-shebang, non-empty line of a hook script, truncated -- used
    as a short "what is this hook for" hint in --check-dir output."""
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#!"):
            continue
        return line[:160]
    return "(empty)"


def git_hook_guards(path: Path) -> list[dict]:
    """Detect active git push/commit guard hooks for `path`.

    Uses `git rev-parse --git-path hooks`, which resolves exactly the
    directory git itself will use for hook execution -- including a
    `core.hooksPath` override and per-worktree config extensions. This is
    deliberately NOT a naive `git_common_dir(path) / "hooks"` join: if
    `core.hooksPath` is configured but the target directory does not exist
    on this host (found empirically: an OneDrive-shared .git/config whose
    hooksPath was hardcoded to another host's user profile), git runs NO
    hooks here at all -- reported below as its own structural entry
    (hook=None) instead of silently returning an empty list that would look
    identical to "no hooks configured".

    Fail-soft: returns [] if `path` is not a git working tree, git is
    missing, or the call fails -- a lock/guard check must never abort
    because git itself is unavailable; it just cannot say anything about
    hooks in that case."""
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--path-format=absolute", "--git-path", "hooks"],
            capture_output=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        raw_hooks = os.fsdecode(result.stdout.strip()) if result.returncode == 0 else ""
    except (OSError, UnicodeError, subprocess.SubprocessError):
        return []
    if not raw_hooks:
        return []

    hooks_dir = Path(raw_hooks)
    if not hooks_dir.is_dir():
        return [{
            "hook": None,
            "path": str(hooks_dir),
            "hint": "core.hooksPath (or default hooks dir) does not exist on "
                    "this host -- git runs no hooks here",
            "embargo": False,
        }]

    guards: list[dict] = []
    for name in KNOWN_GUARD_HOOKS:
        hook_path = hooks_dir / name
        if not hook_path.is_file():
            continue
        try:
            content = hook_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            content = ""
        guards.append({
            "hook": name,
            "path": str(hook_path),
            "hint": _first_hint_line(content),
            "embargo": any(sig in content.lower() for sig in EMBARGO_HOOK_SIGNATURES),
        })
    return guards


# ---------------------------------------------------------------------------
# Twin resolution: clone <-> cloud mirror (T-20260913-785936980)
# ---------------------------------------------------------------------------
# The two-trees rule says a lock at the clone OR at its cloud-synced mirror
# binds equally. active_locks_for_path() resolved only the worktree direction,
# so the mirror twin of a clone stayed invisible.
#
# That is more than an inconvenience, because LOCK.user.* is deliberately NOT
# versioned: a FRESHLY CLONED repository cannot contain it at all. Whoever
# checks only the clone reads a state out of an empty spot. On 2026-09-10 that
# is exactly what happened -- a host that had just re-cloned both repositories
# recorded "the user-held lock is gone", while the lock sat untouched at the
# mirror path and the competition it guarded was still running.
#
# Resolution uses the DECLARED relationship that already exists,
# REPO.pointer.json (field local_locator) -- no guessing from names:
#   direction A  mirror -> clone : the pointer sits in the checked directory.
#   direction B  clone -> mirror : via the host-specific twin index that
#                                  lock_scan.py --write-cache writes alongside
#                                  the caches, from the very directories the
#                                  scan already walks.
#
# FAIL-CLOSED: if the checked path lies under a configured clone_root and the
# index is missing or unreadable, the result is UNDETERMINED -- not "free".
# That exact case overran the judging hold. If twin_resolution is not
# configured at all (another host, no mirror), this is "not applicable" rather
# than an error: unavailable is not empty.

REPO_POINTER_NAME = "REPO.pointer.json"
TWIN_INDEX_NAME = "TWIN-INDEX.<HOST>.json"
TWIN_INDEX_SCHEMA = "ellmos-lock-twin-index-v2"


def twin_index_host() -> str | None:
    """Return the local host identity; an unknown identity cannot own an index."""
    raw = os.environ.get("COMPUTERNAME") or platform.node()
    return raw.strip() or None


def twin_index_path(twin_config: dict | None) -> Path | None:
    """Resolve the same host-specific index path for writers and API readers."""
    raw = (twin_config or {}).get("index_path")
    host = twin_index_host()
    if not raw or not host:
        return None
    safe_host = re.sub(r"[^A-Za-z0-9_-]", "_", host)
    if not safe_host:
        return None
    text = str(raw).replace("{host}", safe_host)
    text = re.sub(r"%COMPUTERNAME%", lambda _m: safe_host, text,
                  flags=re.IGNORECASE)
    path = Path(os.path.expanduser(os.path.expandvars(text)))
    return path if path.is_absolute() else None


def read_repo_pointer(directory: Path) -> dict | None:
    """The REPO.pointer.json of a directory, or None if absent/unreadable."""
    f = Path(directory) / REPO_POINTER_NAME
    try:
        if not f.is_file():
            return None
        data = json.loads(f.read_text(encoding="utf-8"))
        return data if _pointer_fields_valid(data) else None
    except (OSError, ValueError):
        return None


# REPO.pointer.json exists in more than one shape in the wild. Measured on a
# live tree: 3 of 29 pointers used neither field of the documented shape, and
# treating them as "unreadable" would have fail-closed three perfectly healthy
# repositories -- a guard that cries wolf stops being read. So accept every
# shape that names the same two things, and reserve "unreadable" for a pointer
# that truly cannot be parsed or says nothing about its clone.
_CLONE_PATH_FIELDS = ("windows_default", "canonical_path", "local_clone",
                      "clone_path", "source_of_truth")
_REPO_NAME_FIELDS = ("repo_name", "project", "module", "name")
_REPO_ID_FIELDS = ("repo_id", "canonical_remote", "git_remote", "remote",
                   "repository")


def _pointer_fields_valid(pointer: object) -> bool:
    """Reject malformed known locator fields without rejecting legacy shapes."""
    if not isinstance(pointer, dict):
        return False
    if "no_mirror" in pointer and not isinstance(pointer["no_mirror"], bool):
        return False
    if ("mirror_mode" in pointer and pointer["mirror_mode"] is not None
            and not isinstance(pointer["mirror_mode"], str)):
        return False
    locator = pointer.get("local_locator")
    if locator is not None and not isinstance(locator, dict):
        return False
    for source in (pointer, locator or {}):
        fields = (*_CLONE_PATH_FIELDS, *_REPO_NAME_FIELDS, *_REPO_ID_FIELDS)
        if any(key in source and source[key] is not None
               and not isinstance(source[key], str) for key in fields):
            return False
    return True


def _pointer_is_no_mirror(pointer: dict | None) -> bool:
    """True when a REPO.pointer.json explicitly declares that no cloud mirror exists."""
    if not isinstance(pointer, dict):
        return False
    if pointer.get("no_mirror") is True:
        return True
    mode = str(pointer.get("mirror_mode") or "").strip().lower()
    return mode in ("no-mirror", "no_mirror", "none")


def _pointer_clone_path(pointer: dict) -> Path | None:
    if not _pointer_fields_valid(pointer):
        return None
    sources = (pointer.get("local_locator") or {}, pointer)
    for src in sources:
        for field in _CLONE_PATH_FIELDS:
            raw = src.get(field)
            if raw:
                return Path(raw)
    return None


def _pointer_repo_name(pointer: dict) -> str | None:
    if not _pointer_fields_valid(pointer):
        return None
    sources = (pointer.get("local_locator") or {}, pointer)
    for src in sources:
        for field in _REPO_NAME_FIELDS:
            name = src.get(field)
            if name:
                return str(name)
    for key in _REPO_ID_FIELDS:
        raw = str(pointer.get(key) or "")
        if raw:
            tail = raw.rstrip("/").split("/")[-1]
            if tail.endswith(".git"):
                tail = tail[:-4]
            if tail:
                return tail
    # Last resort: the declared clone path names the repository too.
    clone = _pointer_clone_path(pointer)
    return clone.name if clone else None


def _path_key(p: Path) -> str:
    """Comparable form of a path: absolute, normalized, case-insensitive.

    Windows paths differ in case and separators without differing in meaning;
    an index keyed on the raw string would miss the very entry it holds."""
    try:
        return str(Path(os.path.expandvars(str(p))).resolve()).lower()
    except OSError:
        return str(p).lower()


def twin_dirs_for_path(path: Path, twin_config: dict | None):
    """Twin directories of `path` in the other tree.

    Returns (dirs, status). status:
      "ok"                 - resolved (dirs may be empty: no twin declared)
      "not-applicable"     - no twin_resolution configured, or path outside
      "index-missing"      - path is under a clone_root but no readable index
                             -> FAIL-CLOSED, the caller must not report "free"
      "pointer-unreadable" - a pointer cannot be read or verified
      "pointer-unresolved" - a pointer declares an absent clone
      "index-unresolved"  - stale, foreign or invalid index data
      "index-unmapped"    - no declared twin or explicit no-mirror fact
    """
    path = Path(path)
    if not twin_config:
        return [], "not-applicable"

    # Direction A: the checked directory is itself the mirror twin.
    try:
        has_pointer = (path / REPO_POINTER_NAME).is_file()
    except OSError:
        return [], "pointer-unreadable"
    if has_pointer:
        pointer = read_repo_pointer(path)
        if pointer is None:
            return [], "pointer-unreadable"
        if not _pointer_is_no_mirror(pointer):
            clone = _pointer_clone_path(pointer)
            if clone is None:
                return [], "pointer-unreadable"
            return ([clone], "ok") if clone.is_absolute() and clone.is_dir() else (
                [], "pointer-unresolved")

    # Direction B: does the path live under a clone root?
    clone_roots = []
    for raw in twin_config.get("clone_roots", []):
        try:
            clone_roots.append(Path(os.path.expandvars(str(raw))).resolve())
        except OSError:
            continue
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    if not any(resolved == r or r in resolved.parents for r in clone_roots):
        return ([], "ok") if has_pointer else ([], "not-applicable")

    index_path = twin_index_path(twin_config)
    if index_path is None:
        return [], "index-missing"
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], "index-missing"

    def valid_map(value):
        return (isinstance(value, dict)
                and all(isinstance(key, str) and isinstance(paths, list)
                        and all(isinstance(item, str) for item in paths)
                        for key, paths in value.items()))

    def valid_str_list(value):
        return (value is None
                or (isinstance(value, list)
                    and all(isinstance(item, str) and bool(item.strip()) for item in value)))

    if (not isinstance(index, dict)
            or index.get("schema") != TWIN_INDEX_SCHEMA
            or index.get("producer_host") != twin_index_host()
            or not all(valid_map(index.get(key)) for key in (
                "by_repo_name", "by_clone_path", "unreadable_pointers_by_dirname"))
            or not valid_str_list(index.get("no_mirror"))
            or not valid_str_list(twin_config.get("no_mirror"))):
        return [], "index-unresolved"

    no_mirror_paths: set[str] = set()
    no_mirror_names: set[str] = set()
    for raw_item in [*(index.get("no_mirror") or []), *(twin_config.get("no_mirror") or [])]:
        item = raw_item.strip()
        if not item:
            continue
        if "/" in item or "\\" in item or ":" in item:
            no_mirror_paths.add(_path_key(Path(item)))
        else:
            no_mirror_names.add(item.lower())
            no_mirror_paths.add(_path_key(Path(item)))

    # Walk UP to the clone root, not just the given directory. A lock at the
    # repository root binds everything below it, so asking about
    # <clone>/src/thing must find the twin of <clone>. Looking at the last path
    # segment alone made every call from a subdirectory report "free" while the
    # twin was locked -- found in review, T-20260913-715231627.
    by_name = index.get("by_repo_name", {})
    by_path = index.get("by_clone_path", {})
    unreadable = index.get("unreadable_pointers_by_dirname", {})
    for ancestor in (resolved, *resolved.parents):
        if not any(ancestor == r or r in ancestor.parents for r in clone_roots):
            break
        if ancestor.name in unreadable:
            return [], "pointer-unreadable"
        # The declared clone path wins over the name: it is unambiguous even
        # when the folder on disk carries a different name than repo_name.
        # Both maps can name mirrors of this clone. Ignoring extra name hits
        # could hide a lock when one cached list is incomplete.
        hits = list(dict.fromkeys([
            *(by_path.get(_path_key(ancestor)) or []),
            *(by_name.get(ancestor.name) or []),
        ]))
        if not hits:
            if (_path_key(ancestor) in no_mirror_paths
                    or (ancestor.parent in clone_roots
                        and ancestor.name.lower() in no_mirror_names)):
                return [], "ok"
            continue
        dirs = [Path(h) for h in hits]
        for directory in dirs:
            pointer = (read_repo_pointer(directory)
                       if directory.is_absolute() and directory.is_dir() else None)
            declared = _pointer_clone_path(pointer) if pointer else None
            repo_name = _pointer_repo_name(pointer) if pointer else None
            if declared is None:
                return [], "index-unresolved"
            if declared.is_absolute():
                if _path_key(declared) != _path_key(ancestor):
                    return [], "index-unresolved"
            elif os.name != "nt" and re.match(r"^[A-Za-z]:[\\/]", str(declared)):
                if not repo_name or repo_name.lower() != ancestor.name.lower():
                    return [], "index-unresolved"
            else:
                return [], "index-unresolved"
        # The twin of an ancestor covers this path through inheritance; the
        # matching subpath below the twin is checked too, so a scoped lock
        # sitting deeper on the mirror side is not missed either.
        rel = resolved.relative_to(ancestor)
        out = list(dirs)
        if rel != Path("."):
            # A counterpart subdirectory may not exist yet. Keep its logical
            # path so a root lock bound to another subpath is not reported as
            # overlapping simply because only the twin root was returned.
            out += [d / rel for d in dirs]
        return out, "ok"
    return [], "index-unmapped"


def _expand_manifest_path(raw: str, host: str) -> Path:
    text = str(raw).replace("{host}", host)
    text = re.sub(r"%COMPUTERNAME%", lambda _m: host, text, flags=re.IGNORECASE)
    return Path(os.path.expanduser(os.path.expandvars(text)))


def collect_declared_no_mirror(twin_config: dict | None) -> list[str]:
    """Collect explicit no-mirror declarations from `twin_config` (`no_mirror`
    entries and `no_mirror_manifests` such as `.SYNC/<slot>/repos.json` entries
    carrying `"no_mirror": true` or `"mirror_mode": "no-mirror"`)."""
    if not isinstance(twin_config, dict):
        return []
    host = twin_index_host() or "unknown-host"
    out: list[str] = []

    def add(val: object) -> None:
        if isinstance(val, str) and val.strip():
            s = val.strip()
            if s not in out:
                out.append(s)

    raw_list = twin_config.get("no_mirror")
    if isinstance(raw_list, list):
        for item in raw_list:
            if isinstance(item, str) and item.strip():
                expanded = _expand_manifest_path(item.strip(), host)
                if "/" in item or "\\" in item or ":" in item:
                    add(_path_key(expanded))
                else:
                    add(item.strip())

    manifests = twin_config.get("no_mirror_manifests")
    if isinstance(manifests, list):
        for raw_manifest in manifests:
            if not isinstance(raw_manifest, str) or not raw_manifest.strip():
                continue
            mpath = _expand_manifest_path(raw_manifest.strip(), host)
            try:
                data = json.loads(mpath.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            for entry in data.get("repos") or []:
                if not isinstance(entry, dict):
                    continue
                is_no_mirror = (
                    entry.get("no_mirror") is True
                    or entry.get("mirror") is False
                    or str(entry.get("mirror_mode") or "").strip().lower()
                    in ("no-mirror", "no_mirror", "none")
                )
                if not is_no_mirror:
                    continue
                if isinstance(entry.get("path"), str) and entry["path"].strip():
                    add(_path_key(_expand_manifest_path(entry["path"].strip(), host)))
                if isinstance(entry.get("name"), str) and entry["name"].strip():
                    add(entry["name"].strip())
    return out


def build_twin_index(directories, twin_config: dict | None = None) -> dict:
    """Collect every REPO.pointer.json in `directories` into an index
    repo_name -> [twin directories], plus explicit `no_mirror` declarations.

    Written by lock_scan.py during the full scan (--write-cache): that scan
    already walks exactly these directories, so the index costs no second pass.
    """
    host = twin_index_host()
    if host is None:
        raise ValueError("twin index requires a known host identity")
    by_name: dict[str, list[str]] = {}
    by_clone_path: dict[str, list[str]] = {}
    unreadable: dict[str, list[str]] = {}
    no_mirror: list[str] = collect_declared_no_mirror(twin_config)

    def add_no_mirror(val: str) -> None:
        if val and val not in no_mirror:
            no_mirror.append(val)

    for d in directories:
        d = Path(d)
        try:
            if not (d / REPO_POINTER_NAME).is_file():
                continue
        except OSError:
            unreadable.setdefault(d.name, [])
            if str(d) not in unreadable[d.name]:
                unreadable[d.name].append(str(d))
            continue
        pointer = read_repo_pointer(d)
        if pointer is not None and _pointer_is_no_mirror(pointer):
            clone = _pointer_clone_path(pointer)
            if clone is not None:
                add_no_mirror(_path_key(clone))
            name = _pointer_repo_name(pointer)
            if name:
                add_no_mirror(name)
            add_no_mirror(_path_key(d))
            continue
        # The clone folder on disk may be named differently from repo_name
        # (checked out under another name, or a second working copy). Looking up
        # by name alone then finds nothing and the check reports "free" while the
        # twin is locked. So index the DECLARED clone path as well and prefer it.
        clone = _pointer_clone_path(pointer) if pointer is not None else None
        if clone is not None:
            key = _path_key(clone)
            by_clone_path.setdefault(key, [])
            if str(d) not in by_clone_path[key]:
                by_clone_path[key].append(str(d))
        name = _pointer_repo_name(pointer) if pointer is not None else None
        if not name:
            # A pointer is there but unreadable. Do NOT skip it: that would
            # drop this very repository out of the index, and --check-dir would
            # call its clone "free" again while the twin may be locked -- the
            # same fail-open path this feature closes, one level down.
            # Findable on lookup by directory name.
            unreadable.setdefault(d.name, [])
            if str(d) not in unreadable[d.name]:
                unreadable[d.name].append(str(d))
            continue
        by_name.setdefault(name, [])
        if str(d) not in by_name[name]:
            by_name[name].append(str(d))
    result = {
        "schema": TWIN_INDEX_SCHEMA,
        "producer_host": host,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "by_repo_name": by_name,
        "by_clone_path": by_clone_path,
        "unreadable_pointers_by_dirname": unreadable,
    }
    if no_mirror:
        result["no_mirror"] = no_mirror
    return result
