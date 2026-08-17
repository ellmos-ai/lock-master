# Cloud-Sync-Preflight vor dem Lock-Check — Konzept & Empfehlung

> Stand: 2026-08-17 · Autor: governance-worker@ASUS-GEI (Ticket `T-20260816-998265218`) ·
> Status: **Konzept + Empfehlung, kein Code.** Zwei belegte Cross-Host-Kollisionen (Ticket-Master,
> 2026-08-15) sind der Anlass, nicht ein theoretisches Problem.

## 1. Hat lock-master Workflows?

**Ja — im Sinne von dokumentierten, ausführbaren Abläufen, nicht nur Bibliotheksfunktionen —
aber nicht als `workflows/`-Ordner.** `pure-locking/contested.py` implementiert ein
fünfschrittiges Protokoll (ANLEGEN → QUARANTÄNE → RECHECK → ENTSCHEID → VERLIERER), wörtlich
so benannt im Moduldocstring, mit 26 eigenen Tests (`tests/test_contested.py`) und einer
CLI-Anbindung (`lock_create.py --contested/--no-contest/--quarantine/--verbose-contest`,
Exit-Code 3 = Claim verloren). Das ist ein Workflow im geforderten Sinn: ein Ablauf mit
mehreren Schritten, Entscheidungspunkten und einem definierten Ergebnis, nicht nur eine
aufrufbare Funktion.

**Was dieser Workflow löst — und was nicht:** Er löst die **nachträgliche** Kollision (zwei
Hosts haben *bereits* gleichzeitig angelegt) durch Quarantäne + Recheck + deterministischen
Tiebreak. Er läuft **nach** dem eigenen Anlegen, nicht davor. Die vom Ticket verlangte
**Vorab-Prüfung** — den Ordner ansehen, BEVOR man selbst einen Lock schreibt, damit ein
bereits vorhandenes Fremd-Lock schneller sichtbar wird — existiert nicht. Das ist die
tatsächliche Lücke, nicht das Fehlen von Workflows insgesamt.

## 2. `fc_check_cloud_lock` — geprüft, NICHT die halbe Antwort

Live getestet gegen einen OneDrive-Pfad:

```
fc_check_cloud_lock("...\_control-center\_TICKETS")
→ cldflt.sys: aktiv (geladen)
→ In Sync-Ordner: OneDrive
→ Lock-Risiko: HOCH — rename-Operationen können blockiert werden
```

