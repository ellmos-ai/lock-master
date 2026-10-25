---
name: lock-protocol
description: How to coordinate with other agents through lock-master file locks. Use before non-trivial edits in a shared repository or synced folder, when a hook reports a foreign lock, or when the user mentions locks, LOCK files, team locks or parallel agents.
compatibility: Claude Code plugin hooks; the LOCK*.txt convention itself is readable by any agent (Codex, Gemini, Kimi) and by humans.
---

# Lock protocol

lock-master coordinates agents that work in the same tree without a server: a lock is a plain `LOCK*.txt` file in
the project, readable by every agent and every human.

## Rules

1. **Lock before you start, not when someone shows up.** Two agents that both see "no lock" will collide. Use
   `/lock` before non-trivial work.
2. **Keep the scope narrow.** Lock the files or folder you will change (`--scope` plus `--path`), not the project.
3. **Foreign locks are binding.** When the hook denies a change, do not edit around it, rename the file or use
   another tool. Tell the user who holds the lock and why (owner, host, purpose).
4. **Lock types:**
   - exclusive `LOCK.txt` / scoped `LOCK.<scope>.txt` — blocks everyone else;
   - team `LOCK.team.<host>.txt` — agents of the same host share it, other hosts are blocked;
   - user `LOCK.user*.txt` — removed only by the user, never by an agent;
   - until `LOCK.until*.txt` — lifts at a fixed time;
   - condition `LOCK.condition*.txt` — lifts when its condition is met; with an `operations:` field it blocks only
     those operations (for example `publish`).
5. **Fencing:** every lock carries a fence number. If your lock expired and someone else took the area, your fence
   is stale — stop writing.
6. **Release your own lock** with `/unlock` as soon as the protected work is done; expiry is only a safety net.

## Ownership in this plugin

A lock counts as this session's own when its `owner` starts with `claude-code@<HOST>` (or one of the prefixes in
`ELLMOS_LOCK_OWNER`). Parallel Claude Code sessions on the same host therefore share ownership; use different
owner prefixes via `ELLMOS_LOCK_OWNER` if they must block each other.

## Limits

The hook checks the file tools (Edit, Write, MultiEdit, NotebookEdit) and `git push`. Arbitrary shell commands that
write files are not recognised; when a foreign lock covers the area, do not use Bash to write there either.
