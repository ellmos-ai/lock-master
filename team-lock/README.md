# team-lock — atomare Koordination innerhalb eines Systems

Dieses Teilmodul verwaltet die vier Abschnitte einer vorhandenen
`LOCK.team.<host>.txt`: Anwesenheit, Datei-/Ordner-Claims, exklusive
Tool-/MCP-Claims und Nachrichten. Die Lock-Datei bleibt die einzige fachliche
Quelle; es gibt keine zusätzliche Datenbank oder Registry.

| | |
|---|---|
| Stabile ID | `lock-master.team-lock` |
| Liefert | `control.team-locks` |
| Status | `active` |
| CLI | `python team_lock.py` oder installiert `lock-master-team` |

## Vertrag

- Ein Claim-Aufruf bildet genau ein atomares Bundle. Entweder werden alle
  normalisierten Ressourcen eingetragen oder keine.
- Die dateilokale `order`-Nummer trennt mehrere Bundles desselben Agenten und
  legt die FIFO-Priorität für überlappende Wartende fest. Sie ist keine
  dauerhafte Claim-ID und kein Recovery-Nachweis.
- Ein späterer Claim darf einen älteren, überlappenden Wartenden nicht
  überholen. Nicht überlappende Ressourcen können unabhängig fortfahren.
- Freigaben entfernen nur eigene, exakt benannte Ressourcen. Wiederholte
  Freigaben sind erfolgreich und ändern die Datei nicht.
- Einzelne alte Claim-Zeilen werden gelesen. Mehrere alte Zeilen desselben
  Agenten sind als Bundle mehrdeutig und führen deshalb zu einem
  fail-closed-Ergebnis.

## Persistenz und Guard

Der vollständige lokale Lese-/Prüf-/Schreibvorgang wird mit
`msvcrt.locking` unter Windows beziehungsweise `flock` unter POSIX
serialisiert. Die Schwesterdatei `LOCK.team.*.txt.guard` bleibt absichtlich im
Projekt: Sie enthält keinen fachlichen Zustand, und das Betriebssystem gibt den
advisory Lock bei Prozessende frei. Das Beibehalten derselben Guarddatei
verhindert, dass wartende Prozesse nach einem Unlink verschiedene Inodes
sperren. Projekte sollten genau `LOCK.team.*.txt.guard` ignorieren, aber niemals
die aktive `LOCK.team.*.txt` selbst.

Eine Änderung wird zuerst in eine eindeutige temporäre Datei im selben Ordner
geschrieben, geleert und synchronisiert. Erst danach ersetzt `os.replace` die
Zieldatei. Das schützt lokale konkurrierende Prozesse und erhält bei einem
fehlgeschlagenen Replace die bisherige Zieldatei. Es ist kein
hostübergreifendes Transaktions- oder Wiederanlaufprotokoll.

## Grenzen

Claims sind eine Kooperationskonvention und keine Sicherheitsgrenze. Das Modul
verwaltet keine Aufgaben, Abhängigkeiten, Git-Aktionen, Claim-IDs, Abläufe,
Handoffs oder Recovery-Ereignisse. Für die allgemeinen Lock-Regeln und das
kanonische Template siehe [`../LOCK-SYSTEM.md`](../LOCK-SYSTEM.md) und
[`../pure-locking/TEAM_LOCK_TEMPLATE.txt`](../pure-locking/TEAM_LOCK_TEMPLATE.txt).