Das Werkzeug beantwortet *„kann ein Schreib-/Rename-Vorgang gerade vom Cloud-Filter-Treiber
blockiert werden"* — eine reine **lokale** Diagnose des Windows-Filtertreibers (`cldflt.sys`),
kein Signal über die **Aktualität** des angezeigten Verzeichnisinhalts gegenüber dem Server.
Das ist eine andere Frage als die des Tickets (*„ist das, was ich hier sehe, frisch genug, um
ein fehlendes Fremd-Lock als tatsächlich fehlend zu werten"*). **Korrektur der Vorab-Annahme:**
`fc_check_cloud_lock` ist für das Preflight-Problem nicht die halbe Antwort — es beantwortet
eine benachbarte, aber andere Frage. Wiederverwendbar ist nur die zugrundeliegende
Treiber-Erkennung, und die hat `contested.cloud_pressure()` bereits selbst (dieselben
Windows-Attribute: `FILE_ATTRIBUTE_OFFLINE`, `RECALL_ON_OPEN`, `RECALL_ON_DATA_ACCESS`,
Reparse-Point) — ohne Fremdmodul, plattformübergreifend als Fallback über bekannte
Cloud-Ordnernamen.

## 3. Gibt es einen zuverlässigen „Ping", der frischere Sicht erzwingt?

**Ehrlich beantwortet: nein, nicht universell und nicht mit Zusicherung.** Das ist keine
Lücke, die sich billig schließen lässt — es ist eine reale Grenze der Cloud-Sync-Architekturen:

- **OneDrive (Windows, Cloud Files API/`cldflt.sys`):** Ein einfaches Verzeichnis-Listing
  (`os.listdir`/`readdir`) liest **lokal bereits bekannte** Platzhalter-Metadaten — es
  erzwingt keinen Server-Roundtrip für Einträge, die der Client noch nicht kennt. Ein
  **gezieltes Öffnen/Lesen einer bereits bekannten Platzhalterdatei** kann echtes Nachladen
  auslösen (`RECALL_ON_OPEN`/`RECALL_ON_DATA_ACCESS` bedeuten wörtlich: Öffnen löst Abruf vom
  Server aus) — das hilft aber nur, wenn die Datei dem lokalen Client bereits als Platzhalter
  bekannt ist. Für eine Datei, die ein anderer Host gerade **neu** angelegt hat und die der
  lokale Client noch gar nicht in seinem Index hat, gibt es keinen dokumentierten, öffentlich
  nutzbaren Trigger, der „jetzt sofort beim Server nachfragen" erzwingt — das bleibt Sache des
  OneDrive-Sync-Clients und seines eigenen Taktes.
- **Dropbox / Google Drive / iCloud:** eigene Filtertreiber-Äquivalente, i. d. R. **ohne**
  öffentlich dokumentierte Refresh-Trigger, die von einem Python-Skript aus zuverlässig
  aufgerufen werden könnten.
- **macOS/Linux (kein `cldflt`):** andere Dateisystemsemantik; die Windows-Attributprüfung
  entfällt vollständig, es bleibt nur der Pfad-Namens-Fallback aus `CLOUD_ROOT_HINTS`.

**Das ist genau der Grund, warum `contested.py` bereits „Quarantäne + Recheck" statt „Ping"
gewählt hat** (Moduldocstring: *„Cloud-Sync mit 30 s – 5 min Latenz"* wird als **gegebene**
Randbedingung behandelt, nicht als etwas, das sich wegtricksen lässt). Ein Preflight-Mechanismus
sollte demselben Prinzip folgen, nicht einen unzuverlässigen Ping vortäuschen.

## 4. Empfehlung: Preflight nach demselben Muster wie `contested.contest()`, nur davor statt danach

**Kein neuer Mechanismus — dieselbe, bereits gebaute und getestete Logik ein zweites Mal
angewendet, an einer anderen Stelle im Ablauf.**

Vorschlag für `lock_scan.py`/`lock_create.py` (Skizze, nicht Code):

1. Vor dem Schreiben eines neuen Locks: `contested.cloud_pressure(project_dir)` prüfen —
   dieselbe Funktion, die es schon gibt.
2. Nur wenn cloud-backed **und** `looks_like_automation()` (dieselben zwei Bedingungen wie bei
   `should_contest()` — der Sinn dieses Gates ist der Reibungsasymmetrie-Schutz: ein Mensch an
   der Tastatur wartet nie unnötig, s. Abschnitt 5): einen **kurzen** Preflight-Recheck
   durchführen — Verzeichnis listen, `N` Sekunden warten (deutlich kürzer als die 300 s
   Post-Claim-Quarantäne, da hier nur „ist inzwischen etwas Neues sichtbar geworden" geprüft
   wird, kein Tiebreak stattfindet), erneut listen. Erscheint dazwischen ein fremdes Team-/
   Exclusive-Lock, gilt der Bereich als belegt — kein eigener Lock wird geschrieben.
3. **Fail-closed-Formulierung statt Freibrief:** Läuft der Preflight (Cloud + Automation),
   aber es erscheint nichts Neues, ist das Ergebnis **„kein Fremd-Lock sichtbar nach N s
   Nachbeobachtung"** — nicht „garantiert frei". Genau dieselbe Sprache, die
   `controlroom_bridge.py` (ControlCenter-MCP) bereits für Locks verwendet
   (`verdict: unknown`, `safe_to_proceed: false` als Default bei Unsicherheit) — hier sollte
   ein neuer Verdict-Wert wie `"clear_after_preflight"` (statt schlicht `"clear"`) denselben
   Unterschied für Aufrufer sichtbar machen, die zwischen „nachweislich frei" und „nach
   begrenzter Beobachtung nichts gefunden" unterscheiden wollen.
4. Läuft der Preflight nicht (kein Cloud-Ordner, oder interaktive Sitzung): normales
   Sofort-Anlegen wie heute — **keine Verhaltensänderung für den Normalfall.**

## 5. Kosten/Nutzen — Reibungsasymmetrie ausdrücklich geprüft

Die im Auftrag genannte Regel (Ticket `T-20260816-793799370`, FileCommander): *eine Prüfung,
die teurer ist als ihr Verstoß, wird umgangen.* Für den Preflight gilt dasselbe Muster, das
`should_contest()` bereits vorlebt:

- **Gate zuerst, Wartezeit nur danach.** Die beiden Bedingungen (Cloud-Ordner + Automation)
  sind selbst billig zu prüfen (Dateiattribut-Lesen, Umgebungsvariablen) und schließen
  interaktive Sitzungen von jeder Wartezeit aus — ein Mensch merkt vom Preflight nichts.
- **Kürzer als die Post-Claim-Quarantäne.** 300 s vor jedem Automationslauf wären in der
  Praxis genau die Reibung, die zum Umgehen (`--no-contest` dauerhaft setzen) einlädt. Ein
  Preflight in der Größenordnung von Sekunden bis niedriger zweistelliger Sekundenzahl
  (Vorschlag, kein Fixwert — abzustimmen mit dem Repo-Eigentümer) ist der richtige Tausch:
  merklich billiger als eine echte Kollision (zwei Systeme arbeiten Stunden lang am selben
  Bereich), aber teuer genug, dass niemand ihn versehentlich für triviale Edits erzwingt.
- **Ehrlich benannt bleibt:** Ein Preflight senkt die Kollisionswahrscheinlichkeit, beseitigt
  sie aber nicht — genau deshalb bleibt `contested.contest()` als Netz danach unverändert
  nötig. Beide Mechanismen ergänzen sich (vorher: Risiko senken; nachher: unausweichliche
  Restfälle auflösen), keiner ersetzt den anderen.

## 6. Nicht umgesetzt — warum

Dies ist eine Konzept- und Empfehlungsantwort, kein Feature-Patch: `lock_master` ist ein
veröffentlichtes Paket (`1.5.1`, PyPI-fähiges `dist/`) mit 120 grünen Tests und eigener
Release-Disziplin. Eine neue Preflight-Variante von `contested` verdient denselben
Sorgfaltsgrad wie der bestehende Contest-Mechanismus (26 eigene Tests) — das ist Arbeit für
eine eigene, freigegebene Session, nicht ein Nebenprodukt einer Ticket-Recherche. Eintrag
dazu in `TODO.md` unter „Claim-Härtung" ergänzt.
