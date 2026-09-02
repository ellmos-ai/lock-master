# LOCK-SYSTEM -- Project Locks for Multi-Agent Coordination

**Scope:** All project roots listed in your `lock_roots.json`.
**Canonical spec:** This file. Script-level docs are in the individual `.py` files.
**Updated:** 2026-06-25

---

## Purpose

Central coordination principle for parallel work by multiple agents, automated
loops, or humans: a `LOCK*.txt` file in a project directory signals that the
project or a component is in use -- no agent or automated loop modifies that
area while a valid, non-expired lock exists.

---

## Quick Overview: Who Holds What?

Fastest ways to see active locks (in order of speed):

1. **Search tool (fastest, live):** search for `LOCK*.txt` files in the
   relevant root using your file-search tooling.
2. **Cache file (no scan needed):** read the auto-generated `LOCK-CACHE.md`
   (written by `lock_scan.py --write-cache`).
3. **Script:** `python lock_scan.py` (read-only list) or
   `python lock_scan.py --write-cache` (refresh cache).

The `LOCK*.txt` files themselves are always authoritative; the cache is a
derived quick-index only.

---

## Optional Watcher / Web UI

`pure-locking/watcher/` provides an optional local daemon, REST API, and browser
UI. It uses the same scripts and config from `pure-locking/`:

- `lock_roots.json`
- `lock_scan.py`
- `lock_utils.py`
- `prune_stale_locks.py`

The watcher does not change the protocol. `LOCK*.txt` files remain the
authoritative source of truth; SQLite, generated caches, REST responses, and
the UI are derived views.

Runtime data is stored outside the repository by default:

```text
~/.lock_master_watcher/watcher.db
~/.lock_master_watcher/daemon_status.json
```

Override with `LOCK_MASTER_WATCHER_DATA=/path/to/runtime`.

Start from the repository root:

```bash
python pure-locking/watcher/lock_watcher.py --update-cache
python pure-locking/watcher/web_server.py --port 8095
```

Windows shortcut:

```bat
pure-locking\watcher\START.bat
```

Open `http://127.0.0.1:8095`. The web server is intended for local use only.

Watcher scan model:

- full scan every 60 seconds
- quick check of known active locks every 20 seconds
- daemon heartbeat every 5 seconds
- directory statistics every 15 minutes
- same-host singleton detection through PID and heartbeat

---

## Scope via Filename (FILENAME IS AUTHORITATIVE)

- `LOCK.txt` -- entire project locked (scope = `project`).
- `LOCK.<scope>.txt` -- only that component locked; free scope name
  (sub-area / sub-folder), e.g. `LOCK.frontend.txt`, `LOCK.api.txt`,
  `LOCK.mobile.txt`.
- `LOCK.team.<host>.txt` -- Team Lock for the whole project (see below).
- `LOCK.team.<scope>.<host>.txt` -- Team Lock for a specific component (see below).
- `LOCK.user.txt` / `LOCK.user.<scope>.txt` -- User Lock (see below): user-owned
  full lock, removed ONLY by the user.
- Multiple agents can work in parallel on different components of the same
  project using different scoped locks.
- Detection regex: `^LOCK(\.[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*)?\.txt$`
- Legacy `TEST.txt` / `TESTS.txt` -- deprecated, do not create new ones
  (still recognised as a lock, but not subject to automatic expiry).

---

## File Format (one setting per line, `key: value`)

Template: `pure-locking/LOCK_TEMPLATE.txt`. Lines starting with `#` = comment; blank lines
are ignored.

| Field              | Required | Meaning |
|--------------------|----------|---------|
| `owner`            | yes      | Who holds the lock (agent / user / automation). |
| `created`          | yes      | ISO timestamp `YYYY-MM-DDTHH:MM` (base for expiry). |
| `host`             | optional | Machine/hostname holding the lock — which system locked it (cross-system). |
| `expires_after`    | optional | e.g. `24h` / `48h` / `90m`. Default = `24h`. |
| `release_condition`| optional | Free text: what must happen for the lock to be released. |
| `mode`             | optional | `hard` (no changes, default) \| `soft` (reads/hints ok). |
| `purpose`          | optional | Free text: why locked / what is running. |
| `scope`            | optional | Informational only; the filename is authoritative. |

