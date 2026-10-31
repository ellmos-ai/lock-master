---
description: Post a short note (handover, warning, tip) to the message board of this host's team lock
argument-hint: "<message>"
---

Post `$ARGUMENTS` to the message board of the team lock that covers the current project on this host. Agents of
this host see the latest messages at session start.

1. Find the team lock: run `py -3 "${CLAUDE_PLUGIN_ROOT}/pure-locking/lock_scan.py" --check-dir "<project>" --json`
   (`python3` outside Windows) and take the `LOCK.team.<HOST>.txt` or `LOCK.team.<scope>.<HOST>.txt` of this host.
   If there is none, say so and stop; create one with `/ellmos-lock-master:lock <scope> <path> <purpose> --team <HOST>`
   only when the user asks for it.
2. Post the message:
   `py -3 "${CLAUDE_PLUGIN_ROOT}/team-lock/team_lock.py" add-message "<directory of the team lock>" --lock-name <file name> --agent "<session name>" --msg "<message>"`
   (`<session name>` as given by the SessionStart context, `claude-code@<HOST>.<sid8>`; without one, `claude-code@<HOST>`).
   The `board_post` tool does the same without a shell.
3. Report what the command printed. Exit 0 means posted; on any other exit code show the error and do not edit the
   team lock by hand.

Keep messages short and factual. Never put secrets or credentials on the board: every agent on this host reads it.
