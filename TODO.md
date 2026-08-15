# TODO

## STATUS

| Category | Status | Notes |
|---|---|---|
| Release gate | PASS | Final Gate Check: 10 PASS / 0 FAIL / 0 WARN on 2026-06-19. |
| Tests | PASS | `python -X utf8 -m pytest -q` passes from the module root. |
| Documentation | READY | README, localized READMEs, SECURITY, CHANGELOG and LOCK-SYSTEM are present. |
| Integration | READY | Fits `.MODULES` as a standalone, zero-dependency coordination module for shared agent workspaces. |
| Known follow-ups | OPEN | Convenience scripts, CI and watcher polish remain backlog items below. |

## Review 2026-07-04 (Modul-Review-Loop, Subagent-Review — alle Funde gefixt, v1.4.1)

- [x] **(hoch)** Watcher-Daemon crashte bei jedem Scan mit existierendem Lock —
  scanner.py nutzte 5 in diesem Repo nicht existierende lock_utils-Helper
  (Drift wie beim web_server.py-Fall). Helper portiert, Daemon-Loop zusätzlich
  mit try/except abgesichert, Scanner-Regressionstests ergänzt.
- [x] **(hoch)** locks-Tabelle CHECK kannte 'user'/'condition' nicht →
  IntegrityError; Schema erweitert + Auto-Migration (Table-Rebuild) für Alt-DBs.
- [x] **(hoch)** GET-Endpunkte ohne Host-Validierung → DNS-Rebinding-Datenleck;
  Host-Header-Check (Loopback only) für ALLE Methoden.
- [x] **(mittel)** permissions.py plattformabhängiges fnmatch + rm:*-matcht-rmdir;
  jetzt fnmatchcase+casefold (überall case-insensitiv) + Wortgrenze.
- [x] **(niedrig)** lock_scan filter_prefix ohne Segmentgrenze; rooms.py
  match→fullmatch; web_server limit-Parsing → 400.
- [x] **(Folge)** Watcher-Tests deckten nur Helper/Module ab — ein
  Integrationstest startet jetzt einen kurzlebigen HTTP-Server und prüft echte
  GET/POST-Anfragen gegen 127.0.0.1 sowie Host-/Origin-Gates (2026-08-10).
- [x] **(Folge — erledigt 2026-07-04, User-Direktive „immer Verbesserungen
  rückangleichen")** Dieselben Funde in der privaten Live-Instanz
  (`_control-center/_lock_watcher`) bestätigt und portiert: CHECK-Constraint
  ohne user/condition (+ Auto-Migration per Table-Rebuild) und fehlende
  Host-Validierung auf GET/POST/PUT/OPTIONS (DNS-Rebinding). Live verifiziert:
  Web-Server neu gestartet, `Host: evil.example.com` → 403, Loopback ok;
  private Suite 146 Tests grün (1 veralteter user-lock-Test an die kanonische
  v1.4.0-Semantik angeglichen: geschützte Locks laufen zeitbasiert NIE ab).

## Planned

- [x] Additional language versions (es, ja, ru, zh-Hans) -- done: README_es.md, README_ja.md, README_ru.md, README_zh-Hans.md added with language switcher in all READMEs
- [x] `lock_create.py` -- convenience script to stamp a new LOCK*.txt from the template (done 2026-07-04: exclusive/scoped/team/user/condition, Validierung, Überschreibschutz, 9 Tests, README-Zeile EN/DE — Locale-READMEs es/ja/ru/zh noch ohne die neue Zeile)
- [x] Optional HTTP(S) webhook notification on lock expiry (prune hook) -- done 2026-08-13:
      opt-in `LOCK_MASTER_PRUNE_WEBHOOK_URL` / `--webhook-url`, one JSON event per
      real cleanup run, dry-runs and notification failures remain non-destructive
- [x] GitHub Actions CI: run smoke tests on push (done 2026-07-04: pytest-Matrix 3.10–3.13 auf ubuntu+windows)
- [ ] Watcher UI polish after longer real-world daemon runs: empty roots, very large roots, stale daemon messaging, mobile layout
- [x] **Drift check 2026-07-03, closed 2026-07-04 (T-20260704-05 audit):** Ported the
      user-neutral part of the private live instance's web_server.py delta: `/api/user-lock`,
      `/api/user-lock/remove` (create/remove `LOCK.user(.<scope>).txt` via the GUI channel) and
      `/api/bulk-lock`, `/api/bulk-unlock` (wired to the already-present `bulk_lock.py`, minus a
      private event-logging side-call with no equivalent here). Room-stats-refresh was already
      in sync. Intentionally NOT ported: a ticket-intake endpoint and a project-docs endpoint --
      both tied to private, user-specific tracking systems, not generic. `pure-locking/watcher/config.py`
      stays divergent by design (portable `LOCK_MASTER_WATCHER_DATA`/`REPO_ROOT` vs. private
      auto-discovery).
- [x] **Follow-up (medium, erledigt 2026-08-13):** `pure-locking/watcher/static/`
      enthält jetzt die lokale UI für User-Lock anlegen/entfernen sowie Dry-Run-
      und Commit-Aktionen für Bulk-Lock/Bulk-Unlock.

## Ideas / Backlog

- [x] `lock_status.py` -- per-project status check (exit 0 = no lock, exit 1 = locked) -- done 2026-07-28
- [ ] Integration example for cron-based stale cleanup
- [ ] Optional installer/launcher wrapper for `pure-locking/watcher/` on non-Windows systems

## Claim-Härtung nach Bandmaster-/ArenaOS-Vergleich (2026-08-15)

> Modulgrenze: Die folgenden Punkte betreffen ausschließlich Sperren, Claims und
> deren Nachweise. `lock-master` verwaltet weiterhin **keine Aufgaben**, keine
> Abhängigkeiten, keine Validierungen und keine Git-Commits.

- [ ] In `team-lock` ein transaktionales `claim_many` entwerfen: Eine Menge
  normalisierter Ressourcen wird entweder vollständig und atomar beansprucht
  oder gar nicht; Teilclaims werden bei Konflikten zurückgerollt.
- [ ] Konflikte nicht nur auf identische Namen, sondern auf überlappende
  Pfadbereiche prüfen (`a/` kollidiert mit `a/b`), mit dokumentierter
  Plattform- und Groß-/Kleinschreibungssemantik.
- [ ] Einen unveränderlichen Claim-Snapshot mit Claim-ID, Besitzer, Ressourcen,
  Erstellungszeit, Ablaufzeit und optionalem Ressourcen-Fingerprint vorsehen.
  Der Fingerprint erkennt Drift, erteilt aber keine Schreibberechtigung.
- [ ] Handoff-/Recovery-Belege ergänzen: Übernahme, Verlängerung, Freigabe,
  Ablauf und erzwungene Bereinigung sollen als append-only Ereignisse
  nachvollziehbar sein, ohne daraus einen Workflow- oder Task-Manager zu machen.
- [ ] Konkurrenz-, Crash- und Wiederanlaufstests für `claim_many` ergänzen:
  genau ein Gewinner, keine Teilclaims, keine fremde Claim-ID im Fehlerpfad,
  idempotente Freigabe und konservatives Verhalten bei beschädigtem Zustand.

## Done

- [x] Portable `watcher/` integration added: localhost daemon, REST API, Web UI, SQLite runtime outside the repo (2026-06-25)
- [x] `.MODULES/README.md` entry registered for module discoverability (2026-06-21)
- [x] Initial release v1.0.0 (2026-06-14)
