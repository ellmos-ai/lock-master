# LOCK-SYSTEM — Projekt-Sperren (Multi-Agenten-Koordination), systemweit

> **Kanonisch ist diese Datei im Repo** `ellmos-ai/lock-master` als
> `LOCK-SYSTEM_de.md`; die Fassung unter `<OneDrive>/_scripts/LOCK-SYSTEM.md` ist ihre
> **Deploy-Kopie**. Aenderungen gehoeren in den Klon und werden von dort deployt
> (`bin/check_deployment.py <zielordner>` meldet Abweichung). Wer stattdessen die
> Deploy-Kopie bearbeitet, baut die Gabelung wieder auf, die T-20260913-633163908
> aufgeloest hat — damals waren beide Fassungen so weit auseinander, dass jede Regeln
> trug, die der anderen fehlten. **Englische Fassung:** `LOCK-SYSTEM.md` im selben Repo
> (Sprachstufe Core nach P-006: DE + EN). [C 2026-09-13]

**Geltung:** Alle Pipelines und Root-Ordner unter `C:\Users\User\OneDrive` (Roots siehe `lock_roots.json`).
**Kanonik:** Dies ist die kanonische, systemweite Spec. Pipeline-lokale Dokumente (z. B. `.SOFTWARE/POLICY-REG.md`, Abschnitt "Projekt-Sperren (LOCK.txt)") sind damit konsistent zu halten und verweisen hierher.
**Aktualisiert:** 2026-07-18 (neu: PRIVATE.txt — Nie-öffentlich-Sperre, Abschnitt am Ende)

---

## Zweck

Zentrales Koordinationsprinzip für paralleles Arbeiten mehrerer Agenten/Loops/Menschen: Eine `LOCK*.txt` im Projektordner sperrt das Projekt oder eine Komponente. Gesperrte Bereiche verändert kein Agent und kein autonomer Loop.

Zusätzlich kennt das System jetzt kooperative Team-/Community-Locks. Diese sperren nicht nur einen Bereich, sondern dienen als gemeinsamer Abstimmungsraum für Anwesenheit, Rollen, Datei-/Tool-Claims, Warteschlangen und Nachrichten.

---

## Ueberblick: Wer haelt gerade was? (beste Methoden)

Den schnellsten Ueberblick ueber aktive Sperren — ohne jeden Projektordner einzeln zu oeffnen — liefern (in dieser Rangfolge):

1. **Agent mit MCP (am schnellsten, live):** ellmos-filecommander Dateisuche nach `LOCK*.txt` (`fc_search_files`, directory = gewuenschter Root). Findet alle Lock-Dateien index-gestuetzt in Sekunden (ganzes OneDrive in einem Aufruf getestet). Fuer sehr grosse Baeume nicht-blockierend via `fc_start_search`/`fc_get_search_results` (langsamer).
2. **Schnelles Nachschauen ohne Suche:** die auto-generierte `LOCK-CACHE.md` lesen — systemweit `_scripts/LOCK-CACHE.md`, nur Software `.SOFTWARE/LOCK-CACHE.md`. Wird per `lock_scan.py --write-cache` befuellt.
3. **Skriptgesteuert:** `PYTHONIOENCODING=utf-8 python "<OneDrive>/_scripts/lock_scan.py"` (read-only Liste) bzw. `--write-cache` zum Aktualisieren der Caches. Ein voller rekursiver Scan ueber alle Roots ist ueber OneDrive langsam — daher der Cache.

Authoritativ bleiben immer die `LOCK*.txt`-Dateien selbst; der Cache ist nur ein abgeleiteter Schnell-Index.

---

## Aktueller Watcher-/Daemon-Stand (lokale Infrastruktur)

Neben den kanonischen Scripts gibt es einen lokalen Lock-Watcher unter:

```text
C:\Users\User\OneDrive\.TOPICS\_control-center\_lock_watcher
```

Der Watcher nutzt weiterhin diese systemweiten Dateien als Quelle:

- `C:\Users\User\OneDrive\_scripts\lock_roots.json`
- `C:\Users\User\OneDrive\_scripts\lock_scan.py`
- `C:\Users\User\OneDrive\_scripts\lock_utils.py`
- `C:\Users\User\OneDrive\_scripts\prune_stale_locks.py`

Die `LOCK*.txt`-Dateien bleiben auch mit Watcher die allein autoritative Wahrheit. SQLite, Cache und Web-UI sind abgeleitete Ansichten.

### Speicherorte

- Lokale SQLite-DB: `%USERPROFILE%\.lock_watcher\watcher.db`
- Daemon-Heartbeat: `%USERPROFILE%\.lock_watcher\daemon_status.json`
- Raum-/Profil-/Bibliotheksdaten: `%USERPROFILE%\.lock_watcher\...`
- Auto-generierte Cache-Dateien: weiter gemäß `lock_roots.json`, z. B. `_scripts/LOCK-CACHE.md`

Die DB liegt bewusst außerhalb von OneDrive, weil SQLite-WAL-Dateien in synchronisierten Ordnern Korruptions- und Konfliktrisiken erzeugen.

### Scan- und Statusmodell

- Full-Scan alle 60 Sekunden über alle Roots aus `lock_roots.json`.
- Quick-Check alle 20 Sekunden nur für bereits bekannte aktive Locks.
- Daemon-Heartbeat alle 5 Sekunden.
- Raumstatistik-Scan alle 15 Minuten.
- Ein frischer Daemon wird über Host, PID und Heartbeat erkannt; ein zweiter Start beendet sich, wenn bereits ein frischer Daemon derselben Maschine läuft.
- Events werden als `detected`, `modified`, `renewed`, `expired` oder `deleted` in SQLite protokolliert.

### Start und Bedienung

```bat
C:\Users\User\OneDrive\.TOPICS\_control-center\_lock_watcher\START.bat
```

`START.bat` startet oder erkennt zuerst den Daemon und öffnet danach die lokale Web-UI unter:

```text
http://127.0.0.1:8095
```

Die Web-UI ist an `127.0.0.1` gebunden und nicht als Netzwerkdienst gedacht.

CLI für LLMs und Menschen:

```bat
cd C:\Users\User\OneDrive\.TOPICS\_control-center\_lock_watcher
PYTHONIOENCODING=utf-8 python cli.py status --json
PYTHONIOENCODING=utf-8 python cli.py history --limit 50
PYTHONIOENCODING=utf-8 python cli.py scan --update-cache
PYTHONIOENCODING=utf-8 python cli.py stats
PYTHONIOENCODING=utf-8 python cli.py cache
PYTHONIOENCODING=utf-8 python cli.py watch --update-cache
```

Wichtige Web-API-Endpunkte:

- `GET /api/stats`, `/api/settings`, `/api/locks`, `/api/rooms`, `/api/room/<key>/history`
- `POST /api/scan` für einen Sofortscan
- `POST /api/prune` für Cleanup über `prune_stale_locks.py`
- `POST /api/lock` zum Erstellen eines Nutzer-Locks innerhalb erlaubter Roots
- `GET/POST /api/room-stats` bzw. `/api/room-stats/refresh` für Raumgrößen und Dateityp-Spektrum

### Nutzerneutrale Variante

Die lokale GUI ist die Arbeitsfassung. Die nutzerneutrale, portable Variante wird im Repo
`C:\Users\User\OneDrive\.TOPICS\.AI\.MODULES\lock-master` im Ordner `watcher/` gepflegt. Dort darf kein lokaler OneDrive-Layoutpfad vorausgesetzt werden; die Pfade kommen aus `lock_roots.json`, die Runtime-Daten liegen außerhalb des Projektordners.

