"""Lock types cannot be combined in the filename — such names are fail-closed.

A lock name carries exactly ONE type marker, in its first segment. Before this
guard, a name like 'LOCK.until.and.condition.zenodo.txt' was read as a plain
until lock with the odd scope 'and.condition.zenodo', the second marker silently
ignored. Whoever wrote that name intending "deadline AND condition" got the
WEAKER of the two locks without any warning — it released as soon as the
deadline passed, condition unmet.

Combining a deadline with a condition is done through the FIELDS
('not_before' plus 'release_condition'), never through the name.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import lock_utils


def _write(folder: Path, name: str, **fields: str) -> Path:
    path = folder / name
    path.write_text(
        "\n".join(f"{key}: {value}" for key, value in fields.items()) + "\n",
        encoding="utf-8",
    )
    return path


# ------------------------------------------------------------------ detection --

def test_combined_type_markers_are_flagged():
    for name in (
        "LOCK.until.and.condition.zenodo.txt",
        "LOCK.until.or.condition.zenodo.txt",
        "LOCK.until.and.not-condition.zenodo.txt",
        "LOCK.condition.and.until.zenodo.txt",
        "LOCK.user.condition.publish.txt",
    ):
        assert lock_utils.is_ambiguous_lock(name), name


def test_legitimate_names_are_not_flagged():
    for name in (
        "LOCK.txt",
        "LOCK.until.winners-announcement.txt",
        "LOCK.user.zenodo-upload.txt",
        "LOCK.condition.publish-release.txt",
        "LOCK.team.assets.ASUS-GEI.txt",
        "LOCK.software.txt",
    ):
        assert not lock_utils.is_ambiguous_lock(name), name


def test_reserved_word_inside_a_segment_stays_valid():
    """Only whole segments count.

    'publication-and-claim-edits' is one segment that happens to contain 'and'.
    Flagging it would break a real lock in the field.
    """
    name = "LOCK.user.publication-and-claim-edits.txt"
    assert not lock_utils.is_ambiguous_lock(name)
    parts = lock_utils.lock_name_parts(name)
    assert parts is not None
    assert parts["scope"] == "publication-and-claim-edits"


# ---------------------------------------------------------------- fail-closed --

def test_ambiguous_name_does_not_release_when_the_deadline_passes(tmp_path: Path):
    """The regression this guard exists for: it used to release here."""
    lock = _write(
        tmp_path, "LOCK.until.and.condition.zenodo.txt",
        owner="test",
        not_before=(datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
        release_condition="review follow-ups are done",
    )
    assert lock_utils.is_expired(lock) is False
    assert not lock_utils.is_prunable(lock)
    active = {name for name, _scope, _legacy in lock_utils.active_locks(tmp_path)}
    assert "LOCK.until.and.condition.zenodo.txt" in active


def test_ambiguous_name_does_not_expire_by_relative_duration(tmp_path: Path):
    lock = _write(
        tmp_path, "LOCK.and.or.txt", owner="test",
        created=(datetime.now() - timedelta(days=99)).strftime("%Y-%m-%dT%H:%M"),
        expires_after="24h",
    )
    assert lock_utils.is_ambiguous_lock(lock.name)
    assert not lock_utils.is_expired(lock)


def test_ambiguous_locks_are_protected_from_deletion():
    assert lock_utils.is_protected_lock("LOCK.until.and.condition.x.txt")


# ---------------------------------------------------------------- regression --

def test_normal_types_keep_expiring(tmp_path: Path):
    """The guard must not make every lock immortal."""
    stale = _write(
        tmp_path, "LOCK.txt", owner="test",
        created=(datetime.now() - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M"),
    )
    assert lock_utils.is_expired(stale)
    assert lock_utils.is_prunable(stale)

    passed = _write(
        tmp_path, "LOCK.until.deadline.txt", owner="test",
        not_before=(datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
    )
    assert lock_utils.is_expired(passed)


def test_parts_carry_the_flag_for_every_branch():
    for name in ("LOCK.txt", "LOCK.software.txt", "LOCK.user.x.txt",
                 "LOCK.team.x.HOST.txt", "TEST.txt"):
        parts = lock_utils.lock_name_parts(name)
        assert parts is not None
        assert "ambiguous" in parts, name
