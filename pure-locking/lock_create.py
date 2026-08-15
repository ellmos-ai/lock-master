#!/usr/bin/env python3
"""lock_create.py -- stamp a new LOCK*.txt into a project directory.

Convenience companion to LOCK_TEMPLATE.txt: builds the correct file name for
exclusive, scoped, team, user and condition locks, fills in the standard
fields and refuses to clobber an existing lock unless --force is given.

Examples:

    python lock_create.py /path/to/project --owner my-agent --purpose "refactor"
    python lock_create.py /path/to/project --scope docs --expires 90m
    python lock_create.py /path/to/project --team LAPTOP --scope assets
    python lock_create.py /path/to/project --user
    python lock_create.py /path/to/project --condition --scope publish \
        --release-condition "PR #42 merged"

Zero dependencies (Python 3.10+). See LOCK-SYSTEM.md for the semantics.
"""
from __future__ import annotations

import argparse

import contested
import platform
import sys
from datetime import datetime
from pathlib import Path

from lock_utils import is_lock_file

_SCOPE_OK = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"


def _check_segment(value: str, what: str) -> str:
    """Validate a file-name segment (scope or team host)."""
    if not value or any(c not in _SCOPE_OK for c in value):
        raise SystemExit(
            f"error: invalid {what} {value!r} -- allowed: letters, digits, '_', '-'"
        )
    return value


def build_lock_name(scope: str | None, team_host: str | None,
                    user: bool, condition: bool) -> str:
    """Build the LOCK*.txt file name from the requested lock kind."""
    if sum(bool(x) for x in (team_host, user, condition)) > 1:
        raise SystemExit("error: --team, --user and --condition are mutually exclusive")
    parts: list[str] = ["LOCK"]
    if user:
        parts.append("user")
    elif condition:
        parts.append("condition")
    elif team_host:
        parts.append("team")
    if scope:
        parts.append(_check_segment(scope, "scope"))
    if team_host:
        parts.append(_check_segment(team_host, "team host"))
    parts.append("txt")
    return ".".join(parts)


