# Security Policy / Sicherheitsrichtlinie

[English](#english) | [Deutsch](#deutsch)

---

<a name="english"></a>
## English

### Supported Versions

| Version | Supported | Notes |
|---------|-----------|-------|
| `1.5.x` | :white_check_mark: | Current active release branch |
| `< 1.5` | :x: | Legacy release -- please upgrade |

### Reporting a Vulnerability

If you discover a potential security vulnerability in `lock-master`, please report it responsibly:

1. **Do not open a public issue.**
2. **Preferred Method**: Use GitHub Private Vulnerability Reporting via  
   [Security Advisories](https://github.com/ellmos-ai/lock-master/security/advisories) → *New draft security advisory*.
3. **Alternative Method**: Contact the maintainers directly via email:
   - `security@ellmos.ai`
   - `support@lukasgeiger.com`
   - `lukas@open-bricks.org`

Include detailed reproduction steps, environment details (OS, Python version), affected lock types, and an impact assessment.

### Security Guarantees & Architecture

- **Local-First & Zero-Egress**: `lock-master` executes 100% locally on the filesystem. It does not initiate outbound network requests, collect telemetry, or transmit workspace data.
- **Unprivileged User-Mode (Non-Elevation)**: Designed to operate entirely with standard user privileges. It does not require or request root or administrator elevation.
- **Path Traversal & Root Boundary Protection**: Target directories and lock roots configured via `lock_roots.json` are traversed defensively with depth limits and directory skipping to prevent arbitrary path traversal.
- **Atomic Locking & File Collision Resilience**: Exclusive lock creation relies on atomic filesystem semantics (`open("x")`) where supported, preventing race conditions between concurrent agents.
- **Safe Stale Lock Pruning**: The `prune_stale_locks.py` tool includes `--dry-run` inspection mode so maintainers and automated supervisors can preview deletions before unlinking files.

### Response Time

Critical security vulnerabilities are acknowledged within 48 hours, with patches prepared and released promptly.

---

<a name="deutsch"></a>
## Deutsch

### Unterstützte Versionen

| Version | Unterstützt | Anmerkungen |
|---------|-------------|-------------|
| `1.5.x` | :white_check_mark: | Aktueller Hauptzweig |
| `< 1.5` | :x: | Veraltet -- bitte aktualisieren |

### Sicherheitslücken melden

Wenn Sie eine Sicherheitslücke in `lock-master` entdecken, melden Sie diese bitte verantwortungsvoll:

1. **Erstellen Sie kein öffentliches GitHub-Issue.**
2. **Bevorzugter Weg**: Nutzen Sie die private Sicherheitsberatung auf GitHub via  
   [Security Advisories](https://github.com/ellmos-ai/lock-master/security/advisories) → *New draft security advisory*.
3. **Alternativer Weg**: Kontaktieren Sie uns direkt per E-Mail:
   - `security@ellmos.ai`
   - `support@lukasgeiger.com`
   - `lukas@open-bricks.org`

Bitte geben Sie eine genaue Problembeschreibung, Reproduktionsschritte, Betriebssystem-/Python-Version und das erwartete Sicherheitsrisiko an.

### Sicherheitsgarantien & Architektur

- **Local-First & Zero-Egress**: `lock-master` arbeitet vollständig lokal auf dem Dateisystem. Es werden keine Daten übertragen, keine Telemetrie erhoben und keine externen Netzwerkverbindungen aufgebaut.
- **Unprivilegierter User-Mode (Non-Elevation)**: Die Ausführung erfordert keinerlei Administrator- oder Root-Rechte.
- **Pfadgrenzen & Traversal-Schutz**: Konfigurierte Projektwurzeln in `lock_roots.json` werden kontrolliert durchsucht; Verzeichnistiefen und Ausschlüsse verhindern unautorisierte Pfadüberschreitungen.
- **Atomares Sperren & Kollisionsschutz**: Die Erstellung exklusiver Sperren nutzt atomare Dateisystem-Primitive (`open("x")`), um Race Conditions zwischen parallelen KI-Agenten zu unterbinden.
- **Sicheres Bereinigen verfallener Sperren**: Das Werkzeug `prune_stale_locks.py` bietet `--dry-run`-Vorschauen, um versehentliches Löschen aktiver Sperren auszuschließen.

### Reaktionszeiten

Sicherheitsrelevante Meldungen werden in der Regel innerhalb von 48 Stunden gesichtet und zeitnah behoben.