If `created` is missing or unparseable, the file's mtime is used as fallback
for expiry calculation.

---

---

## Lock Types: Exclusive vs. Team

### Exclusive Lock (default)

`LOCK.txt` or `LOCK.<scope>.txt` -- locks the area for all systems and all
agents. No other system or agent may modify the locked area while the lock
is active. Use this when only one system should work in an area at a time.

### Team Lock

`LOCK.team.<host>.txt` or `LOCK.team.<scope>.<host>.txt` -- coordinates
multiple agents **within one system** (e.g. several parallel agents on the
same machine). It signals "my system is active here; other systems should
stay out."

**Why per-system, not cross-system?** Cloud-sync latency (30 s -- 5 min
with OneDrive, Dropbox, or other shared filesystems) makes real-time
coordination across system boundaries unreliable. Each system manages its
own agents internally via the Team Lock; cross-system exclusion is achieved
by the presence of the Team Lock file itself (other systems see it and stay
out).

**When a second system wants to enter the same scope:**
- If the scopes do not overlap: it may create its own
  `LOCK.team.<scope>.<its-host>.txt` for its slice.
- If the scopes overlap: treat the existing Team Lock like an Exclusive Lock
  -- wait or choose a different task.

**Conflict copies (cloud-sync rename collision):** When two systems write
a Team Lock simultaneously, one rename wins and one becomes a conflict copy.
The system whose file survived continues; the other must back off and retry.
On NTFS and most cloud-sync filesystems, a rename within the same directory
is atomic and can be used as a lightweight claim mechanism.

### Required content of a Team Lock file

A Team Lock must contain all four sections (use `pure-locking/TEAM_LOCK_TEMPLATE.txt`):

1. **Presence log** -- loop ID, agent name, role, main task, start time.
   Every agent checks in here before working; removes its entry when done.
2. **File/folder claims + queue** -- who is editing what; who is waiting.
3. **Tool/software/MCP claims + queue** -- exclusive resources (e.g. a
   running server, a DB connection, a specific MCP tool). Only claim what
   is truly exclusive; keep claims tight.
4. **Messages, tips, lessons learned** -- short handovers, warnings, notes
   for other agents on the same team.

### Team Lock coordination rules

- **Check in before working:** add your presence entry before touching files.
- **Rotate roles when requested** by the team coordinator (first-in agent
  or designated lead).
- **Choose a complementary slice** if a resource is already claimed; do not
  double-claim.
- **Update claims on task change:** if you switch to a different area,
  update your claim immediately.
- **Respect queue order:** agents listed as waiting in the queue have
  priority when the resource becomes free.
- **Clean up on exit:** remove your presence entry and your claims. Delete
  the Team Lock file only when the presence log is fully empty.

### Atomic Team-Lock CLI and library

`python lock_create.py <project> --team <host>` creates the canonical four
sections. `team_lock.py` (installed command: `lock-master-team`) updates
presence, file/folder bundles, exclusive tool bundles and messages. One file or
tool command is one atomic bundle. A file-local `order` number keeps distinct
calls separate and establishes FIFO priority for overlapping waiters; it is not
a durable claim ID or a recovery journal. Legacy one-resource claim lines are
read, while ambiguous multiple legacy lines from the same agent fail closed.
The filename remains authoritative for host identity: `lock_create --team`
writes that value into `host:`, rejects a differing explicit `--host`, and later
updates fail closed if the header differs from the final filename segment
(`LOCK.team.<scope>.<host>.txt`).

