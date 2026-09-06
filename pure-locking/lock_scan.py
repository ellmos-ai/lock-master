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
from datetime import datetime, timedelta
from pathlib import Path

import lock_utils

DEFAULT_ROOTS_FILE = Path(__file__).resolve().parent / "lock_roots.json"

# System-wide cache is written next to this script (all roots, no filter).
SYSTEM_CACHE_PATH = Path(__file__).resolve().parent / "LOCK-CACHE.md"


def _expand_path(raw: str) -> str:
    """Expand `~` and environment variables in a configured path.

    Configurations are meant to be portable across machines and users, so they
    may reference the home directory or environment variables (`%USERPROFILE%`
    on Windows, `$HOME` on POSIX) instead of hardcoding absolute paths. Without
    this expansion such an entry stays a literal, `Path.exists()` returns False
    and the root is silently skipped -- the scan then reports far fewer locks
    than actually exist, which is worse than failing loudly.
    """
    return os.path.expanduser(os.path.expandvars(str(raw)))


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
    now = now or datetime.now()
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
            if is_legacy:
                remaining = "legacy"
            elif lock_utils.is_ambiguous_lock(name):
                remaining = ("AMBIGUOUS NAME - lock types cannot be combined in "
                             "the filename; holds indefinitely (fail-closed). "
                             "Use the fields: not_before + release_condition.")
            elif lock_utils.is_user_lock(name):
                remaining = "user-held (no time expiry)"
            elif lock_utils.is_condition_lock(name):
                cond = data.get("release_condition", "?")
                remaining = f"until condition met: {cond}"
            elif lock_utils.is_until_lock(name):
                moment = lock_utils.lock_not_before(lock_path)
                if moment is None:
                    remaining = ("MISSING not_before -> holds indefinitely "
                                 "(fail-closed)")
                elif now > moment:
                    remaining = (f"deadline passed {moment.isoformat(timespec='minutes')}"
                                 " - guard may stop; file stays for the user")
                else:
                    remaining = (f"{_format_remaining(moment - now)} "
                                 f"(until {moment.isoformat(timespec='minutes')})")
            else:
                remaining = _format_remaining((created + expires) - now)
            out.append({
                "path": str(lock_path),
                "scope": scope,
                "legacy": is_legacy,
                "owner": data.get("owner", ""),
                "created": created.isoformat(timespec="minutes"),
                "created_source": source,
                "expires_after": str(expires),
                "operations": data.get("operations", ""),
                "release_condition": data.get("release_condition", ""),
                "not_before": data.get("not_before", ""),
                "remaining": remaining,
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
        "| Path | scope | owner | created | remaining |",
        "|---|---|---|---|---|",
    ]
    for r in locks:
        path = r["path"] + (" (legacy)" if r["legacy"] else "")
        owner = r["owner"] or "?"
        lines.append(
            f"| {_md_escape(path)} | {_md_escape(r['scope'])} | {_md_escape(owner)} "
            f"| {_md_escape(r['created'])} | {_md_escape(r['remaining'])} |"
        )
    if not locks:
        lines.append("| _(no active locks)_ |  |  |  |  |")
    return "\n".join(lines) + "\n"


def write_caches(locks: list[dict], scanned_at: datetime, config: dict) -> list[tuple[Path, int]]:
    """Write cache file(s) as defined by the 'caches' key in lock_roots.json.
    Falls back to a single system-wide cache next to this script if not configured.
    Each cache entry may have an optional 'filter_prefix' to restrict which locks appear.
    Returns list of (path, count) for each written cache."""
    results: list[tuple[Path, int]] = []

    cache_defs: list[dict] = config.get("caches", [])

    if not cache_defs:
        # Default: one system-wide cache next to this script.
        SYSTEM_CACHE_PATH.write_text(
            render_cache(locks, scanned_at, "LOCK-CACHE (all roots)"),
            encoding="utf-8",
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
        cache_path.write_text(
            render_cache(filtered, scanned_at, f"LOCK-CACHE — {title}"),
            encoding="utf-8",
        )
        results.append((cache_path, len(filtered)))

    return results


def _check_dir(target: Path, as_json: bool, strict: bool) -> int:
    """--check-dir: worktree-aware lock check for exactly ONE directory (no
    full scan). See lock_utils.active_locks_for_path().

    Also reports active git guard hooks (pre-push/pre-commit/pre-receive)
    for `target` -- a directory can have no LOCK file and still be blocked
    at push time by a hook this check previously never saw
    (T-20260906-910508487). "Free" below means "no LOCK file", not
    "pushable". Hooks are reported as a warning and do NOT change the
    Exit 0/1 semantics unless --strict is given (then: 2 = free of LOCK
    files but a guard hook is present)."""
    target = target.resolve()
    now = datetime.now()
    hits = lock_utils.active_locks_for_path(target, now)
    rows = [
        {
            "path": str(source_dir / name),
            "scope": scope,
            "legacy": is_legacy,
            "from_main_repo_of_worktree": source_dir != target,
        }
        for name, scope, is_legacy, source_dir in hits
    ]
    guards = lock_utils.git_hook_guards(target)

    if as_json:
        print(json.dumps(
            {"checked": str(target), "locks": rows, "hook_guards": guards},
            ensure_ascii=False, indent=2,
        ))
        if rows:
            return 1
        return 2 if (strict and guards) else 0

    if not rows:
        print(f"lock_scan --check-dir: {target} is free (incl. main clone if worktree).")
    else:
        print(f"lock_scan --check-dir: {len(rows)} active lock(s) affect {target}:")
        for r in rows:
            tag = "  (from worktree's main clone)" if r["from_main_repo_of_worktree"] else ""
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
        "Exit 0 = free, 1 = locked, 2 = free but a guard hook is present "
        "(only with --strict).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="With --check-dir: exit 2 if no LOCK file but a guard hook is "
        "present (default: guard hooks are reported but do not change the "
        "exit code).",
    )
    args = parser.parse_args()

    if args.check_dir:
        return _check_dir(Path(args.check_dir), args.json, args.strict)

    config = load_config(Path(args.roots_file))
    scanned_at = datetime.now()
    locks = collect_locks(config, scanned_at)

    if args.write_cache:
        written = write_caches(locks, scanned_at, config)
        for path, count in written:
            print(f"lock_scan --write-cache: {path} ({count} active lock(s))")
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
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
