# lock-master Watcher

Optional local daemon, REST API, and browser UI for `lock-master`.

The watcher does not replace the `LOCK*.txt` protocol. Lock files remain the
authoritative source of truth; SQLite, cache files, and the UI are derived views.

## Layout

The watcher lives in `watcher/` and imports the canonical lock-master scripts
from the repository root:

- `../lock_scan.py`
- `../lock_utils.py`
- `../prune_stale_locks.py`
- `../lock_roots.json`

`lock_roots.json` is intentionally local and ignored by Git.

Resolution order:

1. `LOCK_MASTER_ROOTS_FILE`
2. `pure-locking/lock_roots.json`
3. `<OneDrive>/_scripts/lock_roots.json` from the Windows OneDrive environment
4. `~/OneDrive/_scripts/lock_roots.json`

Startup fails with an actionable list of checked paths when no configuration is
available.

## Runtime Data

Runtime data is stored outside the repository by default:

```text
~/.lock_master_watcher/watcher.db
~/.lock_master_watcher/daemon_status.json
~/.lock_master_watcher/rooms.json
~/.lock_master_watcher/user_profile.json
~/.lock_master_watcher/central_files/
```

Override the directory with:

```bash
LOCK_MASTER_WATCHER_DATA=/path/to/runtime python watcher/web_server.py
```

Keeping SQLite outside synced project folders avoids WAL and cloud-sync conflicts.

### Cache directory

The watcher writes one detail cache per host, `LOCK-CACHE.<host>.md`, next to its code by default.
When the watcher runs from a git checkout, point it elsewhere so the checkout stays clean:

```bash
LOCK_MASTER_WATCHER_CACHE_DIR=/path/to/cache-dir python watcher/lock_watcher.py --update-cache
```

The standard caches written by `lock_scan.write_caches()` have their own targets in the `caches`
block of the roots file and are not affected.

### Host overlay (room names, colors, icons)

Names of your own projects do not belong into this repository. A host overlay supplies them from
outside; the default location is `<runtime data>/overlay/` and `LOCK_MASTER_WATCHER_OVERLAY` moves it:

```text
<overlay dir>/rooms.overlay.json   workspace_root, labels, colors   (see rooms.overlay.example.json)
<overlay dir>/icons/*.svg|png      room icons, listed by /api/icons and served as /icons/<name>
```

`labels` and `colors` are keyed by the root path relative to `workspace_root` (forward slashes). Pin
`workspace_root`: without it the common path of all roots is used, and the room keys (which name the
entries of `rooms.json` and the icons) change as soon as a root is added or removed. Per-room
settings made in the UI (`rooms.json`) still win over the overlay. A missing or unusable overlay is
ignored.

## Start

From the repository root:

```bash
python watcher/lock_watcher.py --update-cache
python watcher/web_server.py --port 8095
```

On Windows:

```bat
watcher\START.bat
```

The web UI is local-only:

```text
http://127.0.0.1:8095
```

### Windowless daemon

`start_daemon_hidden.vbs [--update-cache]` starts the daemon without a console window and writes its
output to `daemon_stdout.log` in the runtime directory (rotated at 5 MB). It waits for the daemon on
purpose, so a scheduled task stays "running" while the daemon lives; the task must not have an
execution time limit. A second start is harmless (the running daemon is detected by its heartbeat), so
a repeating trigger works as a watchdog. `LOCK_MASTER_PYTHONW` names the `pythonw.exe` to use; the
other environment variables (`LOCK_MASTER_WATCHER_DATA`, `LOCK_MASTER_ROOTS_FILE`,
`LOCK_MASTER_WATCHER_CACHE_DIR`, `TICKET_MASTER_LIB`) are inherited from the user environment.

## CLI

```bash
python watcher/cli.py status --json
python watcher/cli.py history --limit 50
python watcher/cli.py scan --update-cache
python watcher/cli.py stats
python watcher/cli.py cache
python watcher/cli.py watch --update-cache
```