Each update validates the lock path, agent and resources in the public Python
API as well as the CLI. Local processes serialize the full read/validate/write
transaction through a persistent `LOCK.team.*.txt.guard` file using
`msvcrt.locking` on Windows or `flock` on POSIX. The guard contains no state and
is intentionally not unlinked: the OS releases the advisory lock on process
exit, while retaining the inode avoids split-lock races among waiters. The Team
Lock is replaced only after a unique temporary file has been flushed and
synced. This protects local concurrent writers; it does not provide a
cross-host transaction or a recovery log.

### User Lock (user-owned full lock -- only the user removes it)

User Locks are a separate, protected category. They lock a project durably, and
**only the user** (manually or via the watcher GUI) may remove them -- agents and
the stale-cleanup (`prune_stale_locks.py`) never touch them, even when nominally
expired.

- `LOCK.user.txt` -- entire project, user-owned lock.
- `LOCK.user.<scope>.txt` -- component, user-owned lock.
- The `user` marker segment is reserved (like `team`). Detection:
  `lock_utils.is_user_lock()`; protection: `lock_utils.is_protected_lock()` /
  `is_prunable()`.
- Easiest via the watcher GUI ("Locks/Permissions" button) or the template with
  `removable_by: user`.
- Since v1.4.0, protected locks (user + condition) also never expire in
  `lock_utils.is_expired()` -- previously a nominally expired user lock could
  incorrectly drop out of `active_locks()`/`lock_scan.py` output.

### Condition Lock (condition-based, operation-scoped -- released when a condition is met)

Condition Locks (since v1.4.0) hold **until a condition is fulfilled** instead of
until a time expires, and typically lock **only specific operations** instead of
the whole project. Use case: "no release/upload of artifact X before the review
follow-ups are done" -- while normal development/research on the project stays
unrestricted.

- `LOCK.condition.txt` -- condition-based lock at project level.
- `LOCK.condition.<scope>.txt` -- condition-based lock for a component,
  e.g. `LOCK.condition.publish-release.txt`.
- The `condition` marker segment is reserved (like `user`/`team`). Detection:
  `lock_utils.is_condition_lock()`; protection: `is_protected_lock()`.
- **No time expiry:** `expires_after` has no effect; `prune_stale_locks.py` and
  bulk-unlock never touch condition locks. `lock_scan.py` shows
  "until condition met: ..." instead of a remaining time.
- **Required field `release_condition:`** -- state precisely and verifiably WHAT
  must be done and WHERE it is documented.
- **Field `operations:`** (comma-separated) names the LOCKED operations
  (e.g. `operations: publish-release, registry-upload`). Everything not listed
  remains explicitly allowed. Without an `operations` field the lock applies to
  the whole scope according to its `mode`. Helper:
  `lock_utils.locked_operations(path)`.
- **Release:** unlike user locks, **any agent** may remove the lock once it has
  verifiably fulfilled the `release_condition` -- document the fulfilment in the
  project register when removing. If unsure whether the condition is met:
  do NOT remove; ask the user.

### Until Lock (deadline-based -- released at an absolute moment)

Until Locks (since v1.6.0) hold **until a fixed point in time** that the file
states, instead of for a relative duration (`expires_after`, default 24h) or
until a free-text condition is met. Use case: a competition judging hold, where
the release moment is a calendar date weeks away that nobody wants to express
as `expires_after: 900h`.

- `LOCK.until.txt` -- deadline lock at project level.
- `LOCK.until.<scope>.txt` -- deadline lock for a component, e.g.
  `LOCK.until.winners-announcement.txt`.
- The `until` marker segment is reserved (like `user`/`team`/`condition`).
  Detection: `lock_utils.is_until_lock()`; the moment itself:
  `lock_utils.lock_not_before()`.
- **Required field `not_before:`** -- an absolute ISO timestamp, with or without
  a UTC offset: `2026-10-08T12:00-07:00` or `2026-10-08 12:00`. An
  offset-aware value is converted to local time; a value without an offset is
  read as local time. Always write the offset when the deadline is announced in
  a foreign timezone -- that is exactly where the mistakes happen.
- **Fail-closed:** a missing or unparsable `not_before` means the lock **never
  expires**. A typo can only lock too long, never release too early.
