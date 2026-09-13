r"""
lock_utils.py — KANONISCHE Logik fuer Projekt-Sperren (LOCK*.txt), systemweit

Single Source of Truth fuer das LOCK-Datei-Format und die Scope-/Verfall-Logik
fuer ALLE Pipelines/Roots unter C:\Users\User\OneDrive (siehe lock_roots.json).
Pipeline-lokale Kopien (z. B. .SOFTWARE/_tools/lock_utils.py) sind nur noch
duenne Shims, die hier re-exportieren — kein Zweitstandard.

Kanonische Spec (Geltung, Stufen, Launch): LOCK-SYSTEM.md (gleicher Ordner).

Konvention:
  - LOCK.txt                       = ganzer Ordner gesperrt        (scope = "project")
  - LOCK.<scope>.txt               = Komponente gesperrt           (scope = "<scope>")
  - LOCK.team.<host>.txt           = Team-Lock fuer ganzen Ordner
  - LOCK.team.<scope>.<host>.txt   = Team-Lock fuer Komponente
  - LOCK.community.<...>.<host>.txt = deprecated Team-Lock-Form
  - LOCK.user(.<scope>).txt        = User-Lock (nur der Nutzer entfernt ihn)
  - LOCK.condition(.<scope>).txt   = Condition-Lock (ab 2026-07-03): verfaellt NICHT
                                     zeitbasiert, sondern gilt bis die release_condition
                                     erfuellt ist. Sperrt typischerweise nur die in
                                     'operations:' benannten Operationen (z. B.
                                     zenodo-upload) — alles andere bleibt frei.
                                     Entfernen darf JEDER Agent, der die Bedingung
                                     nachweislich erfuellt und das im Loeschkontext
                                     dokumentiert (anders als User-Locks).
  - Erkennungsregex: ^LOCK(\.[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*)?\.txt$
  - Legacy (veraltet, nicht mehr anlegen): TEST.txt / TESTS.txt

Dateiformat (eine Einstellung pro Zeile, stdlib-Parser, keine Extra-Dependency):
  - Zeilen mit fuehrendem '#' = Kommentar, Leerzeilen = ignorieren.
  - Sonst am ERSTEN ':' splitten; key/value trimmen; key lowercase.
  Felder:
    owner             (Pflicht)  Wer haelt die Sperre.
    created           (Pflicht)  ISO YYYY-MM-DDTHH:MM (Basis fuer Verfall).
    host              (optional) Maschinenname des Systems, das die Sperre haelt
                                 (z. B. LAPTOP, MACSTUDIO, ASUS-GEI). Fehlt das Feld,
                                 gibt parse_lock_file()/lock_host() None zurueck.
                                 Dient der cross-system-Erkennung (WELCHES System sperrt).
    expires_after     (optional) z. B. "24h" / "48h" / "90m". Default = 24h.
                                 Fuer User-/Condition-Locks ohne Wirkung (kein Zeitverfall).
    release_condition (optional; PFLICHT bei Condition-Locks) Freitext: was muss
                                 passieren, damit frei wird.
    release_mode      (optional; nur bei Until-Locks MIT zusaetzlichem
                                 release_condition wirksam) "all" (Default,
                                 rueckwaertskompatibel) | "any". "all" = Frist
                                 UND Bedingung muessen erfuellt sein -- da die
                                 Bedingung Freitext ist, kann das Werkzeug sie
                                 nicht pruefen und gibt daher NICHT von allein
                                 frei, sobald nur die Frist um ist (fail-closed).
                                 "any" = die Frist allein genuegt, das Werkzeug
                                 gibt sofort frei; die Bedingungsseite bleibt
                                 dann Sache eines Agenten/Menschen. Ungueltiger
                                 Wert -> "all". T-20260903-476807738.
    operations        (optional) Kommagetrennte Liste der GESPERRTEN Operationen
                                 (z. B. "zenodo-upload, github-release"). Fehlt das
                                 Feld, sperrt der Lock gemaess mode alles. Mit Feld
                                 bleibt alles andere ausdruecklich erlaubt
                                 (operationsbezogene Sperre, v. a. fuer Condition-Locks).
    mode              (optional) "hard" (Default) | "soft".
    purpose           (optional) Freitext.
    scope             (optional) nur informativ; AUTORITATIV ist der Dateiname.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

# Aktuelle Lock-Dateien: LOCK.txt + LOCK.<scope>.txt + mehrteilige Team-Locks.
LOCK_RE = re.compile(
    r"^LOCK(?:\.([A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*))?\.txt$",
    re.IGNORECASE,
)
# Legacy-Locks (weiter erkennen, aber als veraltet markiert)
LEGACY_LOCK_NAMES = ("TEST.txt", "TESTS.txt")

DEFAULT_EXPIRES = timedelta(hours=24)

# "24h", "48h", "90m", "30s", "2d"
_DURATION_RE = re.compile(r"^\s*(\d+)\s*([smhd])\s*$", re.IGNORECASE)
_DURATION_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}


TYPE_MARKERS = frozenset({"user", "team", "community", "condition", "until"})
BOOLEAN_WORDS = frozenset({"and", "or", "not"})


def _has_reserved_leftovers(segments: list[str]) -> bool:
    """True, wenn in den Scope-Segmenten noch ein reservierter Marker oder ein
    Bool-Wort steckt.

    Ein Lock-Name traegt GENAU EINEN Typ-Marker, im ersten Segment. Ein zweiter
    Marker oder ein Wort wie "and"/"or"/"not" heisst: der Name wurde
    geschrieben, als liessen sich Lock-Typen im Dateinamen kombinieren -- das
    geht nicht. Ein solcher Name ist mehrdeutig und darf NICHT als die
    schwaechere seiner beiden Lesarten aufgeloest werden.

    Es zaehlen nur ganze Segmente: ein Scope wie "publication-and-claim-edits"
    ist EIN Segment und bleibt gueltig.

    Uebernommen aus dem Klon lock-master (Commit d95ffcd) im
    Reissverschluss-Merge T-20260913-715231627 -- der Fix lag dort seit dem
    2026-09-03, lief aber nie, weil die deployte Fassung ihn nicht hatte."""
    for segment in segments:
        low = segment.lower()
        if low in TYPE_MARKERS or low in BOOLEAN_WORDS:
            return True
    return False


def lock_name_parts(name: str) -> dict[str, str | None | bool] | None:
    """Zerlegt einen Lock-Dateinamen in Typ, Scope und Host.

    Der Dateiort bestimmt Projekt/Raum. Scope meint nur den innerprojektalen
    Unterbereich; die Marker "team"/"community" und das Host-Segment sind
    keine Scope-Bestandteile.
    """
    if name in LEGACY_LOCK_NAMES:
        return {"lock_type": "legacy", "scope": "project", "host": None,
                "is_legacy": True, "ambiguous": False}

    m = LOCK_RE.match(name)
    if not m:
        return None

    raw_scope = m.group(1)
    if not raw_scope:
        return {"lock_type": "exclusive", "scope": "project", "host": None,
                "is_legacy": False, "ambiguous": False}

    segments = raw_scope.split(".")
    marker = segments[0].lower()
    if marker in {"team", "community"}:
        host = segments[-1] if len(segments) >= 2 else None
        scope_segments = segments[1:-1] if len(segments) >= 3 else []
        scope = ".".join(scope_segments) if scope_segments else "project"
        return {"lock_type": "team", "scope": scope, "host": host,
                "is_legacy": False,
                "ambiguous": _has_reserved_leftovers(scope_segments)}

    # User-Lock: LOCK.user.txt (Projekt) / LOCK.user.<scope>.txt (Komponente).
    # Nutzerbasierter Komplett-Lock — nur der User entfernt ihn; Agenten/prune
    # fassen ihn nie an. "user" ist als Marker-Segment reserviert (analog team).
    if marker == "user":
        scope_segments = segments[1:]
        scope = ".".join(scope_segments) if scope_segments else "project"
        return {"lock_type": "user", "scope": scope, "host": None,
                "is_legacy": False,
                "ambiguous": _has_reserved_leftovers(scope_segments)}

    # Condition-Lock: LOCK.condition.txt / LOCK.condition.<scope>.txt (ab 2026-07-03).
    # Bedingungsbasierte Sperre: verfaellt NICHT zeitbasiert (kein prune), sondern
    # gilt bis die release_condition erfuellt ist. Entfernen darf jeder Agent, der
    # die Bedingung nachweislich erfuellt (anders als User-Locks). Typischerweise
    # operationsbezogen ('operations:'-Feld). "condition" ist als Marker reserviert.
    if marker == "condition":
        scope_segments = segments[1:]
        scope = ".".join(scope_segments) if scope_segments else "project"
        return {"lock_type": "condition", "scope": scope, "host": None,
                "is_legacy": False,
                "ambiguous": _has_reserved_leftovers(scope_segments)}

    # Until-Lock: LOCK.until.txt / LOCK.until.<scope>.txt (ab 2026-09-03).
    # Fristsperre: gilt bis zu einem ABSOLUTEN Zeitpunkt im Pflichtfeld
    # 'not_before' statt fuer eine relative Dauer. "until" ist als Marker reserviert.
    if marker == "until":
        scope_segments = segments[1:]
        scope = ".".join(scope_segments) if scope_segments else "project"
        return {"lock_type": "until", "scope": scope, "host": None,
                "is_legacy": False,
                "ambiguous": _has_reserved_leftovers(scope_segments)}

    return {"lock_type": "exclusive", "scope": raw_scope, "host": None,
            "is_legacy": False,
            "ambiguous": _has_reserved_leftovers(segments[1:])}


def scope_from_name(name: str) -> str | None:
    """Scope aus dem Dateinamen ableiten. LOCK.txt -> 'project',
    LOCK.<scope>.txt -> '<scope>'. Bei Team-Locks ohne Komponentensegment
    ist der Scope ebenfalls 'project'. None, wenn kein Lock-Name."""
    parts = lock_name_parts(name)
    if parts is None:
        return None
    return str(parts["scope"])


def is_lock_file(name: str) -> bool:
    return LOCK_RE.match(name) is not None


def parse_lock_file(lock_path: Path) -> dict[str, str]:
    """Parst eine LOCK-Datei in ein key:value-Dict (key lowercase)."""
    data: dict[str, str] = {}
    try:
        text = lock_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return data
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        data[key.strip().lower()] = value.strip()
    return data


def parse_duration(value: str | None) -> timedelta:
    """'24h'/'90m'/... -> timedelta. Default 24h bei Fehlen/Unparsebarkeit."""
    if not value:
        return DEFAULT_EXPIRES
    m = _DURATION_RE.match(value)
    if not m:
        return DEFAULT_EXPIRES
    amount = int(m.group(1))
    unit = _DURATION_UNITS[m.group(2).lower()]
    return timedelta(**{unit: amount})


def _parse_created(value: str | None) -> datetime | None:
    """ISO-Zeitstempel aus 'created' parsen (T- oder Leerzeichen-Trenner,
    Sekunden optional)."""
    if not value:
        return None
    candidate = value.strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(candidate, fmt)
        except ValueError:
            continue
    return None


def lock_created_and_expiry(lock_path: Path) -> tuple[datetime, timedelta, str]:
    """Liefert (created, expires_after, source).
    source = 'header', wenn created aus der Datei kam, sonst 'mtime' (Fallback)."""
    data = parse_lock_file(lock_path)
    created = _parse_created(data.get("created"))
    expires = parse_duration(data.get("expires_after"))
    if created is not None:
        return created, expires, "header"
    mtime = datetime.fromtimestamp(lock_path.stat().st_mtime)
    return mtime, expires, "mtime"


def is_expired(lock_path: Path, now: datetime | None = None) -> bool:
    """Zeitbasierter Verfall. Geschuetzte Locks (User-, Condition-Locks) laufen
    NIE zeitbasiert ab: User-Locks gelten bis der Nutzer sie entfernt,
    Condition-Locks bis ihre release_condition erfuellt und der Lock daraufhin
    entfernt wird. (Fix 2026-07-03: vorher konnten nominell abgelaufene
    User-Locks aus active_locks() herausfallen, obwohl die Spec sie als
    weiterhin gueltig definiert.)"""
    now = now or datetime.now()
    # Mehrdeutige Namen laufen ebenfalls nie ab: ein Name, der liest, als waeren
    # zwei Lock-Typen kombiniert, darf nicht zur schwaecheren Lesart aufgeloest
    # werden (siehe is_ambiguous_lock). Fail-closed, Klon-Commit d95ffcd.
    if is_ambiguous_lock(lock_path.name):
        return False
    if is_until_lock(lock_path.name):
        moment = lock_not_before(lock_path)
        if moment is None:
            return False
        if now <= moment:
            return False
        # Frist erreicht. Ohne zusaetzliches release_condition-Feld bleibt es
        # beim reinen Datumscheck (rueckwaertskompatibel). Ist zusaetzlich eine
        # Bedingung gesetzt, entscheidet release_mode (T-20260903-476807738):
        # "any" -> Frist allein genuegt; "all"/Default/ungueltiger Wert -> die
        # Freitext-Bedingung kann das Werkzeug nicht pruefen, also NICHT von
        # allein freigeben (fail-closed; Bedingungsseite bleibt Agent/Mensch).
        data = parse_lock_file(lock_path)
        if not data.get("release_condition"):
            return True
        return (data.get("release_mode") or "all").strip().lower() == "any"
    if is_protected_lock(lock_path.name):
        return False
    created, expires, _ = lock_created_and_expiry(lock_path)
    return now > created + expires


def lock_host(lock_path: Path) -> str | None:
    """Maschinenname aus dem 'host'-Feld oder Team-Lock-Dateinamen."""
    parts = lock_name_parts(lock_path.name)
    return parse_lock_file(lock_path).get("host") or (
        str(parts["host"]) if parts and parts.get("host") else None
    )


def find_lock_files(project_dir: Path, include_legacy: bool = True):
    """Findet alle Lock-Dateien im Projekt-Root.
    Returns: Liste von (name, scope, is_legacy)."""
    results = []
    for hit in sorted(project_dir.glob("*.txt")):
        if not hit.is_file():
            continue
        if hit.name in LEGACY_LOCK_NAMES:
            continue
        scope = scope_from_name(hit.name)
        if scope is not None:
            results.append((hit.name, scope, False))
    if include_legacy:
        for legacy in LEGACY_LOCK_NAMES:
            for hit in project_dir.glob(legacy):
                if hit.is_file():
                    results.append((hit.name, "project", True))
    return sorted(set(results))


def active_locks(project_dir: Path, now: datetime | None = None):
    """Nicht-abgelaufene Lock-Dateien. Returns Liste von (name, scope, is_legacy).
    Legacy-Locks (TEST.txt/TESTS.txt) haben kein Format -> immer als aktiv gewertet
    (Verfall regelt der Stale-Cleanup nur fuer LOCK*.txt)."""
    now = now or datetime.now()
    out = []
    for name, scope, is_legacy in find_lock_files(project_dir):
        lock_path = project_dir / name
        if is_legacy:
            out.append((name, scope, is_legacy))
            continue
        if not is_expired(lock_path, now):
            out.append((name, scope, is_legacy))
    return out


# ---------------------------------------------------------------------------
# Team-Lock Erkennung und Parsing (ab 2026-06-21)
# ---------------------------------------------------------------------------

def lock_type_from_name(name: str) -> str:
    """Bestimmt den Lock-Typ aus dem Dateinamen.
    'user' fuer LOCK.user.* (nutzerbasierter, geschuetzter Komplett-Lock).
    'condition' fuer LOCK.condition.* (bedingungsbasierte, prune-geschuetzte Sperre).
    'team' fuer LOCK.team.* und LOCK.community.* (deprecated).
    'legacy' fuer TEST.txt / TESTS.txt.
    'exclusive' fuer alle anderen LOCK*.txt."""
    parts = lock_name_parts(name)
    if parts is None:
        return "exclusive"
    return str(parts["lock_type"])


def is_ambiguous_lock(name: str) -> bool:
    """True, wenn der Dateiname liest, als waeren Lock-Typen kombiniert worden.

    Lock-Typen lassen sich im Dateinamen NICHT kombinieren: ein Name traegt
    genau einen Typ-Marker, im ersten Segment. Namen wie
    "LOCK.until.and.condition.<scope>.txt" wurden vorher als einfacher
    Until-Lock mit dem kuriosen Scope "and.condition.<scope>" gelesen -- der
    zweite Marker fiel stillschweigend unter den Tisch. Wer so einen Namen in
    der Absicht "beide Bedingungen" schrieb, bekam ohne jede Warnung den
    SCHWAECHEREN der beiden Locks.

    Mehrdeutige Locks sind fail-closed: is_expired() gibt sie nie frei, der
    Fehler kann also nur zu lange sperren, nie zu kurz. Frist und Bedingung
    kombiniert man ueber die FELDER ("not_before" plus "release_condition"),
    nicht ueber den Namen.

    Uebernommen aus dem Klon lock-master (Commit d95ffcd, 2026-09-03) im
    Reissverschluss-Merge T-20260913-715231627."""
    parts = lock_name_parts(name)
    return bool(parts and parts.get("ambiguous"))


def is_team_lock(name: str) -> bool:
    """True, wenn der Dateiname ein Team-Lock ist (LOCK.team.*.txt bzw. das
    deprecated LOCK.community.*.txt).

    Die Erkennung selbst leistet lock_name_parts(); diese Funktion ist der
    Bequemlichkeits-Wrapper, den der Klon lock-master bereits hatte und auf den
    fremde Aufrufer zeigen koennen, ohne das Parts-Dict zu kennen."""
    parts = lock_name_parts(name)
    return bool(parts and parts.get("lock_type") == "team")


def is_user_lock(name: str) -> bool:
    """True wenn LOCK.user(.<scope>).txt — ein nutzerbasierter Komplett-Lock.

    User-Locks werden NUR vom Nutzer (manuell oder ueber die Watcher-GUI)
    entfernt; Agenten und der Stale-Cleanup (prune) fassen sie nie an, auch
    wenn sie nominell abgelaufen sind."""
    parts = lock_name_parts(name)
    return bool(parts and parts.get("lock_type") == "user")


def is_condition_lock(name: str) -> bool:
    """True wenn LOCK.condition(.<scope>).txt — eine bedingungsbasierte Sperre.

    Condition-Locks (ab 2026-07-03) verfallen NICHT zeitbasiert; sie gelten,
    bis die im Feld 'release_condition' beschriebene Bedingung erfuellt ist.
    Der Stale-Cleanup (prune) und Bulk-Unlock fassen sie nie an. Entfernen
    darf — anders als bei User-Locks — JEDER Agent, der die Bedingung
    nachweislich erfuellt und die Erfuellung beim Loeschen dokumentiert
    (z. B. im Projekt-Register). Typische Nutzung: operationsbezogene Sperren
    ueber das 'operations:'-Feld (z. B. 'operations: zenodo-upload'), waehrend
    normale Forschungs-/Entwicklungsarbeit am Projekt frei bleibt."""
    parts = lock_name_parts(name)
    return bool(parts and parts.get("lock_type") == "condition")


def is_until_lock(name: str) -> bool:
    """True fuer LOCK.until(.<scope>).txt — eine Fristsperre.

    Ein Until-Lock gilt bis zu einem ABSOLUTEN Zeitpunkt, den die Datei im
    Pflichtfeld 'not_before' nennt — nicht fuer eine relative Dauer
    ('expires_after', Default 24h) und nicht bis zu einer Freitext-Bedingung.

    Anlassfall: Wettbewerbs-Sperren. Der Freigabezeitpunkt ist ein fester
    Kalendermoment Wochen spaeter, den niemand als 'expires_after: 900h'
    schreiben will. Vor dieser Lockart wurde der Bedarf zweimal mit
    Ad-hoc-Feldern improvisiert ('LOCK_FALLS_NOT_BEFORE', 'REQUIRED_EVENT'),
    die kein Werkzeug je ausgewertet hat.

    Diese Art trennt als erste zwei Dinge, die alle anderen zusammenfassen:
      * sie laeuft zeitbasiert ab — aber an einem absoluten Moment, sodass ein
        Waechter von selbst aufhoert, dieses Projekt zu bewachen;
      * sie ist trotzdem vor automatischer Loeschung geschuetzt, damit die
        Datei fuer die Nutzerentscheidung und die Belegpflicht aus
        'release_condition' erhalten bleibt.
    """
    m = LOCK_RE.match(name)
    if not m or not m.group(1):
        return False
    return m.group(1).split(".")[0].lower() == "until"


def lock_not_before(lock_path: Path) -> datetime | None:
    """Absoluter Freigabezeitpunkt aus dem 'not_before'-Feld eines Until-Locks.

    Akzeptiert ISO mit oder ohne UTC-Offset, z. B. '2026-10-08T12:00-07:00'
    oder '2026-10-08 12:00'. Ein Wert mit Offset wird in lokale naive Zeit
    gewandelt, damit er mit datetime.now() vergleichbar ist; ein Wert ohne
    Offset gilt als Lokalzeit.

    Gibt None zurueck, wenn das Feld fehlt oder unparsbar ist — Aufrufer
    MUESSEN das als "laeuft nie ab" behandeln (fail-closed), nie als
    "abgelaufen".
    """
    raw = parse_lock_file(lock_path).get("not_before")
    if not raw:
        return None
    candidate = raw.strip()
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        parsed = _parse_created(candidate)
    if parsed is None:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def is_protected_lock(name: str) -> bool:
    """True wenn der Lock vor automatischer Entfernung geschuetzt ist.

    Das sind User-Locks (nur der Nutzer entfernt sie), Condition-Locks
    (bedingungsgesteuert) und Until-Locks (fristgesteuert). Geschuetzte Locks
    werden von prune und von Bulk-Unlock-Aktionen niemals geloescht.

    ACHTUNG: Loeschschutz und Zeitablauf sind zwei verschiedene Dinge, und
    Until-Locks trennen sie als erste. User- und Condition-Locks laufen nie
    zeitbasiert ab; ein Until-Lock LAEUFT AB, naemlich zum Zeitpunkt in seinem
    'not_before'-Feld — seine Datei bleibt aber liegen, weil Nutzerentscheidung
    und Belegpflicht die Frist ueberdauern. Siehe is_expired."""
    return (is_user_lock(name) or is_condition_lock(name)
            or is_until_lock(name) or is_ambiguous_lock(name))


def locked_operations(lock_path: Path) -> list[str]:
    """Liest das 'operations:'-Feld als Liste gesperrter Operationen.

    Leere Liste = kein operationsbezogener Lock (der Lock sperrt gemaess
    mode/Typ den ganzen Bereich). Nicht-leere Liste = NUR diese Operationen
    sind gesperrt; alle andere Arbeit am Projekt bleibt erlaubt."""
    raw = parse_lock_file(lock_path).get("operations", "")
    return [op.strip() for op in raw.split(",") if op.strip()]


def is_prunable(lock_path: Path, now: datetime | None = None) -> bool:
    """True wenn der Lock vom Stale-Cleanup entfernt werden darf.

    Bedingung: abgelaufen UND nicht geschuetzt (kein User-Lock) UND kein
    Legacy-Lock (TEST*.txt haben kein Verfallsformat)."""
    name = lock_path.name
    if name in LEGACY_LOCK_NAMES:
        return False
    if is_protected_lock(name):
        return False
    return is_expired(lock_path, now)


def normalize_lock_fields(data: dict[str, str]) -> dict[str, str]:
    """Mappt alternative Feldnamen auf kanonische Namen.
    Nur sichere 1:1-Mappings. Started/Expires werden NICHT gemappt,
    weil das Roblox-Format TZ-Suffixe und absolute Timestamps verwendet
    die _parse_created/parse_duration nicht korrekt verarbeiten koennen.
    Gibt eine Kopie zurueck, Originalschluessel bleiben erhalten."""
    result = dict(data)
    _FIELD_MAP = {
        "task": "purpose",
    }
    for old_key, new_key in _FIELD_MAP.items():
        if old_key in result and new_key not in result:
            result[new_key] = result[old_key]
    return result


_SECTION_RE = {
    "presence": re.compile(
        r"^(?:#{1,3}\s*)?(?:\d+\.?\s*)?Anwesenheit(?:slog)?", re.IGNORECASE
    ),
    "file_claims": re.compile(
        r"^(?:#{1,3}\s*)?(?:\d+\.?\s*)?(?:Datei|Files?\s+claimed)", re.IGNORECASE
    ),
    "tool_claims": re.compile(
        r"^(?:#{1,3}\s*)?(?:\d+\.?\s*)?(?:Tool|Tools?\s+claimed)", re.IGNORECASE
    ),
    "messages": re.compile(
        r"^(?:#{1,3}\s*)?(?:\d+\.?\s*)?(?:Nachrichten|Notes)", re.IGNORECASE
    ),
    "queue": re.compile(
        r"^(?:#{1,3}\s*)?(?:\d+\.?\s*)?(?:Queue|Warteschlange)", re.IGNORECASE
    ),
}


def parse_team_lock_sections(raw_content: str) -> dict | None:
    """Parst die strukturierten Bereiche eines Team-/Community-Locks.
    Gibt ein Dict mit den Sektionen zurueck oder None bei Nicht-Team-Locks.
    Jede Sektion ist eine Liste von Strings (Bullet-Points / Eintraege)."""
    if not raw_content:
        return None

    sections: dict[str, list[str]] = {
        "presence": [],
        "file_claims": [],
        "tool_claims": [],
        "messages": [],
        "queue": [],
    }

    current_section: str | None = None

    for line in raw_content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        new_section = None
        for sec_name, pattern in _SECTION_RE.items():
            if pattern.match(stripped):
                new_section = sec_name
                break

        if new_section is not None:
            current_section = new_section
            continue

        if current_section is None:
            continue

        if stripped.startswith("#"):
            continue

        entry = stripped.lstrip("- ").strip()
        if entry:
            sections[current_section].append(entry)

    has_content = any(entries for entries in sections.values())
    return sections if has_content else None


def compute_expires_at(lock_path: Path) -> str | None:
    """Berechnet den absoluten Ablaufzeitpunkt als ISO-String.
    Gibt None zurueck wenn kein Verfall bestimmbar.

    Geschuetzte Locks (User-, Condition-Locks) haben KEINEN zeitbasierten
    Verfall (siehe is_expired/is_protected_lock) - liefert bewusst None statt
    eines nominellen created+expires-Zeitpunkts. Konsumenten die diesen Wert
    persistieren (z.B. lock_watcher/storage.py) wuerden sonst einen laengst
    "abgelaufenen" absoluten Zeitpunkt speichern und einen geschuetzten Lock
    faelschlich als expired behandeln, obwohl is_expired() ihn korrekt als
    aktiv einstuft. (Fund T-20260714-02: User-Locks wurden dadurch im
    Lock-Watcher als expired angezeigt, obwohl sie laut Spec nie ablaufen.)"""
    if is_protected_lock(lock_path.name):
        return None
    try:
        created, expires, _ = lock_created_and_expiry(lock_path)
        expiry = created + expires
        return expiry.isoformat(timespec="seconds")
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Worktree-bewusste Aufloesung (ab 2026-09-03, T-20260903-592302105)
# ---------------------------------------------------------------------------
#
# Anlass: Ein Lock lag im Repo-Klon (C:\_Local_DEV\repos\<repo>\LOCK.*.txt),
# gearbeitet wurde in einem `git worktree` (C:\_Local_DEV\worktrees\<name>) --
# einem EIGENEN Verzeichnis ohne eigene LOCK-Datei. Wer dort nur lokal auf
# `LOCK*.txt` prueft (Stufe 1, LOCK-SYSTEM.md), findet nichts und haelt den
# Bereich faelschlich fuer frei, obwohl der Lock fuer denselben Vorgang gilt.
# `git rev-parse --git-common-dir` fuehrt vom Worktree zuverlaessig zum
# gemeinsamen .git und damit zum Hauptklon.

def git_common_dir(path: Path) -> Path | None:
    """`git rev-parse --git-common-dir` fuer `path`, als absoluter Pfad.

    None wenn `path` kein Git-Arbeitsverzeichnis ist, git fehlt oder der
    Aufruf fehlschlaegt (fail-soft: dann wird nur `path` selbst geprueft,
    nie umgekehrt ein Lock uebersehen)."""
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    common_dir = Path(result.stdout.strip())
    if not common_dir.is_absolute():
        common_dir = Path(path) / common_dir
    try:
        return common_dir.resolve()
    except OSError:
        return common_dir


def worktree_main_dir(path: Path) -> Path | None:
    """Hauptklon-Verzeichnis fuer einen git-Worktree.

    Ein linked worktree hat kein eigenes `.git`-Verzeichnis, nur eine
    `.git`-Datei, die auf das gemeinsame `.git` im Hauptklon zeigt; dessen
    Elternverzeichnis ist der Hauptklon. Returns None wenn `path` kein
    Git-Verzeichnis ist ODER `path` bereits der Hauptklon selbst ist (dann
    gibt es nichts zusaetzlich zu pruefen)."""
    common = git_common_dir(path)
    if common is None:
        return None
    main_dir = common.parent
    try:
        if main_dir.resolve() == Path(path).resolve():
            return None
    except OSError:
        pass
    return main_dir


def active_locks_for_path(path: Path, now: datetime | None = None):
    """Worktree-bewusster Lock-Check fuer GENAU EIN Verzeichnis -- der
    Stufe-1-Check ("BEACHTEN") vor Arbeitsbeginn an genau diesem Ort.

    Liefert (name, scope, is_legacy, source_dir) fuer eigene Locks in `path`
    PLUS, falls `path` ein linked git-Worktree ist, die Locks seines
    Hauptklons. Ein Aufrufer, der nur lokal `active_locks(path)` prueft,
    sieht Hauptklon-Locks eines Worktrees nie -- das ist die genaue Luecke
    aus T-20260903-592302105.

    ponytail: deckt den gemeldeten Fall ab (ein Worktree, ein Hauptklon);
    verschachtelte Worktrees/Submodule nicht gesondert behandelt."""
    path = Path(path)
    out = [(name, scope, legacy, path) for name, scope, legacy in active_locks(path, now)]
    main_dir = worktree_main_dir(path)
    if main_dir is not None and main_dir.is_dir():
        out.extend(
            (name, scope, legacy, main_dir)
            for name, scope, legacy in active_locks(main_dir, now)
        )
    return out



# ---------------------------------------------------------------------------
# Git-Hook-Guards (T-20260906-910508487)
# ---------------------------------------------------------------------------
# lock_scan --check-dir sah bisher nur LOCK*.txt. Ein Verzeichnis kann dort
# "frei" melden und trotzdem beim Push durch einen Git-Hook blockiert werden,
# den das Lock-System nie sieht -- dreimal in derselben OneDrive-geteilten
# BACH-.git-Instanz gefunden (Rest-pre-push-Hook des am 26.08. aufgehobenen
# Build-Week-Judging-Embargos: WORKSTATION-LG 31.08., ASUS-GEI-OneDrive-
# Spiegel 06.09.). "Frei" heisst "keine LOCK-Datei", nicht "pushbar".

KNOWN_GUARD_HOOKS = ("pre-push", "pre-commit", "pre-receive")

# Case-insensitive Teilstrings, die die Build-Week-Judging-Embargo-Hook-Linie
# identifizieren (archiviert unter
# .SYNC/_archive/2026-08-26-build-week-hold-released/pre-push-hooks/).
EMBARGO_HOOK_SIGNATURES = (
    "build week judging",
    "buildweek-no-push",
    "operator competition embargo",
)


def _first_hint_line(content: str) -> str:
    """Erste nicht-leere, nicht-Shebang-Zeile eines Hook-Skripts, gekuerzt --
    dient als kurzer "wofuer ist dieser Hook"-Hinweis in --check-dir."""
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#!"):
            continue
        return line[:160]
    return "(leer)"


def git_hook_guards(path: Path) -> list[dict]:
    """Erkennt aktive Git-Push-/Commit-Guard-Hooks fuer `path`.

    Nutzt `git rev-parse --git-path hooks`, was genau das Verzeichnis
    aufloest, das Git selbst fuer die Hook-Ausfuehrung verwenden wuerde --
    inklusive `core.hooksPath`-Override und Per-Worktree-Config-Extension.
    Bewusst KEIN naiver `git_common_dir(path) / "hooks"`-Join: Ist
    `core.hooksPath` gesetzt, das Zielverzeichnis existiert aber auf diesem
    Host nicht (empirisch gefunden: ein OneDrive-geteiltes .git/config mit
    hartkodiertem hooksPath auf ein fremdes Host-Nutzerprofil), fuehrt Git
    dort GAR KEINE Hooks aus -- unten als eigener struktureller Eintrag
    (hook=None) gemeldet statt als leere Liste, die identisch zu "keine
    Hooks konfiguriert" aussehen wuerde.

    Fail-soft: liefert [] wenn `path` kein Git-Arbeitsverzeichnis ist, git
    fehlt oder der Aufruf fehlschlaegt -- ein Lock-/Guard-Check darf nie
    abbrechen, nur weil git selbst nicht verfuegbar ist; er kann dann nur
    nichts ueber Hooks aussagen."""
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--path-format=absolute", "--git-path", "hooks"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0 or not result.stdout.strip():
        return []

    hooks_dir = Path(result.stdout.strip())
    if not hooks_dir.is_dir():
        return [{
            "hook": None,
            "path": str(hooks_dir),
            "hint": "core.hooksPath (oder das Standard-Hooks-Verzeichnis) "
                    "existiert auf diesem Host nicht -- git fuehrt hier "
                    "keine Hooks aus",
            "embargo": False,
        }]

    guards: list[dict] = []
    for name in KNOWN_GUARD_HOOKS:
        hook_path = hooks_dir / name
        if not hook_path.is_file():
            continue
        try:
            content = hook_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            content = ""
        guards.append({
            "hook": name,
            "path": str(hook_path),
            "hint": _first_hint_line(content),
            "embargo": any(sig in content.lower() for sig in EMBARGO_HOOK_SIGNATURES),
        })
    return guards


# ---------------------------------------------------------------------------
# Zwillingsaufloesung Klon <-> OneDrive-Spiegel (T-20260913-785936980)
# ---------------------------------------------------------------------------
# Die Zwei-Baeume-Regel (LOCK-SYSTEM.md) sagt: ein Lock am Klon
# C:/_Local_DEV/repos/<repo> ODER am OneDrive-Pfad ist gleichermassen bindend.
# active_locks_for_path() loeste bisher nur die Worktree-Richtung auf -- der
# OneDrive-Zwilling eines Klons blieb unsichtbar. Das ist mehr als eine
# Unbequemlichkeit, weil LOCK.user.* absichtlich NICHT versioniert ist
# (LOCK-SYSTEM.md, Zeile "nicht committen"): ein FRISCH GEKLONTES Repo kann
# den User-Lock gar nicht enthalten. Wer dort prueft, liest aus einer leeren
# Stelle einen Zustand -- am 2026-09-10 real geschehen (WORKSTATION-LG las
# "Der nutzergehaltene SentinelFleet-Lock ist entfernt"; der Lock vom 27.08.
# lag unveraendert am OneDrive-Pfad).
#
# Aufgeloest wird ueber die bereits vorhandene, DEKLARIERTE Beziehung
# REPO.pointer.json (Feld local_locator). Kein Namensraten:
#   Richtung A  OneDrive -> Klon : Pointer liegt im geprueften Verzeichnis.
#   Richtung B  Klon -> OneDrive : ueber den Index TWIN-INDEX.json, den
#                                  lock_scan.py --write-cache mitschreibt.
#
# FAIL-CLOSED: Liegt der gepruefte Pfad unter einem konfigurierten clone_root
# und der Index fehlt oder ist unlesbar, gilt das Ergebnis als UNBESTIMMT --
# nicht als "frei". Genau dieser Fall hat den Judging-Hold ueberschritten.
# Ist twin_resolution gar nicht konfiguriert (anderer Host, kein Spiegel),
# ist das kein Fehler, sondern "nicht anwendbar" -- unavailable ist nicht empty.

REPO_POINTER_NAME = "REPO.pointer.json"
TWIN_INDEX_NAME = "TWIN-INDEX.json"


def read_repo_pointer(directory: Path) -> dict | None:
    """REPO.pointer.json eines Verzeichnisses, oder None."""
    f = Path(directory) / REPO_POINTER_NAME
    if not f.is_file():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _pointer_clone_path(pointer: dict) -> Path | None:
    loc = (pointer or {}).get("local_locator") or {}
    raw = loc.get("windows_default")
    return Path(raw) if raw else None


def _pointer_repo_name(pointer: dict) -> str | None:
    loc = (pointer or {}).get("local_locator") or {}
    name = loc.get("repo_name")
    if name:
        return name
    repo_id = (pointer or {}).get("repo_id") or ""
    return repo_id.split("/")[-1] or None


def twin_dirs_for_path(path: Path, twin_config: dict | None):
    """Zwillingsverzeichnisse von `path` im jeweils anderen Baum.

    Returns (dirs, status). status:
      "ok"              - Zwilling(e) aufgeloest (dirs kann leer sein: keiner deklariert)
      "not-applicable"  - keine twin_resolution konfiguriert / Pfad ausserhalb
      "index-missing"   - Pfad liegt unter einem clone_root, aber kein lesbarer Index
                          -> FAIL-CLOSED, der Aufrufer darf nicht "frei" melden
      "pointer-unreadable" - der Zwilling traegt eine REPO.pointer.json, die nicht
                          gelesen werden konnte -> ebenfalls FAIL-CLOSED
    """
    path = Path(path)
    if not twin_config:
        return [], "not-applicable"

    # Richtung A: das gepruefte Verzeichnis ist selbst der OneDrive-Zwilling.
    pointer = read_repo_pointer(path)
    if pointer is not None:
        clone = _pointer_clone_path(pointer)
        return ([clone] if clone and clone.is_dir() else []), "ok"

    # Richtung B: liegt der Pfad unter einem Klon-Root?
    clone_roots = []
    for raw in twin_config.get("clone_roots", []):
        try:
            clone_roots.append(Path(os.path.expandvars(str(raw))).resolve())
        except OSError:
            continue
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    under_clone_root = any(
        resolved == r or r in resolved.parents for r in clone_roots
    )
    if not under_clone_root:
        return [], "not-applicable"

    index_raw = twin_config.get("index_path")
    if not index_raw:
        return [], "index-missing"
    index_path = Path(os.path.expandvars(str(index_raw)))
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], "index-missing"

    if resolved.name in index.get("unreadable_pointers_by_dirname", {}):
        return [], "pointer-unreadable"

    hits = index.get("by_repo_name", {}).get(resolved.name, [])
    return [Path(h) for h in hits if Path(h).is_dir()], "ok"


def build_twin_index(directories) -> dict:
    """Sammelt alle REPO.pointer.json aus `directories` zu einem Index
    repo_name -> [Zwillingsverzeichnisse]. Wird von lock_scan.py beim
    Vollscan (--write-cache) mitgeschrieben; der Scan laeuft ohnehin ueber
    genau diese Verzeichnisse, der Index kostet also keinen zweiten Durchlauf."""
    by_name: dict[str, list[str]] = {}
    unreadable: dict[str, list[str]] = {}
    for d in directories:
        d = Path(d)
        pointer_file = d / REPO_POINTER_NAME
        if not pointer_file.is_file():
            continue
        pointer = read_repo_pointer(d)
        name = _pointer_repo_name(pointer) if pointer is not None else None
        if not name:
            # Pointer liegt da, ist aber unlesbar/unvollstaendig. NICHT
            # ueberspringen: sonst faellt genau dieses Repo aus dem Index und
            # --check-dir meldet seinen Klon als "frei", obwohl der Zwilling
            # gesperrt sein kann -- derselbe Fail-Open-Pfad, den dieses Ticket
            # schliesst. Beim Lookup ueber den Verzeichnisnamen auffindbar.
            unreadable.setdefault(d.name, [])
            if str(d) not in unreadable[d.name]:
                unreadable[d.name].append(str(d))
            continue
        by_name.setdefault(name, [])
        if str(d) not in by_name[name]:
            by_name[name].append(str(d))
    return {
        "schema": "ellmos-lock-twin-index-v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "by_repo_name": by_name,
        "unreadable_pointers_by_dirname": unreadable,
    }
