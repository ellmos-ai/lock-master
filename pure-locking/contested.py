r"""contested.py -- Auflösung gleichzeitiger Claims über synchronisierte Ordner.

Das Lock-System benennt das Problem selbst (`LOCK-SYSTEM.md`, „Lock-by-default"):
*„Zwei gleichzeitig startende Agenten sehen sonst beide ‚leer' → Kollision."*
Gelöst ist bisher nur der Fall, dass einer erkennbar zuerst da war. Bei
Cloud-Sync mit 30 s – 5 min Latenz sehen aber **beide** einen leeren Ordner,
beide legen an, und beide halten sich für den Inhaber. Das ist kein Randfall:
Zeitgesteuerte Automationen starten auf mehreren Hosts zur selben Uhrzeit.

Dieses Modul liefert das fehlende Stück -- ohne Server, ohne Datenbank:

    1. ANLEGEN      Lock exklusiv schreiben (kein check-then-write)
    2. QUARANTAENE  warten, bis der Sync die Sicht angeglichen hat
    3. RECHECK      Verzeichnis erneut lesen
    4. ENTSCHEID    frühestes `created` gewinnt; Gleichstand -> kleinerer Host
    5. VERLIERER    eigenen Lock entfernen und den Bereich freigeben

Beide Seiten kommen bei gleicher Datenlage zum selben Ergebnis.

**Wofür es gilt.** Die Regel braucht zwei *sichtbare* Ansprüche. Das sind
Team-Locks: Sie tragen den Host im Namen, koexistieren also im Verzeichnis, und
wirken laut Spec für fremde Systeme wie ein Exclusive Lock -- für den Fall
gleicher Scopes war bislang offen, wer gewinnt. Exclusive Locks (`LOCK.txt`)
tragen keinen Host; dort verhindert Stufe 1 den lokalen Wettlauf, während der
Cloud-Dienst bei echter Gleichzeitigkeit eine Konfliktkopie erzeugt, die
sichtbar bleibt statt still zu überschreiben.

**Wann es sich lohnt.** Ein Vorlauf von Minuten ist teuer für einen kurzen
Edit und billig für einen Automationslauf, der danach Stunden arbeitet. Deshalb
ist das Verfahren nicht der Default, sondern wird angetestet, wenn es sich
rechnet: Liegt der Bereich überhaupt in einem Cloud-Ordner (`cloud_pressure`)
und läuft hier eine Automation? Nur dann. Das ist die Umkehrung des sonst
üblichen Reflexes, bei erkanntem Cloud-Druck *gar nicht* zu arbeiten -- ein
Protokoll ist die bessere Antwort als Verzicht.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import lock_utils

# --- Cloud-Erkennung --------------------------------------------------------

#: Windows-Attribute der Cloud-Platzhalter (OneDrive Files On-Demand u. a.).
#: Sie sind der Grund, warum ein Verzeichnislisting eine andere Wahrheit zeigen
#: kann als der Server: Die Datei ist bekannt, der Inhalt aber noch nicht da.
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
FILE_ATTRIBUTE_OFFLINE = 0x1000
FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x40000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000

_CLOUD_ATTR_MASK = (
    FILE_ATTRIBUTE_OFFLINE
    | FILE_ATTRIBUTE_RECALL_ON_OPEN
    | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
)

#: Fallback, wenn keine Attribute lesbar sind (nicht-Windows, Netzlaufwerk).
CLOUD_ROOT_HINTS = (
    "onedrive",
    "dropbox",
    "google drive",
    "googledrive",
    "icloud",
    "nextcloud",
    "sharepoint",
)

DEFAULT_QUARANTINE_SECONDS = 300


@dataclass
class CloudSignal:
    """Liegt der Bereich in einem synchronisierten Ordner?"""

    cloud_backed: bool
    reason: str
    source: str = "attributes"

    def __bool__(self) -> bool:  # bequem in if-Abfragen
        return self.cloud_backed


def cloud_pressure(path: Path, prober=None) -> CloudSignal:
    """Prüft, ob ein Pfad cloud-synchronisiert ist.

    Drei Stufen, die erste die antwortet gewinnt:

    1. ``prober`` -- optionaler externer Prüfer (z. B. ein FileCommander-Aufruf).
       Er wird nur *gefragt*, nie vorausgesetzt.
    2. Windows-Dateiattribute der Cloud-Platzhalter -- das genaueste Signal und
       ohne Fremdmodul verfügbar.
    3. Bekannte Cloud-Ordnernamen im Pfad -- grob, aber besser als nichts auf
       Plattformen ohne diese Attribute.
    """
    target = Path(path)

    if prober is not None:
        try:
            answer = prober(target)
        except Exception:  # noqa: BLE001 - ein defekter Prober darf nichts kippen
            answer = None
        if answer is not None:
            return CloudSignal(bool(answer), "externer Prüfer", source="prober")

    try:
        attributes = getattr(os.stat(target), "st_file_attributes", 0)
    except (OSError, AttributeError):
        attributes = 0
    if attributes & _CLOUD_ATTR_MASK:
        return CloudSignal(True, "Cloud-Platzhalter-Attribut gesetzt")
    if attributes & FILE_ATTRIBUTE_REPARSE_POINT:
        return CloudSignal(True, "Reparse-Point (Cloud- oder Link-Ziel)")

    lowered = str(target).lower()
    for hint in CLOUD_ROOT_HINTS:
        if hint in lowered:
            return CloudSignal(True, f"Pfad enthält '{hint}'", source="path-hint")

    return CloudSignal(False, "keine Cloud-Anzeichen")


def looks_like_automation() -> bool:
    """Best-effort: läuft hier eine unbeaufsichtigte Automation?

    Bewusst konservativ -- im Zweifel *nicht* als Automation gewertet, damit ein
    Mensch an der Tastatur nicht minutenlang wartet.
    """
    if os.environ.get("LOCK_MASTER_AUTOMATION", "").strip().lower() in ("1", "true", "yes"):
        return True
    for marker in ("CI", "TF_BUILD", "GITHUB_ACTIONS", "SCHEDULED_TASK"):
        if os.environ.get(marker):
            return True
    return not sys.stdin.isatty() if hasattr(sys.stdin, "isatty") else False


@dataclass
class ContestDecision:
    """Lohnt sich das Verfahren hier -- und warum (nicht)?"""

    contest: bool
    reason: str
    cloud: CloudSignal | None = None
    automation: bool = False


def should_contest(
    path: Path,
    force: bool | None = None,
    is_automation: bool | None = None,
    prober=None,
) -> ContestDecision:
    """Kosten-Nutzen-Test statt pauschaler Wartezeit."""
    if force is not None:
        return ContestDecision(force, "ausdrücklich gesetzt")

    automation = looks_like_automation() if is_automation is None else is_automation
    signal = cloud_pressure(path, prober=prober)

    if not signal.cloud_backed:
        return ContestDecision(False, "kein Cloud-Ordner -- kein Wettlauf zu erwarten", signal, automation)
    if not automation:
        return ContestDecision(
            False,
            "Cloud-Ordner, aber interaktiv -- Wartezeit wäre teurer als der Nutzen",
            signal,
            automation,
        )
    return ContestDecision(True, f"Cloud-Ordner ({signal.reason}) und Automation", signal, automation)


# --- Konfliktauflösung ------------------------------------------------------


#: ``scope_from_name`` liefert für einen Lock ohne Scope-Segment ``"project"``.
#: Ein Projekt-Lock beansprucht den ganzen Baum und überlappt daher mit allem.
PROJECT_SCOPE = "project"


def scopes_overlap(left: str | None, right: str | None) -> bool:
    """Beanspruchen zwei Scopes denselben Bereich?

    Ein Projekt-Lock überlappt mit allem. Sonst gilt Containment mit
    Segmentgrenze: ``assets`` überlappt ``assets.images``, aber nicht
    ``assets-backup`` -- ein reines Zeichenpräfix ist keine Vorfahrbeziehung
    (genau der Punkt aus TODO „Claim-Härtung").
    """
    if left is None or right is None:
        return True
    a, b = left.strip().lower(), right.strip().lower()
    if a == b:
        return True
    if a == PROJECT_SCOPE or b == PROJECT_SCOPE:
        return True
    return a.startswith(b + ".") or b.startswith(a + ".")


def competing_locks(
    project_dir: Path, my_lock: Path, now: datetime | None = None
) -> list[Path]:
    """Fremde, aktive Locks, die denselben Bereich beanspruchen.

    Nur Team-Locks können hier auftauchen: Exclusive Locks tragen keinen Host
    und existieren pro Bereich nur einmal, User- und Condition-Locks sind
    geschützte Kategorien und werden nie überstimmt.
    """
    my_name = my_lock.name
    my_scope = lock_utils.scope_from_name(my_name)
    my_host = _host_of(my_name)

    rivals: list[Path] = []
    for name, scope, _is_legacy in lock_utils.find_lock_files(project_dir):
        if name == my_name or not lock_utils.is_team_lock(name):
            continue
        candidate = project_dir / name
        if lock_utils.is_expired(candidate, now=now):
            continue
        if not scopes_overlap(my_scope, scope):
            continue
        if _host_of(name) == my_host:
            continue
        rivals.append(candidate)
    return rivals


@dataclass
class ContestResult:
    won: bool
    reason: str
    winner: Path | None = None
    competitors: list[Path] = field(default_factory=list)


def _host_of(lock_name: str, fold: bool = True) -> str:
    """Host aus dem DATEINAMEN -- der ist im Lock-System autoritativ, die
    gleichnamigen Felder im Inhalt sind informativ.

    ``fold`` steuert die Kleinschreibung: Der Tiebreak vergleicht gefaltet,
    damit die Reihenfolge nicht von der Schreibweise abhängt; Meldungen zeigen
    den echten Namen, weil ein kleingeschriebener Host im Log wie ein anderer
    Rechner aussieht.
    """
    parts = lock_utils.lock_name_parts(lock_name) or {}
    host = str(parts.get("host") or "")
    return host.lower() if fold else host


def _claim_order(lock_path: Path) -> tuple[datetime, str]:
    created, _expires, _raw = lock_utils.lock_created_and_expiry(lock_path)
    return created, _host_of(lock_path.name)


def resolve_contest(
    project_dir: Path, my_lock: Path, now: datetime | None = None
) -> ContestResult:
    """Entscheidet einen gleichzeitigen Anspruch deterministisch.

    Gewinner ist das früheste ``created``; bei exakter Gleichheit der
    lexikografisch kleinere Host. Beide Seiten rechnen dasselbe Ergebnis aus
    denselben Dateien aus -- ohne Server und ohne Echtzeitkanal.

    Ein **abgelaufener eigener** Lock gewinnt nie: Andere Systeme haben ihn
    längst herausgefiltert, sodass er sich sonst als frühesten Anspruch sähe,
    während ihn niemand sonst noch sieht -- zwei Gewinner bei identischer
    Datenlage, ganz ohne Sync-Verzögerung.
    """
    if not my_lock.is_file():
        return ContestResult(False, "eigener Lock nicht mehr vorhanden")
    if lock_utils.is_expired(my_lock, now=now):
        return ContestResult(False, "eigener Lock ist abgelaufen -- neu anlegen")

    rivals = competing_locks(project_dir, my_lock, now=now)
    if not rivals:
        return ContestResult(True, "kein konkurrierender Anspruch", winner=my_lock)

    ranked = sorted([my_lock, *rivals], key=_claim_order)
    winner = ranked[0]
    if winner == my_lock:
        return ContestResult(True, "frühester Anspruch", winner=my_lock, competitors=rivals)

    mine_created, _mine_host = _claim_order(my_lock)
    win_created, _win_host = _claim_order(winner)
    label = _host_of(winner.name, fold=False) or winner.name
    reason = (
        f"früherer Anspruch von {label}"
        if win_created < mine_created
        else f"Gleichstand, Host-Reihenfolge entscheidet für {label}"
    )
    return ContestResult(False, reason, winner=winner, competitors=rivals)


def contest(
    project_dir: Path,
    my_lock: Path,
    quarantine_seconds: int = DEFAULT_QUARANTINE_SECONDS,
    sleeper=time.sleep,
    now_provider=None,
) -> ContestResult:
    """Vollständiges Verfahren: Quarantäne abwarten, dann entscheiden.

    Die Wartezeit ist der Kern, nicht Beiwerk: Ohne sie liest der Recheck
    dieselbe unsynchronisierte Sicht wie zuvor, und beide Seiten gewinnen.
    """
    if quarantine_seconds > 0:
        sleeper(quarantine_seconds)
    now = now_provider() if now_provider else None
    return resolve_contest(project_dir, my_lock, now=now)
