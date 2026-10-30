---
description: Set a lock-master lock on the area you are about to work on (scoped, team or exclusive)
argument-hint: "<scope> <relative-path> <purpose> [--team HOST]"
---

Set a lock before non-trivial work so other agents (Claude, Codex, Gemini, Kimi) see that this area is taken.

1. Check first: run `py -3 "${CLAUDE_PLUGIN_ROOT}/pure-locking/lock_scan.py" --check-dir "<project>" --json`
   (use `python3` instead of `py -3` outside Windows). Exit 0 = free, 1 = locked. If a foreign lock covers the
   area, stop and tell the user instead of locking around it.
2. Create the lock with the arguments from `$ARGUMENTS`:
   `py -3 "${CLAUDE_PLUGIN_ROOT}/pure-locking/lock_create.py" "<project>" --scope <scope> --path <relative-path> --owner "claude-code@<HOST>" --purpose "<purpose>"`
   - `<project>` is the project root (the git top level when inside a repository).
   - `--team <HOST>` creates a team lock: agents of the same host share it, other hosts are blocked.
   - Keep the scope narrow: lock what you will actually edit, not the whole project.
3. Report the lock file path and the fence number the command printed.

Never write a lock file by hand and never change or delete a foreign lock.
