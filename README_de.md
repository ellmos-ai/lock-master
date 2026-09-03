<p align="center"><img src="assets/banner.svg" alt="lock-master" width="100%"></p>

# lock-master

[![CI](https://github.com/ellmos-ai/lock-master/actions/workflows/tests.yml/badge.svg)](https://github.com/ellmos-ai/lock-master/actions/workflows/tests.yml)
[![Pytest](https://img.shields.io/badge/pytest-passing-brightgreen.svg)](#tests-ausführen)
[![Python 3.10 | 3.11 | 3.12 | 3.13](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue.svg)](https://www.python.org/downloads/)
[![Plattform: Windows | Linux | macOS](https://img.shields.io/badge/Plattform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey.svg)](https://github.com/ellmos-ai/lock-master)
[![Privatsphäre: Zero-Egress](https://img.shields.io/badge/Privatsph%C3%A4re-100%25%20Offline%20%7C%20Zero--Egress-success.svg)](SECURITY.md)
[![Sicherheit: Local-First](https://img.shields.io/badge/Sicherheit-Local--First%20%7C%20Zero--Dependency-blue.svg)](SECURITY.md)
[![Version 1.6.2](https://img.shields.io/badge/Version-1.6.2-informational.svg)](VERSION)
[![Lizenz: MIT](https://img.shields.io/badge/Lizenz-MIT-yellow.svg)](LICENSE)
[![LLM Indexierung](https://img.shields.io/badge/llms.txt-indexiert-purple.svg)](llms.txt)
[![ellmos-ai](https://img.shields.io/badge/Ökosystem-ellmos--ai-blue.svg)](https://github.com/ellmos-ai)
[![open-bricks](https://img.shields.io/badge/Dachorganisation-open--bricks-orange.svg)](https://github.com/open-bricks)

[EN](README.md) | **DE** | [ES](README_es.md) | [JA](README_ja.md) | [RU](README_ru.md) | [ZH](README_zh-Hans.md)

**Portables, config-gesteuertes Datei-Sperrsystem für Multi-Agenten-Projektkoordination.**

> [!NOTE]
> **KI- / LLM-Indexierung**: KI-Agenten und automatisierte Werkzeuge können [llms.txt](llms.txt) für eine maschinenlesbare Zusammenfassung, Suchbegriffe und Disambiguation nutzen. Letzte Prüfung: **21.08.2026**.

### Schnellnavigation

- [Einstieg](#einstieg)
- [Auffindbarkeit und Abgrenzung](#auffindbarkeit-und-abgrenzung)
- [Features & Architektur](#features)
- [Team-Lock & Konflikt-Reconcile-Lebenszyklus](#team-lock--sub-claim-lebenszyklus)
- [Schnellstart](#schnellstart)
- [Konfiguration (`lock_roots.json`)](#2-lock_rootsjson-erstellen)
- [Dateistruktur & Shims](#dateien)
- [Tests ausführen](#tests-ausführen)
- [Sicherheitsrichtlinie](SECURITY.md)
- [Ökosystem & Geschwisterwerkzeuge](#ökosystem--geschwisterwerkzeuge)
- [LLM-Kontext (`llms.txt`)](llms.txt)

---

lock-master bietet ein leichtgewichtiges, abhängigkeitsfreies Sperrprotokoll auf Basis von Klartextdateien. Eine `LOCK*.txt`-Datei in einem Projektordner signalisiert, dass das Projekt oder eine Komponente gerade in Bearbeitung ist -- kein Agent, keine Automation und kein autonomer Loop verändert diesen Bereich, solange eine gültige, nicht abgelaufene Sperre existiert.

---

## Einstieg

| Bedarf | Nutzen |
|--------|--------|
| Zwei KI-Agenten sollen nicht gleichzeitig dasselbe Repo bearbeiten | `LOCK.txt` im Projekt-Root |
| Agenten sollen parallel an getrennten Komponenten arbeiten | `LOCK.api.txt`, `LOCK.docs.txt` oder ein anderer Scope |
| Aktive Sperren über viele Projektbäume sehen | `python lock_scan.py` |
| Einen schnellen, menschenlesbaren Status veröffentlichen | `python lock_scan.py --write-cache` |
| Vergessene Sperren sicher entfernen | zuerst `python prune_stale_locks.py --dry-run` |
| Neue Sperre stempeln statt Template von Hand editieren | `python lock_create.py <projekt> [--scope docs] [--team HOST] [--user] [--condition]` |

## Auffindbarkeit und Abgrenzung

lock-master passt zu Codex, Claude Code, Gemini/agy, lokalen Automationsloops
und menschlichen Maintainern, die denselben Dateisystem-Workspace teilen. Es ist
kein Redis-Mutex, keine Datenbanksperre, kein Git-Branch-Lock, kein Türschloss
und keine Cloud-Dateifreigabe-API. Gute Suchphrasen kombinieren den Projektnamen
mit `LOCK*.txt`, `Multi-Agenten-Dateisperre`, `KI-Agenten-Projektkoordination`
oder `Codex Claude Lock-Dateien`.

---

## Features

```mermaid
graph TD
    A["Agent / Automation Start"] --> B["Workspaces scannen via lock_scan.py"]
    B --> C{"LOCK*.txt vorhanden?"}
    C -- "Nein" --> D["Zugriff gewährt (Freigabe)"]
    C -- "Ja" --> E{"Sperrtyp & Verfall prüfen"}
    E -- "Abgelaufen & Bereinigbar" --> F["prune_stale_locks.py ausführen -> Zugriff gewährt"]
    E -- "Exklusive Sperre (Aktiv)" --> G["Zugriff verweigert (Warten / Umschalten)"]
    E -- "Team-Lock (Aktiv)" --> H["Sub-Claims prüfen (Dateien/MCP/Tools)"]
    E -- "User- / Condition-Lock" --> I["Geschützt: Nicht antasten bis explizit freigegeben"]
```

- **Scope-basiertes Sperren:** `LOCK.txt` sperrt das gesamte Projekt; `LOCK.<scope>.txt` sperrt eine Komponente. Mehrere Agenten können parallel an verschiedenen Scopes desselben Projekts arbeiten.
- **Team-Locks:** `LOCK.team.<host>.txt` koordiniert mehrere Agenten desselben Systems intern -- Anwesenheitslog, Datei-Claims, Tool-Claims und Nachrichtenbrett in einer Datei. Andere Systeme sehen die Datei und bleiben draußen.
- **Cloud-Ready:** konzipiert für OneDrive, Dropbox und andere geteilte Dateisysteme. Team-Locks sind pro System, um mit Cloud-Sync-Latenz (30 s -- 5 min) umzugehen. Rename-basierte Claims sind auf NTFS und den meisten Cloud-Sync-Dateisystemen atomar.
- **Auto-Verfall:** jede Sperre hat eine konfigurierbare `expires_after`-Dauer (Standard 24h). Ein Cleanup-Script entfernt vergessene Sperren.
- **Read-only-Scan:** `lock_scan.py` listet alle aktiven Sperren über alle konfigurierten Roots, ohne Dateien zu verändern.
- **Markdown-Cache:** `lock_scan.py --write-cache` schreibt eine `LOCK-CACHE.md` für einen schnellen Überblick ohne Scan.
- **Dry-run-Prune:** `prune_stale_locks.py --dry-run` zeigt vorab, was entfernt würde.
- **Optionale lokale Watcher-UI:** `pure-locking/watcher/` ergänzt Daemon, REST-API und Browser-UI auf localhost für Live-Status, Raumkarte, Verlauf, Userlocks und Prune-Aktionen.
- **Keine Abhängigkeiten:** reine Python-Standardbibliothek (3.10+).
- **Config-gesteuert:** alle Roots, Tiefenbegrenzungen, Skip-Verzeichnisse und Cache-Ziele liegen in `lock_roots.json` -- keine hartkodierten Pfade im Code.

### Team-Lock- & Sub-Claim-Lebenszyklus

```mermaid
sequenceDiagram
    autonumber
    actor AgentA as "Agent A (Dev / Refactor)"
    actor AgentB as "Agent B (Docs / Test)"
    participant FS as "Dateisystem (NTFS / Cloud-Sync)"
    participant LM as "lock-master Engine"
    participant Admin as "Admin / Auto-Pruner"

    Note over AgentA,FS: Phase 1: Team-Lock Präsenz-Registrierung
    AgentA->>LM: Team-Lock anfordern (HOST_A)
    LM->>FS: Atomares Schreiben von LOCK.team.HOST_A.txt (Präsenz + Heartbeat)
    FS-->>AgentA: Präsenz erfolgreich registriert

    Note over AgentA,AgentB: Phase 2: Granulare Datei- & Scope-Claims
    AgentA->>LM: Claim für core/engine.py & Scope refactor
    LM->>FS: Claims-Sektion in LOCK.team.HOST_A.txt aktualisieren
    AgentB->>LM: Workspace vor Arbeitsbeginn prüfen
    LM->>FS: LOCK.team.HOST_A.txt & aktive Claims einlesen
    FS-->>AgentB: core/engine.py belegt; docs/ frei
    AgentB->>LM: Claim für docs/api.md & Scope docs eintragen
    LM->>FS: Agent-B-Claim in LOCK.team.HOST_A.txt aufnehmen (Kollisionsfrei)

    Note over AgentA,Admin: Phase 3: Verfall, Stale-Prüfung & Sichere Freigabe
    AgentA->>FS: Aufgabe beendet -> File-Claim freigeben
    alt Sperre abgelaufen / Verwaister Agent
        Admin->>LM: prune_stale_locks.py --dry-run ausführen
        LM->>FS: Zeitstempel gegen expires_after prüfen
        Admin->>LM: prune_stale_locks.py anwenden
        LM->>FS: Sicheres Löschen abgelaufener LOCK*.txt
    else Reguläre Freigabe
        AgentA->>LM: Team-Lock freigeben
        LM->>FS: Sauberes Entfernen von LOCK.team.HOST_A.txt
    end
```

---

## Schnellstart

### 1. Scripts kopieren

Alles, was reines Sperren braucht, liegt in `pure-locking/`:

```
pure-locking/lock_utils.py
pure-locking/lock_scan.py
pure-locking/prune_stale_locks.py
pure-locking/LOCK_TEMPLATE.txt
```

In ein Verzeichnis deiner Wahl legen (z. B. `scripts/`). Die Dateien importieren
einander flach, müssen also nebeneinander liegen.

Was bei einer Teilentnahme **fehlt**, steht in
[pure-locking/README.md](pure-locking/README.md).

### 2. `lock_roots.json` erstellen

`pure-locking/lock_roots.example.json` kopieren, zu `lock_roots.json` umbenennen und die Platzhalter-Pfade durch echte Projektpfade ersetzen. Die Datei wird von `.gitignore` ausgeschlossen (sie enthält lokale absolute Pfade).

Der optionale Watcher löst diese Datei in folgender Reihenfolge auf:
`LOCK_MASTER_ROOTS_FILE`, lokale `pure-locking/lock_roots.json`, die aktive
Windows-OneDrive-Position unter `_scripts/lock_roots.json` und zuletzt
`~/OneDrive/_scripts/lock_roots.json`. Wird keine Datei gefunden, bricht der
Start mit allen geprüften Pfaden ab, statt eine leere oder irreführende Ansicht
zu öffnen.

```json
{
  "default_max_depth": 4,
  "shallow_depth": 2,
  "skip_dirs": [".git", ".venv", "node_modules", "__pycache__", "build", "dist"],
  "roots": [
    { "path": "/pfad/zu/projekt-a" },
    { "path": "/pfad/zu/projekt-b" },
    { "path": "/pfad/zu/grossem-baum", "shallow": true }
  ],
  "caches": [
    {
      "name": "systemweit",
      "path": "/pfad/zu/scripts/LOCK-CACHE.md"
    }
  ]
}
```

### 3. Sperre anlegen

`pure-locking/LOCK_TEMPLATE.txt` in den Projektordner kopieren, Felder ausfüllen und in `LOCK.txt` (oder `LOCK.<scope>.txt` für Komponenten-Sperren) umbenennen:

```
owner: mein-agent
created: 2026-06-14T10:00
host: laptop
expires_after: 24h
mode: hard
purpose: Auth-Modul refaktorieren
```

### 4. Aktive Sperren anzeigen

```bash
python lock_scan.py
python lock_scan.py --json
```

### 5. Abgelaufene Sperren entfernen

```bash
# Vorschau (löscht nichts):
python prune_stale_locks.py --dry-run

# Tatsächlich entfernen:
python prune_stale_locks.py
```

### 6. Cache aktualisieren

```bash
python lock_scan.py --write-cache
```

Schreibt `LOCK-CACHE.md` gemäß den Einträgen im `"caches"`-Schlüssel von `lock_roots.json`.

---

## Optionale Watcher-UI

Der Ordner `pure-locking/watcher/` enthält einen optionalen lokalen Daemon, eine
REST-API und eine Browser-UI. Er nutzt dieselbe `lock_roots.json`, `lock_scan.py`,
`lock_utils.py` und `prune_stale_locks.py` aus `pure-locking/`.

Aus dem Repo-Root:

```bash
python pure-locking/watcher/lock_watcher.py --update-cache
python pure-locking/watcher/web_server.py --port 8095
```

Unter Windows:

```bat
pure-locking\watcher\START.bat
```

Öffnen:

```text
http://127.0.0.1:8095
```

Die lokale UI bietet unter **Sperren/Rechte** jetzt das Anlegen und Entfernen
geschützter User-Locks sowie Vorschau und bestätigte Bulk-Sperrung/-Entsperrung.
Bulk-Aktionen verlangen eine ausdrückliche Bestätigung; User-Locks werden weder
von Bulk-Entsperren noch vom Stale-Cleanup entfernt. Für eine optionale einzelne
JSON-Webhooksendung nach echten Bereinigungen kann
`LOCK_MASTER_PRUNE_WEBHOOK_URL` oder `--webhook-url <http(s)-URL>` verwendet werden.

Runtime-Daten liegen standardmäßig außerhalb des Repos in
`~/.lock_master_watcher` und können mit `LOCK_MASTER_WATCHER_DATA` umgeleitet
werden. Details zu API und Daemon stehen in
[pure-locking/watcher/README.md](pure-locking/watcher/README.md).

---

## Lock-Dateiformat

Klartext, eine `key: value`-Einstellung pro Zeile. Zeilen mit `#` sind Kommentare.

| Feld                | Pflicht | Beispiel             | Bedeutung |
|---------------------|---------|----------------------|-----------|
| `owner`             | ja      | `mein-agent`         | Wer hält die Sperre. |
| `created`           | ja      | `2026-06-14T10:00`   | ISO-Zeitstempel; Basis für Verfallsberechnung. |
| `host`              | optional | `laptop`, `server`  | Maschine, die die Sperre hält (cross-system: welches System sperrt). |
| `expires_after`     | optional | `24h`, `90m`, `2d`  | Dauer-String. Standard: `24h`. |
| `release_condition` | optional | `PR gemergt`        | Freitext: wann kann die Sperre freigegeben werden. |
| `mode`              | optional | `hard` \| `soft`    | `hard` = keine Änderungen (Standard); `soft` = Lesen/Hinweis ok. |
| `purpose`           | optional | `Feature X hinzufügen` | Freitext-Beschreibung der laufenden Arbeit. |
| `scope`             | optional | `frontend`           | Nur informativ; der **Dateiname** ist autoritativ. |

Fehlt `created` oder ist nicht parsebar, wird die Datei-mtime als Fallback verwendet.

---

## Scope-Konvention

| Dateiname                         | Erkannter Scope | Was gesperrt ist |
|-----------------------------------|-----------------|------------------|
| `LOCK.txt`                        | `project`       | Gesamtes Projektverzeichnis |
| `LOCK.api.txt`                    | `api`           | Nur die `api`-Komponente |
| `LOCK.frontend.txt`               | `frontend`      | Nur die `frontend`-Komponente |
| `LOCK.my_scope.txt`               | `my_scope`      | Beliebig benannter Teilbereich |
| `LOCK.team.LAPTOP.txt`            | `project`       | Team-Lock -- gesamtes Projekt, System `LAPTOP` |
| `LOCK.team.api.LAPTOP.txt`        | `api`           | Team-Lock -- `api`-Komponente, System `LAPTOP` |

Erkennungsregex: `^LOCK(\.[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*)?\.txt$` (case-insensitive).

---

## Team-Locks

Ein **Team-Lock** koordiniert mehrere Agenten, die **auf demselben System** parallel laufen (z. B. ein Schwarm paralleler Codex/Claude-Agenten auf einer Maschine). Er erfüllt vier Aufgaben in einer Datei:

1. **Anwesenheitslog** -- jeder Agent trägt sich vor der Arbeit ein und aus, wenn er fertig ist.
2. **Datei-/Ordner-Claims + Warteschlange** -- wer bearbeitet was; wer wartet.
3. **Tool-/Software-/MCP-Claims + Warteschlange** -- exklusive Ressourcen (DB-Verbindungen, laufende Server, MCP-Tool-Sessions).
4. **Nachrichten/Tipps** -- kurze Übergaben und Warnungen für Teammitglieder.

### Warum pro System?

Cloud-Sync-Latenz (30 s -- 5 min bei OneDrive oder Dropbox) macht systemübergreifende Echtzeit-Sperren unzuverlässig. Jedes System verwaltet seine eigenen Agenten über seinen eigenen Team-Lock; die Präsenz der Datei signalisiert anderen Systemen: draußen bleiben.

### Team-Lock-Lebenszyklus

```
ANLEGEN (erster Agent)  -->  EINCHECKEN (jeder Agent)  -->  ARBEITEN  -->  AUSCHECKEN  -->  LÖSCHEN (letzter Agent)
```

- **Vor dem Bearbeiten von Dateien:** eigenen Anwesenheitseintrag hinzufügen.
- **Bei Aufgabenwechsel:** Datei-/Tool-Claims sofort aktualisieren.
- **Beim Verlassen:** eigenen Eintrag und Claims entfernen; Datei löschen, wenn man der letzte ist.
- **Konfliktkopie (zwei Systeme haben gleichzeitig geschrieben):** ein Rename gewinnt. Das System, dessen Datei überschrieben wurde, muss zurückrollen und es erneut versuchen.

### Team-Lock anlegen

Die kanonische Datei mit allen vier Abschnitten direkt anlegen:

```bash
python lock_create.py . --team LAPTOP --owner agent-lead --purpose "Paralleles Refactoring des Auth-Moduls"
```

Alternativ `pure-locking/TEAM_LOCK_TEMPLATE.txt` in den Projektordner kopieren und den Header ausfüllen:

```
owner: agent-lead
created: 2026-06-19T10:00
host: LAPTOP
expires_after: 24h
purpose: Paralleles Refactoring des Auth-Moduls
```

Der Wert von `--team` wird zugleich als `host:` geschrieben. Der Dateiname ist
autoritativ; ein explizit abweichendes `--host` oder eine spätere Abweichung im
Header wird abgelehnt.

Dann Anwesenheits- und Claim-Einträge in den entsprechenden Abschnitten ergänzen, oder die CLI `team_lock.py` verwenden, um sie atomar zu verwalten:

```bash
# Präsenz registrieren
python team_lock.py claim-presence . --lock-name LOCK.team.LAPTOP.txt --agent "agent-1" --role "dev" --task "Auth-Modul refaktorieren"

# Mehrere Dateien atomar beanspruchen (bricht ab, falls eine überlappende Datei belegt ist)
python team_lock.py claim-file . --lock-name LOCK.team.LAPTOP.txt --agent "agent-1" --resource "src/auth.py" --resource "tests/test_auth.py"

# Bei einer Überschneidung mit einem Halter oder älteren Wartenden in die FIFO-Warteschlange gehen
python team_lock.py claim-file . --lock-name LOCK.team.LAPTOP.txt --agent "agent-2" --resource "src/auth.py" --queue

# Nachricht für andere Agenten hinterlassen
python team_lock.py add-message . --lock-name LOCK.team.LAPTOP.txt --agent "agent-1" --msg "Fehler in auth.py gefunden, überprüfe die Ursache."

# Dateien nach Abschluss freigeben
python team_lock.py release-file . --lock-name LOCK.team.LAPTOP.txt --agent "agent-1" --resource "src/auth.py" --resource "tests/test_auth.py"
```

Ein Claim-Befehl schreibt genau eine Bundle-Zeile. Die dateilokale
`order`-Nummer hält getrennte Aufrufe auseinander und legt die FIFO-Priorität
fest; sie ist keine dauerhafte Claim-ID und kein Recovery-Nachweis. Die
persistente Schwesterdatei `LOCK.team.*.txt.guard` dient ausschließlich dem
lokalen Betriebssystem-Guard (`msvcrt.locking` oder `flock`). Sie enthält keinen
Zustand und wird nicht entfernt, damit wartende Prozesse nicht auf getrennten
Guarddateien laufen. Zustandsänderungen werden über eine eindeutige, geleerte
und synchronisierte temporäre Datei mit anschließendem `os.replace` gespeichert.

---

## Lebenszyklus

```
BEACHTEN  -->  CLAIMEN  -->  FREIGEBEN
```

1. **BEACHTEN:** Vor Arbeitsbeginn an einem Projekt oder einer Komponente prüfen, ob eine aktive `LOCK*.txt` für den betroffenen Bereich existiert. Wenn ja und nicht abgelaufen: anderes Projekt wählen oder warten.
2. **CLAIMEN:** eigene Lock-Datei nach Vorlage anlegen (`owner`, `created`, `expires_after`, `purpose`).
3. **FREIGEBEN:** die **selbst angelegte Lock-Datei löschen**, wenn fertig. Aktives Freigeben durch den Ersteller ist Pflicht; der `expires_after`-Timeout ist nur ein Sicherheitsnetz für vergessene Sperren. Bei längerer Laufzeit `created` erneuern, damit die Sperre nicht vorzeitig verfällt.

---

## Konfigurationsreferenz (`lock_roots.json`)

| Schlüssel           | Typ      | Standard | Beschreibung |
|---------------------|----------|----------|--------------|
| `default_max_depth` | int      | `4`      | Maximale Rekursionstiefe ab jedem Root. |
| `shallow_depth`     | int      | `2`      | Tiefe für Roots mit `"shallow": true`. |
| `skip_dirs`         | string[] | `[]`     | Verzeichnisnamen, die komplett übersprungen werden (inkl. Unterbaum). |
| `roots`             | object[] | `[]`     | Liste von `{ "path": "...", "shallow": true/false }`. |
| `caches`            | object[] | `[]`     | Cache-Ziele: `{ "name", "path", "filter_prefix?" }`. |

**Cache-Eintrags-Felder:**

| Schlüssel       | Pflicht | Beschreibung |
|-----------------|---------|--------------|
| `name`          | ja      | Anzeigename, der als Cache-Titel verwendet wird. |
| `path`          | ja      | Absoluter Pfad, in den `LOCK-CACHE.md` geschrieben wird. |
| `filter_prefix` | optional | Nur Locks einschließen, deren Pfad mit diesem Präfix beginnt. |

Fehlt `"caches"`, schreibt `--write-cache` eine einzige `LOCK-CACHE.md` neben `lock_scan.py`.

---

## Python-API

```python
from pathlib import Path
import lock_utils

projekt = Path("/pfad/zu/meinem-projekt")

# Vor Arbeitsbeginn prüfen
aktiv = lock_utils.active_locks(projekt)
if aktiv:
    print(f"Gesperrt: {aktiv}")
else:
    print("Frei zum Arbeiten.")

# Eine konkrete Lock-Datei parsen
data = lock_utils.parse_lock_file(projekt / "LOCK.txt")
print(data["owner"], data["created"])

# Verfall prüfen
from datetime import datetime
abgelaufen = lock_utils.is_expired(projekt / "LOCK.txt", now=datetime.now())
```

---

## Tests ausführen

```bash
python -m pytest tests/ -v
```

Erfordert `pytest` (`pip install pytest`).

---

## Dateistruktur

Seit dem 26.07.2026 ist das Repository ein **Stack aus drei Teilmodulen**, der
als ein Modul ausgeliefert wird. Jedes Teilmodul hat ein eigenes
`ellmos-module.v2.json` und eine README, die sagt, was bei einer Teilentnahme
fehlt.

```
lock-master/                        # Stack -- wird als EIN Modul ausgeliefert
├── pure-locking/                   # Das Sperren selbst
│   ├── lock_utils.py               # Kernbibliothek: Parsen, Scope, Verfall, Team-Lock-Hilfsfunktionen
│   ├── lock_scan.py                # CLI: aktive Sperren auflisten, Cache schreiben
│   ├── prune_stale_locks.py        # CLI: abgelaufene Sperren entfernen
│   ├── lock_create.py              # CLI: korrekten Lock-Dateinamen und Header bauen
│   ├── bulk_lock.py                # CLI: viele Projektordner auf einmal sperren
│   ├── watcher/                    # Optionale localhost-Daemon-, REST-API- und Web-UI
│   ├── LOCK_TEMPLATE.txt           # Vorlage für neue Exclusive-Lock-Dateien
│   ├── TEAM_LOCK_TEMPLATE.txt      # Vorlage für neue Team-Lock-Dateien
│   └── lock_roots.example.json     # Annotiertes Beispiel-Config
├── permission-control/             # Das Regelschema LOCK.permissions.json
│   ├── permissions.py              # Auswertung allow / deny / ask
│   └── LOCK_PERMISSIONS_TEMPLATE.json
├── team-lock/                      # Team-Lock-CLI und installierbare Implementierung
│   ├── team_lock.py                # CLI-Einstieg im Quell-Checkout
│   └── _lock_master_team/          # Validierte API, FIFO, OS-Guard, atomares Schreiben
│
├── lock_scan.py                    # Kompatibilitäts-Shims: die flachen Einstiegs-
├── lock_utils.py                   #   punkte funktionieren weiter aus dem Repo-Root.
├── lock_create.py                  #   Jeder lädt das echte Modul unter dem eigenen
├── bulk_lock.py                    #   Namen, `import lock_scan` liefert also das
├── prune_stale_locks.py            #   Original und keinen Re-Export.
├── permissions.py                  #
├── team_lock.py                    # Root-Shim; installierte CLI: lock-master-team
│
├── LOCK-SYSTEM.md                  # Kanonische Spec und Lebenszyklus-Referenz
├── KONZEPT-ZERLEGUNG.md            # Warum der Stack zerlegt wurde
├── tests/
│   └── test_smoke.py               # Smoke-Tests
├── LICENSE                         # MIT
├── CHANGELOG.md
├── TODO.md
├── SECURITY.md
├── llms.txt
└── VERSION
```

---

## Anforderungen

- Python 3.10+
- Keine Drittanbieter-Abhängigkeiten (nur Standardbibliothek)
- Für Tests: `pytest`

---

## Teil der ellmos-Stack-Familie

lock-master ist bewusst beides: ein eigenständiges Dev-Tool und ein Kernmodul
der ellmos-Stack-Familie.

Kernmodul von [ellmos-ai/agent-ops-stack](https://github.com/ellmos-ai/agent-ops-stack)
(Rolle `locking`); Familie/Katalog: [ellmos-ai/stacks](https://github.com/ellmos-ai/stacks);
Org-Übersicht: [ellmos-ai](https://github.com/ellmos-ai).

## Ökosystem & Geschwisterwerkzeuge

Teil der [ellmos-ai](https://github.com/ellmos-ai) Multi-Agenten-Infrastruktur und des übergeordneten [open-bricks](https://github.com/open-bricks) Open-Source-Software-Ökosystems:

| Werkzeug | Organisation | Beschreibung |
|----------|--------------|--------------|
| [ticket-master](https://github.com/ellmos-ai/ticket-master) | ellmos-ai | Autonomes Ticket-Routing und Task-Dispatching Triage-Konsole |
| [clutch](https://github.com/ellmos-ai/clutch) | ellmos-ai | Adaptiver Multi-Modell-LLM-Router und Agent-Execution-Gear |
| [coma](https://github.com/ellmos-ai/coma) | ellmos-ai | Single-Binary Multi-Agenten-Orchestrator und Ausführungskoordinator |
| [swarm-ai](https://github.com/ellmos-ai/swarm-ai) | ellmos-ai | Schwarmintelligenz und autonomer Agenten-Konsensmotor |
| [gardener](https://github.com/ellmos-ai/gardener) | ellmos-ai | Local-First autonomes Session- und Kontext-Gedächtnissystem |
| [system-gap-master](https://github.com/ellmos-ai/system-gap-master) | ellmos-ai | Serverlose Multi-Agenten-Workspace-Synchronisation & Abgleich |
| [system-explorer](https://github.com/ellmos-ai/system-explorer) | ellmos-ai | Evidenzbasierte Funktionsauflösung & Komponentendrift-Auditor |
| [open-compute-mcp](https://github.com/ellmos-ai/open-compute-mcp) | ellmos-ai | Sichere Operator-Interaktion, Signal-Overlay & MCP-Computing-Bridge |
| [ellmos-filecommander-mcp](https://github.com/ellmos-ai/ellmos-filecommander-mcp) | ellmos-ai | Hochperformanter Dateisystem- & Workspace-Operations MCP-Server |
| [ellmos-codecommander-mcp](https://github.com/ellmos-ai/ellmos-codecommander-mcp) | ellmos-ai | Semantische Code-Analyse, AST-Inspektion & Refactoring MCP-Server |
| [n8n-manager-mcp](https://github.com/ellmos-ai/n8n-manager-mcp) | ellmos-ai | Sicherheitsüberwachter n8n Workflow-Manager & Automations-Orchestrator |
| [prompt-evidence-collector](https://github.com/ellmos-ai/prompt-evidence-collector) | ellmos-ai | Revisionssichere LLM-Interaktionserfassung und kryptografischer Evidenzspeicher |
| [policy-registry](https://github.com/ellmos-ai/policy-registry) | ellmos-ai | Einheitliche Agenten-Rechte- und Richtlinienverwaltung |
| [sqlite-transit-sync](https://github.com/ellmos-ai/sqlite-transit-sync) | ellmos-ai | Multi-Agenten-Statussynchronisation via SQLite-WAL-Journale |
| [workflowhooker](https://github.com/ellmos-ai/workflowhooker) | ellmos-ai | Event-Hooks und automatisierte Agenten-Workflow-Trigger |
| [memoryhooker](https://github.com/ellmos-ai/memoryhooker) | ellmos-ai | Transparente SQLite/FTS5-Arbeitsgedächtniserfassung für Agenten |
| [DevCenter](https://github.com/dev-bricks/DevCenter) | dev-bricks | Entwickler-Leitstand, Repository-Dashboard und Umgebungsmanager |
| [CodeBox](https://github.com/dev-bricks/CodeBox) | dev-bricks | Polyglotter Code-Snippet-Manager und Entwickler-Werkbank |
| [safe-start-for-codex](https://github.com/dev-bricks/safe-start-for-codex) | dev-bricks | Sicherer Starter und Rechte-Isolator für Codex-CLI-Sitzungen |
| [automation-master](https://github.com/dev-bricks/automation-master) | dev-bricks | Automations-Orchestrierung und lokaler Job-Scheduler |
| [CleanMarkdown](https://github.com/doc-bricks/CleanMarkdown) | doc-bricks | Markdown-Formatierung, Linting und strukturelle Bereinigung |
| [PDFtoPDFocr](https://github.com/doc-bricks/PDFtoPDFocr) | doc-bricks | PDF-OCR-Verarbeitung, durchsuchbare Textschicht-Einbettung & Validierung |
| [open-bricks](https://github.com/open-bricks/open-bricks) | open-bricks | Gesamtkatalog & übergreifendes Architektur-Register |

---

## Lizenz

MIT -- Copyright (c) 2026 Lukas Geiger. Siehe [LICENSE](LICENSE).
