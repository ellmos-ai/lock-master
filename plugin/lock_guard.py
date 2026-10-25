"""Claude Code plugin hooks for lock-master.

Modes (first argument):
  pretooluse  -- deny Edit/Write/MultiEdit/NotebookEdit on paths covered by a
                 foreign active lock, and `git push` from a locked project.
  session     -- report the active locks that affect the session's working
                 directory as additional context.

All lock semantics come from pure-locking/lock_utils.py (no second
implementation). Local files only: nothing is sent anywhere.

Ownership: a lock is the session's own when its `owner` field starts with one
of the prefixes in ELLMOS_LOCK_OWNER (comma-separated), default
"claude-code@<HOST>". Team locks of the same host are shared by that host's
agents. User and until locks block always; condition locks block only the
operations they name.

Errors fail closed (deny with the reason). ELLMOS_LOCK_FAIL_OPEN=1 turns an
internal error into an allow with a warning.
"""
from __future__ import annotations

import json
import os
import re
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pure-locking"))

import lock_utils as lu  # noqa: E402

EDIT_TOOLS = {"Edit": "file_path", "Write": "file_path", "MultiEdit": "file_path", "NotebookEdit": "notebook_path"}
GIT_PUSH = re.compile(r"\bgit\b[^\n;&|]*\bpush\b")
EDIT_OPS = {"edit", "write", "modify", "all", "*"}
PUSH_OPS = {"push", "git-push", "publish", "all", "*"}


def my_host() -> str:
    return (os.environ.get("COMPUTERNAME") or socket.gethostname() or "").upper()


def owner_prefixes() -> list[str]:
    raw = os.environ.get("ELLMOS_LOCK_OWNER", "")
    prefixes = [p.strip().lower() for p in raw.split(",") if p.strip()]
    return prefixes or [f"claude-code@{my_host().lower()}"]


def targets(payload: dict) -> list[tuple[str, Path]]:
    """(operation, path) pairs a tool call would touch; empty = not lock-relevant."""
    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    cwd = Path(payload.get("cwd") or os.getcwd())
    if tool in EDIT_TOOLS:
        raw = tool_input.get(EDIT_TOOLS[tool])
        if not raw:
            return []
        path = Path(raw)
        return [("edit", path if path.is_absolute() else cwd / path)]
    if tool == "Bash" and GIT_PUSH.search(tool_input.get("command", "")):
        return [("push", cwd)]
    return []


def decide(lock_path: Path, op: str, prefixes: list[str], host: str) -> str:
    """'block' or the reason it does not block ('own', 'team', 'other-operation')."""
    data = lu.parse_lock_file(lock_path)
    if lu.is_user_lock(lock_path, data) or lu.is_until_lock(lock_path, data):
        return "block"
    ops = {o.lower() for o in lu.locked_operations(lock_path)}
    if ops and not ops & (EDIT_OPS if op == "edit" else PUSH_OPS):
        return "other-operation"
    if lu.is_condition_lock(lock_path, data):
        return "block"
    owner = (data.get("owner") or "").lower()
    if any(owner.startswith(p) for p in prefixes):
        return "own"
    if lu.is_team_lock(lock_path.name) and (lu.lock_host(lock_path) or "").upper() == host:
        return "team"
    return "block"


def blocking_locks(op: str, path: Path) -> list[Path]:
    prefixes, host = owner_prefixes(), my_host()
    found = []
    for name, _scope, _legacy, source_dir in lu.active_locks_for_path(path):
        lock_path = Path(source_dir) / name
        if lock_path not in found and decide(lock_path, op, prefixes, host) == "block":
            found.append(lock_path)
    return found


def describe(lock_path: Path) -> str:
    data = lu.parse_lock_file(lock_path)
    return (f"{lock_path} (owner: {data.get('owner', '?')}, host: {data.get('host', '?')}, "
            f"purpose: {data.get('purpose', '?')})")


def pretooluse(payload: dict) -> dict | None:
    for op, path in targets(payload):
        hits = blocking_locks(op, path)
        if hits:
            what = "push from" if op == "push" else "change"
            reason = (f"lock-master: a foreign active lock covers this {what} {path}. "
                      "Do not work around it; coordinate with the holder or the user. "
                      + " | ".join(describe(h) for h in hits))
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                           "permissionDecision": "deny",
                                           "permissionDecisionReason": reason}}
    return None


def session(payload: dict) -> dict | None:
    cwd = Path(payload.get("cwd") or os.getcwd())
    locks = lu.active_locks_for_path(cwd)
    if not locks:
        return None
    lines = [f"- {describe(Path(src) / name)}" for name, _s, _l, src in locks]
    text = ("lock-master: active locks affect this working directory. Respect foreign locks; "
            "set your own with /lock before non-trivial work.\n" + "\n".join(lines))
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}


def main(argv: list[str]) -> int:
    mode = argv[1] if len(argv) > 1 else ""
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        result = pretooluse(payload) if mode == "pretooluse" else session(payload) if mode == "session" else None
    except Exception as exc:  # fail closed, with an explicit escape hatch
        if mode == "pretooluse" and os.environ.get("ELLMOS_LOCK_FAIL_OPEN") != "1":
            result = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": f"lock-master: lock check failed ({exc}); "
                                             "set ELLMOS_LOCK_FAIL_OPEN=1 to allow on errors."}}
        else:
            print(f"lock-master: lock check failed ({exc})", file=sys.stderr)
            return 0
    if result:
        print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
