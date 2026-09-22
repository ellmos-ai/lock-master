#!/usr/bin/env python3
r"""Prueft einen TEILWEISEN Deploy dieses Klons gegen sein Deploy-Manifest.

Warum es das gibt (T-20260913-715231627): Die Dateien dieses Repos werden nicht
als Vollspiegel ausgeliefert, sondern einzeln in einen gemischten Werkzeugordner
kopiert -- auf diesem System `<OneDrive>/_scripts`, in dem auch fremde Skripte
liegen. Fuer so einen Teil-Deploy greift weder `repo_mirror.py` (erwartet einen
Vollspiegel samt `REPO.pointer.json`) noch ein `git ls-files`-Vergleich.

Ohne Vertrag ist genau das passiert, was das Ticket aufgearbeitet hat: Beide
Seiten wurden ueber Monate unabhaengig weiterentwickelt, bis jede
Sicherheitsfunktionen trug, die der anderen fehlten -- der Fail-closed-Guard fuer
mehrdeutige Locknamen lag im Klon und lief nie, Worktree-Fix und Hook-Guards
lagen im Deploy und waren nicht versioniert. Niemand hat es gemerkt, weil nichts
die beiden Baeume je verglichen hat.

Das Manifest liegt beim DEPLOY (nicht im Repo): `<ziel>/DEPLOY.lock-master.json`.
Dort steht, welche Zieldatei aus welcher Quelldatei dieses Repos stammt. So
beschreibt sich der Zielordner selbst, und der Check funktioniert auch dann, wenn
verschiedene Hosts verschiedene Teilmengen deployen.

Verglichen wird CRLF-normalisiert: OneDrive und Windows-Editoren aendern
Zeilenenden, ohne dass sich der Inhalt aendert -- ein Byte-Vergleich wuerde hier
Dauer-Alarm schlagen und damit nichts mehr melden.

Seit T-20260920-699139879 kann dasselbe Werkzeug den Deploy auch AUSFUEHREN.
Vorher gab es nur den Pruefer, und die Auslieferung war Handarbeit -- weshalb
`lock_create.py` monatelang fehlte, obwohl die zentrale Regel fuers Lock-Setzen
auf genau diesen Ordner verweist. Ein Werkzeug, das einen Missstand nur meldet,
laesst ihn bestehen.

`--apply` ist bewusst asymmetrisch, denn die beiden Faelle sind es auch:

    nicht-deployt   wird kopiert. Da ist nichts zu verlieren.
    ABWEICHEND      wird NICHT angefasst, ausser mit zusaetzlichem --force.

Genau im zweiten Fall ist die Gabelung aus T-20260913-715231627 entstanden: Der
Deploy trug Sicherheitsfunktionen, die der Klon nicht hatte. Wer ihn blind
ueberschreibt, loescht sie. Mit --force wird die Vorversion vorher nach
`_deploy-backups/` gelegt.

Kopiert wird byteweise, nicht zeilenweise: so kommen die Zeilenenden der Quelle
mit und der CRLF-Drift verschwindet, den eine robocopy-Spiegelung hinterlaesst.

Aufruf:
    python bin/check_deployment.py <zielordner>
    python bin/check_deployment.py <zielordner> --json
    python bin/check_deployment.py <zielordner> --apply [--force] [--dry-run]

Exit 0 = Deploy entspricht dem Klon (bzw. --apply hat ihn hergestellt).
Exit 1 = mindestens eine Datei weicht ab oder fehlt (Drift).
Exit 2 = Aufruffehler (Ordner/Manifest fehlt) -- fail-closed, nie "alles gut".
"""
from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_NAME = "DEPLOY.lock-master.json"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")


