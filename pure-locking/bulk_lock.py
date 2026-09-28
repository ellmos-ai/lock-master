r"""
bulk_lock.py — Sofortsperrung/-freigabe aller angebundenen Projektordner.

Schreibt bzw. entfernt exklusive Projekt-Locks (LOCK.txt) ueber viele Roots in
einem Schritt ("zentrale Sofortsperrung"). Bewusst guard-geschuetzt:

  * commit=False (Default) = Dry-Run: es wird NICHTS geschrieben/geloescht.
  * Idempotent: Roots mit bereits aktivem Lock werden uebersprungen (kein
    Ueberschreiben).
  * Reversibel: gesetzte Locks tragen 'created_by: bulk'; bulk_unlock() entfernt
    NUR solche Locks.
  * Schutzinvariante: User-Locks (LOCK.user*.txt) werden NIE angetastet — weder
    beim Sperren (Root gilt als schon gesperrt) noch beim Entsperren.

Optionales Session-Manifest (~/.lock_watcher/bulk_lock_session.json) erlaubt die
exakte Ruecknahme einer bestimmten Sofortsperrung.

Hintergrund Guard-Pflicht: ungeguardete Bulk-Schreibaktionen haben real Schaden
verursacht (240 Backups / 123 GB). Daher commit-Flag + Idempotenz + Manifest.
"""

from __future__ import annotations

import argparse
import json
import socket
from datetime import datetime
from pathlib import Path

import lock_utils

BULK_MARKER = "bulk"
DEFAULT_MANIFEST = Path.home() / ".lock_watcher" / "bulk_lock_session.json"


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M")


def _lock_body(owner: str, created: str, host: str, reason: str) -> str:
    return (
        "# LOCK.txt — zentrale Sofortsperrung (bulk)\n"
        f"owner: {owner}\n"
        f"created: {created}\n"
        f"host: {host}\n"
        f"created_by: {BULK_MARKER}\n"
        "expires_after: 24h\n"
        "mode: hard\n"
        f"purpose: {reason or 'Zentrale Sofortsperrung'}\n"
        "scope: project\n"
    )


def _has_active_lock(root: Path) -> bool:
    if lock_utils.active_locks(root):
        return True
    # User locks count as permanently locking — even when nominally expired
    # (protection invariant: never additionally bulk-lock a user-lock folder).
    for name, _scope, _legacy in lock_utils.find_lock_files(root):
        if lock_utils.is_protected_lock(root / name):
            return True
    return False


def _is_bulk_lock(path: Path) -> bool:
    if path.name != "LOCK.txt":
        return False
    data = lock_utils.parse_lock_file(path)
    return data.get("created_by", "").strip().lower() == BULK_MARKER


def bulk_lock(roots, owner: str = "user", reason: str = "", host: str | None = None,
              created: str | None = None, commit: bool = False,
              manifest_path: Path | None = None) -> dict:
    """Setzt LOCK.txt (created_by: bulk) in jedem Root ohne aktiven Lock.

    commit=False -> Dry-Run. Returns Statistik-Dict."""
    host = host or socket.gethostname()
    created = created or _now_iso()
    locked, skipped, would = [], [], []

    for root in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        if _has_active_lock(root):
            skipped.append(str(root))
            continue
        target = root / "LOCK.txt"
        if not commit:
            would.append(str(target))
            continue
        target.write_text(_lock_body(owner, created, host, reason), encoding="utf-8")
        locked.append(str(target))

    result = {
        "locked": len(locked), "skipped": len(skipped), "would_lock": len(would),
        "locked_paths": locked, "skipped_roots": skipped, "would_lock_paths": would,
        "committed": commit, "created": created,
    }
    if commit and locked:
        mpath = manifest_path or DEFAULT_MANIFEST
        try:
            mpath.parent.mkdir(parents=True, exist_ok=True)
            mpath.write_text(json.dumps(
                {"created": created, "owner": owner, "reason": reason, "locks": locked},
                ensure_ascii=False, indent=2), encoding="utf-8")
            result["manifest"] = str(mpath)
        except OSError:
            result["manifest"] = None
    return result


def bulk_unlock(roots=None, manifest_path: Path | None = None, commit: bool = False) -> dict:
    """Entfernt NUR bulk-gesetzte LOCK.txt; niemals User-/manuelle Locks.

    Quelle der zu pruefenden Roots: explizite `roots` ODER das Session-Manifest.
    commit=False -> Dry-Run."""
    candidates: list[Path] = []
    if roots is not None:
        for root in roots:
            candidates.append(Path(root) / "LOCK.txt")
    else:
        mpath = manifest_path or DEFAULT_MANIFEST
        try:
            data = json.loads(mpath.read_text(encoding="utf-8"))
            candidates = [Path(p) for p in data.get("locks", [])]
        except (OSError, json.JSONDecodeError):
            candidates = []

    unlocked, kept = [], []
    for path in candidates:
        # Doppelter Schutz: nur LOCK.txt mit created_by: bulk, nie geschuetzte Locks.
        if not path.exists() or lock_utils.is_protected_lock(path) or not _is_bulk_lock(path):
            kept.append(str(path))
            continue
        if not commit:
            unlocked.append(str(path))
            continue
        try:
            path.unlink()
            unlocked.append(str(path))
        except OSError:
            kept.append(str(path))

    return {"unlocked": len(unlocked), "kept": len(kept),
            "unlocked_paths": unlocked, "committed": commit}


def _load_roots_from_config() -> list[Path]:
    """Top-level roots from lock_roots.json (the connected project folders) —
    NOT every sub-directory. Immediate lockdown acts on the roots themselves."""
    from lock_scan import DEFAULT_ROOTS_FILE, load_config
    config = load_config(DEFAULT_ROOTS_FILE)
    roots = []
    for entry in config.get("roots", []):
        p = Path(entry["path"])
        if p.is_dir():
            roots.append(p)
    return list(dict.fromkeys(roots))


def main() -> int:
    parser = argparse.ArgumentParser(description="Zentrale Sofortsperrung/-freigabe (LOCK.txt).")
    parser.add_argument("action", choices=["lock", "unlock"])
    parser.add_argument("--owner", default="user")
    parser.add_argument("--reason", default="")
    parser.add_argument("--commit", action="store_true",
                        help="Tatsaechlich schreiben/loeschen (sonst Dry-Run).")
    args = parser.parse_args()

    roots = _load_roots_from_config()
    if args.action == "lock":
        res = bulk_lock(roots, owner=args.owner, reason=args.reason, commit=args.commit)
    else:
        res = bulk_unlock(roots, commit=args.commit)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
