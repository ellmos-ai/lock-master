"""Focused contract tests for atomic Team-Lock updates."""

from __future__ import annotations

import multiprocessing
import os
from pathlib import Path

import pytest

import lock_create
import team_lock
from team_lock import (
    TEAM_SECTIONS,
    acquire_os_lock,
    main,
    paths_overlap,
    release_os_lock,
    update_team_lock,
)


def _make_lock(
    project: Path,
    body: str | None = None,
    *,
    name: str = "LOCK.team.LAPTOP.txt",
) -> Path:
    lock_file = project / name
    lock_file.write_text(
        body or ("owner: tester\nhost: LAPTOP\n\n" + TEAM_SECTIONS), encoding="utf-8"
    )
    return lock_file


def _process_claim(lock_name: str, project_name: str, agent: str, start, results) -> None:
    start.wait(10)
    result = update_team_lock(
        Path(lock_name),
        "file_claims",
        "claim",
        agent,
        resources=["src/shared.py", "tests/test_shared.py"],
        project_dir=Path(project_name),
    )
    results.put(result)


def _crash_while_holding_guard(lock_name: str, ready) -> None:
    acquire_os_lock(Path(lock_name), timeout=2)
    ready.set()
    os._exit(23)


def test_paths_overlap() -> None:
    assert paths_overlap("src", "src/module.py")
    assert paths_overlap("SRC/Module.py", "src/module.py")
    assert not paths_overlap("src2", "src")
    assert not paths_overlap("tests", "src")


def test_lock_create_team_has_canonical_four_sections(tmp_path: Path) -> None:
    assert lock_create.main(
        [str(tmp_path), "--team", "LAPTOP", "--owner", "agent-lead"]
    ) == 0
    content = (tmp_path / "LOCK.team.LAPTOP.txt").read_text(encoding="utf-8")
    assert "host: LAPTOP" in content
    markers = ["# PRESENCE:", "# FILE-CLAIMS:", "# TOOL-CLAIMS:", "# MESSAGES:"]
    assert all(content.count(marker) == 1 for marker in markers)
    assert [content.index(marker) for marker in markers] == sorted(content.index(marker) for marker in markers)
    assert "order=000001" in content


def test_presence_and_message_lifecycle(tmp_path: Path) -> None:
    lock_file = _make_lock(tmp_path)
    success, _ = update_team_lock(
        lock_file,
        "presence",
        "claim",
        "loop-01 | agent-1 | dev | Refactoring",
        project_dir=tmp_path,
    )
    assert success
    assert "[agent-1] | dev | Refactoring" in lock_file.read_text(encoding="utf-8")

    success, _ = update_team_lock(
        lock_file,
        "presence",
        "claim",
        "loop-01 | agent-1 | reviewer | Prüfung",
        project_dir=tmp_path,
    )
    assert success
    assert lock_file.read_text(encoding="utf-8").count("[agent-1] |") == 1

    success, _ = update_team_lock(
        lock_file,
        "messages",
        "claim",
        "agent-1: Ursache geprüft",
        project_dir=tmp_path,
    )
    assert success
    assert "[agent-1]: Ursache geprüft" in lock_file.read_text(encoding="utf-8")

    success, _ = update_team_lock(
        lock_file, "presence", "release", "agent-1", project_dir=tmp_path
    )
    assert success
    assert "[agent-1] |" not in lock_file.read_text(encoding="utf-8")


def test_claim_many_is_atomic_and_reports_holder(tmp_path: Path) -> None:
    lock_file = _make_lock(tmp_path)
    success, _ = update_team_lock(
        lock_file,
        "file_claims",
        "claim",
        "agent-1",
        resources=["src/module.py", "tests/test_module.py"],
        project_dir=tmp_path,
    )
    assert success
    content_before = lock_file.read_text(encoding="utf-8")
    assert "[agent-1] EDITING  order=000001 | src/module.py | tests/test_module.py" in content_before

    success, message = update_team_lock(
        lock_file,
        "file_claims",
        "claim",
        "agent-2",
        resources=["docs", "src"],
        project_dir=tmp_path,
    )
    assert not success
    assert "agent-1" in message
    content_after = lock_file.read_text(encoding="utf-8")
    assert content_after == content_before
    assert "docs" not in content_after


