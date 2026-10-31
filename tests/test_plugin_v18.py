"""lock-master 1.8: session names, fencing in the hook, team-board file claims, Codex / Kimi tool shapes."""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "plugin"))

import lock_guard  # noqa: E402
import _lock_master_team as team  # noqa: E402
import lock_utils as lu  # noqa: E402


def write_lock(directory: Path, name: str, **fields):
    base = {"created": lu.created_utc_iso(), "expires_after": "24h", "mode": "hard"}
    base.update(fields)
    (directory / name).write_text("".join(f"{k}: {v}\n" for k, v in base.items()), encoding="utf-8")


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPUTERNAME", "TESTHOST")
    monkeypatch.delenv("ELLMOS_LOCK_OWNER", raising=False)
    monkeypatch.delenv("ELLMOS_LOCK_ACTOR", raising=False)
    monkeypatch.delenv("ELLMOS_LOCK_LEASES", raising=False)
    monkeypatch.setenv("ELLMOS_LOCK_STATE_DIR", str(tmp_path / "_state"))
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    return tmp_path


def edit(path: Path, sid: str = "", tool: str = "Edit") -> dict:
    payload = {"tool_name": tool, "cwd": str(path.parent), "tool_input": {"file_path": str(path)}}
    return {**payload, "session_id": sid} if sid else payload


def denied(payload: dict) -> bool:
    out = lock_guard.pretooluse(payload)
    return bool(out) and out.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


def fenced(project, name="LOCK.txt", owner="claude-code@testhost", fence=100, **fields):
    write_lock(project, name, owner=owner, host="TESTHOST", fence=fence, **fields)


# --- session names ---------------------------------------------------------------------------------------------

def test_session_name_is_a_valid_team_agent_name():
    name = lock_guard.session_name({"session_id": "AB12cd34-eeee-ffff"})
    host = re.sub(r"[^A-Za-z0-9_-]", "-", lock_guard.my_host())  # macOS host names end in .local
    assert name == f"claude-code@{host}.ab12cd34"
    assert team.normalize_agent_name(name) == name  # no ':' and inside the team-lock agent grammar
    assert lock_guard.session_name({}) is None


def test_session_start_names_the_session_even_without_locks(project):
    out = lock_guard.session({"cwd": str(project), "session_id": "AB12cd34-eeee"})
    assert "claude-code@TESTHOST.ab12cd34" in out["hookSpecificOutput"]["additionalContext"]
    assert lock_guard.session({"cwd": str(project)}) is None  # no id and no locks: nothing to say


def test_lock_named_after_the_session_is_its_own_and_nobody_elses(project):
    fenced(project, owner="claude-code@TESTHOST.ab12cd34")
    target = project / "src" / "a.py"
    assert not denied(edit(target, "ab12cd34-1"))
    assert denied(edit(target, "ffff0000-2"))  # a parallel session of the same host is held off


# --- fencing ---------------------------------------------------------------------------------------------------

def test_fence_moved_under_a_shared_owner_denies(project):
    target = project / "src" / "a.py"
    fenced(project, fence=100)
    assert not denied(edit(target, "s-a"))   # first use: trusted, fence 100 remembered
    assert not denied(edit(target, "s-a"))   # negative control: unchanged, still allowed
    fenced(project, fence=200)               # granted anew to a session with the same owner string
    out = lock_guard.pretooluse(edit(target, "s-a"))
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "fencing" in reason and "200" in reason and "/lock" in reason


def test_fence_going_backwards_or_vanishing_denies(project):
    target = project / "src" / "a.py"
    fenced(project, fence=100)
    assert not denied(edit(target, "s-a"))
    fenced(project, fence=50)                # restored copy / clock jump
    assert denied(edit(target, "s-a"))
    fenced(project, fence=100)               # back to the remembered grant: held again
    assert not denied(edit(target, "s-a"))
    write_lock(project, "LOCK.txt", owner="claude-code@testhost", host="TESTHOST")  # re-created by an old writer
    assert denied(edit(target, "s-a"))