- **`expires_after` has no effect.** The absolute moment replaces it.

**This type is the first to separate two things every other type conflates:**

| | expires by time | protected from deletion |
|---|---|---|
| exclusive | yes, after `expires_after` | no |
| user | never | yes |
| condition | never | yes |
| **until** | **yes, at `not_before`** | **yes** |

A guard therefore stops watching that project **by itself** once the moment has
passed -- `is_expired()` turns true and `active_locks()` drops it. The **file
still survives**: `prune_stale_locks.py` and bulk-unlock never touch it, because
the human decision and the evidence obligation outlive the deadline.

- **Optional field `release_condition:`** -- what must additionally be proven
  before the lock is removed (e.g. "winners actually announced on <source>").
  The deadline ends the *watching*; it does not by itself prove the event
  happened. If the event is postponed, the lock stays fail-closed and the
  `not_before` value is corrected.
- **Field `operations:`** works as for condition locks: it names the LOCKED
  operations; everything else stays explicitly allowed.
- **Release:** removing the file follows what the lock itself says. A deadline
  lock guarding a user decision states so in `release_condition` and is removed
  by the user; `lock_scan.py` shows "deadline passed <moment> - guard may stop;
  file stays for the user".
- `lock_scan.py` shows the remaining time plus the moment, e.g.
  `35d 4h (until 2026-10-08T21:00)`.

---

---

## Permission System: LOCK.permissions + Immediate Lockdown

Agent-neutral, folder-scoped permission layer alongside the `LOCK*.txt` files --
readable by **all** agents (Claude, Codex, Gemini, Kimi).

### `LOCK.permissions.json` -- per-folder permissions

Syntax borrowed from `.claude/settings.json`, but agent-wide and folder-scoped:

```json
{ "format": "lock-permissions-v1", "default": "allow",
  "rules": { "allow": ["Read(**)"], "deny": ["Bash(rm:*)", "Write(**/CREDENTIALS/**)"], "ask": ["Write(**)"] },
  "applies_to_agents": ["claude","codex","gemini","kimi","*"] }
```

- Patterns: `Tool(glob)` (`Bash(...)`, `Read(...)`, `Write(...)`), `mcp__vendor__tool`, `*`.
- Precedence: `deny > ask > allow > default`. Evaluation: `permission-control/permissions.py::evaluate(perm, agent, action)`.
- Enforcement = voluntary convention + GUI/audit (like `LOCK*.txt`). Template:
  `permission-control/LOCK_PERMISSIONS_TEMPLATE.json`.

### Immediate lockdown (central kill switch)

`bulk_lock.py` sets/removes exclusive `LOCK.txt` across all connected top-level
roots (`lock_roots.json`) in one step:

- `bulk_lock(roots, commit=False)` -- dry-run by default; idempotent (existing locks
  stay); created locks carry `created_by: bulk` (exact rollback via session manifest).
- `bulk_unlock(...)` -- removes **only** `created_by: bulk` locks; **never** user locks.
- CLI: `python bulk_lock.py lock|unlock --commit`.

---

## Two Tiers of Enforcement

**Tier 1 -- RESPECT (always required, everywhere):**
Before modifying any project or component, check whether a non-expired
`LOCK*.txt` exists for the affected area (the project-wide `LOCK.txt` blocks
everything). If one exists and has not expired: do not touch it -- pick another
project or wait. This applies system-wide to every agent in every pipeline.

**Tier 2 -- CREATE (optional unless mandated):**
Actively creating a lock at the start of work is not universally required.
- If a project marks itself with `LOCK-required: yes` in its documentation,
  creating a lock is mandatory there.
- Otherwise: recommended whenever parallel work is possible.

**Fallback / precedence:** This spec is the system-wide default.
Project-specific rules take local precedence (more specific beats more general):
a project may declare stricter requirements (e.g. mandatory creation) or use
a custom scope name.

---

## Lifecycle: RESPECT -> CLAIM -> RELEASE