---

## Scope ueber Dateiname (AUTORITATIV ist der Dateiname)

### Exclusive Locks (klassisch — sperren alle Systeme/Agenten)

- `LOCK.txt` = ganzes Projekt gesperrt (scope = `project`).
- `LOCK.<scope>.txt` = nur diese Komponente gesperrt; freier Scope-Name (Unterbereich/Unterordner), z. B. `LOCK.software.txt`, `LOCK.web.txt`, `LOCK.dev.txt`, `LOCK.desktop.txt`, `LOCK.mobile.txt`, `LOCK.flutter_port.txt`.
- So koennen z. B. Desktop- und Mobile-Loops dasselbe Projekt parallel an verschiedenen Komponenten bearbeiten.
- Kein Systemname im Dateinamen noetig — der Lock blockiert alle. Das `host:`-Feld im Inhalt sagt informativ, wer sperrt.

### Team Locks (koordinieren Agenten eines Systems; blockieren andere Systeme)

Team-Locks sind eine eigene Kategorie: Sie koordinieren mehrere Agenten, die **auf demselben System** parallel an einem Projekt arbeiten (Anwesenheit, Datei-/Tool-Claims, Queue, Nachrichten). Fuer alle **anderen Systeme** wirken sie wie ein Exclusive Lock.

**Warum pro System:** OneDrive-Sync hat 30s–5min Latenz — Echtzeit-Koordination ueber Systemgrenzen ist damit nicht zuverlaessig. Agenten auf demselben System teilen Dateisystem und Prozessraum und koennen direkt koordinieren.

**Neue Konvention (ab 2026-06-19):**

- `LOCK.team.<host>.txt` = Team-Lock fuer das gesamte Projekt auf einem System, z. B. `LOCK.team.LAPTOP.txt`.
- `LOCK.team.<scope>.<host>.txt` = Team-Lock fuer eine Komponente, z. B. `LOCK.team.assets.ASUS-GEI.txt`.
- Wenn ein zweites System eintreten will: eigenen `LOCK.team.<scope>.<sein-host>.txt` anlegen, sofern die Scopes sich nicht ueberlappen.

**Deprecated (ab 2026-06-19, bleibt gueltig waehrend Uebergang):**

- `LOCK.community.<arbeitsfeld>.<agentengruppe>.<host>.txt` — alte Konvention (z. B. `LOCK.community.loop.codex.WORKSTATION.txt`). Wird weiterhin als Lock erkannt und respektiert. **Neue Team-Locks nicht mehr in diesem Format anlegen** — stattdessen `LOCK.team.*` verwenden. Bestehende Community-Locks muessen nicht sofort umbenannt werden; sie laufen normal ab oder werden bei Gelegenheit auf das neue Format umgestellt.

### User Locks (nutzerbasierte Komplettsperre — nur der Nutzer entfernt sie)

User-Locks sind eine eigene, geschuetzte Kategorie (ab 2026-06-27). Sie sperren ein Projekt
dauerhaft, und **nur der Nutzer** (manuell oder ueber die Lock-Watcher-GUI) darf sie wieder
entfernen — Agenten und der Stale-Cleanup (`prune_stale_locks.py`) fassen sie **nie** an, auch
wenn sie nominell abgelaufen sind.

- `LOCK.user.txt` = ganzes Projekt, nutzerbasiert gesperrt.
- `LOCK.user.<scope>.txt` = Komponente, nutzerbasiert gesperrt.
- Marker-Segment `user` ist reserviert (analog `team`). Erkennung: `lock_utils.is_user_lock()`;
  Schutz: `lock_utils.is_protected_lock()` / `is_prunable()`.
