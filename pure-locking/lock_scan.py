r"""
lock_scan.py — Systemweiter, READ-ONLY Ueberblick aller aktiven Projekt-Sperren

Listet alle aktiven (nicht abgelaufenen) LOCK*.txt ueber alle in lock_roots.json
konfigurierten Wurzeln: Pfad, Scope, Owner, created, Restzeit bis Verfall.
Legacy TEST.txt/TESTS.txt werden als aktiv mitgelistet (kein Verfall-Format).

Read-only by default: ohne --write-cache wird nichts geschrieben. Mit --write-cache
werden ausschliesslich die abgeleiteten LOCK-CACHE.md-Artefakte geschrieben
(LOCK*.txt selbst werden nie veraendert).

Aufruf:
  PYTHONIOENCODING=utf-8 python lock_scan.py
  PYTHONIOENCODING=utf-8 python lock_scan.py --json
  PYTHONIOENCODING=utf-8 python lock_scan.py --write-cache
  PYTHONIOENCODING=utf-8 python lock_scan.py --roots-file <pfad>

--write-cache schreibt zwei feste Caches (auto-generiert, nicht manuell editieren):
  - systemweit: _scripts/LOCK-CACHE.md            (alle aktiven Locks ueber alle Roots)
  - nur Software: .SOFTWARE/LOCK-CACHE.md         (nur Locks unterhalb von .SOFTWARE)

Kanonik: LOCK-SYSTEM.md (gleicher Ordner). Format-/Verfall-Logik: lock_utils.py.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import lock_utils

DEFAULT_ROOTS_FILE = Path(__file__).resolve().parent / "lock_roots.json"

# Feste Cache-Zielpfade (Punkt 1, T-37): systemweit + nur Software.
SYSTEM_CACHE_PATH = Path(__file__).resolve().parent / "LOCK-CACHE.md"


def load_config(roots_file: Path) -> dict:
    """Konfiguration laden. Fehlt die Datei, greift lock_roots.example.json,
    sonst konservative Defaults -- ein fehlendes Konfigfile darf nicht dazu
    fuehren, dass gar nichts gescannt wird.

    Reissverschluss-Merge T-20260913-715231627: Struktur (example-Fallback,
    Expansion auch fuer caches/filter_prefix) stammt aus dem Klon lock-master;
    die Pfadaufloesung bleibt _resolve_root_path, weil nur sie den Host-Fallback
    fuer alte Konfigurationen mit hartkodiertem C:/Users/User kennt -- auf
    WORKSTATION-LG heisst der Nutzer lukas, dort waeren solche Roots sonst
    still unsichtbar."""
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
            entry["path"] = str(_resolve_root_path(entry["path"]))

    for entry in config.get("caches", []):
        if "path" in entry:
            entry["path"] = str(_resolve_root_path(entry["path"]))
        if entry.get("filter_prefix"):
            entry["filter_prefix"] = str(_resolve_root_path(entry["filter_prefix"]))

    return config


def _resolve_root_path(raw_path: str) -> Path:
    expanded = Path(os.path.expandvars(os.path.expanduser(raw_path)))
    if expanded.exists():
        return expanded

    # Alte Konfigurationen nutzten C:\Users\User als Platzhalter. Auf lokalen
    # Workstations soll der Scanner trotzdem den aktuellen USERPROFILE-Root sehen.
    marker = r"C:\Users\User"
    if raw_path.lower().startswith(marker.lower()):
        home = os.environ.get("USERPROFILE")
        if home:
            candidate = Path(home + raw_path[len(marker):])
            if candidate.exists():
                return candidate
            return candidate
    return expanded


def iter_lock_dirs(config: dict):
    """Generator ueber alle zu pruefenden Verzeichnisse aller Roots,
    mit Tiefenbegrenzung und Skip-Listen aus der Konfiguration.
    Yields Path-Objekte (Verzeichnisse), in denen LOCK*.txt gesucht wird."""
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
        return "abgelaufen"
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
            lock_type = lock_utils.lock_type_from_name(name)
            if is_legacy:
                remaining = "legacy"
            elif lock_type == "user":
                remaining = "user-held (kein Zeitverfall)"
            elif lock_type == "condition":
                cond = data.get("release_condition", "?")
                remaining = f"bis Bedingung erfuellt: {cond}"
            elif lock_type == "until":
                moment = lock_utils.lock_not_before(lock_path)
                if moment is None:
                    remaining = ("not_before FEHLT -> gilt unbefristet "
                                 "(fail-closed)")
                elif now > moment:
                    remaining = (f"Frist abgelaufen {moment.isoformat(timespec='minutes')}"
                                 " - Waechter darf aufhoeren; Datei bleibt fuer den Nutzer")
                else:
                    remaining = (f"{_format_remaining(moment - now)} "
                                 f"(bis {moment.isoformat(timespec='minutes')})")
            else:
                remaining = _format_remaining((created + expires) - now)
            out.append({
                "path": str(lock_path),
                "scope": scope,
                "legacy": is_legacy,
                "lock_type": lock_type,
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
    """Pipe in Tabellenzellen escapen, Zeilenumbrueche entfernen."""
    return value.replace("|", "\\|").replace("\n", " ").replace("\r", " ").strip()


def render_cache(locks: list[dict], scanned_at: datetime, title: str) -> str:
    """Rendert eine LOCK-CACHE.md (auto-generiert) aus der Lock-Liste."""
    lines = [
        "<!-- AUTO-GENERIERT von lock_scan.py --write-cache. NICHT manuell editieren. "
        "Authoritativ sind die LOCK*.txt-Dateien selbst. -->",
        "",
        f"# {title}",
        "",
        f"Stand: {scanned_at.isoformat(timespec='seconds')}",
        "",
        f"Aktive Locks: {len(locks)}",
        "",
        "| Projekt/Pfad | scope | owner | created | Restzeit |",
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
        lines.append("| _(keine aktiven Locks)_ |  |  |  |  |")
    return "\n".join(lines) + "\n"


def write_caches(locks: list[dict], scanned_at: datetime, config: dict) -> list[tuple[Path, int]]:
    """Schreibt die Cache-Datei(en) aus dem 'caches'-Block von lock_roots.json.
    Ohne Konfiguration ein einzelner systemweiter Cache neben diesem Skript.
    Jeder Eintrag kann ein 'filter_prefix' tragen, das die aufgenommenen Locks
    einschraenkt. Returns Liste von (Pfad, Anzahl) je geschriebenem Cache.

    Reissverschluss-Merge T-20260913-715231627: aus dem Klon lock-master
    uebernommen. Die vorherige _scripts-Fassung hatte die zwei Cache-Ziele
    HARTKODIERT und ignorierte den 'caches'-Block -- auf einem Host mit
    anderer Ablage schrieb sie damit an die falsche Stelle."""
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


def write_twin_index(config: dict) -> tuple[Path, int] | None:
    """Schreibt TWIN-INDEX.json (repo_name -> Zwillingsverzeichnisse) aus den
    REPO.pointer.json-Dateien der ohnehin durchlaufenen Scan-Verzeichnisse.
    Ohne diesen Index kann --check-dir die Klon->OneDrive-Richtung der
    Zwei-Baeume-Regel nicht aufloesen (T-20260913-785936980)."""
    twin_cfg = config.get("twin_resolution") or {}
    raw = twin_cfg.get("index_path")
    if not raw:
        return None
    index_path = Path(os.path.expandvars(str(raw)))
    index = lock_utils.build_twin_index(iter_lock_dirs(config))
    index_path.write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return index_path, len(index["by_repo_name"])


def _check_dir(target: Path, as_json: bool, strict: bool = False,
               roots_file: Path | None = None) -> int:
    """--check-dir: worktree-bewusster Lock-Check fuer EIN Verzeichnis (kein
    Vollscan). Siehe lock_utils.active_locks_for_path().

    Meldet zusaetzlich aktive Git-Guard-Hooks (pre-push/pre-commit/
    pre-receive) fuer `target` -- ein Verzeichnis kann keine LOCK-Datei
    haben und trotzdem beim Push durch einen Hook blockiert werden, den
    dieser Check bisher nie sah (T-20260906-910508487). "Frei" bedeutet
    unten weiterhin nur "keine LOCK-Datei", nicht "pushbar". Hooks aendern
    die Exit-0/1-Semantik NICHT, ausser --strict ist gesetzt (dann:
    2 = keine LOCK-Datei, aber ein Guard-Hook vorhanden)."""
    target = target.resolve()
    now = datetime.now()
    hits = lock_utils.active_locks_for_path(target, now)

    # Zwei-Baeume-Regel: der Zwilling im jeweils anderen Baum zaehlt mit
    # (T-20260913-785936980). Ein frischer Klon kann den unversionierten
    # LOCK.user.* gar nicht enthalten -- ohne diesen Schritt liest man aus
    # einer leeren Stelle einen Zustand.
    twin_cfg = (load_config(roots_file or DEFAULT_ROOTS_FILE) or {}).get("twin_resolution")
    twins, twin_status = lock_utils.twin_dirs_for_path(target, twin_cfg)
    for twin in twins:
        hits.extend(
            (name, scope, legacy, twin)
            for name, scope, legacy in lock_utils.active_locks(twin, now)
        )

    rows = [
        {
            "path": str(source_dir / name),
            "scope": scope,
            "legacy": is_legacy,
            "from_main_repo_of_worktree": source_dir != target,
            "from_twin": source_dir in twins,
        }
        for name, scope, is_legacy, source_dir in hits
    ]
    guards = lock_utils.git_hook_guards(target)

    if as_json:
        print(json.dumps(
            {"checked": str(target), "locks": rows, "hook_guards": guards,
             "twin_status": twin_status,
             "twins_checked": [str(t) for t in twins]},
            ensure_ascii=False, indent=2,
        ))
        if rows:
            return 1
        if twin_status in ("index-missing", "pointer-unreadable"):
            return 1
        return 2 if (strict and guards) else 0

    if not rows:
        if twin_status == "pointer-unreadable":
            print(
                f"lock_scan --check-dir: {target} -- UNBESTIMMT, nicht 'frei'. "
                f"Der OneDrive-Zwilling traegt eine {lock_utils.REPO_POINTER_NAME}, "
                "die nicht lesbar ist -- seine Locks sind damit unsichtbar. "
                "Pointer reparieren, dann lock_scan.py --write-cache."
            )
        elif twin_status == "index-missing":
            print(
                f"lock_scan --check-dir: {target} -- UNBESTIMMT, nicht 'frei'. "
                "Der Pfad liegt unter einem Klon-Root, aber der Zwillings-Index "
                f"({lock_utils.TWIN_INDEX_NAME}) fehlt oder ist unlesbar. Ein "
                "unversionierter LOCK.user.* am OneDrive-Zwilling waere hier "
                "unsichtbar (T-20260913-785936980). Index erzeugen: "
                "lock_scan.py --write-cache"
            )
        else:
            suffix = f", {len(twins)} Zwilling(e) mitgeprueft" if twins else ""
            print(
                f"lock_scan --check-dir: {target} ist frei "
                f"(inkl. Hauptklon, falls Worktree{suffix})."
            )
    else:
        print(f"lock_scan --check-dir: {len(rows)} aktive Sperre(n) betreffen {target}:")
        for r in rows:
            if r["from_twin"]:
                tag = "  (aus dem Zwilling im anderen Baum)"
            elif r["from_main_repo_of_worktree"]:
                tag = "  (aus Hauptklon des Worktrees)"
            else:
                tag = ""
            legacy = " [LEGACY]" if r["legacy"] else ""
            print(f"  {r['path']}{legacy}  scope={r['scope']}{tag}")

    for g in guards:
        if g["hook"] is None:
            print(f"  GUARD-HINWEIS: {g['hint']} ({g['path']})")
            continue
        tag = "  [EMBARGO-SIGNATUR]" if g["embargo"] else ""
        print(f"  GUARD: {g['hook']} ({g['hint']}){tag}")
    if guards:
        print(
            "  HINWEIS: 'frei' oben heisst keine LOCK-Datei -- NICHT 'pushbar'. "
            "Der/die obige(n) Guard(s) koennen die Aktion trotzdem blockieren."
        )

    if rows:
        return 1
    if twin_status in ("index-missing", "pointer-unreadable"):
        return 1
    return 2 if (strict and guards) else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Listet alle aktiven Projekt-Sperren (LOCK*.txt) systemweit (read-only)."
    )
    parser.add_argument("--json", action="store_true", help="Ausgabe als JSON.")
    parser.add_argument("--write-cache", action="store_true",
                        help="Schreibt LOCK-CACHE.md (systemweit + nur .SOFTWARE).")
    parser.add_argument("--roots-file", default=str(DEFAULT_ROOTS_FILE),
                        help="Pfad zu lock_roots.json.")
    parser.add_argument("--check-dir", metavar="PFAD",
                        help="Schneller, WORKTREE-BEWUSSTER Check fuer GENAU dieses "
                        "eine Verzeichnis (Stufe-1-Check vor Arbeitsbeginn) statt "
                        "des Vollscans aller Roots. Liegt PFAD in einem git-Worktree, "
                        "wird zusaetzlich der zugehoerige Hauptklon geprueft "
                        "(T-20260903-592302105). Meldet zusaetzlich aktive Git-Guard-"
                        "Hooks (pre-push/pre-commit/pre-receive) fuer PFAD -- 'frei' "
                        "heisst keine LOCK-Datei, nicht 'pushbar' "
                        "(T-20260906-910508487). Exit 0 = frei, 1 = gesperrt, "
                        "2 = frei aber Guard-Hook vorhanden (nur mit --strict).")
    parser.add_argument("--strict", action="store_true",
                        help="Mit --check-dir: Exit 2 wenn keine LOCK-Datei, aber "
                        "ein Guard-Hook vorhanden ist (Standard: Guard-Hooks werden "
                        "gemeldet, aendern aber den Exit-Code nicht).")
    args = parser.parse_args()

    if args.check_dir:
        return _check_dir(Path(args.check_dir), args.json, args.strict,
                          Path(args.roots_file))

    config = load_config(Path(args.roots_file))
    scanned_at = datetime.now()
    locks = collect_locks(config, scanned_at)

    if args.write_cache:
        written = write_caches(locks, scanned_at, config)
        for path, count in written:
            print(f"lock_scan --write-cache: {path} ({count} aktive Locks)")
        twin = write_twin_index(config)
        if twin is not None:
            print(f"lock_scan --write-cache: {twin[0]} ({twin[1]} Repo-Zwillinge)")
        return 0

    if args.json:
        print(json.dumps(locks, ensure_ascii=False, indent=2))
        return 0

    if not locks:
        print("lock_scan: keine aktiven Locks gefunden.")
        return 0

    print(f"lock_scan: {len(locks)} aktive Lock(s):")
    for r in locks:
        legacy = " [LEGACY]" if r["legacy"] else ""
        owner = r["owner"] or "?"
        print(f"  {r['path']}{legacy}")
        print(f"      scope={r['scope']} owner={owner} created={r['created']} "
              f"({r['created_source']}) Restzeit={r['remaining']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
