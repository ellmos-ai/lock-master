"""A delegated worker owns exactly `<session name>:<agent_id>` (T-20261107-958032576, after the #39 revert).

Claude Code documents `agent_id` as the unique identifier of the subagent or in-process teammate a hook fires in;
`agent_type` is only the agent's type (hooks.md, "Common input fields"), so it is not an identity. Workers share the
lead's `session_id`, hence its session name. Only the exact pair of session name and agent_id is the worker's own:
no prefix, no trust on first use. Without `agent_id` everything behaves as before this change (fail-closed).
"""
import importlib.util
import json

import pytest

from tests.test_plugin_v18 import ROOT, denied, lock_guard, lu, write_lock

SID = "20eae07e-eaa3-4181-95fc-df4799e5d776"  # lead and teammates share it, as Claude Code sends it
AID = "aopus-worker-o1-6efb3da985f51ceb"      # agent id of a named teammate (transcript agent-<id>.jsonl)
OWN = f"claude-code@TESTHOST.20eae07e:{AID}"
WORKER = {"agent_id": AID, "agent_type": "general-purpose"}
SIBLING = {"agent_id": "asonnet-worker-s1-0d6819b9cd4a38ce", "agent_type": "general-purpose"}


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPUTERNAME", "TESTHOST")
    for var in ("ELLMOS_LOCK_OWNER", "ELLMOS_LOCK_ACTOR", "ELLMOS_LOCK_LEASES"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ELLMOS_LOCK_STATE_DIR", str(tmp_path / "_state"))
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    return tmp_path


def edit_as(target, caller=None, sid=SID) -> dict:
    payload = {"tool_name": "Edit", "cwd": str(target.parent), "session_id": sid,
               "tool_input": {"file_path": str(target)}}
    return {**payload, **(caller or {})}


def lock(project, owner=OWN, name="LOCK.txt", fence=100, **fields):
    write_lock(project, name, owner=owner, host="TESTHOST", fence=fence, **fields)


# --- the one positive case ------------------------------------------------------------------------------------

def test_worker_owns_its_exact_owner_also_on_resume_and_case_folded(project):
    target = project / "src" / "a.py"
    lock(project)
    assert not denied(edit_as(target, WORKER))
    assert not denied(edit_as(target, WORKER))  # resume / next message: same agent_id, still its own
    lock(project, owner=OWN.upper())
    assert not denied(edit_as(target, WORKER))  # compared case-folded


# --- everybody else stays out -----------------------------------------------------------------------------------

def test_lead_sibling_new_spawn_and_bare_agent_type_stay_blocked(project):
    target = project / "src" / "a.py"
    lock(project)
    assert denied(edit_as(target))           # the lead: same session id, no agent_id
    assert denied(edit_as(target, SIBLING))  # a teammate with another agent_id
    assert denied(edit_as(target, {"agent_id": "aopus-worker-o1-1111111111111111"}))  # same name re-spawned
    assert denied(edit_as(target, {"agent_type": "general-purpose"}))  # no agent_id: no worker (old Claude Code)


def test_foreign_session_with_the_same_agent_id_is_denied(project):
    lock(project)
    assert denied(edit_as(project / "src" / "a.py", WORKER, sid="ffff0000-1111-2222-3333-444444444444"))


@pytest.mark.parametrize("owner", [
    f"{OWN}:x", f"{OWN}x", OWN[:-1], f"claude-code@OTHERHOST.20eae07e:{AID}", f"codex@TESTHOST.20eae07e:{AID}",
    f"claude-code@TESTHOST.20eae07f:{AID}", f"claude-code@TESTHOST.20eae07e{AID}", AID,
])
def test_near_miss_owners_are_foreign(project, owner):
    lock(project, owner=owner)
    assert denied(edit_as(project / "src" / "a.py", WORKER))


@pytest.mark.parametrize("owner", [
    OWN.replace("worker", "worKer"),   # KELVIN SIGN lowercases to an ASCII k
    OWN.replace("aopus", "аopus"),     # CYRILLIC SMALL LETTER A
    OWN.replace("-o1-", "-o１-"),       # FULLWIDTH DIGIT ONE
])
def test_unicode_lookalike_owners_are_foreign(project, owner):
    lock(project, owner=owner)
    assert denied(edit_as(project / "src" / "a.py", WORKER))


@pytest.mark.parametrize("agent_id", [
    "", "a:b", "a\nowner: x", "a b", "a\tb", "a\x00b", "a\x7fb", "x" * 129, "аopus", 123, None,
])
def test_unusable_agent_ids_give_no_worker_owner(project, agent_id):
    payload = edit_as(project / "src" / "a.py", {"agent_id": agent_id})
    assert lock_guard.worker_owner(payload) is None
    assert lock_guard.subagent({"session_id": SID, "agent_id": agent_id}) is None
    if isinstance(agent_id, str) and agent_id:
        lock(project, owner=f"claude-code@TESTHOST.20eae07e:{agent_id}".splitlines()[0])
        assert denied(payload)


# --- r1's probes of #39 / #40 stay as on 861fff9 -----------------------------------------------------------------

@pytest.mark.parametrize("name", ["codex-cli", "codex-app", "agy", "ocean", "work-autonomous", "codex-operator"])
def test_colliding_spawn_names_get_no_foreign_lock(project, name):
    lock(project, owner=f"{name}@TESTHOST")
    assert denied(edit_as(project / "src" / "a.py", {"agent_id": f"a{name}-0000", "agent_type": name}))


def test_shared_session_name_and_actor_prefix_forms_unchanged(project):
    target = project / "src" / "a.py"
    for owner in ("claude-code@TESTHOST.20eae07e", "claude-code@TESTHOST:opus-worker-o1"):
        lock(project, owner=owner, fence=1)
        assert not denied(edit_as(target, SIBLING)), owner
        assert not denied(edit_as(target)), owner


def test_user_lock_with_the_workers_own_owner_still_blocks(project):
    write_lock(project, "LOCK.user.txt", owner=OWN, host="TESTHOST")
    assert denied(edit_as(project / "src" / "a.py", WORKER))


def test_expired_lock_is_inactive_for_everyone(project):
    lock(project, created="2026-01-01T00:00:00Z", expires_after="1h")
    assert not denied(edit_as(project / "src" / "a.py", SIBLING))


def test_spawn_name_form_of_39_stays_foreign(project):
    lock(project, owner="opus-worker-o1@TESTHOST")
    assert denied(edit_as(project / "src" / "a.py", {**WORKER, "agent_type": "opus-worker-o1"}))


# --- how the worker learns its owner ------------------------------------------------------------------------------

def test_subagent_start_names_the_owner_in_one_short_line_without_side_effects(project, tmp_path):
    payload = {"session_id": SID, "agent_id": AID, "agent_type": "general-purpose", "cwd": str(project),
               "hook_event_name": "SubagentStart"}
    first, again = lock_guard.subagent(payload), lock_guard.subagent(payload)
    assert first == again  # fires again on resume and on every teammate message: same text, nothing piles up
    out = first["hookSpecificOutput"]
    assert out["hookEventName"] == "SubagentStart"
    assert OWN in out["additionalContext"] and "\n" not in out["additionalContext"]
    assert len(out["additionalContext"]) < 300
    assert not (tmp_path / "_state").exists()  # no state written
    assert lock_guard.subagent({"session_id": SID}) is None  # not inside a worker: nothing to say


def test_hooks_json_registers_the_subagent_hook_exactly_once():
    config = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    commands = [h["command"] for event in config.values() for group in event for h in group["hooks"]]
    assert [c for c in commands if c.endswith(" subagent")] == ['sh "${CLAUDE_PLUGIN_ROOT}/hooks/run.sh" subagent']
    assert [h["command"] for group in config["SubagentStart"] for h in group["hooks"]] == \
        ['sh "${CLAUDE_PLUGIN_ROOT}/hooks/run.sh" subagent']


def test_denial_names_the_workers_own_owner_as_fallback(project):
    lock(project, owner="codex@TESTHOST")
    out = lock_guard.pretooluse(edit_as(project / "src" / "a.py", WORKER))
    assert OWN in out["hookSpecificOutput"]["permissionDecisionReason"]
    lead = lock_guard.pretooluse(edit_as(project / "src" / "a.py"))
    assert ":" + AID not in lead["hookSpecificOutput"]["permissionDecisionReason"]


def test_owner_round_trip_through_lock_create_and_the_parser(project):
    # The implementation the plugin's /lock runs (the root lock_create.py is a shim onto it).
    spec = importlib.util.spec_from_file_location("lc_round_trip", ROOT / "pure-locking" / "lock_create.py")
    lock_create = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lock_create)
    assert lock_create.main([str(project), "--owner", OWN, "--no-contest", "--purpose", "round trip"]) == 0
    text = (project / "LOCK.txt").read_text(encoding="utf-8")
    assert lu._parse_lock_text(text)["owner"] == OWN
    assert not denied(edit_as(project / "src" / "a.py", WORKER))
    assert denied(edit_as(project / "src" / "a.py", SIBLING))
