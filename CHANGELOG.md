# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
This project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **Discoverability, README-Design, Badges & Metadata Parity Check (2026-08-16)**:
  - Synchronized badges across `README.md` & `README_de.md` (Pytest 120 passed, Version 1.5.1, Python 3.10+, Ecosystem `ellmos-ai`, Umbrella `open-bricks`, `llms.txt` indexed).
  - Added bilingual Ecosystem & Sibling Tools matrix tables referencing multi-agent infrastructure (`ticket-master`, `clutch`, `coma`, `swarm-ai`, `gardener`, `prompt-evidence-collector`, `policy-registry`, `sqlite-transit-sync`, `workflowhooker`, `memoryhooker`, `DevCenter`, `CodeBox`, `safe-start-for-codex`, `automation-master`).
  - Added PEP 621 `[tool.ruff]` and `[tool.ruff.lint]` configuration in `pyproject.toml`.
  - Harmonized repository URLs across all submodule manifests (`ellmos-module.v2.json`, `pure-locking`, `permission-control`, `team-lock`) to `https://github.com/ellmos-ai/lock-master`.
  - Implemented automated metadata, documentation, manifest, and discoverability parity test suite in `tests/test_metadata.py` (6 tests).
  - Updated `llms.txt` timestamp to `2026-08-16` with verified 120-test count and enhanced search disambiguation.
- **`pure-locking/contested.py`** — resolution of simultaneous claims over a

  synced folder: quarantine, recheck, deterministic loser rule (earliest
  `created`, host order as tiebreak). Three-stage cloud detection: an optional
  external prober (FileCommander-style, *asked* rather than required), Windows
  placeholder attributes, path hints. The procedure does not run always but when
  it pays off (cloud folder **and** automation) — attempt rather than abstain.
- `lock_create.py`: `--contested`, `--no-contest`, `--quarantine`,
  `--verbose-contest`. Exit code 3 = claim lost, own lock removed.
- 26 tests in `tests/test_contested.py`, including the invariant "exactly one
  winner across a shared view" and "an expired own claim never wins".

### Changed

- **`lock_create.py` creates locks exclusively** (`open("x")`) instead of
  `exists()`-check-then-write. Between check and write there was a window in
  which two processes create the same file and both consider themselves the
  holder. `--force` still overwrites.
- `created` is now written with seconds. At minute granularity near-simultaneous
  claims land in the host tiebreak, where the same host loses structurally every
  time. `_parse_created` has always accepted seconds, so this is not a format
  break.
- `LOCK-SYSTEM.md`: new section "Contested Locks: Simultaneous Claims Over a
  Synced Folder".

Origin: moved here from `ellmos-ai/system-auditor` (audit host lock). There
exclusion was unwanted — parallel audits are the product — while here exclusion
is the purpose.

## [1.5.1] - 2026-07-28

### Added

- `lock_status.py` CLI utility and `pure-locking/lock_status.py` implementation for per-project lock status checks (Exit 0 = no lock / free, Exit 1 = locked, Exit 2 = error). Supports `--json` output and alternative `--project <path>` argument. Unit test suite added in `tests/test_lock_status.py`.

## [Unreleased]

### Added

- A loopback HTTP integration regression for the watcher: it starts the real
  server on an ephemeral port and verifies allowed GET/POST handling plus
  rejected DNS-rebinding Host headers.
- Watcher UI controls for protected user-lock creation/removal and guarded
  bulk-lock/bulk-unlock preview and commit flows.
- An opt-in stale-lock notification hook: `LOCK_MASTER_PRUNE_WEBHOOK_URL` or
  `--webhook-url` sends one JSON event after real removals.

### Fixed

- `load_config()` now expands `~` and environment variables (`%USERPROFILE%`, `$HOME`) in configured `roots[].path`, `caches[].path` and `caches[].filter_prefix`. Previously such entries stayed literal strings, `Path.exists()` returned False and the root was skipped **silently** — no exception, no warning, just fewer results. Observed 2026-08-01 on a consumer whose `lock_roots.json` referenced all OneDrive roots via `%USERPROFILE%`: the watcher reported 2 instead of 12 active locks. A lock watcher that misses locks is worse than none, because it reports false safety. Regression tests in `tests/test_config_path_expansion.py`.
- `watcher/cache_writer.py` called `lock_scan.write_caches()` with the two-argument signature while this module's version requires `config` as a third argument, breaking the cache write path with `TypeError`. The call now adapts to either signature.
- `_pid_is_running()` (in `watcher/lock_watcher.py` and `watcher/cache_writer.py`) queried `tasklist` via `subprocess` and read `result.stdout`. Under `pythonw.exe` there is no standard output, so `stdout` is `None` and `.splitlines()` raised `AttributeError` — the daemon could not be started windowless whenever a recent heartbeat was still present in `daemon_status.json`, which is exactly the restart case. Replaced by a `ctypes`/`OpenProcess` check: no console required, no window, and considerably faster. Simply guarding against `None` would have been wrong — it would make the function always return False and thus permit duplicate daemons.
- Fixed hardcoded static timestamp in `tests/test_watcher_resilience.py` (`test_real_lock_appears_and_disappears_across_scans`) by using dynamic ISO timestamps to prevent test failure on non-July-27 run dates.

