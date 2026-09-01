# KONZEPT — lock-master wird ein Stack aus drei Teilmodulen

> Entscheidung Lukas Geiger, 2026-07-26. Erarbeitet in Session „OPUS WORKSTATION".
> Status: **beschlossen und umgesetzt.** Diese Datei ist die Begründung;
> der Umsetzungsstand steht am Ende.

## Beschluss

```
lock-master  (Stack — wird als EIN Modul ausgeliefert)
  ├─ pure-locking        LOCK*.txt: Anlegen, Scannen, Prunen, Watcher/GUI
  ├─ permission-control  permissions.py, LOCK.permissions.json
  └─ team-lock           atomare Ressourcen-Bundles und lokale FIFO-Warteschlangen
```

Abgeleitete Stacks:

```
comalock      = lock-master + coma        (lokal, offline, heute)
comaroshambo  = roshambo    + coma        (verteilt, Cloud, später)
swarm-ai      = Skilldokumente + Nutzung von team-lock
```

**Die Funktion ändert sich nicht.** Es ändert sich die Kapselung: Teilbereiche werden
einzeln versionierbar und einzeln entnehmbar. Ein Stack ist in diesem Ökosystem auch
als ein Modul lesbar und auslieferbar; das Manifest liegt bei, damit ein Nutzer
Teilordner gezielt aktualisieren kann.

Der Name `lock-master` bleibt und wandert auf den Stack: „Master" heißt, dass alles
Wichtige enthalten ist — wer nur das Sperren braucht, entnimmt `pure-locking`.

## Warum die Zerlegung risikoarm ist: die Naht existiert bereits

Geprüft am 2026-07-26 im Code, nicht vermutet:

- `permissions.py` importiert **ausschließlich Standardbibliothek** (`fnmatch`, `json`,
  `re`, `pathlib`). Kein Import aus `lock_utils`, `lock_scan` oder sonst etwas
  Lock-Bezogenem.
- Sein Docstring sagt es selbst: ein Rechtesystem, „das **neben** den `LOCK*.txt` in
  einem Projektordner liegt".
- Umgekehrt enthalten `lock_scan.py` und `lock_utils.py` **null** Treffer auf
  „permission".

Die Kopplung ist heute null. Geteilt werden nur ein Dateinamenspräfix (`LOCK.`) und
ein Ordner. Die Zerlegung schneidet also nichts auf, sondern legt eine vorhandene
Naht frei.

## Offene Frage, die durch die Zerlegung erst sichtbar wird

**Zwischen Lock und Recht gibt es keine Schiedslogik — und das ist bisher niemandem
aufgefallen, weil beides in einem Modul und einem Ordner steckt.**

Liegt in einem Projekt eine aktive `LOCK.txt` **und** eine `LOCK.permissions.json` mit
`"default": "allow"` — was gilt? Heute entscheidet das jeder Agent für sich; die beiden
Auswertungen sehen einander nie.

Das ist **bewusst als offene Frage vertagt**, nicht mitgelöst. Wer sie beantwortet,
sollte sie im Stack beantworten (`lock-master`), nicht in den Teilmodulen und schon gar
nicht in den Konsumenten — sonst entsteht die Verrechnungslogik mehrfach und
unterschiedlich.

## Gilt für alle Teile: Konvention, keine Sicherheitsgrenze

Aus dem Docstring von `permissions.py`: „Durchsetzung = **freiwillige Konvention** +
GUI/Audit (analog `LOCK*.txt`)."

Weder Locks noch Permissions werden technisch erzwungen. Das gehört in die README
**jedes** Teilmoduls, sonst importiert jemand `permission-control` und hält es für eine
Sandbox.

**Teilentnahme-Warnung:** Wer nur `pure-locking` zieht, bekommt Sperren ohne
Deny-Regeln. Weil beide nie interagiert haben, geht dabei keine Verrechnung verloren —
die Regeln selbst aber schon. Gehört in die README von `pure-locking`.

## KRITISCH — die deployten Einstiegspunkte dürfen nicht brechen