1. **RESPECT:** check for an active lock on the area before working.
2. **CLAIM:** create your own `LOCK.txt` or `LOCK.<scope>.txt` from the
   template (`owner`, ISO `created`, `expires_after`, `purpose`).
3. **RELEASE:** delete the lock file you created when done. Active release
   by the creator is required; the 24h expiry is only a safety net for
   forgotten locks. If work takes longer, renew `created` so the lock does
   not expire prematurely.

---

## Contested Locks: Simultaneous Claims Over a Synced Folder

"Two Tiers of Enforcement" already names the problem: two agents starting at the
same time both see an empty folder. Solved so far is only the case where one was
demonstrably first. With cloud sync at 30 s – 5 min latency **both** see an empty
folder, both create, and both consider themselves the holder. This is not an edge
case: scheduled automations start on several hosts at the same clock time.

**Stage 1 — exclusive creation (always on).** `lock_create.py` creates the lock
file exclusively (`open("x")`) instead of checking and then writing. Between the
check and the write there used to be a window in which a second process creates
the same file. `--force` still overwrites deliberately.

**Stage 2 — contest procedure (attempted when it pays off).**

```
1. CREATE      write the lock exclusively
2. QUARANTINE  wait (default 300 s) for the sync to align both views
3. RECHECK     read the directory again
4. DECIDE      earliest `created` wins; exact tie -> lexicographically smaller host
5. LOSER       remove own lock (exit code 3), release the area
```

Both sides compute the same result from the same files — no server, no database,
no real-time channel. The wait *is* the mechanism, not decoration: without it the
recheck reads the same unsynchronised view as before.

**When it runs.** Not always. Minutes of lead time are expensive for a short edit
and cheap for an automation run that then works for hours. `should_contest()`
therefore asks two questions: is the area in a cloud folder at all, and is this an
automation? Only then.

> **This deliberately inverts a common reflex.** When cloud pressure is detected
> (e.g. via the placeholder attributes that FileCommander also inspects), tools
> often skip the work entirely to avoid conflicts. That is safe but expensive —
> work that could have happened does not. A protocol is the better answer than
> abstention: attempt it rather than give up.

**Cloud detection, three stages** (`contested.cloud_pressure`): an optional
external prober (e.g. a FileCommander call) is *asked*, never required; otherwise
the Windows placeholder attributes (`FILE_ATTRIBUTE_OFFLINE`, `RECALL_ON_OPEN`,
`RECALL_ON_DATA_ACCESS`, reparse point); otherwise known cloud folder names in the
path.

**What the decision rule applies to.** It needs two *visible* claims — that means
**team locks**: they carry the host in the name, so they coexist in the directory,
and they act like an exclusive lock towards foreign systems. For equal or
overlapping scopes it was previously undefined who wins. Exclusive locks carry no
host; there Stage 1 applies, and on true simultaneity the cloud service produces a
conflict copy that stays visible instead of silently overwriting. **User and
condition locks are never overruled** — protected categories do not take part.

**Scope overlap** (`contested.scopes_overlap`): a project lock overlaps
everything; otherwise containment with a segment boundary — `assets` overlaps
`assets.images` but **not** `assets-backup`. A mere character prefix is not an
ancestor relation.

**Time resolution:** `created` is now written with seconds
(`%Y-%m-%dT%H:%M:%S`). At minute granularity two near-simultaneous claims
regularly land in the host tiebreak, where the same host loses structurally every
time. `lock_utils._parse_created` has always read seconds ("seconds optional"), so
this is not a format break.

**An expired own lock never wins.** Other systems filtered it out long ago;
without that check it would see itself as the earliest claim while nobody else
still sees it — two winners against identical data, with no sync delay involved.

**Honest limit:** if a second system starts *after* the first one's quarantine has
elapsed but before its lock has synced, the rule does not apply. The residual risk
is in the range of seconds instead of, as without the procedure, a total failure of
coordination. Hard exclusion would require a shared transactional instance.

**Invocation:**

