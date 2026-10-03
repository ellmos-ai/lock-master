# Contributing to lock-master / Mitwirken an lock-master

Welcome! We welcome contributions to `lock-master` (Portable, zero-dependency multi-agent file-lock system — Exclusive and Team Locks from [ellmos-ai](https://github.com/ellmos-ai)). To maintain deterministic agent execution loops, air-gapped process isolation, single-writer filesystem safety, and compliance across multi-host environments, all contributions must adhere to the quality standards and operational invariants defined below.

---

## English

### 1. General Principles & Quality Gates
1. **Local-First & Zero-Egress (`INV-LOCAL-01`)**: All lock management, scanning, and inspection routines operate 100% offline using the Python standard library. Zero outbound network sockets, zero telemetry, and zero phone-home tracking.
2. **Unprivileged User-Mode Execution (`INV-SEC-02` / `RunAsInvoker`)**: All CLI runners, lock checkers, background watchers, and pruning routines execute strictly in unprivileged user space. Administrative elevation (UAC/root/sudo) is strictly forbidden.
3. **Fail-Closed Default Semantics (`INV-FAIL-03`)**: Ambiguous states, corrupt lock files, lock acquisition races, or unparsable conditions fail closed immediately with a safe, read-only denial.
4. **Hierarchical & Scoped Locking (`INV-SCOPE-04`)**: Root `LOCK.txt` protects an entire project; scoped locks (`LOCK.<scope>.txt`) provide fine-grained concurrency across decoupled workspace subsystems.
5. **Multi-Agent Team Coordination (`INV-TEAM-05`)**: Intra-host coordination (`LOCK.team.<host>.txt`) coordinates co-located agents (presence, file claims, tool claims, and message boards) while signaling peer hosts during cloud-sync latency.
6. **Deterministic TTL & Safe Stale Pruning (`INV-TTL-06`)**: All locks define explicit `expires_after` durations (default 24h); stale cleanup (`prune_stale_locks.py`) provides safe `--dry-run` inspection before atomic unlinking.
7. **Declarative Permission Engine (`INV-PERM-07`)**: Rule evaluations against `LOCK.permissions.json` enforce strict priority ordering (`deny` > `ask` > `allow` > default) with deterministic path matching.
8. **Read-Only Inspection & Atomic Cache (`INV-AUDIT-08`)**: Status queries and scanner operations (`lock_scan.py`) operate in pure read-only mode; cache files (`LOCK-CACHE.md`) are written atomically without perturbing active locks.
9. **100% Permissive Dependency Stack (`INV-LIC-09`)**: Core runtime has zero external dependencies (`dependencies = []`); development tools are strictly MIT, Apache-2.0, PSFL, and BSD-3-Clause audited with full attribution in [NOTICE](NOTICE).
10. **Dual Security SLA (`INV-SLA-10`)**: We commit to a 48h acknowledgment and 5-business-day triage SLA, with a 30-day remediation timeline pursuant to [SECURITY.md](SECURITY.md).
11. **Clean Code & Regression Testing**: Every feature, enhancement, or fix must include regression tests in `tests/`. Keep test coverage at a 100% pass rate.
12. **Bilingual Parity**: Maintain synchronized structural and navigational parity across `README.md` and `README_de.md` (18-point dual anchors `sec-01` through `sec-18`).

### 2. Local Development Workflow (Plan D)
```bash
# Clone the repository (canonical Plan D location)
git clone https://github.com/ellmos-ai/lock-master.git "C:\_Local_DEV\repos\lock-master"
cd "C:\_Local_DEV\repos\lock-master"

# Install package in editable mode with development dependencies
pip install -e ".[dev]"

# Run comprehensive test suite
pytest -ra -v

# Run fast static code analysis
ruff check .

# Check bytecode compilation
python -m compileall -q .

# Check whitespace and git diff cleanliness
git diff --check
```

### 3. Submission Protocol
- Open an issue for architectural discussions before large refactoring.
- Keep provider API keys, tokens, and private credentials strictly outside the repository.
- Ensure all 10 governance invariants (`INV-LOCAL-01` to `INV-SLA-10`) remain VERIFIED.
- Pull requests must target the `main` branch.

### 4. License
By contributing to `lock-master`, you agree that your contributions will be licensed under the [MIT License](LICENSE).

---

## Deutsch

### 1. Grundsätze & Qualitäts-Tore
1. **Local-First & Zero-Egress (`INV-LOCAL-01`)**: Sämtliche Sperrverwaltungs-, Scan- und Prüfroutinen arbeiten standardmäßig zu 100% offline ausschließlich mit Modulen der Python-Standardbibliothek. Keine Telemetrie, keine externen Sockets, kein Phone-Home.
2. **Unprivilegierte Benutzer-Ausführung (`INV-SEC-02` / `RunAsInvoker`)**: Sämtliche CLI-Befehle, Lock-Checker, Hintergrund-Watcher und Pruning-Skripte laufen strikt im unprivilegierten Standard-Benutzerkontext. Administrative Rechte oder UAC-Elevationen sind verboten.
3. **Fail-Closed Standard-Semantik (`INV-FAIL-03`)**: Mehrdeutige Zustände, beschädigte Sperrdateien oder unauflösbare Bedingungen schließen sofort fehl (Fail-Closed) mit sicherem Lesezugriff bzw. Verweigerung.
4. **Hierarchisches & bereichsbezogenes Sperren (`INV-SCOPE-04`)**: Ein globales `LOCK.txt` schützt das gesamte Projekt; bereichsbezogene Sperren (`LOCK.<scope>.txt`) ermöglichen granulare Parallelität zwischen unabhängigen Teilsystemen.
5. **Multi-Agenten-Team-Koordination (`INV-TEAM-05`)**: Intra-Host-Koordination (`LOCK.team.<host>.txt`) koordiniert lokale Agenten (Präsenz, Dateiansprüche, Werkzeugansprüche, Nachrichten) und signalisiert entfernten Hosts Wartezustände während der Cloud-Sync-Latenz.
6. **Deterministische TTL & sicheres Bereinigen (`INV-TTL-06`)**: Jede Sperre definiert eine explizite Gültigkeitsdauer `expires_after` (Standard: 24h); die Bereinigung (`prune_stale_locks.py`) bietet eine sichere `--dry-run`-Vorschau vor atomarem Löschen.
7. **Deklarative Rechteverwaltung (`INV-PERM-07`)**: Regelauswertungen gegen `LOCK.permissions.json` erzwingen strikte Prioritäten (`deny` > `ask` > `allow` > default) mit deterministischem Pfadabgleich.
8. **Reine Lese-Inspektion & atomarer Cache (`INV-AUDIT-08`)**: Statusabfragen und Scanner-Läufe (`lock_scan.py`) arbeiten rein lesend; Cache-Dateien (`LOCK-CACHE.md`) werden atomar geschrieben, ohne aktive Sperren zu verändern.
9. **100% permissiver Abhängigkeits-Stack (`INV-LIC-09`)**: Die Kernlaufzeit besitzt keinerlei externe Abhängigkeiten (`dependencies = []`); Entwicklungswerkzeuge sind strikt MIT-, Apache-2.0-, PSFL- und BSD-3-Clause-auditiert mit vollständiger Nennung in [NOTICE](NOTICE).
10. **Duale Sicherheits-SLA (`INV-SLA-10`)**: Verbindliche Zusage von 48h Reaktionszeit, 5 Werktagen Triage-Bewertung und 30 Tagen Behebungsfrist gemäß [SECURITY.md](SECURITY.md).
11. **Sauberer Code & Regressionstests**: Jede Änderung erfordert begleitende Tests in `tests/`. Die Testsuite muss zu 100% grün bleiben.
12. **Bilinguale Parität**: Strukturelle und navigatorische Parität zwischen `README.md` und `README_de.md` (18-Punkte Dual-Anker `sec-01` bis `sec-18`) ist zwingend einzuhalten.

### 2. Lokaler Entwicklungs-Workflow (Plan D)
```bash
# Klonen des Repositories (kanonischer Plan D Pfad)
git clone https://github.com/ellmos-ai/lock-master.git "C:\_Local_DEV\repos\lock-master"
cd "C:\_Local_DEV\repos\lock-master"

# Paket im Entwicklungsmodus mit Testabhängigkeiten installieren
pip install -e ".[dev]"

# Testsuite ausführen
pytest -ra -v

# Schnelle statische Code-Prüfung ausführen
ruff check .

# Bytecode-Kompilierung validieren
python -m compileall -q .

# Whitespace- und Diff-Sauberkeit prüfen
git diff --check
```

### 3. Einreichungsprotokoll
- Öffnen Sie bei größeren Architekturentscheidungen vorab ein GitHub Issue.
- Halten Sie API-Schlüssel, Tokens und private Zugangsdaten strikt außerhalb des Repositories.
- Stellen Sie sicher, dass alle 10 Invarianten (`INV-LOCAL-01` bis `INV-SLA-10`) verifiziert bleiben.
- Pull Requests müssen gegen den `main`-Branch gerichtet sein.

### 4. Lizenz
Mit Ihrer Mitwirkung an `lock-master` erklären Sie sich damit einverstanden, dass Ihre Beiträge unter der [MIT-Lizenz](LICENSE) lizenziert werden.
