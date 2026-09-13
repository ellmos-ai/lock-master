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

Aufruf:
    python bin/check_deployment.py <zielordner>
    python bin/check_deployment.py <zielordner> --json

Exit 0 = Deploy entspricht dem Klon.
Exit 1 = mindestens eine Datei weicht ab oder fehlt (Drift).
Exit 2 = Aufruffehler (Ordner/Manifest fehlt) -- fail-closed, nie "alles gut".
"""
from __future__ import annotations

import json
import sys
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
    return rows, None


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    as_json = "--json" in argv
    if len(args) != 1:
        print(__doc__.strip().splitlines()[0])
        print("Aufruf: python bin/check_deployment.py <zielordner> [--json]")
        return 2

    target = Path(args[0])
    if not target.is_dir():
        print(f"FEHLER: Zielordner existiert nicht: {target}")
        return 2

    rows, err = check(target)
    if err:
        print(f"FEHLER: {err}")
        return 2

    drift = [r for r in rows if r["status"] != "ok"]
    if as_json:
        print(json.dumps({"target": str(target), "files": rows}, ensure_ascii=False, indent=2))
        return 1 if drift else 0

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
    print(f"Deploy entspricht dem Klon ({len(rows)} Dateien, CRLF-normalisiert).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
