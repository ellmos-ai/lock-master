r"""
lock_scan.py -- Read-only overview of all active project locks

Lists all active (non-expired) LOCK*.txt files across all roots configured in
lock_roots.json: path, scope, owner, created, time remaining until expiry.
Legacy TEST.txt/TESTS.txt are listed as active (no expiry format).

Read-only by default: without --write-cache nothing is written. With
--write-cache, only the derived LOCK-CACHE.md artefact(s) are written
(LOCK*.txt files themselves are never modified).

Usage:
  python lock_scan.py
  python lock_scan.py --json
  python lock_scan.py --write-cache
  python lock_scan.py --roots-file <path>

--write-cache writes cache(s) as defined in lock_roots.json ("caches" key).
Each cache entry:
  { "name": "system-wide",  "path": "/path/to/LOCK-CACHE.md" }
  { "name": "my-workspace", "path": "/path/to/workspace/LOCK-CACHE.md",
    "filter_prefix": "/path/to/workspace" }   <- optional prefix filter

Canonical spec: LOCK-SYSTEM.md (same directory).
Format/expiry logic: lock_utils.py.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import lock_utils

DEFAULT_ROOTS_FILE = Path(__file__).resolve().parent / "lock_roots.json"

# An unchanged cache is rewritten at least this often, so its "As of:" line
# stays a usable freshness signal for readers (sync_mirror accepts 180 min).
CACHE_REFRESH_SECONDS = 30 * 60
CHECK_DIR_MAX_DESCENDANTS = 1024
CHECK_DIR_MAX_DESCENDANTS_CEILING = 1_000_000


def _is_directory_redirect(path: Path) -> bool:
    """Detect directory links without treating OneDrive cloud placeholders as links.

    Python 3.10/3.11 lack Path.is_junction(). Windows reparse *name
    surrogates* redirect path lookup; ordinary cloud tags do not. An unreadable
    or unclassified reparse tag cannot prove that a subtree is complete.
    """
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    if not attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
        return False
    tag = getattr(info, "st_reparse_tag", None)
    return tag is None or bool(tag & 0x20000000)  # IO_REPARSE_TAG_NAME_SURROGATE


def _query_redirect_or_error(target: Path, config: dict) -> str | None:
    """Reject redirects that remove a lexical ancestor from the physical path.

    A prefix alias is safe only above every enclosing configured scan/clone
    root and validated Git/pointer root. A narrow scan root cannot override a
    wider project boundary: redirects inside that project can change the
    relative path used for twin locks, even inside one repository.
    """
    if ".." in target.parts:
        return "target-parent-segment"
    lexical = target if target.is_absolute() else Path.cwd() / target
    try:
        resolved = lexical.resolve()
        physical_ancestors = {resolved, *resolved.parents}
    except (OSError, RuntimeError):
        return "target-unreadable"
    redirects = []
    for component in reversed((lexical, *lexical.parents)):
        try:
            if _is_directory_redirect(component):
                redirects.append(component)
        except FileNotFoundError:
            continue  # _check_dir reports a nonexistent target below
        except (OSError, RuntimeError):
            return "target-unreadable"
    if not redirects:
        return None

    boundaries = []
    twin_cfg = config.get("twin_resolution") or {}
    configured = [entry.get("path") for entry in config.get("roots", [])]
    configured += twin_cfg.get("clone_roots", [])
    for raw in configured:
        if not raw:
            continue
        root = Path(os.path.expandvars(str(raw)))
        if not root.is_absolute():
            root = Path.cwd() / root
        if (root == lexical or root in lexical.parents) and root.is_dir():
            boundaries.append(root)
    for ancestor in (lexical, *lexical.parents):
        if (ancestor / lock_utils.REPO_POINTER_NAME).is_file():
            if lock_utils.read_repo_pointer(ancestor) is not None:
                boundaries.append(ancestor)
        if (ancestor / ".git").exists():
            try:
                top = subprocess.run(
                    ["git", "-C", str(ancestor), "rev-parse", "--show-toplevel"],
                    capture_output=True, timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                top_text = os.fsdecode(top.stdout.strip()) if top.returncode == 0 else ""
                if top_text and Path(top_text).resolve() == ancestor.resolve():
                    boundaries.append(ancestor)
            except (OSError, RuntimeError, UnicodeError, subprocess.SubprocessError):
                return "target-unreadable"
    try:
        for component in redirects:
            if component.parent.resolve() not in physical_ancestors:
                return "target-redirect"
            if not boundaries or not all(
                component in root.parents and root.resolve() in physical_ancestors
                for root in boundaries
            ):
                return "target-redirect"
    except (OSError, RuntimeError):
        return "target-unreadable"
    return None


def _verified_regular_file_link(path: Path) -> bool:
    """Classify a non-traversed file link without weakening query redirects.

    A file has no child directory locks to visit. Unknown targets, directory
    links and changing link identities cannot establish that fact. Metadata
    reads never open or change the linked file, including a live database.
    """
    try:
        before = path.lstat()
        if not stat.S_ISLNK(before.st_mode):
            return False
        first = path.stat()
        after = path.lstat()
        last = path.stat()
    except (OSError, RuntimeError):
        return False
    identity_fields = ("st_dev", "st_ino", "st_mode", "st_size",
                       "st_mtime_ns", "st_ctime_ns", "st_reparse_tag")
    same_link = all(getattr(before, field, None) == getattr(after, field, None)
                    for field in identity_fields)
    return (same_link and stat.S_ISREG(first.st_mode) and stat.S_ISREG(last.st_mode)
            and first.st_dev == last.st_dev and first.st_ino == last.st_ino)


def _descendant_locks(target: Path, now: datetime, limit: int | None = None):
    """Find nested locks for a subtree query, with a fail-closed work bound.

    The normal ancestor lookup is fast, but asking about a project root must
    also see a LOCK under tests/ or src/. On a huge tree the bounded walk may
    stop early; callers must report that as undetermined, never free.
    """
    hits = []
    if limit is None:
        limit = CHECK_DIR_MAX_DESCENDANTS
    pending = [target]
    visited = 0
    while pending:
        current = pending.pop()
        try:
            if _is_directory_redirect(current):
                return hits, False
            entries = list(current.iterdir())
            children = []
            for entry in entries:
                if entry.name == ".git":
                    continue
                if _is_directory_redirect(entry):
                    if _verified_regular_file_link(entry):
                        continue
                    return hits, False
                if entry.is_dir():
                    children.append(entry)
        except OSError:
            return hits, False
        for child in children:
            visited += 1
            if visited > limit:
                return hits, False
            try:
                hits.extend(lock_utils.active_locks_affecting_target(child, target, now))
            except OSError:
                return hits, False
            pending.append(child)
    return hits, True


def host_name() -> str:
    """Name of this host, safe for use inside a file name.

    Several hosts share one synced folder. A cache file with a fixed name is
    then overwritten by every host and the sync service answers each collision
    with a conflict copy (~1700 per folder were measured). One file per host
    cannot collide."""
    raw = os.environ.get("COMPUTERNAME") or platform.node() or "unknown-host"
    return re.sub(r"[^A-Za-z0-9_-]", "_", raw)


def host_cache_name(stem: str = "LOCK-CACHE") -> str:
    """File name of the per-host cache, e.g. `LOCK-CACHE.IDEAPAD-GEI.md`."""
    return f"{stem}.{host_name()}.md"


# System-wide cache is written next to this script (all roots, no filter).
SYSTEM_CACHE_PATH = Path(__file__).resolve().parent / host_cache_name()


# Home directories that older configurations wrote out literally. Kept as a
# list rather than one hardcoded value so a second such legacy prefix can be
# added without touching the logic.
_LEGACY_HOME_MARKERS = (
    os.path.join("C:" + os.sep, "Users", "User"),
    os.path.join("C:" + os.sep, "Users", "lukas"),
)


def _expand_path(raw: str) -> str:
    """Expand `~` and environment variables in a configured path.

    Configurations are meant to be portable across machines and users, so they
    may reference the home directory or environment variables (`%USERPROFILE%`
    on Windows, `$HOME` on POSIX) instead of hardcoding absolute paths. Without
    this expansion such an entry stays a literal, `Path.exists()` returns False
    and the root is silently skipped -- the scan then reports far fewer locks
    than actually exist, which is worse than failing loudly.
    """
    # Host placeholders are resolved first and by hand: `%COMPUTERNAME%` is only
    # expanded by os.path.expandvars on Windows, and the cache path must never
    # silently degrade to one shared file on macOS/Linux.
    host = host_name()
    text = str(raw).replace("{host}", host)
    text = re.sub(r"%COMPUTERNAME%", lambda _m: host, text, flags=re.IGNORECASE)
    expanded = os.path.expanduser(os.path.expandvars(text))
    if os.path.exists(expanded):
        return expanded

    # Host fallback for older configurations that hardcoded one machine's home
    # directory. Such an entry resolves nowhere on a host whose user is named
    # differently -- the root is then silently skipped and the scan reports
    # fewer locks than exist, which is the dangerous direction. Re-anchor the
    # tail of the path on the real home directory instead.
    #
    # Carried over from the deployed copy in the drift merge
    # T-20260913-633163908: it lived only there, so every host reading the
    # repository fassung lost it.
    home = os.path.expanduser("~")
    for marker in _LEGACY_HOME_MARKERS:
        if expanded.lower().startswith(marker.lower()) and home:
            candidate = home + expanded[len(marker):]
            if os.path.exists(candidate):
                return candidate
    return expanded


def load_config(roots_file: Path) -> dict:
    if not roots_file.exists():
        example_file = roots_file.with_name("lock_roots.example.json")
        if example_file.exists():
            roots_file = example_file
        else:
            return {
                "default_max_depth": 4,
                "shallow_depth": 2,
                "skip_dirs": [
                    ".git", ".venv", "venv", "env", "node_modules",
                    "__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache",
                    "build", "dist", "releases", "_archive",
                ],
                "roots": [],
                "caches": [],
            }

    with open(roots_file, "r", encoding="utf-8") as f:
        config = json.load(f)

    for entry in config.get("roots", []):
        if "path" in entry:
            entry["path"] = _expand_path(entry["path"])

    for entry in config.get("caches", []):
        if "path" in entry:
            entry["path"] = _expand_path(entry["path"])
        if entry.get("filter_prefix"):
            entry["filter_prefix"] = _expand_path(entry["filter_prefix"])

    return config


def iter_lock_dirs(config: dict):
    """Generator over all directories to check across all roots,
    respecting depth limits and skip-lists from the configuration.
    Yields Path objects (directories) in which LOCK*.txt files are searched."""
    default_depth = int(config.get("default_max_depth", 4))
    shallow_depth = int(config.get("shallow_depth", 2))
    skip_dirs = {d.lower() for d in config.get("skip_dirs", [])}

    for entry in config.get("roots", []):
        root = Path(entry["path"])
        if not root.exists() or not root.is_dir():
            continue
        max_depth = shallow_depth if entry.get("shallow") else default_depth
        yield from _walk(root, root, max_depth, skip_dirs)


def _walk(current: Path, root: Path, max_depth: int, skip_dirs: set):
    yield current
    depth = len(current.relative_to(root).parts)
    if depth >= max_depth:
        return
    try:
        children = list(current.iterdir())
    except OSError:
        return
    for child in children:
        if not child.is_dir():
            continue
        if child.name.lower() in skip_dirs:
            continue
        yield from _walk(child, root, max_depth, skip_dirs)


def _format_remaining(delta: timedelta) -> str:
    secs = int(delta.total_seconds())
    if secs < 0:
        return "expired"
    h, rem = divmod(secs, 3600)
    m, _ = divmod(rem, 60)
    return f"{h}h{m:02d}m"


def collect_locks(config: dict, now: datetime | None = None) -> list[dict]:
    now = datetime.now(timezone.utc) if now is None else now
    now_utc = lock_utils._as_utc(now)
    seen: set[Path] = set()
    out: list[dict] = []
    for d in iter_lock_dirs(config):
        if d in seen:
            continue
        seen.add(d)
        for name, scope, is_legacy in lock_utils.active_locks(d, now):
            lock_path = d / name
            created, expires, source = lock_utils.lock_created_and_expiry(lock_path)
            data = lock_utils.parse_lock_file(lock_path)
            expires_at = ""
            if is_legacy:
                remaining = "legacy"
            elif lock_utils.is_ambiguous_lock(name):
                remaining = ("AMBIGUOUS NAME - lock types cannot be combined in "
                             "the filename; holds indefinitely (fail-closed). "
                             "Use the fields: not_before + release_condition.")
            elif lock_utils.is_user_lock(lock_path, data=data):
                remaining = "user-held (no time expiry)"
            elif lock_utils.is_condition_lock(lock_path, data=data):
                cond = data.get("release_condition", "?")
                remaining = f"until condition met: {cond}"
            elif lock_utils.is_until_lock(lock_path, data=data):
                moment = lock_utils.lock_not_before(lock_path, data=data)
                if moment is None:
                    remaining = ("MISSING not_before -> holds indefinitely "
                                 "(fail-closed)")
                elif now_utc is None:
                    remaining = "current time unavailable -> holds (fail-closed)"
                else:
                    now_local = now_utc.astimezone().replace(tzinfo=None)
                    if now_local > moment:
                        remaining = (f"deadline passed {moment.isoformat(timespec='minutes')}"
                                     " - guard may stop; file stays for the user")
                    else:
                        remaining = (f"{_format_remaining(moment - now_local)} "
                                     f"(until {moment.isoformat(timespec='minutes')})")
                        expires_at = moment.isoformat(timespec="minutes")
            elif source == "invalid":
                remaining = "invalid created timestamp -> active (fail-closed)"
            elif now_utc is None:
                remaining = "current time unavailable -> active (fail-closed)"
            else:
                expiry = created + expires
                remaining = _format_remaining(expiry - now_utc)
                expires_at = lock_utils.format_utc_iso(expiry, timespec="minutes")
            created_display = (
                None if source == "invalid"
                else lock_utils.format_utc_iso(created, timespec="minutes")
            )
            out.append({
                "path": str(lock_path),
                "scope": scope,
                "legacy": is_legacy,
                "owner": data.get("owner", ""),
                "created": created_display,
                "created_source": source,
                "expires_after": str(expires),
                "operations": data.get("operations", ""),
                "release_condition": data.get("release_condition", ""),
                "not_before": data.get("not_before", ""),
                "fence": data.get("fence", ""),
                "remaining": remaining,
                "expires_at": expires_at,
            })
    out.sort(key=lambda r: r["path"])
    return out


def _md_escape(value: str) -> str:
    """Escape pipes in table cells, strip line breaks."""
    return value.replace("|", "\\|").replace("\n", " ").replace("\r", " ").strip()


def render_cache(locks: list[dict], scanned_at: datetime, title: str) -> str:
    """Render a LOCK-CACHE.md (auto-generated) from the lock list."""
    lines = [
        "<!-- AUTO-GENERATED by lock_scan.py --write-cache. DO NOT edit manually. "
        "The authoritative source is the LOCK*.txt files themselves. -->",
        "",
        f"# {title}",
        "",
        f"As of: {scanned_at.isoformat(timespec='seconds')}",
        "",
        f"Active locks: {len(locks)}",
        "",
        "| Path | scope | owner | created | expires |",
        "|---|---|---|---|---|",
    ]
    for r in locks:
        path = r["path"] + (" (legacy)" if r["legacy"] else "")
        owner = r["owner"] or "?"
        # Absolute expiry when the lock has one, else the static state text
        # (user-held, condition, legacy ...). Never a countdown: a countdown
        # changes every minute and forces a rewrite -> sync traffic.
        expires = r.get("expires_at") or r["remaining"]
        lines.append(
            f"| {_md_escape(path)} | {_md_escape(r['scope'])} | {_md_escape(owner)} "
            f"| {_md_escape(r['created'])} | {_md_escape(expires)} |"
        )
    if not locks:
        lines.append("| _(no active locks)_ |  |  |  |  |")
    return "\n".join(lines) + "\n"


def _volatile(line: str) -> bool:
    return line.startswith(("As of:", "Stand:", "Daemon-Heartbeat", "*Generiert:"))


def write_if_changed(path: Path, text: str, refresh_seconds: int = CACHE_REFRESH_SECONDS) -> bool:
    """Write `text` to `path` unless the content (ignoring the timestamp line)
    is unchanged and the file is younger than `refresh_seconds`.

    Every rewrite is a sync event for the cloud folder; rewriting identical
    content every few minutes is pure load. Returns True if written."""
    try:
        if path.exists():
            old = path.read_text(encoding="utf-8")
            same = [ln for ln in old.splitlines() if not _volatile(ln)] == [
                ln for ln in text.splitlines() if not _volatile(ln)
            ]
            age = datetime.now().timestamp() - path.stat().st_mtime
            if same and age < refresh_seconds:
                return False
    except OSError:
        pass
    path.write_text(text, encoding="utf-8")
    return True


def write_caches(locks: list[dict], scanned_at: datetime, config: dict) -> list[tuple[Path, int]]:
    """Write cache file(s) as defined by the 'caches' key in lock_roots.json.
    Falls back to a single system-wide cache next to this script if not configured.
    Each cache entry may have an optional 'filter_prefix' to restrict which locks appear.
    Returns list of (path, count) for each written cache."""
    results: list[tuple[Path, int]] = []

    cache_defs: list[dict] = config.get("caches", [])

    if not cache_defs:
        # Default: one system-wide cache next to this script.
        write_if_changed(
            SYSTEM_CACHE_PATH,
            render_cache(locks, scanned_at, "LOCK-CACHE (all roots)"),
        )
        results.append((SYSTEM_CACHE_PATH, len(locks)))
        return results

    for entry in cache_defs:
        cache_path = Path(entry["path"])
        title = entry.get("name", cache_path.name)
        prefix = entry.get("filter_prefix")
        if prefix:
            # Match on a path-segment boundary: ".../SOFTWARE" must not also
            # capture ".../SOFTWARE-ARCHIVE" or ".../SOFTWARE2".
            prefix_lower = str(prefix).lower().rstrip("\\/")
            bounded = (prefix_lower + "\\", prefix_lower + "/")
            filtered = [
                r for r in locks
                if r["path"].lower() == prefix_lower
                or r["path"].lower().startswith(bounded)
            ]
        else:
            filtered = locks
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        write_if_changed(
            cache_path,
            render_cache(filtered, scanned_at, f"LOCK-CACHE — {title}"),
        )
        results.append((cache_path, len(filtered)))

    return results



def write_twin_index(config: dict) -> tuple[Path, int] | None:
    """Write the host's twin index (repo_name -> twin directories) from the
    REPO.pointer.json files of the directories the scan walks anyway.

    Without this index --check-dir cannot resolve the clone -> mirror direction
    of the two-trees rule (T-20260913-785936980). Returns None when no
    twin_resolution.index_path is configured."""
    twin_cfg = config.get("twin_resolution") or {}
    raw = twin_cfg.get("index_path")
    if not raw:
        return None
    index_path = lock_utils.twin_index_path(twin_cfg)
    if index_path is None:
        raise ValueError("twin index requires a known host and index path")
    index = lock_utils.build_twin_index(iter_lock_dirs(config))
    index_path.parent.mkdir(parents=True, exist_ok=True)
    # Preserve the host file when only `generated_at` differs, avoiding sync
    # traffic on every scan. The path itself is resolved for this host above.
    try:
        old = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = None
    if not (isinstance(old, dict)
            and {k: v for k, v in old.items() if k != "generated_at"}
            == {k: v for k, v in index.items() if k != "generated_at"}):
        index_path.write_text(
            json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    return index_path, len(index["by_repo_name"])

def _check_dir(target: Path, as_json: bool, strict: bool,
               roots_file: Path | None = None) -> int:
    """--check-dir: worktree-aware lock check for exactly ONE directory (no
    full scan). See lock_utils.active_locks_for_path().

    Also reports active git guard hooks (pre-push/pre-commit/pre-receive)
    for `target` -- a directory can have no LOCK file and still be blocked
    at push time by a hook this check previously never saw
    (T-20260906-910508487). "Free" below means "no LOCK file", not
    "pushable". Hooks are reported as a warning and do NOT change the
    Exit 0/1 semantics unless --strict is given (then: 2 = free of LOCK
    files but a guard hook is present).

    A target that does not exist (typo'd or stale path) is a usage error,
    not "free": Path.resolve() normalizes a nonexistent path without
    raising, so without this check a mistyped --check-dir call silently
    reported exit 0 "free" -- worse than any other failure mode, since it
    looks identical to a genuinely clear directory (T-20260922-626871087)."""
    config = load_config(roots_file or DEFAULT_ROOTS_FILE)
    descendant_limit = config.get("check_dir_max_descendants", CHECK_DIR_MAX_DESCENDANTS)
    if (type(descendant_limit) is not int
            or not 1 <= descendant_limit <= CHECK_DIR_MAX_DESCENDANTS_CEILING):
        if as_json:
            print(json.dumps({"checked": str(target), "error": "invalid-descendant-limit",
                              "locks": [], "descendants_complete": False},
                             ensure_ascii=False))
        else:
            print("lock_scan --check-dir: UNDETERMINED, check_dir_max_descendants "
                  f"must be an integer from 1 to {CHECK_DIR_MAX_DESCENDANTS_CEILING}.")
        return 1
    query_error = _query_redirect_or_error(target, config)
    if query_error is not None:
        if as_json:
            print(json.dumps({"checked": str(target), "error": query_error,
                              "locks": [], "descendants_complete": False},
                             ensure_ascii=False))
        else:
            print(f"lock_scan --check-dir: {target} -- UNDETERMINED ({query_error}); "
                  "a query-path redirect can hide lexical ancestor locks.")
        return 1
    target = target.resolve()
    if not target.is_dir():
        msg = f"lock_scan --check-dir: {target} does not exist -- not 'free', a usage error."
        if as_json:
            print(json.dumps({"checked": str(target), "error": "target-not-found"},
                              ensure_ascii=False))
        else:
            print(msg, file=sys.stderr)
        return 2
    now = datetime.now(timezone.utc)
    try:
        main_root = lock_utils.worktree_main_dir(target)
        hits = lock_utils.active_locks_for_path(target, now)
    except lock_utils.GitPathResolutionError as exc:
        if as_json:
            print(json.dumps({"checked": str(target), "error": "git-worktree-unresolved",
                              "detail": str(exc), "locks": [],
                              "descendants_complete": False}, ensure_ascii=True))
        else:
            print(f"lock_scan --check-dir: {target} -- UNDETERMINED ({exc}).")
        return 1
    nested_hits, descendants_complete = _descendant_locks(target, now, limit=descendant_limit)
    hits.extend(nested_hits)

    # Two-trees rule: the twin in the other tree counts as well
    # (T-20260913-785936980). A freshly cloned repo cannot contain the
    # deliberately unversioned LOCK.user.* -- without this step one reads a
    # state out of an empty spot.
    twin_cfg = config.get("twin_resolution")
    # A worktree is not registered as a separate clone in the twin index.
    # Resolve its main clone first; likewise walk up from mirror subfolders to
    # the pointer. Keep the same relative path on the opposite tree.
    twins, twin_status = lock_utils.twin_dirs_for_path(target, twin_cfg)
    # twin_dirs_for_path returns all registered twins, and for a subpath also
    # its mapped child in EACH twin. Keep every terminal mapped path, not only
    # the last one: a lock in mirror A must not disappear because B is listed.
    mapped_twins = [t for t in twins if not any(t in other.parents for other in twins)]
    if not twins and twin_status in ("ok", "not-applicable", "index-unmapped"):
        anchor = main_root
        if anchor is None:
            anchor = next((p for p in (target, *target.parents)
                           if (p / lock_utils.REPO_POINTER_NAME).is_file()), None)
        if anchor is not None and anchor != target:
            anchor_twins, anchor_status = lock_utils.twin_dirs_for_path(anchor, twin_cfg)
            if anchor_status != "not-applicable":
                twins, twin_status = anchor_twins, anchor_status
            if twins:
                try:
                    if main_root is not None:
                        top = lock_utils.git_worktree_top_dir(target)
                        if top is None:
                            raise lock_utils.GitPathResolutionError(
                                f"Git worktree root is missing: {target}")
                        relative = target.relative_to(top)
                    else:
                        relative = target.relative_to(anchor)
                except (OSError, ValueError, subprocess.SubprocessError):
                    relative = Path(".")  # root query is conservative on uncertainty
                mapped_twins = [t / relative for t in twins]
    twin_hits = []
    for mapped in mapped_twins:
        twin_hits.extend(lock_utils.active_locks_for_path(mapped, now))
        if mapped.is_dir():
            twin_nested, twin_complete = _descendant_locks(mapped, now, limit=descendant_limit)
            twin_hits.extend(twin_nested)
            descendants_complete = descendants_complete and twin_complete
    hits.extend(twin_hits)

    rows = [
        {
            "path": str(source_dir / name),
            "scope": scope,
            "legacy": is_legacy,
            "from_main_repo_of_worktree": bool(
                main_root is not None
                and (source_dir == main_root or source_dir in main_root.parents)
                and source_dir != target and source_dir not in target.parents),
            "from_twin": (name, scope, is_legacy, source_dir) in twin_hits,
        }
        for name, scope, is_legacy, source_dir in hits
    ]
    guards = lock_utils.git_hook_guards(target)

    if as_json:
        print(json.dumps(
            {"checked": str(target), "locks": rows, "hook_guards": guards,
             "twin_status": twin_status,
             "descendants_complete": descendants_complete,
             "twins_checked": [str(t) for t in twins]},
            ensure_ascii=False, indent=2,
        ))
        if rows:
            return 1
        if twin_status not in ("ok", "not-applicable") or not descendants_complete:
            return 1
        return 2 if (strict and guards) else 0

    if not rows:
        if not descendants_complete:
            print(f"lock_scan --check-dir: {target} -- UNDETERMINED, nested lock scan incomplete (limit, unreadable directory or link).")
        elif twin_status == "pointer-unreadable":
            print(
                f"lock_scan --check-dir: {target} -- UNDETERMINED, not 'free'. "
                f"The mirror twin carries a {lock_utils.REPO_POINTER_NAME} that "
                "cannot be read, so its locks are invisible. Repair the pointer, "
                "then run lock_scan.py --write-cache."
            )
        elif twin_status == "index-missing":
            print(
                f"lock_scan --check-dir: {target} -- UNDETERMINED, not 'free'. "
                "The path lies under a clone root, but the twin index "
                f"({lock_utils.TWIN_INDEX_NAME}) is missing or unreadable. An "
                "unversioned LOCK.user.* on the mirror twin would be invisible "
                "here (T-20260913-785936980). Create it: lock_scan.py --write-cache"
            )
        elif twin_status not in ("ok", "not-applicable"):
            print(
                f"lock_scan --check-dir: {target} -- UNDETERMINED, not 'free'. "
                f"Twin resolution status: {twin_status}. Verify the local "
                "pointer/index and refresh the host index under the normal locks."
            )
        else:
            suffix = f", {len(twins)} twin(s) checked" if twins else ""
            print(
                f"lock_scan --check-dir: {target} is free "
                f"(incl. main clone if worktree{suffix})."
            )
    else:
        print(f"lock_scan --check-dir: {len(rows)} active lock(s) affect {target}:")
        for r in rows:
            if r["from_twin"]:
                tag = "  (from the twin in the other tree)"
            elif r["from_main_repo_of_worktree"]:
                tag = "  (from worktree's main clone)"
            else:
                tag = ""
            legacy = " [LEGACY]" if r["legacy"] else ""
            print(f"  {r['path']}{legacy}  scope={r['scope']}{tag}")

    for g in guards:
        if g["hook"] is None:
            print(f"  GUARD-NOTE: {g['hint']} ({g['path']})")
            continue
        tag = "  [EMBARGO SIGNATURE]" if g["embargo"] else ""
        print(f"  GUARD: {g['hook']} ({g['hint']}){tag}")
    if guards:
        print(
            "  NOTE: 'free' above means no LOCK file -- it does NOT mean "
            "pushable. The guard(s) above can still block the operation."
        )

    if rows:
        return 1
    if twin_status not in ("ok", "not-applicable") or not descendants_complete:
        return 1
    return 2 if (strict and guards) else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="List all active project locks (LOCK*.txt) across all configured roots (read-only)."
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON.")
    parser.add_argument(
        "--write-cache",
        action="store_true",
        help="Write LOCK-CACHE.md as configured in lock_roots.json ('caches' key).",
    )
    parser.add_argument(
        "--roots-file",
        default=str(DEFAULT_ROOTS_FILE),
        help="Path to lock_roots.json.",
    )
    parser.add_argument(
        "--check-dir",
        metavar="PATH",
        help="Fast, WORKTREE-AWARE check for exactly this one directory "
        "(stage-1 check before starting work) instead of a full scan of "
        "all roots. If PATH is inside a git worktree, its main clone is "
        "checked too (T-20260903-592302105). Also reports active git guard "
        "hooks (pre-push/pre-commit/pre-receive) for PATH -- 'free' means "
        "no LOCK file, not 'pushable' (T-20260906-910508487). "
        "Exit 0 = free, 1 = locked, 2 = usage error (PATH does not exist, "
        "or -- only with --strict -- free but a guard hook is present).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="With --check-dir: exit 2 if no LOCK file but a guard hook is "
        "present (default: guard hooks are reported but do not change the "
        "exit code).",
    )
    parser.add_argument(
        "--verify-fence",
        metavar="LOCKFILE",
        help="Check whether the grant number given with --fence is still the "
        "current one for LOCKFILE. Exit 0 = still held, 1 = lost (the lease "
        "moved on, the file is gone, or the own grant expired), 2 = usage "
        "error. Fail-closed: anything that is not provably still your grant "
        "counts as lost.",
    )
    parser.add_argument(
        "--fence",
        type=int,
        default=None,
        help="With --verify-fence: the grant number recorded at acquisition "
        "(printed by lock_create.py).",
    )
    args = parser.parse_args()

    if args.verify_fence:
        if args.fence is None:
            print("lock_scan --verify-fence: --fence <n> is required",
                  file=sys.stderr)
            return 2
        status, reason = lock_utils.fence_status(Path(args.verify_fence), args.fence)
        if args.json:
            print(json.dumps({"lock": args.verify_fence, "fence": args.fence,
                              "status": status, "reason": reason},
                             ensure_ascii=False))
        else:
            print(f"lock_scan --verify-fence: {status.upper()} -- {reason}")
        return 0 if status == lock_utils.FENCE_HELD else 1

    if args.check_dir:
        return _check_dir(Path(args.check_dir), args.json, args.strict,
                          Path(args.roots_file))

    config = load_config(Path(args.roots_file))
    twin_cfg = config.get("twin_resolution") or {}
    if (args.write_cache and twin_cfg.get("index_path")
            and lock_utils.twin_index_path(twin_cfg) is None):
        print("lock_scan --write-cache: twin index host/path is unresolved",
              file=sys.stderr)
        return 1
    scanned_at = datetime.now(timezone.utc)
    locks = collect_locks(config, scanned_at)

    if args.write_cache:
        written = write_caches(locks, scanned_at, config)
        for path, count in written:
            print(f"lock_scan --write-cache: {path} ({count} active lock(s))")
        twin = write_twin_index(config)
        if twin is not None:
            print(f"lock_scan --write-cache: {twin[0]} ({twin[1]} repo twin(s))")
        return 0

    if args.json:
        print(json.dumps(locks, ensure_ascii=False, indent=2))
        return 0

    if not locks:
        print("lock_scan: no active locks found.")
        return 0

    print(f"lock_scan: {len(locks)} active lock(s):")
    for r in locks:
        legacy = " [LEGACY]" if r["legacy"] else ""
        owner = r["owner"] or "?"
        print(f"  {r['path']}{legacy}")
        print(
            f"      scope={r['scope']} owner={owner} created={r['created']} "
            f"({r['created_source']}) remaining={r['remaining']}"
            + (f" fence={r['fence']}" if r.get("fence") else "")
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