```
python lock_create.py <project> --team <HOST>              # procedure on demand
python lock_create.py <project> --team <HOST> --contested  # force it
python lock_create.py <project> --team <HOST> --no-contest # suppress it
python lock_create.py <project> --team <HOST> --quarantine 120
```

Exit code **3** = claim lost, own lock removed.

Origin: the procedure comes from `ellmos-ai/system-auditor` (audit host lock,
2026-08-15). There exclusion was *unwanted* — parallel audits are the product —
which is why it moved here, where exclusion is the purpose.

---

## Scripts

Place all scripts in the same directory as `lock_roots.json`.
On Windows, always set `PYTHONIOENCODING=utf-8` (cp1252 default encoding).

**List active locks system-wide (read-only):**
```
python lock_scan.py
python lock_scan.py --json
```

**Remove expired locks:**
```
# Preview first (deletes nothing):
python prune_stale_locks.py --dry-run
# Actually remove:
python prune_stale_locks.py
```

**Refresh LOCK-CACHE.md:**
```
python lock_scan.py --write-cache
```
Writes cache file(s) as defined in `lock_roots.json` ("caches" key).

**Custom roots file:**
```
python lock_scan.py --roots-file /path/to/my_roots.json
python prune_stale_locks.py --roots-file /path/to/my_roots.json
```

**Scan performance:** `lock_roots.json` controls `default_max_depth` (default 4),
`shallow_depth` (default 2, for roots with `"shallow": true` for large trees),
and `skip_dirs` (directories skipped including their subtrees, e.g.
`node_modules`, `.venv`, `.git`, `build`, `releases`).

---

## Library

`lock_utils.py` is the canonical format/scope/expiry library (incl. `is_user_lock`,
`is_protected_lock`, `is_prunable`). Import it from your own scripts rather than
re-implementing the logic. Companion modules: `permissions.py` (LOCK.permissions
evaluation) and `bulk_lock.py` (immediate lockdown / reversal).


---

## PRIVATE.txt / PUBLIC.txt — Veröffentlichungs-Locks [U 2026-07-18, Rückspiegelung aus _scripts/LOCK-SYSTEM.md]

Zwei besondere Lock-Arten für die VERÖFFENTLICHUNG (nicht Bearbeitung) von
Repositories/Projekten; kanonische Vollfassung: `OneDrive/_scripts/LOCK-SYSTEM.md`.

**`PRIVATE.txt`** im lokalen Projekt-/Repo-Root = Projekt darf NICHT public
werden (GitHub-Visibility, Forks/Mirrors, Registries, Zenodo/Preprints,
öffentliche Doku). Aufhebung: LEERE Datei → nur der User entfernt sie, kein
Verfall, kein prune. MIT INHALT können Blocker/Aufhebebedingungen definiert
sein — sind alle nachweislich erfüllt, darf auch ein LLM/Agent die Datei
löschen (mit dokumentiertem Nachweis); ohne explizite Bedingungen gilt sie
wie leer. Pflichtprüfung vor `gh repo create --public`, `--visibility
public`, `npm publish`, Registry-Submits; GithubBot//repo-publish-check
behandeln solche Projekte als „nie public stellen/vorschlagen".

**`PUBLIC.txt`** = Veröffentlichung FREIGEGEBEN (auch wenn noch nicht
public); kann Aufträge enthalten, die MIT der Veröffentlichung zu erledigen
sind (Registries, Banner, llms.txt, Release-Tag …). Anlage nur durch den
User oder auf dokumentierte User-Freigabe.

**Gemeinsame Regeln:** Beide Dateien sind lokale Steuerdateien und werden
NICHT committet (in Projekt-`.gitignore` eintragen; Bots prüfen den lokalen
Projektspiegel). Konflikt beider Dateien → fail-closed PRIVATE.txt gilt,
User informieren. Bewusst außerhalb des `LOCK*.txt`-Scanmusters von
`lock_scan.py` (Publikations-, keine Bearbeitungssperre). Anlass: ein
internes Repository wurde ohne User-Freigabe public angelegt und am
2026-07-18 auf privat zurückgestellt.