def check(target: Path) -> tuple[list[dict], str | None]:
    manifest_path = target / MANIFEST_NAME
    if not manifest_path.is_file():
        return [], f"Kein Deploy-Manifest: {manifest_path}"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        return [], f"Manifest ist kein gueltiges JSON: {exc}"

    # Nicht jede Zieldatei ist eine Kopie. Manche sind absichtlich ein duenner
    # Shim, der an die Fassung im Klon delegiert -- fuer die waere "ABWEICHEND"
    # dauerhaft richtig und damit wertlos: ein Check, der immer rot ist, wird
    # nicht mehr gelesen. Solche Eintraege stehen im Manifest unter "shims" und
    # werden nur auf Existenz geprueft.
    shims = manifest.get("shims", {})
    rows: list[dict] = []
    for dest_name, source_rel in sorted(manifest.get("files", {}).items()):
        dest, source = target / dest_name, REPO_ROOT / source_rel
        if not source.is_file():
            rows.append({"file": dest_name, "status": "quelle-fehlt", "source": source_rel})
        elif not dest.is_file():
            rows.append({"file": dest_name, "status": "nicht-deployt", "source": source_rel})
        elif _read(dest) != _read(source):
            rows.append({"file": dest_name, "status": "ABWEICHEND", "source": source_rel})
        else:
            rows.append({"file": dest_name, "status": "ok", "source": source_rel})
    for dest_name, note in sorted(shims.items()):
        dest = target / dest_name
        rows.append({
            "file": dest_name,
            "status": "shim" if dest.is_file() else "shim-fehlt",
            "source": str(note),
        })
    return rows, None


def apply(target: Path, rows: list[dict], force: bool, dry_run: bool) -> list[str]:
    """Fehlende Dateien ausliefern; abweichende nur auf ausdrueckliches --force.

    Gibt die Meldungszeilen zurueck. Byteweise Kopie, damit die Zeilenenden der
    Quelle gelten und der CRLF-Drift einer robocopy-Spiegelung verschwindet.
    """
    notes: list[str] = []
    backups = target / "_deploy-backups" / datetime.now().strftime("%Y%m%dT%H%M%S")
    for row in rows:
        if row["status"] not in ("nicht-deployt", "ABWEICHEND"):
            continue
        dest, source = target / row["file"], REPO_ROOT / row["source"]
        if row["status"] == "ABWEICHEND" and not force:
            notes.append(
                f"  uebersprungen  {row['file']}   (ABWEICHEND -- erst den Diff lesen, "
                "dann --force; der Deploy kann etwas tragen, das der Klon nicht hat)"
            )
            continue
        if dry_run:
            notes.append(f"  wuerde-kopieren {row['file']}   (aus {row['source']})")
            continue
        if dest.is_file():
            backups.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(dest, backups / row["file"])
        shutil.copyfile(source, dest)
        row["status"] = "ok"
        notes.append(f"  kopiert        {row['file']}   (aus {row['source']})")
    if backups.is_dir():
        notes.append(f"  Vorversionen liegen in {backups}")
    return notes


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    as_json = "--json" in argv
    do_apply = "--apply" in argv
    force = "--force" in argv
    dry_run = "--dry-run" in argv
    if len(args) != 1:
        print(__doc__.strip().splitlines()[0])
        print("Aufruf: python bin/check_deployment.py <zielordner> "
              "[--json] [--apply [--force] [--dry-run]]")
        return 2
    if force and not do_apply:
        print("FEHLER: --force wirkt nur zusammen mit --apply.")
        return 2

    target = Path(args[0])
    if not target.is_dir():
        print(f"FEHLER: Zielordner existiert nicht: {target}")
        return 2

    rows, err = check(target)
    if err:
        print(f"FEHLER: {err}")
        return 2

    applied: list[str] = []
    if do_apply:
        applied = apply(target, rows, force, dry_run)

    drift = [r for r in rows if r["status"] not in ("ok", "shim")]
    if as_json:
        print(json.dumps({"target": str(target), "files": rows, "applied": applied},
                         ensure_ascii=False, indent=2))
        return 1 if drift else 0

    for line in applied:
        print(line)
    for r in drift:
        print(f"  {r['status']:14s} {r['file']}   (Quelle: {r['source']})")
    if drift:
        print(f"\nDRIFT: {len(drift)} von {len(rows)} Dateien weichen ab.")
        print("Der Klon ist die Quelle -- neu deployen, ODER (wenn der Deploy etwas")
        print("traegt, das der Klon nicht hat) erst zurueckfuehren. NICHT blind")
        print("ueberschreiben: genau so ist die Gabelung aus T-20260913-715231627")
        print("entstanden, bei der jede Seite eine Sicherheitsfunktion der anderen")
        print("nicht hatte.")
        return 1
    shim_count = sum(1 for r in rows if r["status"] == "shim")
    suffix = f", davon {shim_count} bewusste Shim(s)" if shim_count else ""
    print(f"Deploy entspricht dem Klon ({len(rows)} Eintraege{suffix}, CRLF-normalisiert).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
