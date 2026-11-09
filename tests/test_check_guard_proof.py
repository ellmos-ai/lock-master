"""The guard-proof CI gate (.github/scripts/check_guard_proof.py, T-20261107-441069325).

A pull request that changes a guard file must name an adversarial probe and who ran it; the PR text is data only.
"""
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / ".github" / "scripts" / "check_guard_proof.py"
spec = importlib.util.spec_from_file_location("check_guard_proof", SCRIPT)
gp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gp)

FILLED = ("Adversarial-Probe: pytest tests/test_probe.py -> 10/10 denied (foreign actors, look-alikes, Unicode)\n"
          "Probe-Ausgefuehrt-von: opus-reviewer-r1@ASUS-GEI\n")


def test_changes_outside_the_guard_files_pass_without_lines():
    assert gp.check("", ["README.md", "tests/test_x.py", "lock_scan.py", ".github/workflows/tests.yml"]) == []


def test_guard_change_without_the_lines_fails_naming_both():
    errors = gp.check("Summary only", ["plugin/lock_guard.py", "README.md"])
    assert len(errors) == 2 and all("plugin/lock_guard.py" in e for e in errors)
    assert "Adversarial-Probe" in errors[0] and "Probe-Ausgefuehrt-von" in errors[1]


def test_filled_lines_pass_also_as_bullets_or_bold():
    assert gp.check(FILLED, ["hooks/hooks.json"]) == []
    assert gp.check("- **Adversarial-Probe:** link + 6/6 denied\n* Probe-Ausgefuehrt-von: r1", [".mcp.json"]) == []


@pytest.mark.parametrize("value", ["", "   ", "<command or link + result>", "n/a", "N/A", "-", "none", "tbd", "TODO"])
def test_empty_placeholder_and_non_values_fail(value):
    body = f"Adversarial-Probe: {value}\nProbe-Ausgefuehrt-von: r1\n"
    assert len(gp.check(body, ["plugin/lock_guard.py"])) == 1


def test_the_unfilled_template_fails_and_lines_inside_comments_do_not_count():
    template = (ROOT / ".github" / "pull_request_template.md").read_text(encoding="utf-8")
    assert len(gp.check(template, ["plugin/lock_guard.py"])) == 2
    assert gp.check(template, ["README.md"]) == []  # the template itself passes for ordinary changes
    hidden = "<!--\n" + FILLED + "-->\n"
    assert len(gp.check(hidden, ["plugin/lock_guard.py"])) == 2


# r1 review of 1f51963, finding 3: what GitHub does not render as a plain line must not count either.
@pytest.mark.parametrize("body", [
    "```\n" + FILLED + "```\n",                      # inside a code block
    "~~~text\n" + FILLED + "~~~\n",                  # tilde fence with an info string
    "   ```\n" + FILLED,                             # indented, never closed: the rest is code
    "<!--\n" + FILLED,                               # unclosed comment: GitHub hides the rest
    "<!-- note -->\n<!--\n" + FILLED,                # a closed comment before an unclosed one
])
def test_lines_github_does_not_show_as_text_do_not_count(body):
    assert len(gp.check(body, ["plugin/lock_guard.py"])) == 2


@pytest.mark.parametrize("value", ["​", "‌", "‍", "⁠", "﻿", " ​⁠ ", " "])
def test_invisible_values_do_not_count(value):
    body = f"Adversarial-Probe: {value}\nProbe-Ausgefuehrt-von: r1\n"
    assert len(gp.check(body, ["plugin/lock_guard.py"])) == 1


def test_text_after_a_closed_code_block_counts_again():
    assert gp.check("```\nexample\n```\n" + FILLED, ["plugin/lock_guard.py"]) == []


@pytest.mark.parametrize("path", [
    "plugin/lock_guard.py", "plugin/board_mcp.py", "hooks/hooks.json", "hooks/run.sh", ".claude-plugin/plugin.json",
    ".mcp.json", "pure-locking/lock_utils.py", "team-lock/_lock_master_team/__init__.py",
    ".github/scripts/check_guard_proof.py", ".github/workflows/guard-proof.yml", "plugin\\lock_guard.py",
    # r1 finding 1: a new module next to a guard module shadows a stdlib import of the hook (sys.path[0] = plugin/,
    # pure-locking/ and team-lock/ prepended), so everything below these directories is a guard file
    "plugin/json.py", "pure-locking/platform.py", "team-lock/argparse.py", "team-lock/msvcrt.py",
    "pure-locking/fcntl.py", "hooks/other.sh", ".claude-plugin/marketplace.json", ".github/scripts/json.py",
    # r1 finding 2: case variants and a leading ./ name the same file on Windows and macOS
    "Plugin/Lock_Guard.py", "PLUGIN/JSON.PY", "Pure-Locking/Lock_Utils.py", "./plugin/lock_guard.py",
    ".\\Hooks\\Run.sh", ".MCP.json", ".GitHub/Workflows/Guard-Proof.yml",
])
def test_every_guard_path_is_recognised(path):
    assert gp.is_guard(path)


@pytest.mark.parametrize("path", ["plugin.bak/lock_guard.py", "xplugin/lock_guard.py", "hooks.json",
                                  "lock_utils.py", "tests/plugin/lock_guard.py", "docs/hooks/run.sh",
                                  ".mcp.json.bak", ".github/workflows/tests.yml"])
def test_look_alike_paths_are_not_guard_files(path):
    assert not gp.is_guard(path)