def test_prefencing_lock_stays_allowed_until_it_is_recreated_with_a_fence(project):
    target = project / "src" / "a.py"
    write_lock(project, "LOCK.txt", owner="claude-code@testhost", host="TESTHOST")
    assert not denied(edit(target, "s-a"))
    assert not denied(edit(target, "s-a"))
    fenced(project, fence=300)
    assert denied(edit(target, "s-a"))


def test_fence_memory_is_per_session_and_dies_with_it(project):
    target = project / "src" / "a.py"
    fenced(project, fence=100)
    assert not denied(edit(target, "s-a"))
    fenced(project, fence=200)
    assert denied(edit(target, "s-a"))
    assert not denied(edit(target, "s-b"))   # known limit: a session that never saw fence 100 trusts what it finds
    lock_guard.session_end({"session_id": "s-a", "cwd": str(project)})
    assert not denied(edit(target, "s-a"))   # memory gone: trust on first use again


def test_session_start_remembers_the_fence_of_existing_shared_locks(project):
    target = project / "src" / "a.py"
    fenced(project, fence=100)
    lock_guard.session({"cwd": str(project), "session_id": "s-a"})   # the session sees the lock before anyone replaces it
    fenced(project, fence=200)                                       # taken over before the first edit
    assert denied(edit(target, "s-a"))
    assert not denied(edit(target, "s-b"))                           # negative control: a session that saw nothing earlier


def test_without_session_id_the_owner_prefix_alone_decides(project):
    target = project / "src" / "a.py"
    fenced(project, fence=100)
    assert not denied(edit(target))
    fenced(project, fence=200)
    assert not denied(edit(target))


