---
description: Set a lock-master lock on the area you are about to work on (scoped, team or exclusive)
argument-hint: "<scope> <relative-path> <purpose> [--team HOST]"
---

Set a lock before non-trivial work so other agents (Claude, Codex, Gemini, Kimi) see that this area is taken.

1. Check first: run `py -3 "${CLAUDE_PLUGIN_ROOT}/pure-locking/lock_scan.py" --check-dir "<project>" --json`
   (use `python3` instead of `py -3` outside Windows). Exit 0 = free, 1 = locked. If a foreign lock covers the
   area, stop and tell the user instead of locking around it.
2. Create the lock with the arguments from `$ARGUMENTS`:
   `py -3 "${CLAUDE_PLUGIN_ROOT}/pure-locking/lock_create.py" "<project>" --scope <scope> --path <relative-path> --owner "<session name>" --purpose "<purpose>"`
   - `<session name>` is the name the SessionStart context gave this session (`claude-code@<HOST>.<sid8>`). A lock
     named after the session is unmistakably yours: parallel sessions of the same host are held off, and nobody can
     mistake a newer grant of the same area for yours. Only when the context named no session, use `claude-code@<HOST>`.
   - `<project>` is the project root (the git top level when inside a repository).
   - `--team <HOST>` creates a team lock: agents of the same host share it, other hosts are blocked.
   - Keep the scope narrow: lock what you will actually edit, not the whole project.
3. Report the lock file path and the fence number the command printed.
4. With `--team`, also announce yourself and claim what you will edit through the board tools (`board_presence`,
   `board_claim`; pass your session name as `agent`), so the other sessions on this host are held off those files.

Never write a lock file by hand and never change or delete a foreign lock.