@pytest.mark.parametrize("path", [
    "PURE-L~1/lock_utils.py",
    "pure-l~1/guard.py",
    "TEAM-L~1/_lock_master_team/__init__.py",
    "team-l~1/x.py",
    "PLUGIN~1/lock_guard.py",
    "LOCK-M~1/pure-locking/lock_utils.py",
    "some/path/with~1/segment.py",
    ".\\PURE-L~1\\guard.py",
    "foo~2/bar.py",
    "docs/notes~1.md",
])
def test_short_83_name_segments_treated_as_guard_fail_closed(path):
    assert gp.is_guard(path)


@pytest.mark.parametrize("path", [
    "my_long_filename~1.md",
    "docs/my_long_notes~1.txt",
    "some/deep/very_long_directory_name~1/file.py",
])
def test_non_83_longer_prefix_segments_are_not_flagged(path):
    assert not gp.is_guard(path)


def test_contributing_lists_every_guard_path_in_both_languages():
    text = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    english, german = text.split("## Deutsch", 1)
    for path in gp.GUARD_PATHS:
        assert f"`{path}`" in english and f"`{path}`" in german, path


def test_workflow_never_puts_pr_text_into_a_shell_and_has_minimal_rights():
    workflow = (ROOT / ".github" / "workflows" / "guard-proof.yml").read_text(encoding="utf-8")
    assert "github.event.pull_request" not in workflow and "${{" not in workflow
    assert "pull_request_target" not in workflow
    assert "contents: read" in workflow and "pull-requests: read" in workflow and "write" not in workflow
    assert "python3 .github/scripts/check_guard_proof.py" in workflow


# --- main(): the event file, real git, fail closed ---------------------------------------------------------------

def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    work = tmp_path / "repo"
    (work / "plugin").mkdir(parents=True)
    git(tmp_path, "init", "-q", str(work))
    git(work, "config", "user.email", "t@example.invalid")
    git(work, "config", "user.name", "T")
    (work / "plugin" / "lock_guard.py").write_text("x = 1\n", encoding="utf-8")
    (work / "README.md").write_text("r\n", encoding="utf-8")
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "base")
    monkeypatch.chdir(work)
    return work, git(work, "rev-parse", "HEAD")


def event(tmp_path, base, head, body):
    path = tmp_path / "event.json"
    path.write_text(json.dumps({"pull_request": {"body": body, "base": {"sha": base}, "head": {"sha": head}}}),
                    encoding="utf-8")
    return path


def test_main_on_a_real_guard_change(repo, tmp_path, capsys):
    work, base = repo
    (work / "plugin" / "lock_guard.py").write_text("x = 2\n", encoding="utf-8")
    git(work, "commit", "-q", "-am", "guard change")
    head = git(work, "rev-parse", "HEAD")
    assert gp.main(event(tmp_path, base, head, "no probe")) == 1
    assert "plugin/lock_guard.py" in capsys.readouterr().out
    assert gp.main(event(tmp_path, base, head, FILLED)) == 0


@pytest.mark.parametrize("new_file", ["plugin/json.py", "Plugin/Json.py", "plugin/änderung.py",
                                      "hooks/mit leerzeichen.sh"])
def test_main_counts_a_new_module_that_would_shadow_a_hook_import(repo, tmp_path, new_file):
    work, base = repo
    (work / new_file).parent.mkdir(parents=True, exist_ok=True)
    (work / new_file).write_text("print('HIJACKED')\n", encoding="utf-8")
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "shadow module")
    assert gp.main(event(tmp_path, base, git(work, "rev-parse", "HEAD"), "no probe")) == 1


def test_main_counts_a_guard_file_renamed_away(repo, tmp_path):
    work, base = repo
    git(work, "mv", "plugin/lock_guard.py", "plugin/elsewhere.py")
    git(work, "commit", "-q", "-m", "rename")
    assert gp.main(event(tmp_path, base, git(work, "rev-parse", "HEAD"), "")) == 1


def test_main_passes_ordinary_changes(repo, tmp_path):
    work, base = repo
    (work / "README.md").write_text("changed\n", encoding="utf-8")
    git(work, "commit", "-q", "-am", "docs")
    assert gp.main(event(tmp_path, base, git(work, "rev-parse", "HEAD"), "")) == 0


def test_pr_text_is_data_never_executed(repo, tmp_path, capsys):
    work, base = repo
    (work / "plugin" / "lock_guard.py").write_text("x = 3\n", encoding="utf-8")
    git(work, "commit", "-q", "-am", "guard change")
    head = git(work, "rev-parse", "HEAD")
    canary = tmp_path / "pwned"
    hostile = (f"Adversarial-Probe: $(touch {canary}) `touch {canary}` \"; touch {canary}; #\n"
               f"::error::injected\nProbe-Ausgefuehrt-von: '; rm -rf / #\n")
    assert gp.main(event(tmp_path, base, head, hostile)) == 0  # both lines are non-empty text
    assert not canary.exists()
    assert "::error::" not in capsys.readouterr().out  # the PR text is never echoed


@pytest.mark.parametrize("base, head", [("", "a" * 40), ("a" * 40, "HEAD"), ("A" * 40, "a" * 40),
                                        ("a" * 40 + "; rm -rf /", "a" * 40)])
def test_main_fails_closed_on_malformed_shas(repo, tmp_path, base, head):
    assert gp.main(event(tmp_path, base, head, FILLED)) == 1


def test_main_fails_closed_without_a_readable_event(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    assert gp.main() == 1
    assert gp.main(tmp_path / "missing.json") == 1
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    assert gp.main(tmp_path / "bad.json") == 1
    (tmp_path / "nopr.json").write_text("{}", encoding="utf-8")
    assert gp.main(tmp_path / "nopr.json") == 1


def test_main_fails_closed_when_git_cannot_diff(repo, tmp_path):
    assert gp.main(event(tmp_path, "b" * 40, "c" * 40, FILLED)) == 1  # unknown commits
