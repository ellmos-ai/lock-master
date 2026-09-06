"""
tests/test_check_dir.py -- lock_scan --check-dir + git hook guards
(T-20260906-910508487)

Covers:
  - lock_utils.git_hook_guards(): no git dir, no hooks, a known guard hook,
    the Build-Week-Judging embargo signature, a broken core.hooksPath.
  - lock_utils.active_locks_for_path() + git_common_dir/worktree_main_dir:
    plain directory vs. linked git worktree.
  - lock_scan._check_dir(): combined lock + guard reporting, exit codes
    (0/1 unchanged by hooks; 2 only with strict=True), JSON shape.

Run:
  python -m pytest tests/test_check_dir.py -v
"""

import json
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

import lock_utils
from lock_scan import _check_dir


def _git(*args, cwd: Path):
    result = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"
    return result.stdout


@pytest.fixture
def git_repo(tmp_path):
    """A minimal, real git repository (not a worktree)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    _git("config", "user.email", "test@example.com", cwd=repo)
    _git("config", "user.name", "test", cwd=repo)
    (repo / "README.md").write_text("x", encoding="utf-8")
    _git("add", "README.md", cwd=repo)
    _git("commit", "-q", "-m", "init", cwd=repo)
    return repo


def write_lock(path: Path, owner: str = "test-agent") -> None:
    ts = datetime.now().strftime("%Y-%m-%dT%H:%M")
    (path / "LOCK.txt").write_text(
        f"owner: {owner}\ncreated: {ts}\nexpires_after: 24h\nscope: project\n",
        encoding="utf-8",
    )


def write_hook(repo: Path, name: str, body: str) -> Path:
    hooks_dir = repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook_path = hooks_dir / name
    hook_path.write_text(body, encoding="utf-8")
    return hook_path


EMBARGO_HOOK_BODY = (
    "#!/bin/sh\n"
    'echo "Push blocked: operator competition embargo for ellmos-ai/bach '
    '(Build Week Judging, bis ~2026-08-12)." >&2\n'
    "exit 1\n"
)

PLAIN_HOOK_BODY = (
    "#!/bin/sh\n"
    "# run the linter before every commit\n"
    "exec ruff check .\n"
)


# ---------------------------------------------------------------------------
# lock_utils.git_hook_guards
# ---------------------------------------------------------------------------

class TestGitHookGuards:
    def test_non_git_dir_returns_empty(self, tmp_path):
        assert lock_utils.git_hook_guards(tmp_path) == []

    def test_git_dir_no_hooks_returns_empty(self, git_repo):
        assert lock_utils.git_hook_guards(git_repo) == []

    def test_finds_pre_push_hook(self, git_repo):
        write_hook(git_repo, "pre-push", PLAIN_HOOK_BODY)
        guards = lock_utils.git_hook_guards(git_repo)
        assert len(guards) == 1
        assert guards[0]["hook"] == "pre-push"
        assert guards[0]["embargo"] is False
        assert "linter" in guards[0]["hint"]

    def test_ignores_sample_hooks(self, git_repo):
        # git init always ships pre-push.sample etc. -- must not be picked up.
        sample = git_repo / ".git" / "hooks" / "pre-push.sample"
        assert sample.is_file(), "expected git init to ship pre-push.sample"
        assert lock_utils.git_hook_guards(git_repo) == []

    def test_detects_embargo_signature(self, git_repo):
        write_hook(git_repo, "pre-push", EMBARGO_HOOK_BODY)
        guards = lock_utils.git_hook_guards(git_repo)
        assert len(guards) == 1
        assert guards[0]["embargo"] is True

    def test_multiple_known_hooks_all_reported(self, git_repo):
        write_hook(git_repo, "pre-push", PLAIN_HOOK_BODY)
        write_hook(git_repo, "pre-commit", PLAIN_HOOK_BODY)
        guards = lock_utils.git_hook_guards(git_repo)
        assert {g["hook"] for g in guards} == {"pre-push", "pre-commit"}

    def test_unknown_hook_name_not_reported(self, git_repo):
        write_hook(git_repo, "post-commit", PLAIN_HOOK_BODY)
        assert lock_utils.git_hook_guards(git_repo) == []

    def test_broken_hooks_path_reports_structural_note(self, git_repo):
        # Simulates the empirically-found case: core.hooksPath configured,
        # target directory does not exist on this host.
        missing = git_repo / "does-not-exist-hooks"
        _git("config", "core.hooksPath", str(missing), cwd=git_repo)
        guards = lock_utils.git_hook_guards(git_repo)
        assert len(guards) == 1
        assert guards[0]["hook"] is None
        assert "does not exist" in guards[0]["hint"]


# ---------------------------------------------------------------------------
# lock_utils.active_locks_for_path / worktree awareness
# ---------------------------------------------------------------------------

class TestActiveLocksForPath:
    def test_plain_dir_own_lock(self, tmp_path):
        write_lock(tmp_path)
        hits = lock_utils.active_locks_for_path(tmp_path)
        assert len(hits) == 1
        name, scope, legacy, source_dir = hits[0]
        assert name == "LOCK.txt"
        assert source_dir == tmp_path

    def test_worktree_sees_main_clone_lock(self, git_repo, tmp_path):
        write_lock(git_repo)
        worktree = tmp_path / "wt"
        _git("worktree", "add", "-q", "--detach", str(worktree), cwd=git_repo)
        try:
            hits = lock_utils.active_locks_for_path(worktree)
            assert len(hits) == 1
            name, scope, legacy, source_dir = hits[0]
            assert name == "LOCK.txt"
            assert source_dir == git_repo
        finally:
            _git("worktree", "remove", "--force", str(worktree), cwd=git_repo)

    def test_worktree_guard_hook_visible_from_worktree_dir(self, git_repo, tmp_path):
        # Hooks live in the shared .git (git-common-dir), so a hook placed
        # in the main clone must also be visible when checking the
        # worktree directory (default: hooks are not per-worktree).
        write_hook(git_repo, "pre-push", EMBARGO_HOOK_BODY)
        worktree = tmp_path / "wt2"
        _git("worktree", "add", "-q", "--detach", str(worktree), cwd=git_repo)
        try:
            guards = lock_utils.git_hook_guards(worktree)
            assert len(guards) == 1
            assert guards[0]["embargo"] is True
        finally:
            _git("worktree", "remove", "--force", str(worktree), cwd=git_repo)


# ---------------------------------------------------------------------------
# lock_scan._check_dir -- combined behaviour + exit codes
# ---------------------------------------------------------------------------

class TestCheckDir:
    def test_free_dir_no_guards_exit_0(self, tmp_path, capsys):
        code = _check_dir(tmp_path, as_json=False, strict=False)
        assert code == 0
        assert "is free" in capsys.readouterr().out

    def test_locked_dir_exit_1_regardless_of_strict(self, tmp_path, capsys):
        write_lock(tmp_path)
        assert _check_dir(tmp_path, as_json=False, strict=False) == 1
        assert _check_dir(tmp_path, as_json=False, strict=True) == 1

    def test_free_with_guard_exit_0_by_default(self, git_repo, capsys):
        write_hook(git_repo, "pre-push", PLAIN_HOOK_BODY)
        code = _check_dir(git_repo, as_json=False, strict=False)
        assert code == 0  # unchanged Exit-0/1 semantics without --strict
        out = capsys.readouterr().out
        assert "GUARD: pre-push" in out
        assert "does NOT mean" in out

    def test_free_with_guard_exit_2_with_strict(self, git_repo):
        write_hook(git_repo, "pre-push", PLAIN_HOOK_BODY)
        assert _check_dir(git_repo, as_json=False, strict=True) == 2

    def test_locked_dir_with_guard_exit_1_even_with_strict(self, git_repo):
        # A LOCK file always wins -- 1 takes priority over the guard-only 2.
        write_lock(git_repo)
        write_hook(git_repo, "pre-push", PLAIN_HOOK_BODY)
        assert _check_dir(git_repo, as_json=False, strict=True) == 1

    def test_json_shape_includes_hook_guards(self, git_repo, capsys):
        write_hook(git_repo, "pre-push", EMBARGO_HOOK_BODY)
        code = _check_dir(git_repo, as_json=True, strict=False)
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["locks"] == []
        assert len(payload["hook_guards"]) == 1
        assert payload["hook_guards"][0]["embargo"] is True

    def test_embargo_signature_flagged_in_text_output(self, git_repo, capsys):
        write_hook(git_repo, "pre-push", EMBARGO_HOOK_BODY)
        _check_dir(git_repo, as_json=False, strict=False)
        assert "[EMBARGO SIGNATURE]" in capsys.readouterr().out