def build_lock_body(args: argparse.Namespace, scope_label: str) -> str:
    """Render the key: value body of the lock file."""
    lines = [
        f"owner: {args.owner}",
        # Sekundengenau: Bei Minutengenauigkeit landen zwei fast gleichzeitige
        # Anspruechen im Host-Tiebreak, und dort verliert derselbe Host
        # strukturell immer. lock_utils._parse_created liest Sekunden bereits.
        f"created: {datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}",
        f"host: {args.host}",
    ]
    if args.user:
        lines.append("removable_by: user")
    elif args.condition:
        # Condition locks never expire by time; the condition gates removal.
        pass
    else:
        lines.append(f"expires_after: {args.expires}")
    if args.release_condition:
        lines.append(f"release_condition: {args.release_condition}")
    lines.append(f"mode: {args.mode}")
    if args.purpose:
        lines.append(f"purpose: {args.purpose}")
    lines.append(f"scope: {scope_label}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create a LOCK*.txt in a project directory."
    )
    parser.add_argument("project_dir", type=Path,
                        help="project directory that should be locked")
    parser.add_argument("--scope", default=None,
                        help="component scope (LOCK.<scope>.txt); default: whole project")
    parser.add_argument("--team", metavar="HOST", default=None,
                        help="create a team lock for this system host name")
    parser.add_argument("--user", action="store_true",
                        help="create a user lock (only the user removes it)")
    parser.add_argument("--condition", action="store_true",
                        help="create a condition lock (requires --release-condition)")
    parser.add_argument("--owner", default=None,
                        help="lock owner (default: lock_create/<host>)")
    parser.add_argument("--host", default=None,
                        help="host name (default: this machine's name)")
    parser.add_argument("--expires", default="24h",
                        help="expiry duration for time-based locks (default: 24h)")
    parser.add_argument("--mode", choices=("hard", "soft"), default="hard",
                        help="hard = no changes (default), soft = read/notice ok")
    parser.add_argument("--purpose", default=None,
                        help="free-text: why the area is locked")
    parser.add_argument("--release-condition", default=None,
                        help="free-text release condition (required for --condition)")
    parser.add_argument("--contested", action="store_true",
                        help="Konfliktverfahren erzwingen (Quarantaene + Entscheid)")
    parser.add_argument("--no-contest", action="store_true",
                        help="Konfliktverfahren unterdruecken, auch im Cloud-Ordner")
    parser.add_argument("--quarantine", type=int,
                        default=contested.DEFAULT_QUARANTINE_SECONDS,
                        help="Wartezeit des Konfliktverfahrens in Sekunden")
    parser.add_argument("--verbose-contest", action="store_true",
                        help="auch melden, wenn das Verfahren uebersprungen wird")
    parser.add_argument("--force", action="store_true",
                        help="overwrite an existing lock file of the same name")
    args = parser.parse_args(argv)

    if args.condition and not args.release_condition:
        parser.error("--condition requires --release-condition")

    project_dir: Path = args.project_dir
    if not project_dir.is_dir():
        raise SystemExit(f"error: not a directory: {project_dir}")

    args.host = args.host or platform.node() or "unknown"
    args.owner = args.owner or f"lock_create/{args.host}"

    name = build_lock_name(args.scope, args.team, args.user, args.condition)
    if not is_lock_file(name):  # defence in depth: must match the canonical regex
        raise SystemExit(f"error: generated name {name!r} is not a valid lock name")

    lock_path = project_dir / name
    scope_label = args.scope or "project"
    body = build_lock_body(args, scope_label)

    # Exklusiv anlegen statt exists()-pruefen-dann-schreiben: Zwischen Pruefung
    # und Schreiben liegt sonst ein Fenster, in dem ein zweiter Prozess dieselbe
    # Datei anlegt -- beide halten sich fuer den Inhaber. "x" laesst das
    # Dateisystem entscheiden, atomar und ohne Fenster.
    if args.force:
        lock_path.write_text(body, encoding="utf-8")
    else:
        try:
            with lock_path.open("x", encoding="utf-8") as handle:
                handle.write(body)
        except FileExistsError:
            raise SystemExit(
                f"error: {lock_path} already exists (use --force to overwrite)"
            ) from None
    print(f"created: {lock_path}")

    # --- Stufe 2: gleichzeitiger Anspruch ueber einen Sync-Ordner ----------
    # Exklusives Anlegen schuetzt nur lokal. Liegt der Bereich in einem
    # synchronisierten Ordner, sehen zwei Hosts einander beim Anlegen gar nicht
    # -- dafuer gibt es das Verfahren in contested.py. Es laeuft nicht immer,
    # sondern wenn es sich rechnet.
    decision = contested.should_contest(
        project_dir,
        force=True if args.contested else (False if args.no_contest else None),
    )
    if not decision.contest:
        if args.contested or args.verbose_contest:
            print(f"contest: uebersprungen ({decision.reason})")
        return 0

    print(
        f"contest: {decision.reason}; Quarantaene {args.quarantine}s, "
        "danach Entscheidung"
    )
    result = contested.contest(project_dir, lock_path, quarantine_seconds=args.quarantine)
    if result.won:
        print(f"contest: gewonnen ({result.reason})")
        return 0

    # Verlierer geben den Bereich frei -- sonst blockieren zwei Locks einander
    # bis zum Verfall, und der Gewinner arbeitet gegen einen fremden Anspruch.
    try:
        lock_path.unlink()
    except OSError as exc:  # pragma: no cover - Freigabe soll nie hart scheitern
        print(f"contest: verloren, Lock konnte nicht entfernt werden: {exc}")
        return 1
    print(f"contest: verloren ({result.reason}) -- eigener Lock entfernt")
    return 3


if __name__ == "__main__":
    sys.exit(main())
