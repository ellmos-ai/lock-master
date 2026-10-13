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
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

import lock_utils
import lock_create
import lock_scan
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

    def test_content_aware_user_lock_reported_locked(self, tmp_path, capsys):
        """T-20260926-592074763: LOCK.assets.txt with LOCK.user header must be reported as locked (exit 1)."""
        (tmp_path / "LOCK.assets.txt").write_text(
            "LOCK.user.claude-code@ASUS-GEI\n"
            "Grund: Banner-Entwurf (zwei Motiv-Varianten, agy-generiert) fuer README/README_de, Review vor Commit\n"
            "Gesetzt: 2026-09-20\n",
            encoding="utf-8",
        )
        assert _check_dir(tmp_path, as_json=False, strict=False) == 1
        out = capsys.readouterr().out
        assert "1 active lock(s) affect" in out
        assert "LOCK.assets.txt" in out


class TestExplicitProtectedPaths:
    def test_declared_root_after_prefix_alias_preserves_lock_checks(self, tmp_path, capsys):
        real = tmp_path / "physical"
        repo = real / "repos" / "demo"
        (repo / "tests").mkdir(parents=True)
        alias = tmp_path / "prefix"
        if sys.platform == "win32":
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(real)],
                                    capture_output=True, text=True, timeout=10)
            assert result.returncode == 0, result.stderr
        else:
            alias.symlink_to(real, target_is_directory=True)
        roots = tmp_path / "prefix-roots.json"
        unrelated = tmp_path / "unrelated"
        unrelated.mkdir()
        roots.write_text(json.dumps({"roots": [{"path": str(alias / "repos")},
                                              {"path": str(unrelated)}]},
                                    ensure_ascii=False), encoding="utf-8")
        try:
            checked = alias / "repos" / "demo" / "tests"
            unknown_roots = tmp_path / "unknown-roots.json"
            unknown_roots.write_text('{"roots": []}', encoding="utf-8")
            assert _check_dir(checked, True, False, unknown_roots) == 1
            assert json.loads(capsys.readouterr().out)["error"] == "target-redirect"
            assert _check_dir(checked, True, False, roots) == 0
            assert json.loads(capsys.readouterr().out)["descendants_complete"] is True
            assert lock_create.main([str(repo), "--scope", "suite", "--path",
                                     "tests", "--no-contest"]) == 0
            capsys.readouterr()
            assert _check_dir(checked, True, False, roots) == 1
            assert json.loads(capsys.readouterr().out)["locks"]
            assert _check_dir(alias / "repos" / "demo" / "missing", True, False, roots) == 2
            assert json.loads(capsys.readouterr().out)["error"] == "target-not-found"
        finally:
            assert alias.parent.resolve().is_relative_to(tmp_path.resolve())
            assert alias.resolve().is_relative_to(tmp_path.resolve())
            if sys.platform == "win32":
                os.rmdir(alias)
            else:
                alias.unlink()

    def test_internal_alias_cannot_hide_registered_mirror_lock(self, git_repo, tmp_path, capsys):
        (git_repo / "other" / "pkg").mkdir(parents=True)
        (git_repo / "tests").mkdir()
        mirror = tmp_path / "mirror"
        (mirror / "alias" / "pkg").mkdir(parents=True)
        (mirror / "tests").mkdir()
        assert lock_create.main([str(mirror / "alias"), "--scope", "mirror",
                                 "--path", "pkg", "--no-contest"]) == 0
        capsys.readouterr()
        index = tmp_path / "alias-twins.json"
        index.write_text(json.dumps({"by_repo_name": {},
            "by_clone_path": {lock_utils._path_key(git_repo): [str(mirror)]},
            "unreadable_pointers_by_dirname": {}}), encoding="utf-8")
        roots = tmp_path / "alias-roots.json"
        roots.write_text(json.dumps({"roots": [{"path": str(tmp_path)}],
            "twin_resolution": {"clone_roots": [str(tmp_path)], "index_path": str(index)}}),
            encoding="utf-8")

        def make_link(link, target):
            if sys.platform == "win32":
                result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                                        capture_output=True, text=True, timeout=10)
                assert result.returncode == 0, result.stderr
            else:
                link.symlink_to(target, target_is_directory=True)

        created = []
        try:
            alias = git_repo / "alias"
            make_link(alias, git_repo / "other")
            created.append(alias)
            assert _check_dir(alias / "pkg", True, False, roots) == 1
            payload = json.loads(capsys.readouterr().out)
            assert payload["error"] == "target-redirect"
            assert payload["checked"] == str(alias / "pkg")
            # A narrower scan root must not override the enclosing Git root.
            # Resolving this alias first would project the query to mirror/other
            # and lose the only lock, which lives under mirror/alias.
            unrelated = tmp_path / "unrelated"
            unrelated.mkdir()
            nested_roots = tmp_path / "nested-alias-roots.json"
            nested_roots.write_text(json.dumps({
                "roots": [{"path": str(alias / "pkg")}, {"path": str(unrelated)}],
                "twin_resolution": {"clone_roots": [str(alias / "pkg")],
                                    "index_path": str(index)},
            }), encoding="utf-8")
            assert _check_dir(alias / "pkg", True, False, nested_roots) == 1
            payload = json.loads(capsys.readouterr().out)
            assert payload["error"] == "target-redirect"
            assert payload["checked"] == str(alias / "pkg")
            assert payload["descendants_complete"] is False
            assert _check_dir(git_repo / "tests", True, False, roots) == 0
            capsys.readouterr()
            (git_repo / "c" / "pkg").mkdir(parents=True)
            make_link(git_repo / "b", git_repo / "c")
            created.append(git_repo / "b")
            make_link(git_repo / "a", git_repo / "b")
            created.append(git_repo / "a")
            assert _check_dir(git_repo / "a" / "pkg", True, False, roots) == 1
            assert json.loads(capsys.readouterr().out)["error"] == "target-redirect"
        finally:
            for link in reversed(created):
                assert link.parent.resolve().is_relative_to(tmp_path.resolve())
                assert link.resolve().is_relative_to(tmp_path.resolve())
                if sys.platform == "win32":
                    os.rmdir(link)
                else:
                    link.unlink()

    @pytest.mark.parametrize("project_authority", ["pointer", "clone_root"])
    def test_nested_scan_root_cannot_override_other_project_boundaries(
            self, tmp_path, capsys, project_authority):
        repo = tmp_path / "repo"
        (repo / "other" / "pkg").mkdir(parents=True)
        (repo / "tests").mkdir()
        if project_authority == "pointer":
            (repo / lock_utils.REPO_POINTER_NAME).write_text(json.dumps({
                "schema": "ellmos-repo-pointer-v1", "repo_name": "demo",
                "canonical_path": str(repo),
            }), encoding="utf-8")
        alias = repo / "alias"
        if sys.platform == "win32":
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(alias),
                                     str(repo / "other")], capture_output=True,
                                    text=True, timeout=10)
            assert result.returncode == 0, result.stderr
        else:
            alias.symlink_to(repo / "other", target_is_directory=True)
        roots = tmp_path / "nested-boundary-roots.json"
        cfg = {"roots": [{"path": str(alias / "pkg")}]}
        if project_authority == "clone_root":
            index = tmp_path / "empty-twins.json"
            index.write_text(json.dumps({"by_repo_name": {}, "by_clone_path": {},
                                         "unreadable_pointers_by_dirname": {}}),
                             encoding="utf-8")
            cfg["twin_resolution"] = {"clone_roots": [str(repo)],
                                      "index_path": str(index)}
        roots.write_text(json.dumps(cfg), encoding="utf-8")
        try:
            assert _check_dir(alias / "pkg", True, False, roots) == 1
            payload = json.loads(capsys.readouterr().out)
            assert payload["error"] == "target-redirect"
            assert payload["checked"] == str(alias / "pkg")
            assert _check_dir(repo / "tests", True, False, roots) == 0
            assert json.loads(capsys.readouterr().out)["descendants_complete"] is True
        finally:
            assert alias.parent.resolve().is_relative_to(tmp_path.resolve())
            assert alias.resolve().is_relative_to(tmp_path.resolve())
            if sys.platform == "win32":
                os.rmdir(alias)
            else:
                alias.unlink()

    def test_missing_query_stays_usage_error_and_parent_segment_is_uncertain(self, tmp_path, capsys):
        missing = tmp_path / "missing"
        assert _check_dir(missing, True, False) == 2
        assert json.loads(capsys.readouterr().out)["error"] == "target-not-found"
        (tmp_path / "existing").mkdir()
        parent_segment = tmp_path / "existing" / ".." / "existing"
        assert _check_dir(parent_segment, True, False) == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["error"] == "target-parent-segment"
        assert payload["descendants_complete"] is False

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows junction contract")
    def test_direct_query_through_junction_keeps_lexical_ancestors(self, tmp_path, capsys):
        repo = tmp_path / "repo"
        outside = tmp_path / "outside-free"
        repo.mkdir()
        (repo / "tests").mkdir()
        (outside / "package").mkdir(parents=True)
        assert lock_create.main([str(repo), "--scope", "starter", "--path",
                                 "bin", "--no-contest"]) == 0
        capsys.readouterr()
        link = repo / "bin"
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                                capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        try:
            for query in (link, link / "package"):
                assert _check_dir(query, True, False) == 1
                payload = json.loads(capsys.readouterr().out)
                assert payload["checked"] == str(query)
                assert payload["error"] == "target-redirect"
                assert payload["descendants_complete"] is False
            # A protected path whose endpoint escapes the lock root is itself
            # malformed for scope narrowing and remains a broad legacy guard.
            assert lock_utils.protected_relative_paths(repo / "LOCK.starter.txt") is None
        finally:
            assert link.parent.resolve().is_relative_to(tmp_path.resolve())
            assert link.resolve().is_relative_to(tmp_path.resolve())
            assert lock_scan._is_directory_redirect(link)
            os.rmdir(link)

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows junction contract")
    def test_intermediate_junction_query_and_free_real_sibling(self, tmp_path, capsys):
        repo = tmp_path / "repo"
        (repo / "scope").mkdir(parents=True)
        (repo / "tests").mkdir()
        outside = tmp_path / "outside-free"
        (outside / "child").mkdir(parents=True)
        assert lock_create.main([str(repo), "--scope", "starter", "--path",
                                 "scope", "--no-contest"]) == 0
        capsys.readouterr()
        link = repo / "scope" / "alias"
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                                capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        try:
            assert _check_dir(link / "child", True, False) == 1
            payload = json.loads(capsys.readouterr().out)
            assert payload["checked"] == str(link / "child")
            assert payload["error"] == "target-redirect"
            assert payload["descendants_complete"] is False
            assert _check_dir(repo / "tests", True, False) == 0
            assert json.loads(capsys.readouterr().out)["descendants_complete"] is True
        finally:
            assert link.parent.resolve().is_relative_to(tmp_path.resolve())
            assert link.resolve().is_relative_to(tmp_path.resolve())
            assert lock_scan._is_directory_redirect(link)
            os.rmdir(link)

    def test_unreadable_nested_lock_lookup_is_incomplete(self, tmp_path, monkeypatch, capsys):
        child = tmp_path / "unreadable"
        child.mkdir()
        original = lock_utils.active_locks_affecting_target

        def denied(source, target, now=None):
            if source == child:
                raise PermissionError("synthetic unreadable subtree")
            return original(source, target, now)

        monkeypatch.setattr(lock_utils, "active_locks_affecting_target", denied)
        assert _check_dir(tmp_path, True, False) == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["descendants_complete"] is False

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows junction contract")
    def test_junction_cycle_and_external_target_are_incomplete(self, tmp_path, capsys):
        repo = tmp_path / "repo"
        links = repo / "links"
        outside = tmp_path / "outside"
        links.mkdir(parents=True)
        outside.mkdir()
        (outside / "private").mkdir()
        lock_create.main([str(outside), "--scope", "external", "--path",
                          "private", "--no-contest"])
        capsys.readouterr()
        created_links = []
        try:
            for link, target in ((repo / "cycle", repo), (links / "escape", outside)):
                result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                                        capture_output=True, text=True, timeout=10)
                assert result.returncode == 0, result.stderr
                created_links.append(link)
            assert _check_dir(repo, True, False) == 1
            root = json.loads(capsys.readouterr().out)
            assert root["descendants_complete"] is False
            assert _check_dir(links, True, False) == 1
            subtree = json.loads(capsys.readouterr().out)
            assert subtree["descendants_complete"] is False
            assert _check_dir(links / "escape" / "private", True, False) == 1
            assert lock_scan._is_directory_redirect(links) is False
        finally:
            # rmdir removes the junction itself; never recurse into its target.
            for link in reversed(created_links):
                assert link.parent.resolve().is_relative_to(tmp_path.resolve())
                assert link.resolve().is_relative_to(tmp_path.resolve())
                assert lock_scan._is_directory_redirect(link)
                os.rmdir(link)

    def test_nested_lock_blocks_root_but_not_sibling(self, tmp_path, capsys):
        (tmp_path / "tests").mkdir()
        (tmp_path / "installer").mkdir()
        assert lock_create.main([str(tmp_path / "tests"), "--scope", "suite",
                                 "--path", "unit", "--no-contest"]) == 0
        capsys.readouterr()
        assert _check_dir(tmp_path, True, False) == 1
        root = json.loads(capsys.readouterr().out)
        assert any(row["path"].endswith("LOCK.suite.txt") for row in root["locks"])
        assert _check_dir(tmp_path / "installer", True, False) == 0

    def test_subtree_limit_never_reports_free(self, tmp_path, monkeypatch, capsys):
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        monkeypatch.setattr(lock_scan, "CHECK_DIR_MAX_DESCENDANTS", 1)
        assert _check_dir(tmp_path, True, False) == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["descendants_complete"] is False
        assert payload["locks"] == []

    def test_creator_and_scanner_only_block_actual_area(self, tmp_path, capsys):
        for name in ("bin", "binary", "prompts", "installer", "tests"):
            (tmp_path / name).mkdir()
        assert lock_create.main([str(tmp_path), "--scope", "starter", "--path", "bin",
                                 "--path", "prompts", "--no-contest"]) == 0
        assert _check_dir(tmp_path / "bin", True, False) == 1
        assert _check_dir(tmp_path / "prompts", True, False) == 1
        for name in ("binary", "installer", "tests"):
            assert _check_dir(tmp_path / name, True, False) == 0
        assert _check_dir(tmp_path, True, False) == 1  # root overlaps subareas
        capsys.readouterr()

    @pytest.mark.parametrize("extra", [
        "", "paths_version: 2\nprotected_paths: bin\n",
        "paths_version: 1\nprotected_paths: \n",
        "paths_version: 1\nprotected_paths:  bin\n",
        "paths_version: 1\nprotected_paths: bin |prompts\n",
        "paths_version: 1\nprotected_paths: ../outside\n",
        "paths_version: 1\nprotected_paths: /outside\n",
        "paths_version: 1\nprotected_paths: bin/../tests\n",
    ])
    def test_legacy_or_malformed_metadata_remains_broad(self, tmp_path, extra):
        (tmp_path / "bin").mkdir()
        (tmp_path / "tests").mkdir()
        stamp = datetime.now().strftime("%Y-%m-%dT%H:%M")
        (tmp_path / "LOCK.starter.txt").write_text(
            f"owner: agent\ncreated: {stamp}\nexpires_after: 24h\n{extra}", encoding="utf-8")
        assert _check_dir(tmp_path / "tests", True, False) == 1

    def test_explicit_metadata_cannot_narrow_user_lock(self, tmp_path):
        (tmp_path / "bin").mkdir()
        (tmp_path / "tests").mkdir()
        (tmp_path / "LOCK.user.starter.txt").write_text(
            "owner: user\npaths_version: 1\nprotected_paths: bin\n", encoding="utf-8")
        assert _check_dir(tmp_path / "tests", True, False) == 1

    def test_ancestor_lock_is_not_reported_as_worktree_main(self, tmp_path, capsys):
        (tmp_path / "bin").mkdir()
        write_lock(tmp_path)
        assert _check_dir(tmp_path / "bin", True, False) == 1
        rows = json.loads(capsys.readouterr().out)["locks"]
        assert any(row["path"].endswith("LOCK.txt") for row in rows)
        assert all(row["from_main_repo_of_worktree"] is False for row in rows)

    def test_windows_case_and_segment_boundary(self, tmp_path):
        (tmp_path / "Bin").mkdir()
        (tmp_path / "binary").mkdir()
        (tmp_path / "LOCK.starter.txt").write_text(
            "owner: agent\ncreated: " + datetime.now().strftime("%Y-%m-%dT%H:%M") +
            "\nexpires_after: 24h\npaths_version: 1\nprotected_paths: bin\n", encoding="utf-8")
        assert _check_dir(tmp_path / "Bin", True, False) == (1 if (tmp_path / "bin").exists() else 0)
        assert _check_dir(tmp_path / "binary", True, False) == 0

    def test_worktree_main_lock_maps_relative_subpath(self, git_repo, tmp_path):
        (git_repo / "bin").mkdir()
        (git_repo / "tests").mkdir()
        lock_create.main([str(git_repo), "--scope", "starter", "--path", "bin", "--no-contest"])
        wt = tmp_path / "wt-scoped"
        _git("worktree", "add", "-q", "--detach", str(wt), cwd=git_repo)
        try:
            (wt / "bin").mkdir()
            (wt / "tests").mkdir()
            assert _check_dir(wt / "bin", True, False) == 1
            assert _check_dir(wt / "tests", True, False) == 0
        finally:
            _git("worktree", "remove", "--force", str(wt), cwd=git_repo)

    def test_worktree_sees_nested_main_clone_lock(self, git_repo, tmp_path):
        (git_repo / "src" / "package").mkdir(parents=True)
        lock_create.main([str(git_repo / "src"), "--scope", "nested",
                          "--path", "package", "--no-contest"])
        wt = tmp_path / "wt-main-nested"
        _git("worktree", "add", "-q", "--detach", str(wt), cwd=git_repo)
        try:
            (wt / "src" / "package").mkdir(parents=True)
            (wt / "tests").mkdir()
            assert _check_dir(wt / "src" / "package", True, False) == 1
            assert _check_dir(wt / "tests", True, False) == 0
        finally:
            _git("worktree", "remove", "--force", str(wt), cwd=git_repo)

    def test_twin_root_lock_maps_relative_subpath(self, git_repo, tmp_path):
        mirror = tmp_path / "mirror"
        mirror.mkdir()
        (mirror / "bin").mkdir()
        (mirror / "tests").mkdir()
        (git_repo / "bin").mkdir()
        (git_repo / "tests").mkdir()
        (mirror / "LOCK.starter.txt").write_text(
            "owner: agent\ncreated: " + datetime.now().strftime("%Y-%m-%dT%H:%M") +
            "\nexpires_after: 24h\npaths_version: 1\nprotected_paths: bin\n", encoding="utf-8")
        config = tmp_path / "roots.json"
        index = tmp_path / "twins.json"
        index.write_text(json.dumps({"by_repo_name": {},
            "by_clone_path": {lock_utils._path_key(git_repo): [str(mirror)]},
            "unreadable_pointers_by_dirname": {}}), encoding="utf-8")
        config.write_text(json.dumps({"twin_resolution": {
            "clone_roots": [str(tmp_path)], "index_path": str(index)}}), encoding="utf-8")
        assert _check_dir(git_repo / "bin", True, False, config) == 1
        assert _check_dir(git_repo / "tests", True, False, config) == 0
        (mirror / "tests").rmdir()
        assert _check_dir(git_repo / "tests", True, False, config) == 0
        wt = tmp_path / "wt-twin"
        _git("worktree", "add", "-q", "--detach", str(wt), cwd=git_repo)
        try:
            (wt / "bin").mkdir()
            (wt / "tests").mkdir()
            assert _check_dir(wt / "bin", True, False, config) == 1
            assert _check_dir(wt / "tests", True, False, config) == 0
        finally:
            _git("worktree", "remove", "--force", str(wt), cwd=git_repo)

    def test_all_registered_twins_and_missing_child(self, git_repo, tmp_path):
        (git_repo / "bin").mkdir()
        (git_repo / "installer").mkdir()
        mirrors = [tmp_path / name for name in ("mirror-a", "mirror-b", "mirror-c")]
        for mirror in mirrors:
            mirror.mkdir()
        (mirrors[0] / "bin").mkdir()
        (mirrors[1] / "bin").mkdir()
        # mirror-c deliberately has no bin directory; its root still counts.
        index = tmp_path / "twins-many.json"
        index.write_text(json.dumps({"by_repo_name": {},
            "by_clone_path": {lock_utils._path_key(git_repo): [str(m) for m in mirrors]},
            "unreadable_pointers_by_dirname": {}}), encoding="utf-8")
        config = tmp_path / "roots-many.json"
        config.write_text(json.dumps({"twin_resolution": {
            "clone_roots": [str(tmp_path)], "index_path": str(index)}}), encoding="utf-8")
        stamp = datetime.now().strftime("%Y-%m-%dT%H:%M")
        for i in (0, 2):
            lock = mirrors[i] / "LOCK.starter.txt"
            lock.write_text(f"owner: agent\ncreated: {stamp}\nexpires_after: 24h\n"
                            "paths_version: 1\nprotected_paths: bin\n", encoding="utf-8")
            assert _check_dir(git_repo / "bin", True, False, config) == 1
            assert _check_dir(git_repo / "installer", True, False, config) == 0
            lock.unlink()
