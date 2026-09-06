"""Until locks: release at an ABSOLUTE moment, not after a relative duration.

An until lock is the first type that separates two things every other type
conflates: it DOES expire by time, yet its file is still protected from
automatic deletion. A guard therefore stops watching by itself once the moment
has passed, while the file survives for the human decision and the evidence
obligation recorded in 'release_condition'.

Motivating case: competition judging holds, where the release moment is a fixed
calendar date weeks away that nobody wants to express as 'expires_after: 900h'.
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


def _stamp(offset: timedelta) -> str:
    return (datetime.now() + offset).strftime("%Y-%m-%dT%H:%M")


# --------------------------------------------------------------- recognition --

def test_until_marker_is_recognised_as_its_own_type():
    assert lock_utils.is_until_lock("LOCK.until.txt")
    assert lock_utils.is_until_lock("LOCK.until.winners-announcement.txt")
    assert lock_utils.lock_type_from_name("LOCK.until.foo.txt") == "until"


def test_other_types_are_not_mistaken_for_until():
    assert not lock_utils.is_until_lock("LOCK.txt")
    assert not lock_utils.is_until_lock("LOCK.user.txt")
    assert not lock_utils.is_until_lock("LOCK.condition.x.txt")
    assert not lock_utils.is_until_lock("LOCK.team.HOST.txt")


def test_marker_is_not_part_of_the_scope():
    parts = lock_utils.lock_name_parts("LOCK.until.winners-announcement.txt")
    assert parts is not None
    assert parts["lock_type"] == "until"
    assert parts["scope"] == "winners-announcement"
    assert parts["host"] is None


# -------------------------------------------------------------------- expiry --

def test_holds_before_the_moment_and_releases_after(tmp_path: Path):
    future = _write(tmp_path, "LOCK.until.future.txt",
                    owner="test", not_before=_stamp(timedelta(days=35)))
    past = _write(tmp_path, "LOCK.until.past.txt",
                  owner="test", not_before=_stamp(timedelta(hours=-1)))
    assert not lock_utils.is_expired(future)
    assert lock_utils.is_expired(past)


def test_relative_expiry_does_not_apply(tmp_path: Path):
    """A month-old until lock whose moment is still ahead must stay active.

    Without the dedicated branch the default 24h rule would have released it
    long ago — that is the whole point of the type.
    """
    lock = _write(tmp_path, "LOCK.until.long.txt", owner="test",
                  created=_stamp(timedelta(days=-30)),
                  expires_after="24h",
                  not_before=_stamp(timedelta(days=35)))
    assert not lock_utils.is_expired(lock)


def test_active_locks_drops_it_only_after_the_moment(tmp_path: Path):
    _write(tmp_path, "LOCK.until.future.txt", owner="t",
           not_before=_stamp(timedelta(days=2)))
    _write(tmp_path, "LOCK.until.past.txt", owner="t",
           not_before=_stamp(timedelta(days=-2)))
    active = {name for name, _scope, _legacy in lock_utils.active_locks(tmp_path)}
    assert "LOCK.until.future.txt" in active
    assert "LOCK.until.past.txt" not in active


# --------------------------------------------------------------- fail-closed --

def test_missing_not_before_never_expires(tmp_path: Path):
    """A typo must be able to lock too long, never to release too early."""
    lock = _write(tmp_path, "LOCK.until.nofield.txt", owner="test",
                  created=_stamp(timedelta(days=-99)))
    assert lock_utils.lock_not_before(lock) is None
    assert not lock_utils.is_expired(lock)


def test_unparsable_not_before_never_expires(tmp_path: Path):
    lock = _write(tmp_path, "LOCK.until.broken.txt", owner="test",
                  created=_stamp(timedelta(days=-99)),
                  not_before="sometime next autumn")
    assert lock_utils.lock_not_before(lock) is None
    assert not lock_utils.is_expired(lock)


# ------------------------------------------------------------------ timezone --

def _expected_local_naive(iso_with_offset: str) -> datetime:
    """Independently compute what an offset-aware ISO timestamp converts to
    in THIS system's local time, for THIS specific instant (DST-correct --
    does not reuse "now"'s offset, which can differ from the tested date's
    own offset when the two straddle a DST transition).

    Uses the same stdlib primitive lock_utils.lock_not_before() itself uses
    (fromisoformat -> astimezone() -> strip tzinfo), so this is a contract
    test, not an isolation test: it catches "conversion forgotten",
    "converted to UTC instead of local", or "offset sign flipped" --
    exactly the class of regression a hardcoded expected hour cannot
    survive a timezone change for -- not a broken astimezone() itself.
    A fixed hardcoded expected hour (the previous "21") only holds on a
    system whose local timezone is CEST; GitHub-hosted CI runners default
    to UTC, where the same conversion yields a different hour
    (T-20260906-803989378). No production code changes: lock_not_before()
    converting offset-aware input to naive LOCAL time is the documented,
    intentional contract (it must compare with datetime.now()), not a bug."""
    aware = datetime.fromisoformat(iso_with_offset)
    return aware.astimezone().replace(tzinfo=None)


def test_utc_offset_is_converted_to_local_time(tmp_path: Path):
    """An offset-aware not_before value is converted to local naive time --
    verified against an independently-computed expectation (see
    _expected_local_naive), not a hardcoded hour tied to one timezone."""
    not_before = "2026-10-08T12:00-07:00"
    lock = _write(tmp_path, "LOCK.until.tz.txt", owner="test",
                  not_before=not_before)
    moment = lock_utils.lock_not_before(lock)
    assert moment is not None
    assert moment.tzinfo is None, "must be naive so it compares with now()"
    assert moment == _expected_local_naive(not_before)


def test_utc_offset_conversion_is_correct_across_a_dst_boundary(tmp_path: Path):
    """Same conversion for a date in the other half of the year
    (2026-12-08) than the case above (2026-10-08). On a host that observes
    a DST transition between the two (e.g. Europe/Berlin: CEST -> CET on
    2026-10-25), this additionally exercises a DIFFERENT UTC offset than
    the first test and would catch a fix that hardcodes a single fixed
    offset instead of asking the platform per-date. On a non-DST host (or
    UTC) both dates share the same offset and this degenerates to a
    duplicate of the first assertion -- still correct, just not
    discriminating there."""
    not_before = "2026-12-08T12:00-07:00"
    lock = _write(tmp_path, "LOCK.until.tz-winter.txt", owner="test",
                  not_before=not_before)
    moment = lock_utils.lock_not_before(lock)
    assert moment is not None
    assert moment.tzinfo is None
    assert moment == _expected_local_naive(not_before)


def test_value_without_offset_is_read_as_local_time(tmp_path: Path):
    lock = _write(tmp_path, "LOCK.until.naive.txt", owner="test",
                  not_before="2026-10-08T12:00")
    moment = lock_utils.lock_not_before(lock)
    assert moment is not None
    assert (moment.day, moment.hour) == (8, 12)


# ----------------------------------------------------------------- protection --

def test_protected_from_deletion_even_after_the_moment(tmp_path: Path):
    """Expired by time, but the file stays: the human decision outlives it."""
    past = _write(tmp_path, "LOCK.until.past.txt", owner="test",
                  not_before=_stamp(timedelta(days=-5)))
    assert lock_utils.is_expired(past)
    assert lock_utils.is_protected_lock(past.name)
    assert not lock_utils.is_prunable(past)


# ---------------------------------------------------------------- regression --

def test_other_types_keep_their_behaviour(tmp_path: Path):
    exclusive = _write(tmp_path, "LOCK.txt", owner="t",
                       created=_stamp(timedelta(days=-3)))
    user = _write(tmp_path, "LOCK.user.txt", owner="Lukas",
                  created=_stamp(timedelta(days=-99)))
    condition = _write(tmp_path, "LOCK.condition.x.txt", owner="t",
                       created=_stamp(timedelta(days=-99)),
                       release_condition="something")
    assert lock_utils.is_expired(exclusive) and lock_utils.is_prunable(exclusive)
    assert not lock_utils.is_expired(user)
    assert not lock_utils.is_expired(condition)


# ---------------------------------------------------------- release_mode -----
# T-20260903-476807738: combine a deadline with a free-text condition through
# the FIELDS, not through a name grammar (LOCK.until.and.condition.* etc. were
# explicitly rejected).

def test_deadline_alone_still_releases_without_release_condition(tmp_path: Path):
    """Backward compatibility: a lock with ONLY not_before is unaffected --
    release_mode only matters once release_condition is ALSO present."""
    lock = _write(tmp_path, "LOCK.until.plain.txt", owner="test",
                  not_before=_stamp(timedelta(hours=-1)))
    assert lock_utils.is_expired(lock)


def test_default_all_does_not_auto_release_past_deadline_with_condition(tmp_path: Path):
    lock = _write(tmp_path, "LOCK.until.gated.txt", owner="test",
                  not_before=_stamp(timedelta(hours=-1)),
                  release_condition="review notes incorporated")
    assert not lock_utils.is_expired(lock)


def test_explicit_all_behaves_like_default(tmp_path: Path):
    lock = _write(tmp_path, "LOCK.until.gated2.txt", owner="test",
                  not_before=_stamp(timedelta(hours=-1)),
                  release_condition="x", release_mode="all")
    assert not lock_utils.is_expired(lock)


def test_any_releases_on_deadline_alone(tmp_path: Path):
    lock = _write(tmp_path, "LOCK.until.any.txt", owner="test",
                  not_before=_stamp(timedelta(hours=-1)),
                  release_condition="x", release_mode="any")
    assert lock_utils.is_expired(lock)


def test_any_still_holds_before_the_deadline(tmp_path: Path):
    lock = _write(tmp_path, "LOCK.until.anyfuture.txt", owner="test",
                  not_before=_stamp(timedelta(days=1)),
                  release_condition="x", release_mode="any")
    assert not lock_utils.is_expired(lock)


def test_invalid_release_mode_falls_back_to_all(tmp_path: Path):
    lock = _write(tmp_path, "LOCK.until.badmode.txt", owner="test",
                  not_before=_stamp(timedelta(hours=-1)),
                  release_condition="x", release_mode="whenever")
    assert not lock_utils.is_expired(lock)


def test_release_mode_is_case_insensitive(tmp_path: Path):
    lock = _write(tmp_path, "LOCK.until.anycase.txt", owner="test",
                  not_before=_stamp(timedelta(hours=-1)),
                  release_condition="x", release_mode="ANY")
    assert lock_utils.is_expired(lock)