def test_fifo_waiter_cannot_be_overtaken_and_promotes_as_bundle(tmp_path: Path) -> None:
    lock_file = _make_lock(tmp_path)
    assert update_team_lock(
        lock_file, "file_claims", "claim", "agent-1", ["src"], project_dir=tmp_path
    )[0]
    assert update_team_lock(
        lock_file,
        "file_claims",
        "claim",
        "agent-2",
        ["src", "tests"],
        queue=True,
        project_dir=tmp_path,
    )[0]
    assert update_team_lock(
        lock_file,
        "file_claims",
        "claim",
        "agent-3",
        ["tests"],
        queue=True,
        project_dir=tmp_path,
    )[0]

    waiting = lock_file.read_text(encoding="utf-8")
    assert "[agent-2] WAITING order=000002 | src | tests" in waiting
    assert "[agent-3] WAITING order=000003 | tests" in waiting

    assert update_team_lock(
        lock_file, "file_claims", "release", "agent-1", ["src"], project_dir=tmp_path
    )[0]
    promoted = lock_file.read_text(encoding="utf-8")
    assert "[agent-2] EDITING  order=000002 | src | tests" in promoted
    assert "[agent-3] WAITING order=000003 | tests" in promoted


def test_same_agent_follow_up_bundles_remain_distinct_and_release_is_idempotent(tmp_path: Path) -> None:
    lock_file = _make_lock(tmp_path)
    for resource in ("src/a.py", "tests/test_a.py"):
        assert update_team_lock(
            lock_file,
            "file_claims",
            "claim",
            "agent-1",
            [resource],
            project_dir=tmp_path,
        )[0]
    content = lock_file.read_text(encoding="utf-8")
    assert "order=000001 | src/a.py" in content
    assert "order=000002 | tests/test_a.py" in content

    assert update_team_lock(
        lock_file,
        "file_claims",
        "release",
        "agent-1",
        ["src/a.py"],
        project_dir=tmp_path,
    )[0]
    once = lock_file.read_text(encoding="utf-8")
    assert "src/a.py" not in once
    assert "tests/test_a.py" in once
    assert update_team_lock(
        lock_file,
        "file_claims",
        "release",
        "agent-1",
        ["src/a.py"],
        project_dir=tmp_path,
    )[0]
    assert lock_file.read_text(encoding="utf-8") == once


def test_tool_claims_are_case_insensitive_and_fifo(tmp_path: Path) -> None:
    lock_file = _make_lock(tmp_path)
    assert update_team_lock(
        lock_file,
        "tool_claims",
        "claim",
        "agent-1",
        ["MCP::FileCommander"],
        project_dir=tmp_path,
    )[0]
    success, message = update_team_lock(
        lock_file,
        "tool_claims",
        "claim",
        "agent-2",
        ["mcp::filecommander"],
        project_dir=tmp_path,
    )
    assert not success
    assert "agent-1" in message
    assert update_team_lock(
        lock_file,
        "tool_claims",
        "claim",
        "agent-2",
        ["mcp::filecommander"],
        queue=True,
        project_dir=tmp_path,
    )[0]


@pytest.mark.parametrize(
    "agent,resources",
    [
        ("agent with space", ["src"]),
        ("agent-1\ninjected", ["src"]),
        ("agent-1", ["../outside"]),
        ("agent-1", ["src\n[evil] EDITING  secrets"]),
        ("agent-1", ["C:\\outside"]),
    ],
)
def test_public_api_rejects_injection_and_traversal(
    tmp_path: Path, agent: str, resources: list[str]
) -> None:
    lock_file = _make_lock(tmp_path)
    original = lock_file.read_bytes()
    with pytest.raises(ValueError):
        update_team_lock(
            lock_file,
            "file_claims",
            "claim",
            agent,
            resources,
            project_dir=tmp_path,
        )
    assert lock_file.read_bytes() == original


def test_public_api_rejects_lock_outside_project_and_missing_valid_lock(tmp_path: Path) -> None:
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    outside_lock = _make_lock(outside)
    with pytest.raises(ValueError):
        update_team_lock(
            outside_lock,
            "file_claims",
            "claim",
            "agent-1",
            ["src"],
            project_dir=project,
        )

    missing = project / "LOCK.team.LAPTOP.txt"
    success, message = update_team_lock(
        missing, "file_claims", "claim", "agent-1", ["src"], project_dir=project
    )
    assert not success
    assert "existiert nicht" in message