### Changed

- Cleaned up unused imports and ambiguous variable names across pure-locking/watcher and test suites (`ruff check` 100% clean). Synchronized Pytest test badges (88 passed) in `README.md` & `README_de.md` and updated `llms.txt` verification timestamp to `2026-08-14`. [2026-08-14]
- Refreshed `llms.txt` `Last-checked` timestamp to `2026-08-04`, synchronized Pytest badges (81 passed) and added organisation (`ellmos-ai`) & umbrella (`open-bricks`) badges in `README.md` & `README_de.md`. [2026-08-04]

## [1.5.0] - 2026-07-27

### Fixed

- Portable watcher startup now supports `LOCK_MASTER_ROOTS_FILE` and
  user-neutral discovery of an existing OneDrive `_scripts/lock_roots.json`.
  Missing configuration fails with an actionable diagnostic instead of a bare
  `FileNotFoundError`.
- Daemon heartbeats run independently from full scans. A blocked filesystem scan
  can no longer freeze process health reporting.
- The API reports `degraded` when scan progress or the last completed scan is
  stale, even while the daemon heartbeat is fresh.
- User and condition locks receive no nominal expiry timestamp, and the watcher
  database excludes both types from automatic expiry as a second defence.
- The Windows launcher uses a `ping` delay that works with redirected stdin
  instead of `timeout /t`.

### Added

- Eight resilience regressions covering roots discovery, missing-config
  diagnostics, heartbeat progress during a stuck scan, real scan roundtrips,
  stale-scan health, protected-lock expiry and launcher behavior.

## [1.4.4] - 2026-07-27

### Maintenance & Technical Hygiene

- **Version Alignment**: Synchronized `VERSION` file (`1.4.4`) and `pyproject.toml` (`1.4.4`) metadata with actual project state.
- **Pytest Config Clean-up**: Consolidated test configuration in `pytest.ini` and eliminated redundant `[tool.pytest.ini_options]` in `pyproject.toml` to remove CLI warning output.
- **LLM & Readme Index Refresh**: Updated LLM indexing timestamps (`2026-07-27`) across `llms.txt`, `README.md`, and `README_de.md`.

## [1.4.3] - 2026-07-26

### Added & Improved

- **Mermaid Diagrams**: Added visual multi-agent lock detection lifecycle flowcharts to `README.md` and `README_de.md`.
- **Badges & Callouts**: Added Pytest 64-passed status badge and refreshed LLM indexing date (`2026-07-26`) in `llms.txt`, `README.md`, and `README_de.md`.

## [1.4.2] - 2026-07-25

### Added & Improved

- **Pyproject Metadata**: Added standardized `pyproject.toml` (PEP 621) with package metadata, URLs, keywords, and `pytest` configuration (`[tool.pytest.ini_options]`).
- **Discoverability & Badges**: Added Shields.io badges (Python, License, Multi-Agent Lock Protocol, LLM Indexing) and LLM indexing callouts (`> [!NOTE]`) to `README.md` and `README_de.md`.
- **LLM Index Refresh**: Updated `llms.txt` header timestamp to `2026-07-25`.

## [1.4.1] - 2026-07-04

### Fixed

- **Watcher daemon no longer crashes on its first scan.** `watcher/scanner.py`
  called five `lock_utils` helpers that only existed in a downstream fork of the
  library, not in this repo (`lock_name_parts`, `lock_type_from_name`,
  `normalize_lock_fields`, `parse_team_lock_sections`, `compute_expires_at`) —
  any tree containing a single `LOCK*.txt` killed the daemon with an
  `AttributeError` within one scan interval. The helpers are now part of
  `lock_utils.py`, and the daemon loop additionally guards full scans and quick
  checks so one failing scan can never terminate the process.
