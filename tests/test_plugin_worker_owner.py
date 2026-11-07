"""A delegated worker owns the locks named after it: `<spawn name>@<HOST>` (T-20261107-958032576).

Inside a subagent or teammate the hook payload carries `agent_id` and, as `agent_type`, the spawn name, while
`session_id` is the lead's and shared by the whole team. These tests pin that only the worker itself owns such a
lock -- not the lead, not a sibling, not a payload without `agent_id` -- and that every other guard still holds.
"""
import pytest

from tests.test_plugin_v18 import denied, team_board, write_lock


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPUTERNAME", "TESTHOST")
    for var in ("ELLMOS_LOCK_OWNER", "ELLMOS_LOCK_ACTOR", "ELLMOS_LOCK_LEASES"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ELLMOS_LOCK_STATE_DIR", str(tmp_path / "_state"))
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    return tmp_path


SID = "20eae07e-eaa3-4181-95fc-df4799e5d776"  # one session id for lead and teammates, as Claude Code sends it
WORKER = {"agent_id": "aopus-worker-o1-6efb3da985f51ceb", "agent_type": "opus-worker-o1"}
SIBLING = {"agent_id": "asonnet-worker-s1-0d6819b9cd4a38ce", "agent_type": "sonnet-worker-s1"}


def edit_as(target, caller=None, sid=SID) -> dict:
    payload = {"tool_name": "Edit", "cwd": str(target.parent), "session_id": sid,
               "tool_input": {"file_path": str(target)}}
    return {**payload, **(caller or {})}


def worker_lock(project, owner="opus-worker-o1@TESTHOST", name="LOCK.txt", fence=100, **fields):
    write_lock(project, name, owner=owner, host="TESTHOST", fence=fence, **fields)


def test_worker_owns_the_lock_named_after_it(project):
    worker_lock(project)
    target = project / "src" / "a.py"
    assert not denied(edit_as(target, WORKER))
    assert not denied(edit_as(target, WORKER))  # same fence on the second use: still its own


def test_worker_owner_with_a_suffix_is_its_own_too(project):
    worker_lock(project, owner="opus-worker-o1@TESTHOST:T-20261107-958032576")  # `:<suffix>` as for the prefixes
    assert not denied(edit_as(project / "src" / "a.py", WORKER))


def test_lead_sibling_and_a_bare_agent_type_stay_blocked(project):
    worker_lock(project)
    target = project / "src" / "a.py"
    assert denied(edit_as(target))           # the lead: same session id, no agent_id
    assert denied(edit_as(target, SIBLING))  # a teammate with another name
    assert denied(edit_as(target, {"agent_type": "opus-worker-o1"}))  # agent_type without agent_id is no worker


def test_foreign_host_and_lookalike_owners_stay_blocked(project):
    target = project / "src" / "a.py"
    for owner in ("opus-worker-o1@OTHERHOST", "opus-worker-o1", "opus-worker-o1@TESTHOST.ab12cd34",
                  "x-opus-worker-o1@TESTHOST", "opus-worker-o1@TESTHOSTX"):
        worker_lock(project, owner=owner)
        assert denied(edit_as(target, WORKER)), owner


def test_invalid_and_reserved_spawn_names_get_no_owner_of_their_own(project, monkeypatch):
    target = project / "src" / "a.py"
    cases = [("codex:codex-rescue", "codex:codex-rescue@TESTHOST"), ("", "@TESTHOST"),
             ("bad name", "bad name@TESTHOST"), ("-dash", "-dash@TESTHOST")]
    cases += [(n, f"{n}@TESTHOST") for n in ("claude-desktop", "codex", "kimi", "antigravity", "copilot")]
    for name, owner in cases:
        worker_lock(project, owner=owner)
        assert denied(edit_as(target, {"agent_id": "a-1", "agent_type": name})), name
    monkeypatch.setenv("ELLMOS_LOCK_ACTOR", "codex")  # as Codex, a worker named claude-code gets no claude lock
    worker_lock(project, owner="claude-code@TESTHOST")
    assert denied(edit_as(target, {"agent_id": "a-1", "agent_type": "claude-code"}))


def test_fence_moved_under_a_worker_owner_denies(project):
    target = project / "src" / "a.py"
    worker_lock(project, fence=100)
    assert not denied(edit_as(target, WORKER))  # first use: trusted, fence remembered
    worker_lock(project, fence=200)             # granted anew to another worker of the same name
    assert denied(edit_as(target, WORKER))


def test_user_lock_with_a_worker_owner_still_blocks_the_worker(project):
    write_lock(project, "LOCK.user.txt", owner="opus-worker-o1@TESTHOST", host="TESTHOST")
    assert denied(edit_as(project / "src" / "a.py", WORKER))


def test_expired_worker_lock_is_inactive_for_everyone(project):
    """No special case: an expired lock is no grant and no barrier, exactly as before."""
    worker_lock(project, created="2026-01-01T00:00:00Z", expires_after="1h")
    target = project / "src" / "a.py"
    assert not denied(edit_as(target, SIBLING))
    assert not denied(edit_as(target, WORKER))


def test_worker_board_claim_is_its_own_and_foreign_to_lead_and_siblings(project):
    team_board(project, [("opus-worker-o1@TESTHOST", "src/a.py", False)])
    target = project / "src" / "a.py"
    assert not denied(edit_as(target, WORKER))
    assert denied(edit_as(target, SIBLING))
    assert denied(edit_as(target))


def test_shared_forms_still_admit_the_whole_team_unchanged(project):
    """Documents why the worker form exists: session name and actor prefix are shared by lead and teammates."""
    target = project / "src" / "a.py"
    for owner in ("claude-code@TESTHOST.20eae07e", "claude-code@TESTHOST:opus-worker-o1"):
        worker_lock(project, owner=owner, fence=1)
        assert not denied(edit_as(target, SIBLING)), owner
        assert not denied(edit_as(target)), owner