def test_markerless_header_only_migrates_but_unknown_line_fails_closed(tmp_path: Path) -> None:
    lock_file = _make_lock(tmp_path, "owner: tester\nhost: LAPTOP\nmode: hard\n")
    assert update_team_lock(
        lock_file, "file_claims", "claim", "agent-1", ["src"], project_dir=tmp_path
    )[0]
    migrated = lock_file.read_text(encoding="utf-8")
    assert migrated.count("# FILE-CLAIMS:") == 1
    assert "[agent-1] EDITING  order=000001 | src" in migrated

    unknown = tmp_path / "LOCK.team.OTHER.txt"
    unknown.write_text("owner: tester\nhost: OTHER\nGARBAGE CLAIM\n", encoding="utf-8")
    original = unknown.read_bytes()
    success, message = update_team_lock(
        unknown, "file_claims", "claim", "agent-2", ["src"], project_dir=tmp_path
    )
    assert not success
    assert "Unbekannte Zeile" in message
    assert unknown.read_bytes() == original


@pytest.mark.parametrize("heading", ["Files claimed:", "# Tools claimed:", "## Presence:"])
def test_markerless_recognized_legacy_sections_fail_closed(tmp_path: Path, heading: str) -> None:
    lock_file = _make_lock(
        tmp_path,
        f"owner: tester\nhost: LAPTOP\n{heading}\n[legacy] EDITING src\n",
    )
    original = lock_file.read_bytes()
    success, message = update_team_lock(
        lock_file, "file_claims", "claim", "agent-2", ["tests"], project_dir=tmp_path
    )
    assert not success
    assert "nicht eindeutig migrierbar" in message
    assert lock_file.read_bytes() == original


def test_header_host_must_match_filename_including_scoped_team_lock(tmp_path: Path) -> None:
    mismatch = _make_lock(
        tmp_path,
        "owner: tester\nhost: OTHER\n\n" + TEAM_SECTIONS,
    )
    original = mismatch.read_bytes()
    success, message = update_team_lock(
        mismatch, "file_claims", "claim", "agent-1", ["src"], project_dir=tmp_path
    )
    assert not success
    assert "widerspricht" in message
    assert mismatch.read_bytes() == original

    scoped = _make_lock(
        tmp_path,
        "owner: tester\nhost: LAPTOP\n\n" + TEAM_SECTIONS,
        name="LOCK.team.docs.LAPTOP.txt",
    )
    assert update_team_lock(
        scoped, "file_claims", "claim", "agent-1", ["docs"], project_dir=tmp_path
    )[0]


def test_cli_uses_same_validation_boundaries(tmp_path: Path) -> None:
    lock_file = _make_lock(tmp_path)
    original = lock_file.read_bytes()
    assert main(
        [
            "claim-file",
            str(tmp_path),
            "--lock-name",
            lock_file.name,
            "--agent",
            "agent-1",
            "--resource",
            "../outside",
        ]
    ) == 2
    assert lock_file.read_bytes() == original


def test_corrupt_claim_and_partial_sections_fail_closed_without_rewrite(tmp_path: Path) -> None:
    lock_file = _make_lock(tmp_path)
    content = lock_file.read_text(encoding="utf-8").replace(
        "# FILE-CLAIMS:\n", "# FILE-CLAIMS:\n[agent-1] EDITING order=oops | src\n"
    )
    lock_file.write_text(content, encoding="utf-8")
    original = lock_file.read_bytes()
    success, message = update_team_lock(
        lock_file, "file_claims", "claim", "agent-2", ["tests"], project_dir=tmp_path
    )
    assert not success
    assert "fail-closed" in message
    assert lock_file.read_bytes() == original

    partial = tmp_path / "LOCK.team.OTHER.txt"
    partial.write_text("owner: x\n# FILE-CLAIMS:\n", encoding="utf-8")
    original = partial.read_bytes()
    assert not update_team_lock(
        partial, "file_claims", "claim", "agent-1", ["src"], project_dir=tmp_path
    )[0]
    assert partial.read_bytes() == original


