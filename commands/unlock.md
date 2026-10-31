---
description: Release your own lock-master lock when the work in that area is finished
argument-hint: "[scope]"
---

Release a lock that this session created, as soon as the work it protected is done.

1. List the locks in the project: `py -3 "${CLAUDE_PLUGIN_ROOT}/pure-locking/lock_scan.py" --check-dir "<project>" --json`
   (`python3` outside Windows).
2. Pick the lock whose `owner` is your session name (from the SessionStart context, `claude-code@<HOST>.<sid8>`; for
   older locks exactly `claude-code@<HOST>`) and whose scope matches `$ARGUMENTS`. Another session's lock is not yours,
   even when its owner differs only in the suffix.
3. Delete only that lock file. Do not touch locks of other owners, user locks (`LOCK.user*`), until locks or
   condition locks — those are released by their holder or by the user.
4. Run the check again and report that the area is free.