The CLI configures stdout and stderr as tolerant UTF-8 streams internally, so
Windows callers do not need to set `PYTHONIOENCODING` for Unicode lock data.

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/stats` | database summary |
| `GET /api/settings` | intervals (runtime values and defaults), DB path, daemon status |
| `PUT /api/settings` | change heartbeat/scan intervals at runtime (clamped, stored in `runtime_settings.json`; `{"reset": true}` restores the defaults) |
| `GET /api/locks?status=active|all` | lock list |
| `GET /api/lock/<id>` | lock detail with events |
| `GET /api/rooms` | configured roots as rooms |
| `GET /api/room/<key>/history` | room event history |
| `POST /api/scan` | immediate full scan |
| `POST /api/prune` | run `prune_stale_locks.py` |
| `POST /api/lock` | create a lock inside configured roots |
| `POST /api/user-lock` | create a protected user lock inside configured roots |
| `POST /api/user-lock/remove` | remove a protected user lock through the user channel |
| `POST /api/bulk-lock` | preview or commit a guarded bulk lock (`commit` flag) |
| `POST /api/bulk-unlock` | preview or commit removal of bulk-created locks |
| `GET /api/room-stats` | cached directory statistics |
| `POST /api/room-stats/refresh` | refresh directory statistics |
| `POST /api/ticket` | create an unclaimed ticket (needs ticket-master, see below) |
| `GET/POST /api/project-docs` | state of / create / append to a room's `TODO.md`, `AUFGABEN.txt`, `DONE.md`, `DECISIONS.md` (needs ticket-master) |

Write endpoints are intended for local browser use and validate local origins (before the body is
read). Bodies must be a JSON object of at most 1 MiB; anything else is answered with 400 or 413, as is a
field of the wrong JSON type. Project paths must lie inside a configured root after junctions and
symlinks are resolved.

The `Ticket` button and the `Steuerdateien` panel of a room use two helpers of the optional
ticket-master package (`ticket_writer`, `doc_scanner`). They are imported when ticket-master is on
`sys.path` or when `TICKET_MASTER_LIB` names its `lib` folder; otherwise both endpoints answer 501 and
the rest of the watcher is unaffected. The ticket queue is resolved by ticket-master itself
(`TICKET_MASTER_TICKETS_DIR`, then its config). Room control files are only touched inside the
configured roots.
Two further optional hooks exist for a host: a module `cc_integrations` with `record_event(kind, text)` and
`route_task(text)` (found on `sys.path` or in `LOCK_MASTER_INTEGRATIONS_LIB`) is told about created tickets,
user locks and bulk locks, and its routing hint is added to the ticket response. A missing or failing hook
never fails a request.
The `Sperren/Rechte` button in the browser UI exposes the user-lock and guarded
bulk-lock endpoints. Bulk actions show a dry-run first; committing requires an
explicit confirmation and never touches user locks.

When `prune_stale_locks.py` removes one or more expired locks, an optional
single JSON notification can be sent by setting
`LOCK_MASTER_PRUNE_WEBHOOK_URL` or passing `--webhook-url <http(s)-URL>`.
Notifications are sent only after real removals; dry-runs and failed
notifications do not change cleanup semantics.

## Scan Model

- Full scan: at the earliest every 60 seconds; after each scan the daemon pauses at least `FULL_SCAN_IDLE_FACTOR` (default 4) times the measured scan duration, so slow roots do not make it scan continuously (`LOCK_MASTER_FULL_SCAN_IDLE_FACTOR` overrides, `0` restores the fixed interval). New locks are therefore visible to the daemon only at the next full scan; `lock_scan --check-dir` reads the filesystem directly.
- Protection against runaway scans: each root has a time budget (`scan_root_budget_seconds`, default
  300). A root over budget is cut off between two directories, the scan counts as incomplete
  (`scan_partial`), and the API reports `degraded` instead of `running`. The pause after a scan is capped
  at `full_scan_max_gap_seconds` (default 1800), so one extremely slow scan cannot delay the next by hours.
  Warnings (cut-off roots, the slowest roots of an overlong scan, a capped pause) are logged, kept in
  `daemon_status.json` as `scan_warnings`, returned by `GET /api/settings` and shown in the settings
  dialog. Both settings are runtime settings (`PUT /api/settings`). A single directory that hangs cannot be
  interrupted, only the next one is skipped.
- Quick check of known active locks: every 20 seconds.
- Daemon heartbeat: every 5 seconds, in a thread independent from full scans.
- A fresh heartbeat with an overlong or stale full scan reports `degraded`,
  never `running`.
- Directory statistics: every 15 minutes.
- Singleton behavior: a fresh daemon on the same host is reused; a second daemon exits.