def test_foreign_comments_and_section_order_are_preserved(tmp_path: Path) -> None:
    lock_file = _make_lock(tmp_path)
    content = lock_file.read_text(encoding="utf-8")
    content = content.replace("owner: tester\n", "owner: tester\ncustom_header: keep-me\n")
    content = content.replace("# FILE-CLAIMS:\n", "# FILE-CLAIMS:\n# foreign note stays here\n")
    lock_file.write_text(content, encoding="utf-8")

    assert update_team_lock(
        lock_file, "file_claims", "claim", "agent-1", ["src"], project_dir=tmp_path
    )[0]
    updated = lock_file.read_text(encoding="utf-8")
    assert "custom_header: keep-me" in updated
    assert updated.count("# foreign note stays here") == 1
    markers = ["# PRESENCE:", "# FILE-CLAIMS:", "# TOOL-CLAIMS:", "# MESSAGES:"]
    assert [updated.index(marker) for marker in markers] == sorted(updated.index(marker) for marker in markers)


def test_legacy_singletons_migrate_but_ambiguous_same_agent_fails_closed(tmp_path: Path) -> None:
    lock_file = _make_lock(tmp_path)
    legacy = lock_file.read_text(encoding="utf-8").replace(
        "# FILE-CLAIMS:\n", "# FILE-CLAIMS:\n[legacy-agent] EDITING  src\n"
    )
    lock_file.write_text(legacy, encoding="utf-8")
    assert update_team_lock(
        lock_file, "file_claims", "claim", "agent-2", ["tests"], project_dir=tmp_path
    )[0]
    migrated = lock_file.read_text(encoding="utf-8")
    assert "[legacy-agent] EDITING  order=000001 | src" in migrated

    ambiguous = migrated.replace(
        "[legacy-agent] EDITING  order=000001 | src",
        "[legacy-agent] EDITING  src\n[legacy-agent] EDITING  docs",
    ).replace("[agent-2] EDITING  order=000002 | tests\n", "")
    lock_file.write_text(ambiguous, encoding="utf-8")
    original = lock_file.read_bytes()
    success, message = update_team_lock(
        lock_file, "file_claims", "claim", "agent-3", ["tests"], project_dir=tmp_path
    )
    assert not success
    assert "mehrdeutig" in message
    assert lock_file.read_bytes() == original


def test_multiprocess_exactly_one_winner(tmp_path: Path) -> None:
    lock_file = _make_lock(tmp_path)
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_process_claim,
            args=(str(lock_file), str(tmp_path), f"agent-{index}", start, results),
        )
        for index in range(4)
    ]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(15)
        assert process.exitcode == 0
    outcomes = [results.get(timeout=2)[0] for _ in processes]
    assert outcomes.count(True) == 1
    assert outcomes.count(False) == 3
    content = lock_file.read_text(encoding="utf-8")
    assert content.count("EDITING  order=") == 1
    assert "src/shared.py | tests/test_shared.py" in content


def test_guard_is_released_after_abrupt_process_exit(tmp_path: Path) -> None:
    lock_file = _make_lock(tmp_path)
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    process = context.Process(target=_crash_while_holding_guard, args=(str(lock_file), ready))
    process.start()
    assert ready.wait(10)
    process.join(10)
    assert process.exitcode == 23

    fd, guard_path = acquire_os_lock(lock_file, timeout=1)
    release_os_lock(fd, guard_path)
    assert guard_path.is_file()
    assert guard_path.name.endswith(".txt.guard")


def test_replace_failure_preserves_target_and_cleans_temp(tmp_path: Path, monkeypatch) -> None:
    lock_file = _make_lock(tmp_path)
    original = lock_file.read_bytes()
    import _lock_master_team as implementation

    def fail_replace(source, target) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(implementation.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated"):
        update_team_lock(
            lock_file,
            "file_claims",
            "claim",
            "agent-1",
            ["src"],
            project_dir=tmp_path,
        )
    assert lock_file.read_bytes() == original
    assert not list(tmp_path.glob(f".{lock_file.name}.*.tmp"))


def test_root_shim_and_guard_ignore_contract() -> None:
    root = Path(__file__).resolve().parent.parent
    assert callable(team_lock.main)
    assert callable(team_lock.update_team_lock)
    ignored = {
        line.strip()
        for line in (root / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert "LOCK.team.*.txt.guard" in ignored
    assert "LOCK.team.*.txt" not in ignored