- **Watcher DB accepts user and condition locks.** The `locks` table CHECK
  constraint only knew `exclusive/team/legacy`, so v1.3.0 user locks and v1.4.0
  condition locks could never be persisted (`IntegrityError`). New databases use
  the extended constraint; existing databases are migrated automatically
  (table rebuild, rows preserved).
- **Web UI: Host-header validation against DNS rebinding.** All HTTP handlers
  now verify that the `Host` header is a loopback address (`127.0.0.1`,
  `localhost`, `[::1]`, with the served port). Previously GET endpoints
  (`/api/locks`, `/api/room-file/...`, `/api/settings`, ...) were readable by a
  malicious web page via DNS rebinding; the earlier CORS fix only covered
  write endpoints.
- `permissions.py`: rule matching is now platform-consistent and deliberately
  case-insensitive everywhere (`fnmatchcase` + casefold) — previously the same
  `LOCK.permissions.json` decided differently on Windows vs. POSIX, and deny
  rules could be bypassed by letter case on POSIX. Prefix rules (`rm:*`) now
  respect word boundaries and no longer capture e.g. `rmdir`.
- `lock_scan.py`: cache `filter_prefix` now matches on path-segment boundaries
  (`.../SOFTWARE` no longer leaks locks from `.../SOFTWARE-ARCHIVE`).
- `watcher/rooms.py`: notes filename validation uses `fullmatch` (an embedded
  trailing newline no longer passes).
- `watcher/web_server.py`: invalid `limit` query values return 400 instead of
  an unhandled traceback.

### Added

- `lock_create.py`: convenience script that stamps a new `LOCK*.txt` (exclusive,
  scoped, team, user, condition) with validation and overwrite protection.
- GitHub Actions CI (`.github/workflows/tests.yml`): pytest on Python
  3.10–3.13, Ubuntu + Windows.
- 19 new tests (suite 45 → 64): scanner/storage regression tests including a
  CHECK-constraint migration test, host-validation tests, permissions matching
  tests, cache filter tests, and full `lock_create.py` coverage.

## [1.4.0] - 2026-07-04

### Added

- **Condition Locks** (`LOCK.condition.txt` / `LOCK.condition.<scope>.txt`): condition-based,
  operation-scoped locks. They do NOT expire by time; they hold until the condition in the
  required `release_condition:` field is fulfilled. Prune and bulk-unlock never touch them.
  Unlike user locks, any agent may remove a condition lock once it has verifiably fulfilled
  the release condition (documenting the fulfilment when removing). New helpers in
  `lock_utils.py`: `is_condition_lock()`, `locked_operations()`; `scope_from_name()`
  understands the `condition` marker; `is_protected_lock()` now covers user + condition locks.
- **`operations:` field** (comma-separated): names the operations a lock forbids
  (e.g. `operations: publish-release, registry-upload`); everything not listed remains
  explicitly allowed. Primary use case: block a specific release/upload pipeline until
  review follow-ups are done, while normal development stays unrestricted.
- `lock_scan.py` reports type-aware status: `until condition met: ...` for condition
  locks, `user-held (no time expiry)` for user locks, and exposes `operations` /
  `release_condition` in JSON output.
- Test suite: `tests/test_condition_lock_system.py` (naming, protection, no-expiry,
  active-listing, operations parsing).

### Fixed

- **Protected locks never expire by time** in `lock_utils.is_expired()`: previously a
  nominally expired user lock dropped out of `active_locks()` / `lock_scan.py` output even
  though the spec defines user locks as valid until the user removes them. Protected locks
  (user + condition) now always report as active until removed.

---

## [1.3.0] - 2026-06-27

### Added

- **User Locks** (`LOCK.user.txt` / `LOCK.user.<scope>.txt`): user-owned full locks that are
  removed ONLY by the user (manually or via the watcher GUI). Agents and the stale-cleanup
  never touch them, even when nominally expired. New helpers in `lock_utils.py`:
  `is_user_lock()`, `is_protected_lock()`, `is_prunable()`; `scope_from_name()` understands the
  `user` marker.
- **`LOCK.permissions` permission scheme** (`permissions.py` + `LOCK_PERMISSIONS_TEMPLATE.json`):
  agent-neutral, folder-scoped allow/deny/ask rules (syntax borrowed from `.claude` —
  `Bash(...)`, `Read(...)`, `mcp__x__*`), readable by all agents. `evaluate()` precedence
  deny > ask > allow > default.
