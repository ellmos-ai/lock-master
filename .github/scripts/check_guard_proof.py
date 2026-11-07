"""CI gate: a pull request that changes a guard file must name an adversarial probe and who ran it.

Reads the `pull_request` event from $GITHUB_EVENT_PATH (JSON; the PR text is only ever data, never put into a
shell) and lists the changed files with `git diff --name-only --no-renames <base>...<head>` (argument list, both
SHAs validated first). Fails when a guard file changed and the description lacks a non-empty `Adversarial-Probe:`
or `Probe-Ausgefuehrt-von:` line. Anything it cannot read fails closed. Standard library only.
See CONTRIBUTING.md, "Guard files" (T-20261107-441069325).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Exact files, or directories ending in "/". Keep CONTRIBUTING.md ("Guard files") in step: a test checks it.
GUARD_PATHS = (
    "plugin/lock_guard.py",
    "plugin/board_mcp.py",
    "hooks/hooks.json",
    "hooks/run.sh",
    ".claude-plugin/plugin.json",
    ".mcp.json",
    "pure-locking/lock_utils.py",
    "team-lock/_lock_master_team/",
    ".github/scripts/check_guard_proof.py",
    ".github/workflows/guard-proof.yml",
)
REQUIRED = ("Adversarial-Probe", "Probe-Ausgefuehrt-von")
NON_VALUES = {"-", "--", "n/a", "na", "none", "tbd", "todo", "keine", "offen", "?"}
SHA = re.compile(r"[0-9a-f]{40}")
COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
PLACEHOLDER = re.compile(r"<[^<>]*>")


def is_guard(path: str) -> bool:
    path = path.replace("\\", "/")
    return any(path == g or (g.endswith("/") and path.startswith(g)) for g in GUARD_PATHS)


def field_values(body: str) -> dict[str, str]:
    """The value of each required line. HTML comments (the template's hints), `<...>` placeholders and stand-ins
    such as `n/a` do not count."""
    values: dict[str, str] = {}
    for line in COMMENT.sub("", body or "").splitlines():
        key, sep, value = line.strip().lstrip("-*+ ").partition(":")
        key = key.strip(" *")
        if not sep or key not in REQUIRED or values.get(key):
            continue
        value = PLACEHOLDER.sub("", value).strip(" *\t")
        if value and value.lower() not in NON_VALUES:
            values[key] = value
    return values


def check(body: str, changed: list[str]) -> list[str]:
    guards = sorted({p.replace("\\", "/") for p in changed if is_guard(p)})
    if not guards:
        return []
    values = field_values(body)
    listed = ", ".join(json.dumps(g) for g in guards)  # quoted: a file name cannot start a workflow command
    return [f"guard files changed ({listed}) but the PR description has no non-empty `{key}:` line"
            for key in REQUIRED if not values.get(key)]


def changed_files(base: str, head: str) -> list[str]:
    if not (isinstance(base, str) and isinstance(head, str) and SHA.fullmatch(base) and SHA.fullmatch(head)):
        raise ValueError("pull_request base/head sha missing or not a 40-character lowercase hex sha")
    result = subprocess.run(["git", "diff", "--name-only", "--no-renames", f"{base}...{head}"],
                            capture_output=True, text=True, check=True)
    return [line for line in result.stdout.splitlines() if line]


def main(event_path: str | Path | None = None) -> int:
    try:
        path = event_path or os.environ["GITHUB_EVENT_PATH"]
        pr = json.loads(Path(path).read_text(encoding="utf-8"))["pull_request"]
        errors = check(pr.get("body") or "", changed_files(pr["base"]["sha"], pr["head"]["sha"]))
    except (KeyError, TypeError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"guard-proof: cannot evaluate the pull request ({type(exc).__name__}); failing closed")
        return 1
    for error in errors:
        print(f"guard-proof: {error}. See CONTRIBUTING.md, section Guard files.")
    if not errors:
        print("guard-proof: ok (no guard file changed, or the probe lines are filled in)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