- Anlegen/Entfernen am einfachsten ueber die Watcher-GUI (`http://127.0.0.1:8095`, Button
  „Sperren/Rechte") oder per Vorlage `_scripts/LOCK_TEMPLATE.txt` mit `removable_by: user`.
- Fix 2026-07-03: geschuetzte Locks (User + Condition) laufen auch in `lock_utils.is_expired()`
  nie mehr zeitbasiert ab — sie erschienen vorher nach nominellem Ablauf faelschlich als
  inaktiv in `active_locks()`/`lock_scan.py`, obwohl sie laut Spec weitergalten.

### Condition Locks (bedingungsbasierte, operationsbezogene Sperre — ab 2026-07-03)

Condition-Locks sperren **bis eine Bedingung erfuellt ist** statt bis eine Zeit ablaeuft,
und typischerweise **nur bestimmte Operationen** statt das ganze Projekt. Anwendungsfall:
„Kein Zenodo-Upload von Paper X, bevor die Review-Nachtraege eingearbeitet sind" — die
Forschung am Projekt bleibt dabei voellig frei.

- `LOCK.condition.txt` = Bedingungssperre auf Projektebene.
- `LOCK.condition.<scope>.txt` = Bedingungssperre fuer eine Komponente,
  z. B. `LOCK.condition.zenodo-upload-paper3-4.txt`.
- Marker-Segment `condition` ist reserviert (analog `user`/`team`).
  Erkennung: `lock_utils.is_condition_lock()`; Schutz: `is_protected_lock()`.
- **Kein Zeitverfall:** `expires_after` ist wirkungslos; `prune_stale_locks.py` und
  Bulk-Unlock fassen Condition-Locks nie an. `lock_scan.py` zeigt statt Restzeit
  „bis Bedingung erfuellt: …".
- **Pflichtfeld `release_condition:`** — prazise, nachpruefbar formulieren (WAS muss
  erledigt sein, WO ist es dokumentiert/nachweisbar).
- **Feld `operations:`** (kommagetrennt) benennt die GESPERRTEN Operationen
  (z. B. `operations: zenodo-upload`). Alles nicht Genannte bleibt ausdruecklich
  erlaubt. Ohne `operations`-Feld wirkt der Lock gemaess `mode` auf den ganzen Scope.
  Helper: `lock_utils.locked_operations(path)`.
- **Freigabe:** Anders als bei User-Locks darf **jeder Agent** den Lock entfernen,
  der die `release_condition` nachweislich erfuellt hat — die Erfuellung ist beim
  Entfernen im Projekt-Register (z. B. TODO/BEWEISNOTIZ) zu dokumentieren.
  Bei Unsicherheit, ob die Bedingung erfuellt ist: NICHT entfernen, User fragen.

### Lockarten lassen sich im Dateinamen NICHT kombinieren [C 2026-09-03]

Ein Lock-Name traegt **genau einen** Typ-Marker, im ersten Segment. Namen, die wie eine
Kombination aussehen — `LOCK.until.and.condition.<scope>.txt`,
`LOCK.until.or.condition.<scope>.txt`, `LOCK.user.condition.<scope>.txt` — sind
**mehrdeutig** und gelten fail-closed: `is_expired()` gibt sie nie frei, `prune` fasst sie
nie an, und `lock_scan.py` gibt eine ausdrueckliche Warnung aus.

**Warum fail-closed und nicht einfach abgelehnt:** Bis zu diesem Fix wurde so ein Name als
ERSTER Marker plus seltsamer Scope gelesen (`until` + `and.condition.<scope>`), der zweite
Marker still ignoriert. Wer den Namen schrieb und „beides" meinte, bekam die **schwaechere**
der beiden Sperren — sie fiel, sobald die Frist ablief, Bedingung unerfuellt. Unbefristet
halten ist das sichere Scheitern: es kann nur zu lange sperren, nie zu wenig.

Es zaehlen nur **ganze Segmente**. Ein Scope wie `publication-and-claim-edits` ist EIN
Segment und bleibt gueltig (real im Bestand vorhanden).

**Frist und Bedingung werden ueber die FELDER kombiniert, nicht ueber den Namen:**

```
LOCK.until.winners-announcement.txt
  not_before: 2026-10-08T12:00-07:00      # beendet das Bewachen (maschinell pruefbar)
  release_condition: Verkuendung belegt auf <Quelle>   # Beleg vor dem Entfernen
```

Die Frist beendet das *Bewachen*; die Bedingung sagt, was belegt sein muss, bevor die Datei
entfernt wird. Kombiniert werden beide ueber `release_mode` (siehe Feldtabelle oben und den
Abschnitt „Frist UND/ODER Bedingung kombinieren" unten) — **umgesetzt** in dieser Fassung,
T-20260903-476807738.

> **Korrektur [T-20260903-476807738, lock-worker]:** Der Satz „Erkennung:
> `lock_utils.is_ambiguous_lock()`" stand hier bereits, bevor diese Funktion in DIESER
> Fassung existierte — `is_ambiguous_lock()` gibt es nur in `lock-master/pure-locking`
> (seit v1.6.1), nicht in dieser produktiven `_scripts`-Fassung. Fail-closed-Schutz vor
> mehrdeutigen Namen (`LOCK.until.and.condition.*` etc.) ist hier also NOCH NICHT portiert
> — eigener, offener Folgepunkt (nicht Teil dieses Tickets).

### Until Locks (Fristsperre — gilt bis zu einem absoluten Zeitpunkt, ab 2026-09-03)

Until-Locks gelten **bis zu einem festen Zeitpunkt**, den die Datei nennt — statt fuer eine
relative Dauer (`expires_after`, Default 24h) oder bis eine Freitext-Bedingung erfuellt ist.
Anwendungsfall: Wettbewerbs-Sperren, bei denen der Freigabemoment ein Kalendertermin Wochen
spaeter ist, den niemand als `expires_after: 900h` schreiben will.

- `LOCK.until.txt` = Fristsperre auf Projektebene.
- `LOCK.until.<scope>.txt` = Fristsperre fuer eine Komponente,
  z. B. `LOCK.until.winners-announcement.txt`.
- Marker-Segment `until` ist reserviert (analog `user`/`team`/`condition`).
  Erkennung: `lock_utils.is_until_lock()`; Zeitpunkt: `lock_utils.lock_not_before()`.
- **Pflichtfeld `not_before:`** — absoluter ISO-Zeitstempel, mit oder ohne UTC-Offset:
  `2026-10-08T12:00-07:00` oder `2026-10-08 12:00`. Ein Wert mit Offset wird in Lokalzeit
  gewandelt, ein Wert ohne Offset gilt als Lokalzeit. **Offset immer angeben, wenn die Frist
  in einer fremden Zeitzone verkuendet wurde** — genau dort passieren die Fehler.
- **Fail-closed:** Fehlt `not_before` oder ist es unparsbar, laeuft der Lock **nie** ab. Ein
  Tippfehler kann nur zu lange sperren, nie zu frueh freigeben.
- **`expires_after` ist wirkungslos.** Der absolute Zeitpunkt ersetzt es.

**Diese Art trennt als erste zwei Dinge, die alle anderen zusammenfassen:**

| Art | laeuft zeitbasiert ab | vor Loeschung geschuetzt |
|---|---|---|
| exclusive | ja, nach `expires_after` | nein |
| user | nie | ja |
| condition | nie | ja |
| **until** | **ja, zum `not_before`** | **ja** |

Ein Waechter hoert damit **von selbst** auf, das Projekt zu bewachen, sobald der Zeitpunkt
ueberschritten ist (`is_expired()` wird wahr, `active_locks()` listet ihn nicht mehr). Die
**Datei bleibt trotzdem liegen**: `prune_stale_locks.py` und Bulk-Unlock fassen sie nie an,
denn Nutzerentscheidung und Belegpflicht ueberdauern die Frist.

- **Feld `release_condition:`** (empfohlen) — was zusaetzlich belegt sein muss, bevor die
  Datei entfernt wird (z. B. „Gewinnerverkuendung tatsaechlich erfolgt, Quelle X"). Die Frist
  beendet das *Bewachen*; sie beweist nicht, dass das Ereignis eingetreten ist. Wird ein
  Termin verschoben, bleibt der Lock fail-closed und `not_before` wird korrigiert.
- **Feld `operations:`** wirkt wie bei Condition-Locks: es nennt die GESPERRTEN Operationen,
  alles andere bleibt ausdruecklich erlaubt.
- **Entfernen** richtet sich nach dem, was der Lock selbst sagt. Schuetzt er eine
  Nutzerentscheidung, steht das in `release_condition` und nur der Nutzer entfernt ihn.
  `lock_scan.py` zeigt dann „Frist abgelaufen <Zeitpunkt> - Waechter darf aufhoeren; Datei
  bleibt fuer den Nutzer".
- Vor Ablauf zeigt `lock_scan.py` Restzeit und Zeitpunkt, z. B. `842h13m (bis 2026-10-08T21:00)`.

**Herkunft:** Der Bedarf wurde vorher zweimal improvisiert — `LOCK_FALLS_NOT_BEFORE:` und
`REQUIRED_EVENT:` in der BYUM-Lock standen in keiner Spec und wurden von keinem Werkzeug
ausgewertet. Umgesetzt im Modul `lock-master` v1.6.0 (`pure-locking/lock_utils.py`,
12 Tests in `tests/test_until_lock_system.py`) und hier in die Produktivfassung portiert.

**Frist UND/ODER Bedingung kombinieren — Feld `release_mode` (T-20260903-476807738):**
Kombiniert wird ueber FELDER, nicht ueber eine Namensgrammatik — die zunaechst erwogenen
Varianten (`LOCK.until.and.condition.*`, `LOCK.until.or.condition.*`, ...) wurden ausdruecklich
verworfen (siehe „Lockarten lassen sich im Dateinamen NICHT kombinieren" unten). Setzt ein
Until-Lock ZUSAETZLICH `release_condition`, entscheidet `release_mode`:

- `all` (Default, rueckwaertskompatibel) — Frist UND Bedingung muessen erfuellt sein. Da
  `release_condition` Freitext ist, kann das Werkzeug ihn nicht pruefen: `is_expired()` gibt
  nach der Frist NICHT von allein frei (fail-closed), die Datei bleibt fuer die
  Nutzer-/Agentenpruefung liegen — genau wie bisher bei Condition-Locks.
- `any` — was zuerst eintritt, gibt frei. Die Frist allein genuegt dem Werkzeug; die
  Bedingungsseite bleibt dann Sache eines Agenten/Menschen (das Werkzeug kann sie nicht
  eigenstaendig aufloesen — das klar dokumentieren, sonst wirkt die Sperre staerker als sie ist).
- Ungueltiger Wert -> `all` (im Zweifel nie zu frueh freigeben).
- Fehlt `release_condition` ganz, ist `release_mode` wirkungslos: reiner Datumscheck wie bisher.

### Allgemein

- Erkennungsregex: `^LOCK(\.[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*)?\.txt$`.
- Legacy `TEST.txt` / `TESTS.txt` = veraltet, nicht mehr anlegen (wird noch als Sperre erkannt, aber nicht automatisch verfallen).

---

## Rechtesystem: LOCK.permissions + Sofortsperrung (ab 2026-06-27)

Agent-neutrales, ordner-bezogenes Rechtesystem neben den `LOCK*.txt` — von **allen** Agenten
(Claude, Codex, Gemini, Kimi) lesbar. Verwaltet vom Lock-Watcher (`_control-center`-Saeule).

### `LOCK.permissions.json` — Rechte pro Projektordner

Syntax an `.claude/settings.json` angelehnt, aber agent-uebergreifend und ordner-scoped:

```json
{ "format": "lock-permissions-v1", "default": "allow",
  "rules": { "allow": ["Read(**)"], "deny": ["Bash(rm:*)", "Write(**/CREDENTIALS/**)"], "ask": ["Write(**)"] },
  "applies_to_agents": ["claude","codex","gemini","kimi","*"] }
```

- Pattern: `Tool(glob)` (`Bash(...)`, `Read(...)`, `Write(...)`), `mcp__vendor__tool`, `*`.
- Praezedenz: `deny > ask > allow > default`. Auswertung: `_scripts/permissions.py::evaluate(perm, agent, action)`.
- Durchsetzung = freiwillige Konvention + GUI/Audit (wie `LOCK*.txt`). Vorlage:
  `_scripts/LOCK_PERMISSIONS_TEMPLATE.json`.

### Sofortsperrung (zentrale Notbremse)

`_scripts/bulk_lock.py` setzt/entfernt exklusive `LOCK.txt` in allen angebundenen Top-Level-Roots
(`lock_roots.json`) in einem Schritt:

- `bulk_lock(roots, commit=False)` — Dry-Run per Default; idempotent (vorhandene Locks bleiben);
  gesetzte Locks tragen `created_by: bulk` (exakte Ruecknahme via Session-Manifest).
- `bulk_unlock(...)` — entfernt **nur** `created_by: bulk`-Locks; **niemals User-Locks**.
- Bedienung ueber die Watcher-GUI (Button „Sperren/Rechte", mit Dry-Run-Vorschau + Bestaetigung)
  oder CLI `python bulk_lock.py lock|unlock --commit`.

Kanonische, laufende Logik: `_scripts/` (Daemon-Import). Nutzerneutrale Spiegel:
`.AI/.MODULES/lock-master` (Locks/Rechte) + `.AI/.MODULES/ticket-master` (Ticket/Doku-Helfer).
Zentraler Einstieg fuer alle LLMs: `.TOPICS/_control-center/MANIFEST.md`.

---

## Format (eine Einstellung pro Zeile, `key: value`)

Vorlage: `_scripts/LOCK_TEMPLATE.txt`. Zeilen mit `#` = Kommentar, Leerzeilen ignoriert.

| Feld | Pflicht | Bedeutung |
|---|---|---|
| `owner` | ja | Wer haelt die Sperre (Agent/User/Automation). |
| `created` | ja | ISO-Zeitstempel `YYYY-MM-DDTHH:MM` (Basis fuer Verfall). |
| `host` | optional | Maschinenname des Systems, das die Sperre haelt (z. B. `LAPTOP`, `MACSTUDIO`, `ASUS-GEI`). Ermoeglicht cross-system-Erkennung: WELCHES System sperrt gerade. Fehlt das Feld, gilt `host = None` — kein Fehler, rueckwaertskompatibel. |
| `expires_after` | optional | z. B. `24h` / `48h` / `90m`. Default = `24h`. |
| `release_condition` | optional | Freitext: was muss passieren, damit frei wird. |
| `release_mode` | optional | Nur wirksam bei Until-Locks MIT zusaetzlichem `release_condition`: `all` (Default, rueckwaertskompatibel) \| `any`. Siehe unten. |
| `mode` | optional | `hard` (keine Aenderung, Default) \| `soft` (Lesen/Hinweis ok). |
| `purpose` | optional | Freitext: warum gesperrt / was laeuft gerade. |
| `scope` | optional | nur informativ; autoritativ ist der Dateiname. |
| `fence` | optional | **Vergabenummer** (Epoch-Mikrosekunden), von `lock_create.py` geschrieben. Steigt je Bereich nur. Der Halter merkt sie sich beim Erwerb und prueft sie vor jedem Schreiben — siehe Abschnitt „Fencing-Tokens“. Fehlt das Feld, ist es ein Lock aus der Zeit davor und wird behandelt wie bisher. |

Fehlt `created` (oder unparsebar), gilt die Datei-mtime als Fallback fuer den Verfall.

---

## Fencing-Tokens: wenn der Halter nicht merkt, dass er die Sperre verloren hat

Eine Frist allein verhindert den bekanntesten Fehler von Lease-Verfahren nicht
(Martin Kleppmann, „How to do distributed locking“):

1. Agent A erwirbt den Lock und arbeitet.
2. A pausiert laenger als die TTL — Garbage Collection, Swap, Standby, haengender
   Sync-Aufruf, unterbrochene SSH-Sitzung.
3. Die Sperre verfaellt. `prune_stale_locks.py` raeumt sie, oder Agent B erwirbt sie.
4. A arbeitet weiter. **A weiss nicht, dass er die Sperre verloren hat**, und schreibt
   in denselben Bereich wie B.

Der Fehler ist still: beide melden Erfolg, und ueber OneDrive kann daraus zusaetzlich
eine Konfliktkopie werden (Abweichung A7). Die Frist kann das nicht fangen, weil A
nach dem Aufwachen gar nichts mehr liest.

**Die Nummer schliesst die Luecke.** Jede Vergabe erhaelt eine Zahl im Feld
`fence:`, die je Bereich nur steigen kann. Der Halter merkt sie sich und prueft sie
**vor jedem Schreib- oder Push-Vorgang** gegen die Datei am Zielort. Eine hoehere
Nummer heisst: das Lease ist weitergegangen — Schreibvorgang ablehnen, nicht
heimlich fortsetzen.

```
python lock_create.py <projekt> --scope docs --owner agent-A
  created: ...\LOCK.docs.txt
  fence: 1789902599751740
  hint: merke dir die Nummer und pruefe sie vor jedem Schreiben --
        set LOCK_FENCE=1789902599751740
        set LOCK_FENCE_FILE=...\LOCK.docs.txt
```

Geprueft wird entweder im Prozess

```python
status, grund = lock_utils.fence_status(lock_path, mein_fence)   # "held" | "lost" | "unknown"
```

oder von der Shell aus (Exit 0 = gilt noch, 1 = verloren):

```
python lock_scan.py --verify-fence <LOCK-Datei> --fence <n>
```

**Was als „verloren“ gilt (fail-closed).** Alles, was nicht nachweislich noch der
eigene Anspruch ist: die Datei ist verschwunden; sie traegt eine hoehere (oder
niedrigere) Nummer; sie traegt gar keine Nummer mehr, weil ein aelterer Schreiber sie
neu angelegt hat; oder der **eigene** Anspruch ist abgelaufen, obwohl die Datei noch
daliegt — das Fenster zwischen Fristablauf und `prune`.

**Warum Uhrzeit und kein Zaehler.** Ein Zaehler braucht ein atomares Inkrement, das
alle Schreiber teilen. Ueber einen Cloud-Ordner mit 30 s bis 5 min Sync-Latenz gibt es
das nicht: zwei Hosts lesen beide 5 und schreiben beide 6. Eine per Rename erzeugte
Sequenzdatei hat dieselbe Luecke — das Rename ist auf jedem Host fuer sich atomar, und
genau das hilft einem gemeinsamen Zaehler nicht. Die Uhrzeit hat diese Luecke nicht,
weil eine Vergabe immer **nach** der Vergabe liegt, die sie abloest: B kann erst
erwerben, wenn A's TTL abgelaufen ist, also liegen beide mindestens eine TTL
auseinander — weit ueber jedem plausiblen Uhrenversatz zwischen Hosts.

Das leiht sich keine neue Annahme: `expires_after` wird **ohnehin schon** gegen die
lokale Uhr jedes Hosts gerechnet, und das Contest-Verfahren ordnet Anspruechen
**ohnehin schon** nach `created` mit Host-Tiebreak. Driften die Uhren weit genug,
um Fencing zu brechen, war der Verfall vorher kaputt.

**Verbleibende Luecke, ausdruecklich benannt:** Fencing ist hier so stark wie die
Uhren-Uebereinstimmung der Hosts, und die Durchsetzung bleibt kooperativ — ein
Schreiber, der seine Nummer nie liest, wird von keinem Dateisystem gestoppt. Gewonnen
ist, dass ein Schreiber, der **prueft**, sich nicht mehr darueber irren kann, ob er die
Sperre noch haelt. Ein Wachprozess, der das erzwingt statt es anzubieten, waere ein
eigener Baustein und ist hier nicht gebaut.

**Der Push-Guard prueft dieselbe Bedingung.**
`~/.claude/hooks/lock_push_guard.py` wertet `LOCK_FENCE` und `LOCK_FENCE_FILE` aus und
blockiert den Push, sobald der Anspruch nicht mehr gilt. Ohne gesetztes `LOCK_FENCE`
aendert sich am bisherigen Verhalten nichts; ist `LOCK_FENCE` gesetzt, aber
`LOCK_FENCE_FILE` nicht, blockiert er ebenfalls — ein deklarierter, aber nicht
pruefbarer Anspruch ist kein Freibrief.

**Rueckwaertskompatibel in beide Richtungen.** Fehlt `fence`, gilt der bisherige
Zustand; bestehende Sperren brechen nicht und laufen unveraendert ab. Umgekehrt ist
`fence:` fuer jeden aelteren Leser nur eine weitere `key: value`-Zeile, die er wie alle
ihm unbekannten Felder ignoriert. `fence` fehlt ist **nicht** `fence: 0` — wer beides
gleichsetzt, sperrt alte Locks aus.

**Bei Erneuerung** (`created` nachziehen, damit eine lange Arbeit nicht verfaellt) bleibt
`fence` stehen. Erneuert wird dasselbe Lease, nicht ein neues vergeben. Nur `--force`
zaehlt als neue Vergabe und bekommt eine neue Nummer — dort soll ein alter Halter ja
gerade auffliegen.

---

## Zwei Stufen der Geltung

**Stufe 1 — BEACHTEN (immer Pflicht, ueberall):**
Vor jeder Bearbeitung eines Projekts/Bereichs pruefen, ob fuer den betroffenen Bereich eine nicht-abgelaufene `LOCK*.txt` existiert (die projektweite `LOCK.txt` sperrt alles). Wenn ja und nicht abgelaufen → nicht anfassen (anderes Projekt waehlen oder warten). Das gilt systemweit fuer jeden Agenten in jeder Pipeline.

**Stufe 2 — ANLEGEN (Lock-by-default fuer echte Arbeit) [C 2026-06-29]:**
Lock **proaktiv VOR** Beginn einer nicht-trivialen Bearbeitung setzen — NICHT erst „falls jemand da ist". Begruendung: „noch keiner da" ist kein Freibrief, sondern der Normalfall, in dem DU der Erste bist, der sperrt; verzichten alle mit dieser Logik, arbeiten beliebig viele Agenten ungesperrt parallel. Zwei gleichzeitig startende Agenten sehen sonst beide „leer" → Kollision. Detection ersetzt den Lock NICHT (genau das loest der Lock).
- **Pflicht in allen Dev-/Multi-Agent-Pipelines:** `.SOFTWARE`, `.RESEARCH`, `.ROBLOX`, `.AI`, `.UMBRUCH`, `.PRODUCTION` — autonome Loops und Sessions claimen ihr Projekt zu Arbeitsbeginn.
- **Sensible Projekte:** Marker **`LOCK-Pflicht: ja`** auf Projektebene (`README.md`/`CLAUDE.md`/Statusdatei) → Anlegen ebenfalls Pflicht.
- **Ueberall sonst:** bei jeder nicht-trivialen Bearbeitung locken. **Ausgenommen** nur triviale/read-only/klar-persoenliche Einzeledits.
- **Harter Zusatz-Trigger:** Sobald Anzeichen anderer Aktivitaet sichtbar sind (fremder Lock, kuerzliche Fremd-Aenderungen im Working Tree, Multi-Host-Sync) → **immer** locken, auch in freiwilligen Gebieten.

**FALLBACK / Vorrang:** Systemweit gilt diese Spec als Default. Projektspezifische Vorgaben gehen lokal vor (speziellere Regel schlaegt allgemeinere): Ein Projekt darf strenger sein (z. B. ANLEGEN-Pflicht erklaeren) oder einen abweichenden Scope-Namen vorgeben.

---

### Eine dritte, unsichtbare Stufe: Git-Hook-Guards [T-20260906-910508487]

`LOCK*.txt` ist nicht das Einzige, was eine Operation blockieren kann. Ein
Git-Arbeitsbaum kann einen eigenen `pre-push`/`pre-commit`/`pre-receive`-Hook
tragen, der eine Operation rundheraus ablehnt — unabhaengig vom Lock-System und
fuer dieses unsichtbar. Das ist real vorgekommen: Ein liegengebliebener
`pre-push`-Hook aus einem laengst aufgehobenen Build-Week-Judging-Embargo
blockierte Pushes in einer OneDrive-geteilten `.git`-Instanz auf zwei
verschiedenen Hosts an zwei verschiedenen Tagen, waehrend
`lock_scan.py --check-dir` dasselbe Verzeichnis beide Male als „frei" meldete —
„frei" hiess eben immer nur „keine LOCK-Datei", nie „pushbar".

`lock_scan.py --check-dir` meldet deshalb zusaetzlich jeden vorhandenen
`pre-push`/`pre-commit`/`pre-receive`-Hook des geprueften Verzeichnisses,
aufgeloest ueber `git rev-parse --git-path hooks` — ein `core.hooksPath`-Override
oder eine Worktree-eigene Config-Extension wird also genau so beruecksichtigt,
wie git selbst sie beruecksichtigen wuerde. Ein Hook, dessen Inhalt einer
bekannten Build-Week-Judging-Embargo-Signatur entspricht, wird ausdruecklich
markiert (`[EMBARGO-SIGNATUR]` bzw. `"embargo": true` im JSON). Ist
`core.hooksPath` konfiguriert, das Zielverzeichnis auf diesem Host aber nicht
vorhanden, fuehrt git dort stillschweigend gar keine Hooks aus — auch dieser
strukturelle Fall wird gemeldet (`GUARD-HINWEIS:` bzw. `"hook": null`), statt
aussehen zu duerfen wie „keine Hooks konfiguriert".

**Der Exit-0/1-Vertrag aendert sich dadurch NICHT.** Ein Guard-Hook wird als
Warnung neben dem Lock-Ergebnis gemeldet, nie als Sperre gewertet — ausser
`--strict` ist gesetzt, dann bedeutet Exit 2: keine LOCK-Datei, aber ein
Guard-Hook vorhanden. Wer automatisiert pusht, prueft also beides: den Lock
**und** die Guard-Zeile.

---

## Umstrittene Locks: gleichzeitige Claims ueber einen synchronisierten Ordner

„Zwei Stufen der Geltung" benennt das Problem bereits: Zwei gleichzeitig
startende Agenten sehen beide einen leeren Ordner. Geloest ist bisher nur der
Fall, in dem einer nachweislich zuerst da war. Bei Cloud-Sync mit 30 s bis 5 min
Latenz sehen **beide** einen leeren Ordner, beide legen an, und beide halten sich
fuer den Inhaber. Das ist kein Randfall: Zeitgesteuerte Automationen starten auf
mehreren Hosts zur selben Uhrzeit.

**Stufe 1 — exklusives Anlegen (immer aktiv).** `lock_create.py` legt die
Lockdatei exklusiv an (`open("x")`), statt erst zu pruefen und dann zu schreiben.
Zwischen Pruefung und Schreiben lag frueher ein Fenster, in dem ein zweiter
Prozess dieselbe Datei anlegt. `--force` ueberschreibt weiterhin bewusst.

**Stufe 2 — Contest-Verfahren (wird angewandt, wenn es sich lohnt).**

```
1. ANLEGEN     Lock exklusiv schreiben
2. QUARANTAENE warten (Default 300 s), bis der Sync beide Sichten angleicht
3. NACHLESEN   Verzeichnis erneut lesen
4. ENTSCHEIDEN fruehestes `created` gewinnt; exakter Gleichstand -> lexikografisch
               kleinerer Hostname
5. VERLIERER   eigenen Lock entfernen (Exit-Code 3), Bereich freigeben
```

Beide Seiten berechnen aus denselben Dateien dasselbe Ergebnis — kein Server,
keine Datenbank, kein Echtzeitkanal. Das Warten **ist** der Mechanismus, keine
Zierde: Ohne es liest das Nachlesen dieselbe unsynchronisierte Sicht wie zuvor.

**Wann es laeuft.** Nicht immer. Minuten Vorlauf sind fuer eine kurze Bearbeitung
teuer und fuer einen Automationslauf, der danach stundenlang arbeitet, billig.
Die Entscheidung trifft `should_contest()` anhand von Dauer und Umfang der
geplanten Arbeit; Details und Aufrufwege stehen in der englischen Fassung
(`LOCK-SYSTEM.md`, Abschnitt „Contested Locks").

---

## Team-Locks: Koordination statt nur Sperre

Team-Locks sind eine **eigene Kategorie** neben Exclusive Locks. Sie dienen nicht nur dem gegenseitigen Ausschluss, sondern sind ein Koordinationsprotokoll fuer paralleles Arbeiten: Anwesenheit, Rollen, Datei-/Tool-Claims, Warteschlangen und Nachrichten.

**Konzeptioneller Unterschied:**
- **Exclusive Lock** = "Hier arbeitet jemand, alle anderen: Finger weg."
- **Team Lock** = "Hier arbeitet ein Team, das sich intern abstimmt. Fuer andere Systeme: Finger weg."

### Neue Konvention (ab 2026-06-19)

```text
LOCK.team.<host>.txt                  # Team-Lock ganzes Projekt
LOCK.team.<scope>.<host>.txt          # Team-Lock fuer Komponente
```

Beispiele:

```text
LOCK.team.LAPTOP.txt                  # Laptop-Team arbeitet am Projekt
LOCK.team.assets.ASUS-GEI.txt        # ASUS-GEI-Team arbeitet an Assets
```

### Deprecated Konvention (bleibt gueltig, nicht mehr neu anlegen)

```text
LOCK.community.<arbeitsfeld>.<agentengruppe>.<host>.txt
```

Beispiele (noch im Umlauf, werden bei Gelegenheit migriert):

```text
LOCK.community.loop.codex.WORKSTATION.txt
LOCK.root-docs.codex.WORKSTATION.txt
```

**Migration:** Bestehende Community-Locks nicht sofort umbenennen (laufende Teams nicht stoeren). Bei natuerlichem Ablauf oder Neuanlage das neue `LOCK.team.*`-Format verwenden. Beide Formate werden vom Erkennungsregex und den Scripts erkannt.

### Pflichtbereiche einer Team-Lockdatei

1. **Anwesenheitslog:** Loop-ID, Agent, Rolle, Hauptaufgabe, Startzeit.
2. **Datei-/Ordner-Claims plus Warteschlange:** Wer bearbeitet welche Datei, warum, und wer wartet danach?
3. **Tool-/Software-/MCP-Claims plus Warteschlange:** Studio-Fenster, MCP-Sessions, Browser, Renderer oder andere exklusive Tools.
4. **Nachrichten, Tipps, Lessons Learned und Anfragen:** kurze Uebergaben, Warnungen, offene Beobachtungen.

### Abstimmungsregeln

- Beim Start eintragen, bevor bearbeitet wird.
- Rollen rotieren, wenn der Loop Rollenrotation verlangt; nicht stumpf dieselbe Rolle wie der direkte Vorgaenger waehlen.
- Bei belegten Ressourcen einen komplementaeren Slice waehlen.
- Nur Ressourcen claimen, die wirklich exklusiv sind; Skills, gelesene Doku und allgemeine Regeln werden nicht geclaimt.
- Claims eng halten und bei Aufgabenwechseln aktualisieren.
- Wer eine Ressource in die Warteschlange setzt, hat nach Freigabe Vorrang vor spaeteren Eintraegen.
- Beim Abschluss eigene Claims entfernen und Ergebnisse im passenden Log dokumentieren.
- Die gemeinsame Lockdatei nur loeschen, wenn keine andere aktive Anwesenheit mehr eingetragen ist.

### Herkunft und Referenzen

Das Team-Lock-Konzept stammt aus der Roblox-Pipeline (parallele Codex-Loops mit Rollenrotation):

- `.ROBLOX/TEAM_LOCK_VERFAHREN.md` — Roblox-Root-Destillat des Teamverfahrens.
- `.ROBLOX/CODEX_AUTOMATION_PIPELINE.md` — Einbettung in die Roblox-Codex-Automationspipeline.

Neue Pipelines duerfen das Muster uebernehmen, wenn Teamarbeit mehr braucht als einen einfachen harten Projekt-Lock.

---

## Lebenszyklus: BEACHTEN -> CLAIMEN -> FREIGEBEN

1. **BEACHTEN:** aktive Sperre fuer den Bereich pruefen (Stufe 1).
2. **CLAIMEN:** eigene `LOCK.txt` bzw. `LOCK.<scope>.txt` nach Vorlage anlegen (`owner`, `created` ISO, `expires_after` 24h, `purpose`).
3. **FREIGEBEN:** die selbst angelegte Lock-Datei am Ende **selbst loeschen**. Aktives Freigeben durch den Ersteller ist Pflicht; der 24h-Verfall ist nur ein **Sicherheitsnetz** fuer vergessene Locks. Bei langer Laufzeit `created` erneuern, damit die Sperre nicht vorzeitig verfaellt.

---

## Launch / Scripts

Zentrale Scripts liegen in `C:\Users\User\OneDrive\_scripts\`. Unter Windows immer `PYTHONIOENCODING=utf-8` setzen (cp1252-Encoding).

**Aktive Locks systemweit anzeigen (read-only):**
```
PYTHONIOENCODING=utf-8 python C:\Users\User\OneDrive\_scripts\lock_scan.py
PYTHONIOENCODING=utf-8 python C:\Users\User\OneDrive\_scripts\lock_scan.py --json
```
Listet Pfad, Scope, Owner, created und Restzeit aller nicht-abgelaufenen Locks ueber alle Roots aus `lock_roots.json`.

### Roots pflegen: haeufig frequentierte Ordner gehoeren IMMER hinein [U 2026-07-25]

Ein Lock wirkt nur, wo auch gescannt wird. **Jeder Ordner, in dem mehrere Agenten, Automationen
oder Hosts regelmaessig arbeiten, muss in `lock_roots.json` stehen — auch ausserhalb von OneDrive.**
Wird ein Arbeitsbereich neu eingefuehrt oder verlagert, ist die Root-Liste im selben Arbeitsgang
nachzuziehen.

**Lehrfall 2026-07-25:** Plan D verlagerte die Entwicklung aus OneDrive in Git-Klone unter
`C:\_Local_DEV\repos\<Repo>`; die Lock-Roots blieben bei OneDrive. Folge: Zwei After-Care-Laeufe
bearbeiteten dasselbe Repo im Abstand von 17 Minuten. Einer der beiden **hatte** sogar gelockt —
der Lock war nur fuer Scan und Watcher unsichtbar. Es fehlte also kein Mechanismus, sondern seine
Reichweite. Schlimmer noch: Die Luecke provozierte einen **Ersatzmechanismus** (ein eigener
`IN_PROGRESS`-Status in einer Rotations-Registry), der wieder zurueckgebaut werden musste.

**Merksatz:** Wo gearbeitet wird, muss gescannt werden. Sonst entstehen unsichtbare Locks — und
aus unsichtbaren Locks entstehen Parallelsysteme.

`C:\_Local_DEV\repos` ist seit 2026-07-25 als `shallow`-Root eingetragen (Locks gehoeren an den
Repo-Root, tiefer muss nicht gescannt werden).

### Ein frischer Klon sieht `LOCK.user.*` NICHT — immer den OneDrive-Zwilling mitpruefen [C 2026-09-13]

Die Zwei-Baeume-Regel sagt, ein Lock am Klon `C:\_Local_DEV\repos\<Repo>` **oder** am
OneDrive-Pfad binde gleichermassen. Daraus folgt eine Falle, die nicht offensichtlich ist:

**`LOCK.user.*` ist absichtlich nicht versioniert** (die Lockdatei sagt es selbst: "nicht
committen"; `git ls-files` zeigt keinen Treffer). Ein **frisch geklontes** Repo kann den
User-Lock deshalb gar nicht enthalten — dort steht nicht "kein Lock", dort steht **nichts**.
Wer nur den Klon prueft, liest aus einer leeren Stelle einen Zustand.

**Belegfall 2026-09-10 (T-20260913-785936980):** WORKSTATION-LG hatte beide Klone frisch aus
dem Remote geholt und notierte im Ticketverlauf "Der nutzergehaltene SentinelFleet-Lock ist
entfernt". Der Lock vom 27.08. lag unveraendert am OneDrive-Pfad; das Judging lief noch
(Devpost "Winners announced soon"). Das Schutzgut blieb unversehrt, aber die Zeitklausel des
Judging-Holds wurde irrtuemlich ueberschritten. Es war kein Regelverstoss, sondern eine
Messluecke — und sie trifft **jeden** Host, der frisch klont.

**Seit 2026-09-13 loest `lock_scan.py --check-dir` den Zwilling selbst auf** und meldet dessen
Locks mit (`(aus dem Zwilling im anderen Baum)`). Die Beziehung kommt aus der bereits
vorhandenen, deklarierten `REPO.pointer.json` — kein Namensraten:

| Richtung | Weg |
|---|---|
| OneDrive -> Klon | `REPO.pointer.json` liegt im geprueften Verzeichnis (`local_locator.windows_default`) |
| Klon -> OneDrive | `TWIN-INDEX.json`, den `lock_scan.py --write-cache` aus denselben Verzeichnissen mitschreibt, die es ohnehin durchlaeuft |

Konfiguriert wird das im Block `twin_resolution` in `lock_roots.json` (`clone_roots` +
`index_path`). **Fail-closed:** Liegt der gepruefte Pfad unter einem `clone_root` und der Index
fehlt, ist unlesbar, oder traegt der Zwilling eine kaputte `REPO.pointer.json`, meldet
`--check-dir` **UNBESTIMMT mit Exit 1** — nicht "frei". Fehlt der Block `twin_resolution` ganz
(anderer Host, kein Spiegel), ist die Zwillingspruefung "nicht anwendbar" und aendert nichts.

**Nach einem Umzug, einem neuen Klon oder einem neuen Zwilling** den Index auffrischen:
`lock_scan.py --write-cache`. Steht ein Repo nicht im Index, kennt `--check-dir` seinen
Zwilling nicht.

**Merksatz:** Eine leere Stelle ist kein Zustand. Bei einem frisch geklonten Repo beweist
"keine LOCK-Datei" gar nichts — erst der Blick auf den Zwilling tut das.

**Abgelaufene Locks aufraeumen:**
```
# Erst trocken pruefen (loescht nichts):
PYTHONIOENCODING=utf-8 python C:\Users\User\OneDrive\_scripts\prune_stale_locks.py --dry-run
# Tatsaechlich entfernen:
PYTHONIOENCODING=utf-8 python C:\Users\User\OneDrive\_scripts\prune_stale_locks.py
```
Entfernt `LOCK*.txt` mit `now > created + expires_after`. Legacy `TEST.txt`/`TESTS.txt` bleiben unangetastet. Eigene Roots-Datei: `--roots-file <pfad>`.

**Stale-Cleanup und Host-Erreichbarkeit (vorbereitet):** Abgelaufene Locks eines NICHT erreichbaren Hosts (z. B. Laptop offline, anderer Rechner heruntergefahren) koennen sicher freigegeben werden, sobald ihre Zeit abgelaufen ist — der normale Verfall greift bereits. Kuenftig kann der Cleanup optional die Host-Erreichbarkeit via Tailscale-Ping pruefen (`host_is_reachable(host)` → `True/False/None`), um auch noch nicht abgelaufene Locks eines dauerhaft unerreichbaren Systems frueher aufzuraeumen. Der Hook ist in `prune_stale_locks.py` vorbereitet (Stub-Funktion), aber noch nicht aktiv — bis dahin gilt: Locks laufen nach `expires_after` ab, egal ob der Halter erreichbar ist.

**Schnell-Index (LOCK-CACHE.md) aktualisieren:**
```
PYTHONIOENCODING=utf-8 python C:\Users\User\OneDrive\_scripts\lock_scan.py --write-cache
```
Schreibt zwei auto-generierte Caches: systemweit `_scripts/LOCK-CACHE.md` (alle Roots) und nur Software `.SOFTWARE/LOCK-CACHE.md` (Praefix-Filter auf `.SOFTWARE`). Beide sind abgeleitete Artefakte (nicht manuell editieren, in `.gitignore`); authoritativ bleiben die `LOCK*.txt` selbst.

**Scan-Begrenzung (Performance):** `lock_roots.json` steuert `default_max_depth` (Default 4), `shallow_depth` (Default 2, fuer Roots mit `"shallow": true` wie `.WISSEN`) und `skip_dirs` (uebersprungene Verzeichnisse inkl. Unterbaum, z. B. `node_modules`, `.venv`, `.git`, `build`, `releases`).

---

## Bibliothek

`_scripts/lock_utils.py` ist die kanonische Format-/Scope-/Verfall-Bibliothek (Single Source of Truth). Pipeline-lokale Kopien (z. B. `.SOFTWARE/_tools/lock_utils.py`) sind nur duenne Shims, die hier re-exportieren — kein Zweitstandard.


---

## PRIVATE.txt — Nie-öffentlich-Sperre für Repositories/Projekte [U 2026-07-18]

Besondere Lock-Art neben Exclusive/Team/User: Eine Datei `PRIVATE.txt` im
Projekt-/Repo-Root erklärt das Projekt als **NICHT-ÖFFENTLICH**.

- **Semantik:** Solange `PRIVATE.txt` existiert, ist JEDE Veröffentlichung
  verboten: GitHub-Visibility public, public Forks/Mirrors,
  Registry-/Marketplace-Einträge (npm public, MCP-Registry,
  Plugin-Kataloge), Zenodo/Preprints, öffentliche Websites/Doku mit
  Repo-Inhalten. Die BEARBEITUNG des Projekts bleibt erlaubt —
  `PRIVATE.txt` ist eine Publikationssperre, keine Bearbeitungssperre
  (deshalb bewusst außerhalb des `LOCK*.txt`-Scanmusters von
  `lock_scan.py`).
- **Aufhebung (präzisiert [U 2026-07-18]):**
  - **LEERE Datei** = Sperre auf unbestimmte Zeit; Entfernen NUR durch den
    User (weder Agenten noch prune-Skripte).
  - **MIT INHALT** können darin **Blocker und Aufhebebedingungen**
    definiert werden. Sind ALLE genannten Blocker nachweislich beseitigt
    bzw. die Bedingungen erfüllt, darf auch ein LLM/Agent die Datei
    löschen — mit dokumentiertem Nachweis (Log-/Commit-Vermerk, welcher
    Blocker wie erfüllt wurde). Enthält die Datei KEINE expliziten
    Aufhebebedingungen, gilt sie wie leer (nur User).
- **Gate-Lesart (Verallgemeinerung, user-angeregt [U 2026-08-08], additiv [C]):**
  `PRIVATE.txt` IST das universelle **Publication-Gate** des Systems — die
  Schleuse vor jeder Veröffentlichung (engl. *lock* = Schleuse; das
  LOCK-System trägt den Doppelsinn im Namen). Zwei Gate-Zustände, die
  exakt den beiden Aufhebungsformen oben entsprechen:
  - **`GATE: closed` (only private)** = leere Datei bzw. „Aufhebung NUR
    User" — das Gate ist aktuell NICHT überwindbar; nur der User öffnet.
  - **`GATE: conditional`** = Datei mit Aufhebebedingungen — das Gate ist
    überwindbar, sobald ALLE Bedingungen nachweislich erfüllt sind (dann
    auch durch Agenten, mit Nachweis).
  Die `GATE:`-Kopfzeile im Dateiinhalt ist empfohlene, optionale
  Maschinenlesbarkeit; Dateien ohne Kopfzeile werden nach den Regeln oben
  klassifiziert (rückwärtskompatibel). Es wird bewusst KEIN neuer
  Dateiname eingeführt: Alle Konsumenten (GithubBot, `/repo-publish-check`,
  Agenten-Pflichtprüfung) erkennen `PRIVATE.txt` — ein zweiter Name wäre
  eine Schutzlücke im Übergang.
- **Pflichtprüfung für alle Agenten und Bots:** Vor `gh repo create
  --public`, `gh repo edit --visibility public`, `npm publish` (public),
  Registry-Submits u. ä. MUSS im Zielprojekt (lokal UND im Repo-Root) auf
  `PRIVATE.txt` geprüft werden. GithubBot und `/repo-publish-check`
  behandeln Repos mit `PRIVATE.txt` als „nie public stellen, nie zur
  Veröffentlichung vorschlagen".
- **Empfohlener Inhalt:** Anlass + Datum + `[U]`-Kürzel, Scope
  (`never-public` oder `not-yet-public`), optional Blocker/
  Aufhebebedingungen (siehe oben), Historie.
- **Ablageort — REVIDIERT [U 2026-08-08, ersetzt die gitignore-Pflicht
  vom 2026-07-18]:** In **Git-Repositories werden `PRIVATE.txt` und
  `PUBLIC.txt` COMMITTET** (kein gitignore-Eintrag mehr). Begründung: Das
  Gate soll dort wirken, wo public geschaltet wird — am Remote. Ein
  committeter Marker ist für Web-UI, gh-CLI, Bots, Org-Admins und alle
  Host-Klone sichtbar; die Gate-Bedingungen (`GATE: conditional`) sind
  änderbare Inhalte, deren Historie versioniert gehört; und beim
  regulären Public-Gehen wird der Marker im Aufhebe-Commit entfernt (dass
  er in der Historie war, dokumentiert den Gate-Prozess — der Inhalt ist
  Policy-Text, kein Geheimnis; Secrets gehören ohnehin nie hinein).
  In **Ordnern ohne Repo** (OneDrive-Projekte, Modulordner-Zeiger) bleibt
  der Marker wie bisher eine lokale Datei. Prüfpflicht unverändert
  beidseitig: lokal UND Repo-/Remote-Root. Bestandsrepos mit
  gitignore-Eintrag stellen bei nächster Gelegenheit um (Eintrag
  entfernen, Marker committen).
- **Anlass/Präzedenzfall:** `ellmos-ai/law-checker` wurde 2026-07-11 ohne
  User-Freigabe public angelegt und am 2026-07-18 auf privat
  zurückgestellt (PRIVATE.txt committet). Es gilt weiterhin der Default
  aus `github-repo-care`: Neue Repos IMMER privat anlegen; public NUR mit
  expliziter User-Freigabe.


---

## PUBLIC.txt — Veröffentlichungs-Freigabe [U 2026-07-18]

Gegenstück zu `PRIVATE.txt`: Eine Datei `PUBLIC.txt` im lokalen
Projekt-/Repo-Root erklärt die Veröffentlichung als **FREIGEGEBEN**.

- **Semantik:** Existiert `PUBLIC.txt`, DARF das Projekt public gestellt /
  veröffentlicht werden — auch wenn es das noch nicht ist. Ohne
  `PUBLIC.txt` gilt der Default: privat anlegen, public nur mit
  expliziter, dokumentierter User-Freigabe.
- **Veröffentlichungs-Aufträge:** Die Datei kann Aufträge enthalten, die
  MIT der Veröffentlichung zu erledigen sind (z. B. Registry-/
  Marketplace-Einträge, README-Banner/i18n, llms.txt, Release-Tag,
  Org-Profil-Verlinkung). Der veröffentlichende Agent arbeitet sie beim
  Public-Stellen ab und dokumentiert den Vollzug.
- **Konfliktregel:** Liegen `PRIVATE.txt` UND `PUBLIC.txt` vor, gilt
  fail-closed `PRIVATE.txt`; der Widerspruch ist dem User zu melden.
- **Anlage:** `PUBLIC.txt` legt der User an — oder ein Agent auf
  ausdrückliche, dokumentierte User-Freigabe hin.
- **gitignore-Pflicht:** Wie `PRIVATE.txt` wird auch `PUBLIC.txt` NICHT
  committet (Projekt-`.gitignore`).