- **Bulk lock / immediate lockdown** (`bulk_lock.py`): guard-protected (`commit` flag),
  idempotent, reversible (`created_by: bulk` marker + session manifest). Never touches user
  locks — a folder holding a (even expired) user lock is treated as permanently locked.

### Changed

- `prune_stale_locks.py` now uses `is_prunable()` — user locks are never pruned.

### Notes

- Mirrored from the running `_scripts/` instance (canonical there); this module is the
  user-neutral publishable copy.

## [1.2.0] - 2026-06-19

### Added

- **Team Locks** (`LOCK.team.<host>.txt` / `LOCK.team.<scope>.<host>.txt`): new lock
  type for coordinating multiple agents within the same system. A Team Lock bundles four
  structured sections -- presence log, file/folder claims + queue, tool/MCP claims + queue,
  and messages/tips -- in a single file. Other systems treat the file as an Exclusive Lock.
- **`TEAM_LOCK_TEMPLATE.txt`**: ready-to-use template for Team Locks with all four required
  sections, inline comments, and neutral placeholders.
- **Cloud-Ready support**: Team Locks are designed for shared filesystems (OneDrive, Dropbox).
  Rename-based claiming is atomic on NTFS / most cloud-sync filesystems. Conflict-copy handling
  documented in `LOCK-SYSTEM.md`.
- `is_team_lock(name)` in `lock_utils.py`: returns `True` for `LOCK.team.*` filenames.

### Changed

- **Detection regex** updated from `^LOCK(\.[^.]+)?\.txt$` to
  `^LOCK(\.[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*)?\.txt$` to support multi-segment names
  (e.g. `LOCK.team.LAPTOP.txt`, `LOCK.team.frontend.SERVER-01.txt`).
- `scope_from_name()` updated: Team Locks return the correct component scope
  (or `'project'` when no component segment is present).
- `LOCK-SYSTEM.md`: added "Lock Types: Exclusive vs. Team" section with coordination rules,
  cloud-sync guidance, and conflict-copy handling.
- `README.md` (EN) and `README_de.md` (DE): added Team Lock and Cloud-Ready sections,
  updated scope convention table, updated file tree.
- `llms.txt`: added Team Lock and Cloud-Ready entries; updated `Last-checked` to 2026-06-19.

## [1.1.0] - 2026-06-16

### Added

- **`host` field** in the LOCK file format (optional): the machine/hostname that
  holds the lock, for cross-system coordination — makes visible **which** system
  locked an area. Backwards compatible: `lock_host()` accessor returns `None` when
  the field is absent. Documented in `LOCK-SYSTEM.md`, `LOCK_TEMPLATE.txt` and READMEs.
- `host_is_reachable()` stub in `prune_stale_locks.py` (prepared hook for future
  host-reachability-aware stale cleanup, e.g. via Tailscale ping; not yet active).

## [Unreleased]

### Added

- Optional `watcher/` integration: localhost daemon, SQLite-backed event/history
  store, REST API, static Web UI, room map, user lock creation, prune action,
  cache refresh, daemon heartbeat, and same-host singleton detection.
- `watcher/README.md` documenting runtime data, start commands, CLI, API, and
  scan model.

### Fixed

- Hardened watcher web API path and header handling for CodeQL path-injection
  and HTTP response-splitting findings.

### Documentation

- Added README entry tables and discovery/disambiguation context for multi-agent
  workspace locking, Codex/Claude/Gemini coordination, and `LOCK*.txt` search.
- Standardized `llms.txt` with `Last-checked`, Audience, Search Phrases, and
  Disambiguation sections.

## [1.0.0] - 2026-06-14

### Added

- `lock_utils.py` -- canonical library for LOCK file parsing, scope detection, expiry logic
- `lock_scan.py` -- read-only system-wide active-lock overview; config-driven cache output via `--write-cache`
- `prune_stale_locks.py` -- remove expired LOCK*.txt files with `--dry-run` support
- `LOCK_TEMPLATE.txt` -- copy-paste template for creating a new lock file
- `lock_roots.example.json` -- annotated example configuration with placeholder paths
- `LOCK-SYSTEM.md` -- canonical spec: lifecycle, tiers, format reference, script usage
- `tests/test_smoke.py` -- smoke tests: scope detection, expiry logic, dry-run prune
- `README.md` (EN) and `README_de.md` (DE) -- project documentation
- `SECURITY.md` -- vulnerability reporting policy
- `llms.txt` -- machine-readable project summary for LLM tools
- MIT License
