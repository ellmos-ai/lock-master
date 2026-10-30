---
description: Show which lock-master locks affect the current project or a given path
argument-hint: "[path]"
---

Show the active locks for `$ARGUMENTS`, or for the current working directory if no path is given:

`py -3 "${CLAUDE_PLUGIN_ROOT}/pure-locking/lock_scan.py" --check-dir "<path>" --json` (`python3` outside Windows)

Report each lock with its file, type (exclusive, scoped, team, user, until, condition), owner, host, purpose and
expiry, and say whether it blocks this session. Exit code 0 means free, 1 means locked; treat any other result as
unknown and do not start changes.
