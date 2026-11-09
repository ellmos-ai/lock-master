"""CI gate: a pull request that changes a guard file must name an adversarial probe and who ran it.

Reads the `pull_request` event from $GITHUB_EVENT_PATH (JSON; the PR text is only ever data, never put into a
shell) and lists the changed files with `git diff --name-only --no-renames -z <base>...<head>` (argument list, both
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
import unicodedata
from pathlib import Path

# Exact files, or directories ending in "/" (everything below them). Directories, not single modules: the hook runs
# with plugin/ as sys.path[0] and prepends pure-locking/ and team-lock/, the gate runs with .github/scripts/ as
# sys.path[0], so any new module there with a stdlib name (json.py, platform.py, argparse.py, ...) takes over an
# import (r1 review of 1f51963). Keep CONTRIBUTING.md ("Guard files") in step: a test checks it.
GUARD_PATHS = (
    "plugin/",
    "hooks/",
    ".claude-plugin/",
    "pure-locking/",
    "team-lock/",
    ".mcp.json",
    ".github/scripts/",
    ".github/workflows/guard-proof.yml",
)
REQUIRED = ("Adversarial-Probe", "Probe-Ausgefuehrt-von")
NON_VALUES = {"-", "--", "n/a", "na", "none", "tbd", "todo", "keine", "offen", "?"}
SHA = re.compile(r"[0-9a-f]{40}")
COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
PLACEHOLDER = re.compile(r"<[^<>]*>")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


SHORT_83_SEGMENT = re.compile(r"^[^~]{1,6}~[0-9]+(\.[^.]{0,3})?$", re.IGNORECASE)


def normalize(path: str) -> str:
    """Windows separators, leading ./ and case do not make another file: Windows and default macOS checkouts are
    case-insensitive, so Plugin/Lock_Guard.py is plugin/lock_guard.py there."""
    path = path.replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path.casefold()


def is_guard(path: str) -> bool:
    norm = normalize(path)
    if any(norm == g or (g.endswith("/") and norm.startswith(g)) for g in GUARD_PATHS):
        return True
    # Windows 8.3 short-name segments (e.g. pure-l~1, team-l~1, plugin~1): fail-closed.
    # Segments matching the Windows 8.3 short-name format are treated as protected guard paths
    # because on 8.3 filesystems they could alias a guard directory or file.
    segments = norm.split("/")
    if any(SHORT_83_SEGMENT.match(seg) for seg in segments):
        return True
    return False


def visible_text(body: str) -> str:
    """The description as GitHub shows it as text: closed comments removed, everything after an unclosed `<!--`
    (hidden by GitHub) dropped, fenced code blocks dropped (an unclosed fence runs to the end)."""
    text = COMMENT.sub("", body or "").split("<!--", 1)[0]
    kept, fence = [], None
    for line in text.splitlines():
        match = FENCE.match(line)
        if fence is None and match:
            fence = match.group(1)
        elif fence is not None:
            if match and match.group(1)[0] == fence[0] and len(match.group(1)) >= len(fence) \
                    and not line.strip()[len(match.group(1)):].strip():
                fence = None
        else:
            kept.append(line)
    return "\n".join(kept)


def without_invisibles(value: str) -> str:
    """Drop format characters (zero-width space/joiners, word joiner, BOM: Unicode category Cf)."""
    return "".join(ch for ch in value if unicodedata.category(ch) != "Cf")


def field_values(body: str) -> dict[str, str]:
    """The value of each required line. HTML comments (the template's hints), code blocks, `<...>` placeholders,
    invisible characters and stand-ins such as `n/a` do not count."""
    values: dict[str, str] = {}
    for line in visible_text(body).splitlines():
        key, sep, value = without_invisibles(line).strip().lstrip("-*+ ").partition(":")
        key = key.strip(" *")
        if not sep or key not in REQUIRED or values.get(key):
            continue
        value = PLACEHOLDER.sub("", value).strip().strip("*").strip()
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
    # -z: NUL-separated and unquoted; without it git quotes non-ASCII names ("plugin/\303\244.py"), which would
    # then not start with a guard directory.
    result = subprocess.run(["git", "diff", "--name-only", "--no-renames", "-z", f"{base}...{head}"],
                            capture_output=True, check=True)
    return [name for name in result.stdout.decode("utf-8", "surrogateescape").split("\0") if name]


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