def test_unwritable_state_dir_never_denies(project, monkeypatch, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("ELLMOS_LOCK_STATE_DIR", str(blocker / "sub"))
    fenced(project, fence=100)
    assert not denied(edit(project / "src" / "a.py", "s-a"))


# --- team board file claims ------------------------------------------------------------------------------------

def team_board(project, claims=(), name="LOCK.team.TESTHOST.txt"):
    write_lock(project, name, owner="kimi@TESTHOST", host="TESTHOST", purpose="parallel work")
    with open(project / name, "a", encoding="utf-8") as fh:
        fh.write(team.TEAM_SECTIONS)
    for agent, resource, queue in claims:
        ok, message = team.update_team_lock(project / name, "file_claims", "claim", agent, resources=[resource],
                                            queue=queue, project_dir=project)
        assert ok, message
    return project / name


def test_foreign_edit_claim_denies_with_holder_and_queue_hint(project):
    team_board(project, [("codex@TESTHOST.99999999", "src/a.py", False)])
    out = lock_guard.pretooluse(edit(project / "src" / "a.py", "ab12cd34-1"))
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "codex@TESTHOST.99999999" in reason and "not queued" in reason
    assert "/ellmos-lock-master:message" in reason and "claude-code@TESTHOST.ab12cd34" in reason


def test_own_claim_and_unclaimed_paths_are_allowed(project):
    (project / "src" / "b.py").write_text("y = 2\n", encoding="utf-8")
    team_board(project, [("claude-code@TESTHOST.ab12cd34", "src/a.py", False)])
    assert not denied(edit(project / "src" / "a.py", "ab12cd34-1"))   # own claim
    assert not denied(edit(project / "src" / "b.py", "ffff0000-2"))   # nobody claimed b.py
    assert denied(edit(project / "src" / "a.py", "ffff0000-2"))       # the other session is held off


def test_folder_claim_covers_files_below_it_but_not_siblings_with_the_same_prefix(project):
    (project / "src2").mkdir()
    (project / "src2" / "c.py").write_text("z\n", encoding="utf-8")
    team_board(project, [("codex@TESTHOST.99999999", "src", False)])
    assert denied(edit(project / "src" / "a.py", "ab12cd34-1"))
    assert not denied(edit(project / "src2" / "c.py", "ab12cd34-1"))


def test_queue_position_is_reported(project):
    team_board(project, [("codex@TESTHOST.99999999", "src/a.py", False),
                         ("claude-code@TESTHOST.ab12cd34", "src/a.py", True)])
    reason = lock_guard.pretooluse(edit(project / "src" / "a.py", "ab12cd34-1"))[
        "hookSpecificOutput"]["permissionDecisionReason"]
    assert "queued at position 1 of 1" in reason


def test_shared_prefix_claim_counts_as_own(project):
    team_board(project, [("claude-code@testhost", "src/a.py", False)])
    assert not denied(edit(project / "src" / "a.py", "ab12cd34-1"))


def test_released_claim_lets_the_waiter_in(project):
    lock = team_board(project, [("codex@TESTHOST.99999999", "src/a.py", False),
                                ("claude-code@TESTHOST.ab12cd34", "src/a.py", True)])
    assert denied(edit(project / "src" / "a.py", "ab12cd34-1"))
    team.update_team_lock(lock, "file_claims", "release", "codex@TESTHOST.99999999", resources=["src/a.py"],
                          project_dir=project)
    assert not denied(edit(project / "src" / "a.py", "ab12cd34-1"))  # promoted to EDITING


def test_claims_do_not_touch_other_commands(project):
    team_board(project, [("codex@TESTHOST.99999999", "src/a.py", False)])
    assert lock_guard.pretooluse({"tool_name": "Bash", "cwd": str(project), "session_id": "x",
                                  "tool_input": {"command": "git status"}}) is None


def test_corrupt_claims_warn_but_do_not_block(project):
    lock = team_board(project)
    text = lock.read_text(encoding="utf-8").replace("# FILE-CLAIMS:\n", "# FILE-CLAIMS:\nnot a claim line\n")
    lock.write_text(text, encoding="utf-8")
    out = lock_guard.pretooluse(edit(project / "src" / "a.py", "ab12cd34-1"))
    assert "hookSpecificOutput" not in out and "file claims not checked" in out["systemMessage"]


def test_team_lock_without_board_sections_has_no_claims(project):
    write_lock(project, "LOCK.team.TESTHOST.txt", owner="kimi@TESTHOST", host="TESTHOST")
    assert lock_guard.pretooluse(edit(project / "src" / "a.py", "ab12cd34-1")) is None


def test_session_end_releases_claims_and_presence_and_promotes_the_waiter(project):
    lock = team_board(project, [("claude-code@TESTHOST.ab12cd34", "src/a.py", False),
                                ("codex@TESTHOST.99999999", "src/a.py", True)])
    team.update_team_lock(lock, "presence", "claim", "loop-01 | claude-code@TESTHOST.ab12cd34 | dev | t",
                          project_dir=project)
    lock_guard.session_end({"session_id": "ab12cd34-1", "cwd": str(project)})
    text = lock.read_text(encoding="utf-8")
    assert "claude-code@TESTHOST.ab12cd34" not in text
    assert "[codex@TESTHOST.99999999] EDITING" in text


def test_session_end_leaves_other_sessions_claims(project):
    lock = team_board(project, [("codex@TESTHOST.99999999", "src/a.py", False)])
    lock_guard.session_end({"session_id": "ab12cd34-1", "cwd": str(project)})
    assert "[codex@TESTHOST.99999999] EDITING" in lock.read_text(encoding="utf-8")


# --- Codex apply_patch and Kimi Code file tools ----------------------------------------------------------------

PATCH = ("*** Begin Patch\n*** Update File: src/a.py\n@@\n-x = 1\n+x = 2\n*** Add File: docs/n.md\n+hi\n"
         "*** End Patch\n")


def test_apply_patch_targets_come_from_the_patch_headers(project):
    got = lock_guard.targets({"tool_name": "apply_patch", "cwd": str(project), "tool_input": {"command": PATCH}})
    assert got == [("edit", project / "src" / "a.py"), ("edit", project / "docs" / "n.md")]


def test_apply_patch_move_to_and_delete_headers(project):
    patch = "*** Begin Patch\n*** Delete File: old.py\n*** Update File: a.py\n*** Move to: b.py\n*** End Patch\n"
    got = lock_guard.targets({"tool_name": "apply_patch", "cwd": str(project), "tool_input": {"command": patch}})
    assert [p.name for _op, p in got] == ["old.py", "a.py", "b.py"]


def test_foreign_lock_denies_apply_patch_and_kimi_tools(project):
    write_lock(project, "LOCK.txt", owner="gemini@OTHERHOST", host="OTHERHOST")
    for payload in (
        {"tool_name": "apply_patch", "tool_input": {"command": PATCH}},
        {"tool_name": "StrReplaceFile", "tool_input": {"path": "src/a.py"}},
        {"tool_name": "WriteFile", "tool_input": {"file_path": "src/a.py"}},
        {"tool_name": "DeleteFile", "tool_input": {"path": "src/a.py"}},
        {"tool_name": "MoveFile", "tool_input": {"source": "src/a.py", "destination": "src/z.py"}},
    ):
        assert denied({**payload, "cwd": str(project)}), payload


def test_unreadable_edit_targets_are_denied_not_waved_through(project):
    for payload in (
        {"tool_name": "apply_patch", "tool_input": {"command": "*** Begin Patch\n*** End Patch\n"}},
        {"tool_name": "apply_patch", "tool_input": {"command": "*** Update File: \n"}},
        {"tool_name": "apply_patch", "tool_input": {}},
        {"tool_name": "MoveFile", "tool_input": {"source": "a.py"}},
    ):
        assert denied({**payload, "cwd": str(project)}), payload


def test_known_file_tools_without_a_readable_path_are_denied_other_tools_are_not_relevant(project):
    for tool in ("Edit", "Write", "MultiEdit", "NotebookEdit", "WriteFile", "StrReplaceFile", "DeleteFile"):
        assert denied({"tool_name": tool, "cwd": str(project), "tool_input": {}}), tool
        assert denied({"tool_name": tool, "cwd": str(project), "tool_input": {"other": "x.py"}}), tool
    assert lock_guard.targets({"tool_name": "Read", "cwd": str(project), "tool_input": {"file_path": "x"}}) == []


def test_kimi_0_14_2_write_and_edit_use_the_path_field(project):
    """Kimi Code CLI 0.14.2 names Write and Edit with `path` (no file_path); a foreign lock must stop them."""
    write_lock(project, "LOCK.txt", owner="gemini@OTHERHOST", host="OTHERHOST")
    for tool in ("Write", "Edit"):
        payload = {"tool_name": tool, "cwd": str(project), "tool_input": {"path": "src/a.py"}}
        assert denied(payload), tool
        assert lock_guard.targets(payload) == [("edit", project / "src" / "a.py")]
        assert denied({**payload, "tool_input": {"file_path": "src/a.py"}}), tool  # Claude's form still works
    (project / "LOCK.txt").unlink()
    for tool in ("Write", "Edit"):  # negative control: without the lock the same calls pass
        assert not denied({"tool_name": tool, "cwd": str(project), "tool_input": {"path": "src/a.py"}})


@pytest.mark.parametrize("actor, exit_code", [("kimi", 2), ("codex", 2), ("claude-code", 0)])
def test_denial_contract_per_actor(project, monkeypatch, capsys, actor, exit_code):
    write_lock(project, "LOCK.txt", owner="gemini@OTHERHOST", host="OTHERHOST")
    monkeypatch.setenv("ELLMOS_LOCK_ACTOR", "claude-code")  # restored after the test; main() sets it from --actor
    payload = json.dumps(edit(project / "src" / "a.py"))
    monkeypatch.setattr(sys, "stdin", type("S", (), {"read": staticmethod(lambda: payload)})())
    assert lock_guard.main(["x", "pretooluse", "--actor", actor]) == exit_code
    captured = capsys.readouterr()
    if exit_code == 2:
        assert "foreign active lock" in captured.err and captured.out == ""
    else:
        assert json.loads(captured.out)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_actor_names_owner_prefix_and_session(monkeypatch):
    monkeypatch.setenv("COMPUTERNAME", "TESTHOST")
    monkeypatch.setenv("ELLMOS_LOCK_ACTOR", "codex")
    assert lock_guard.owner_prefixes() == ["codex@testhost"]
    assert lock_guard.session_name({"session_id": "abcdef012345"}) == "codex@TESTHOST.abcdef01"
    monkeypatch.setenv("ELLMOS_LOCK_ACTOR", "../evil")
    assert lock_guard.actor() == "claude-code"