`~/OneDrive/_scripts/lock_scan.py` und `~/OneDrive/_scripts/prune_stale_locks.py` sind
**deployte Kopien** dieses Moduls (Plan-D-Muster §10.4). Sie werden mit **absolutem
Pfad** als Pflicht-Lockcheck genannt in:

- `~/CLAUDE.md` (Abschnitt „Projekt-Sperren")
- `.TOPICS/CLAUDE.md`
- `_scripts/LOCK-SYSTEM.md`
- `_control-center/_tasks/CLAUDE.md` (Loop-Ablauf, Schritt 2)

Ändert die Zerlegung, was diese Dateien importieren, **bricht der Lockcheck jedes
Agenten im ganzen System — und zwar still**, weil ein fehlgeschlagener Scan wie „keine
Locks" aussieht.

**Abnahmekriterium:** Nach jedem Umbauschritt den Befehl aus `~/CLAUDE.md` **wörtlich**
ausführen und bestätigen, dass er scannt. Die beiden Einstiegspunkte bleiben stabil,
unabhängig von der inneren Paketierung.

## Kompatibilitäts-Shims im Root

Die flachen Einstiegspunkte mussten das Verschieben überleben. Im Modulroot
liegen deshalb sechs Shims — `lock_scan.py`, `lock_utils.py`, `lock_create.py`,
`bulk_lock.py`, `prune_stale_locks.py`, `permissions.py`. Sie tragen die Namen
der verschobenen Dateien und zeigen auf deren neuen Ort.

**Kein Re-Export.** Ein `from … import *` hätte private Namen und
nicht-exportierte Konstanten verloren und ein zweites Modulobjekt erzeugt.
Stattdessen lädt jeder Shim das echte Modul **unter seinem eigenen Namen** und
ersetzt sich in `sys.modules`:

```python
_spec = importlib.util.spec_from_file_location(__name__, _REAL)
_module = importlib.util.module_from_spec(_spec)
sys.modules[__name__] = _module
_spec.loader.exec_module(_module)
```

Das funktioniert, weil der Import-Mechanismus nach `exec_module` erneut aus
`sys.modules` liest. Folgen, empirisch geprüft und nicht angenommen:

- `import lock_scan` liefert das Original — `__file__` zeigt auf
  `pure-locking/lock_scan.py`, nicht auf den Shim.
- `sys.modules['lock_scan'] is lock_scan` ist wahr; es gibt kein Duplikat.
- Private Namen (`_split`, `_ACTION_RE`) sind vorhanden.
- `lock_scan.lock_utils is lock_utils` — Quervergleiche bleiben konsistent.
- Beim Skriptaufruf ist `__name__ == "__main__"`, der `main()`-Block des
  Originals greift also unverändert.
- Es ist gleichgültig, ob ein Aufrufer den Modulroot oder das Teilmodul auf
  `sys.path` hat: beide Wege enden bei derselben Datei.

Jeder Shim wirft eine sprechende `ImportError`, wenn sein Teilmodul fehlt —
sonst wäre eine Teilentnahme des Roots ein stiller Fehlschlag, und genau das
soll dieses Modul ja verhindern.

**Nebenwirkung, bewusst in Kauf genommen:** `lock_scan.DEFAULT_ROOTS_FILE` und
`SYSTEM_CACHE_PATH` folgen `__file__` und liegen jetzt in `pure-locking/`. Wer
bisher `lock_roots.json` im Modulroot ablegte, muss sie mitverschieben. Beide
Namen sind weiterhin von `.gitignore` erfasst (die Muster haben keinen
führenden Slash und greifen daher in jeder Tiefe — geprüft).

## Ausdrücklich NICHT Teil dieses Umbaus

- **Kein Plan-D-Umzug.** lock-master liegt in OneDrive; die Migration nach
  `C:\_Local_DEV\repos\` ist laut `.MCP/TODO.md` ein eigener, geplanter Durchgang, weil
  MCP-Profilpfade darauf zeigen. Beides zugleich zu tun ließe keinen sauberen Rückweg.
- **Kein TOM_lm-Anschluss.** `_TOM-lm` / `build-your-users-mind` hält Willensbildung
  (Prosa, Belegkette, Konfidenz, „🔴 = eskalieren statt raten"), lock-master hält
  Durchsetzung (Muster, deterministische Auswertung). Die Richtung ist
  **TOM_lm → lock-master, niemals umgekehrt**, und der Übertrag bleibt ein bewusster
  Akt. Würde eine Vorhersage mit mittlerer Konfidenz automatisch zu einer
  `allow`-Regel, wäre eine Vermutung unsichtbar in eine Berechtigung gewaschen — in der
  `LOCK.permissions.json` steht keine Konfidenz mehr. Zusätzlich liegt auf
  `build-your-users-mind` bis ca. 12.08.2026 ein **User-Lock** (Judging-Hold).
- **Kein Roshambo-Anschluss jetzt.** Roshambo ersetzt später die Speicherung
  (`LOCK*.txt` → DB-Tabelle). Die Packliste `.STACKS/NEW-STACK_ROSHAMBO.md` führt
  `lock-master` heute in einer Zeile mit „Lease-Semantik … (`deny > ask > allow`)" —
  das vermischt beide Teile. **Nachzuziehen nach der Zerlegung:** `pure-locking`
  liefert die Leases, `permission-control` die Auswertungsordnung.

## Dauerhafte Modulgrenze von `team-lock`

Bandmaster und ArenaOS zeigen nützliche Muster für atomare Claims,
Zustandssnapshots, Ereignisbelege und deterministische Wiederaufnahme. Übernommen
wird davon nur die Koordinationsschicht: Ressourcenmengen atomar beanspruchen,
Überlappungen erkennen und den Claim-Lebenszyklus nachvollziehbar machen.

`team-lock` kennt bewusst keine Aufgabenbeschreibung, Priorität,
Abhängigkeitsgrafen, Testausführung, Ergebnisbewertung, Commit-Erzeugung oder
Workflow-Finalisierung. Solche Informationen dürfen höchstens als opake
`reference` auf den eigentlichen Besitzer zeigen. Damit bleibt die Richtung
eindeutig: Task-/Workflow-Systeme konsumieren Locks; Lock-Systeme werden nicht
zu Task-/Workflow-Systemen.

## Umsetzungsstand

Stand 2026-07-26, Lauf „lockmaster-split" (Auftrag
`_control-center/_agentjobs/IN/lockmaster-split.md`).

- [x] Konzept beschlossen und begründet (diese Datei)
- [x] Physische Trennung `pure-locking` / `permission-control` / `team-lock`
      — per `git mv`, Historie erhalten. Commit `cd70ea3`.
- [x] `ellmos-module.v2.json` je Teilmodul — Commit `1e14227`. Gegen
      `_templates/ellmos.module.v2.schema.json` geprüft. IDs gepunktet
      (`lock-master.pure-locking` usw.), `provides` aufgeteilt statt dupliziert.
- [x] README je Teilmodul mit Teilentnahme-Warnung und dem Satz, dass
      Durchsetzung Konvention und keine Sicherheitsgrenze ist.
- [x] Kompatibilitäts-Shims im Root (siehe eigener Abschnitt oben)
- [x] Abnahmetest: Lockcheck-Befehl aus `~/CLAUDE.md` wörtlich — scannt.
      Deploy-Kopie **und** Modul, zusätzlich Direktaufruf aus `pure-locking/`.
- [x] Doku-Pfade nachgezogen (8 Dateien, ~50 Stellen) — Commit `1379dce`.
      Vorher tot: der Link `watcher/README.md` in `README.md`.
- [x] Tests grün nach jedem Teilschritt: **64 passed**, unverändert zur Basis.
- [x] **Nachtrag 2026-09-01:** `team-lock` als eigenständige CLI/Bibliothek
      umgesetzt. Die frühere `swarm-ai`-Quelle wurde nur als Legacy-Referenz
      gelesen und nicht verändert. Das neue Teilmodul verwaltet atomare
      Ressourcen-Bundles, lokale FIFO-Warteschlangen, Anwesenheit und
      Nachrichten direkt in `LOCK.team.*.txt`; Manifest und README beschreiben
      die bewusst engeren Grenzen ohne Claim-ID-/Recovery-Vertrag.
- [ ] `modules.catalog.json` neu erzeugen — **war nicht Teil des Auftrags und
      ist auch nicht nötig.** Geprüft statt vermutet:
      `build_catalog.discover_manifests()` setzt nach einem Fund
      `dirnames[:] = []` („without entering module children"). Ein Lauf über
      `.CONTROL` findet weiterhin genau `lock-master/ellmos-module.v2.json`,
      nicht die drei Teilmanifeste. Der Stack bleibt **ein** Katalogeintrag —
      genau die Zusage aus dem Beschluss. Es entsteht also keine Registry-Drift.
- [ ] `NEW-STACK_COMALOCK.md` → `validate_composition.py` → `stacks.catalog.json`
      → `STACK-MAPPING.md` — nicht beauftragt, nicht ausgeführt.
- [ ] `.STACKS/NEW-STACK_ROSHAMBO.md` nachziehen: die Zeile, die `lock-master`
      mit „Lease-Semantik … (`deny > ask > allow`)" führt, vermischt beide
      Teile. Nach der Zerlegung gilt: `pure-locking` liefert die Leases,
      `permission-control` die Auswertungsordnung.

### Offene Punkte, die dieser Lauf gefunden, aber nicht angefasst hat

- **Deploy ≠ Modul.** `~/OneDrive/_scripts/lock_scan.py` und
  `prune_stale_locks.py` sind ältere **deutsche** Fassungen (03.07. 23:56); das
  Modul wurde am 04.07. 00:39 ins Englische übersetzt. 245 Diffzeilen, im Kern
  Docstrings. Im Lauf direkt sichtbar geworden: derselbe Scan meldet im Deploy
  „1 aktive Lock(s) … Restzeit=22h47m", im Modul „1 active lock(s) …
  remaining=22h47m". **Die Deploy-Kopien wurden nicht verändert** — ein
  Re-Deploy ist eine eigene Entscheidung des Nutzers. Solange er ausbleibt,
  ist der Deploy von der Zerlegung unabhängig und damit auch unbedroht.
- **Laufzeit des Vollscans.** Der Deploy-Scan über alle Roots braucht mehr als
  120 Sekunden (OneDrive-Vollbegehung), läuft dann aber sauber durch: 17 aktive
  Locks, leeres stderr, Exit 0. Das ist eine Laufzeiteigenschaft, kein Defekt.
  Wer ihn in einem Timeout-Kontext aufruft, sollte das wissen.
- **Erledigt:** `VERSION` und `pyproject.toml` stehen inzwischen beide auf
  `1.5.1`.
- **Erledigt 2026-09-01:** `pyproject.toml` deklariert die flachen Module, das
  installierbare `_lock_master_team`-Paket und den Konsolenbefehl
  `lock-master-team` explizit. Ein Wheel-Smoke in einer isolierten virtuellen
  Umgebung hat Import und CLI-Einstieg geprüft.
- **`visibility` steht überall auf `public-candidate`**, gespiegelt vom
  Root-Manifest. `.MODULES/TODO.md` führt die Korrektur als eigenen Sweep mit
  begründetem Verzicht auf stückweises Vorgehen — hier nicht vorgegriffen.
- **Externe Verweise auf Modul-Interna** wurden über ganz OneDrive gesucht.
  Kein Code-Konsument, nur Dokumente. Zwei Live-Wegweiser in
  `_control-center/_lock_watcher/` (README, TODO) zeigten nach dem Verschieben
  ins Leere und wurden korrigiert. Nicht angefasst: `_control-center/PLAN.md`
  (dort steht Phase 1 durchgängig auf `- [ ]`, obwohl erledigt — der Plan ist
  im Erledigungsstand veraltet, nicht nur in den Pfaden; gehört als eigener
  Vorgang nachgeführt), dessen `.SYNC`-Snapshot, das fremd-geclaimte Ticket
  `T-20260715-01` und `_scripts/LOCK-SYSTEM.md` (Spec der Deploy-Kopie, die nie
  ein lokales `watcher/` hatte).
