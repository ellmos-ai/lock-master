r"""
lock_status.py -- Per-project status check (exit 0 = no lock, exit 1 = locked)

Checks whether a specified project directory has active (non-expired) locks.

Usage:
  python lock_status.py <project_path>
  python lock_status.py --project <project_path> [--json]

Exit codes:
  0: No active locks found for project (free / unlocked)
  1: Project has one or more active locks (locked)
  2: Error (e.g. project path invalid or missing argument)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import lock_utils


def check_project_status(project_dir: Path, now: datetime | None = None) -> list[dict]:
    """Check active locks for a project directory.
    Returns list of lock data dicts."""
    now = now or datetime.now()
    if not project_dir.exists() or not project_dir.is_dir():
        raise ValueError(f"Project directory does not exist: {project_dir}")

    active = lock_utils.active_locks(project_dir, now=now)
    out = []
    for name, scope, is_legacy in active:
        lock_path = project_dir / name
        created, expires, source = lock_utils.lock_created_and_expiry(lock_path)
        data = lock_utils.parse_lock_file(lock_path)
        if is_legacy:
            remaining = "legacy"
        elif lock_utils.is_user_lock(lock_path, data=data):
            remaining = "user-held (no time expiry)"
        elif lock_utils.is_condition_lock(lock_path, data=data):
            cond = data.get("release_condition", "?")
            remaining = f"until condition met: {cond}"
        else:
            secs = int(((created + expires) - now).total_seconds())
            if secs < 0:
                remaining = "expired"
            else:
                h, rem = divmod(secs, 3600)
                m, _ = divmod(rem, 60)
                remaining = f"{h}h{m:02d}m"

        out.append({
            "name": name,
            "path": str(lock_path),
            "scope": scope,
            "legacy": is_legacy,
            "owner": data.get("owner", ""),
            "created": created.isoformat(timespec="minutes"),
            "created_source": source,
            "expires_after": str(expires),
            "operations": data.get("operations", ""),
            "release_condition": data.get("release_condition", ""),
            "remaining": remaining,
        })
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check whether a project directory is locked (exit 0 = unlocked, exit 1 = locked)."
    )
    parser.add_argument(
        "project",
        nargs="?",
        default=None,
        help="Path to project directory.",
    )
    parser.add_argument(
        "--project",
        dest="project_flag",
        default=None,
        help="Path to project directory (alternative flag).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output result as JSON.",
    )

    args = parser.parse_args()
    project_path_str = args.project or args.project_flag
    if not project_path_str:
        parser.print_help()
        return 2

    project_dir = Path(project_path_str).resolve()

    try:
        locks = check_project_status(project_dir)
    except Exception as e:
        if args.json:
            print(json.dumps({"error": str(e)}, ensure_ascii=False))
        else:
            print(f"lock_status error: {e}", file=sys.stderr)
        return 2

    is_locked = len(locks) > 0

    if args.json:
        result = {
            "project": str(project_dir),
            "locked": is_locked,
            "active_locks": locks,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if is_locked:
            print(f"lock_status: {project_dir} is LOCKED ({len(locks)} active lock(s)):")
            for r in locks:
                owner = r["owner"] or "?"
                print(f"  {r['name']} (scope={r['scope']}, owner={owner}, remaining={r['remaining']})")
        else:
            print(f"lock_status: {project_dir} is UNLOCKED (no active locks).")

    return 1 if is_locked else 0


if __name__ == "__main__":
    raise SystemExit(main())
