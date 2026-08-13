r"""
prune_stale_locks.py -- System-wide stale cleanup for project locks (LOCK*.txt)

Scans all roots configured in lock_roots.json (with depth limits and
skip-lists) for LOCK*.txt files and removes expired ones:
  created + expires_after < now  (default expires_after = 24h)
Fallback for missing 'created': file mtime.
Legacy TEST.txt/TESTS.txt are NOT touched (no expiry format).

Usage:
  python prune_stale_locks.py
  python prune_stale_locks.py --dry-run
  python prune_stale_locks.py --roots-file <path>

Canonical spec: LOCK-SYSTEM.md (same directory).
Format/expiry logic: lock_utils.py.
Directory walk + config loading: lock_scan.py (DRY, no second standard).
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

import lock_utils
from lock_scan import DEFAULT_ROOTS_FILE, iter_lock_dirs, load_config


WEBHOOK_ENV = "LOCK_MASTER_PRUNE_WEBHOOK_URL"
WEBHOOK_TIMEOUT_SECONDS = 5


def host_is_reachable(host: str | None) -> bool | None:  # noqa: ARG001
    """Whether the system named in a lock's 'host' field is reachable.

    STUB / prepared hook -- not yet active, always returns None. A future
    implementation could ping the host (e.g. via Tailscale) so that locks of a
    permanently unreachable system can be cleaned up earlier; until then locks
    expire purely via 'expires_after'."""
    return None


def notify_expired_locks(webhook_url: str, removed_paths: list[str]) -> bool:
    """Send one best-effort JSON notification for a completed prune run.

    The hook is opt-in and runs only after real removals (never for a dry-run).
    Network failures are reported but do not turn a successful local cleanup
    into a failed cleanup.
    """
    parsed = urllib.parse.urlparse(webhook_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        print("[WARNING] prune notification skipped: webhook URL must use http(s).")
        return False

    payload = {
        "event": "lock_expired",
        "removed": len(removed_paths),
        "paths": removed_paths,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }
    request = urllib.request.Request(
        webhook_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "lock-master-prune/1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=WEBHOOK_TIMEOUT_SECONDS) as response:
            if not 200 <= response.status < 300:
                print(
                    f"[WARNING] prune notification returned HTTP {response.status}."
                )
                return False
    except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
        print(f"[WARNING] prune notification failed: {exc}")
        return False
    print(f"prune notification sent: {len(removed_paths)} expired lock(s).")
    return True


def prune(config: dict, dry_run: bool = False, webhook_url: str | None = None) -> int:
    now = datetime.now()
    removed = 0
    kept = 0
    removed_paths: list[str] = []
    seen: set[Path] = set()

    for d in iter_lock_dirs(config):
        if d in seen:
            continue
        seen.add(d)
        for name, _scope, _is_legacy in lock_utils.find_lock_files(d):
            lock_path = d / name
            # is_prunable() excludes legacy (no expiry), user locks (user-only
            # removal) and non-expired locks.
            if not lock_utils.is_prunable(lock_path, now):
                kept += 1
                continue
            created, expires, source = lock_utils.lock_created_and_expiry(lock_path)
            age_h = (now - created).total_seconds() / 3600
            if dry_run:
                print(f"[would remove] {lock_path} "
                      f"(age {age_h:.1f}h, expires_after {expires}, source {source})")
            else:
                try:
                    lock_path.unlink()
                    print(f"[removed] {lock_path} "
                          f"(age {age_h:.1f}h, expires_after {expires}, source {source})")
                    removed_paths.append(str(lock_path))
                except OSError as exc:
                    print(f"[ERROR] {lock_path} could not be removed: {exc}")
                    kept += 1
                    continue
            removed += 1

    configured_webhook = (
        webhook_url if webhook_url is not None else os.environ.get(WEBHOOK_ENV, "")
    ).strip()
    if configured_webhook and removed_paths and not dry_run:
        notify_expired_locks(configured_webhook, removed_paths)

    verb = "would remove" if dry_run else "removed"
    print(f"prune_stale_locks: {removed} expired LOCK*.txt {verb}, "
          f"{kept} active lock(s) kept.")
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Remove expired project locks (LOCK*.txt) across all configured roots."
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be removed without deleting anything.")
    parser.add_argument("--roots-file", default=str(DEFAULT_ROOTS_FILE),
                        help="Path to lock_roots.json.")
    parser.add_argument(
        "--webhook-url",
        default=None,
        help=(
            "Optional HTTP(S) endpoint notified once after expired locks are "
            f"removed (or use {WEBHOOK_ENV})."
        ),
    )
    args = parser.parse_args()

    config = load_config(Path(args.roots_file))
    prune(config, dry_run=args.dry_run, webhook_url=args.webhook_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
