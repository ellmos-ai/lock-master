"""Fencing tokens: a holder that stalled past its TTL must not write on.

The failure this covers is the standard criticism of TTL locks without fencing
(Kleppmann, "How to do distributed locking"), and it is silent: holder A pauses
longer than its lease (GC, swap, standby, a hung cloud-sync call), the lock
expires, prune clears it or holder B takes it, and A wakes up and keeps writing
into the same area. Both agents report success. Expiry alone cannot catch this,
because A never re-reads anything.

The grant number closes it: A records the number it acquired with, and every
write re-checks it. A number that moved on means the lease did too.

Ticket T-20260920-692115839.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import lock_create
import lock_utils
import prune_stale_locks

REPO_ROOT = Path(__file__).resolve().parents[1]


def _acquire(folder: Path, owner: str, expires: str = "24h", scope: str = "work") -> int:
    """Acquire through the real CLI path and return the granted fence."""
    rc = lock_create.main([
        str(folder), "--scope", scope, "--owner", owner,
        "--expires", expires, "--no-contest",
    ])
    assert rc == 0, f"acquisition failed with exit {rc}"
    return lock_utils.lock_fence(folder / f"LOCK.{scope}.txt")


def _age(lock: Path, hours: float) -> None:
    """Backdate 'created' so the lease looks that many hours old."""
    stamp = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S")
    text = "\n".join(
        f"created: {stamp}" if line.lower().startswith("created:") else line
        for line in lock.read_text(encoding="utf-8").splitlines()
    )
    lock.write_text(text + "\n", encoding="utf-8")


# --- the ticket scenario ---------------------------------------------------

def test_displaced_holder_is_refused(tmp_path):
    """A loses the lease by TTL, B acquires, A tries to write on."""
    lock = tmp_path / "LOCK.work.txt"

    fence_a = _acquire(tmp_path, "agent-A", expires="1h")
    assert lock_utils.fence_status(lock, fence_a)[0] == lock_utils.FENCE_HELD

    # A stalls past its lease.
    _age(lock, hours=2)
    assert lock_utils.is_expired(lock), "precondition: the lease must have run out"

    # prune clears the stale lock -- exactly the transition from the ticket.
    removed = prune_stale_locks.prune({"roots": [{"path": str(tmp_path)}]}, dry_run=False)
    assert removed == 1, "precondition: prune must have removed the stale lock"
    assert not lock.exists()

    # B takes the area.
    fence_b = _acquire(tmp_path, "agent-B", expires="1h")
    assert fence_b > fence_a, "a new grant must carry a higher number"

    # A wakes up and wants to write. Before fencing it had no way to tell.
    status, reason = lock_utils.fence_status(lock, fence_a)
    assert status == lock_utils.FENCE_LOST, reason
    assert str(fence_b) in reason

    # B, in turn, is still the rightful holder.
    assert lock_utils.fence_status(lock, fence_b)[0] == lock_utils.FENCE_HELD


def test_expired_own_grant_is_refused_before_anyone_takes_over(tmp_path):
    """The window before prune runs: the file is still A's, the lease is not."""
    lock = tmp_path / "LOCK.work.txt"
    fence_a = _acquire(tmp_path, "agent-A", expires="1h")
    _age(lock, hours=2)

    status, reason = lock_utils.fence_status(lock, fence_a)
    assert status == lock_utils.FENCE_LOST, "an expired own grant is not a held grant"
    assert "expired" in reason


def test_vanished_lock_is_refused(tmp_path):
    """Lock deleted by hand or by a sweeper: no file, no grant."""
    lock = tmp_path / "LOCK.work.txt"
    fence_a = _acquire(tmp_path, "agent-A")
    lock.unlink()
    assert lock_utils.fence_status(lock, fence_a)[0] == lock_utils.FENCE_LOST


# --- backwards compatibility ----------------------------------------------

def test_lock_without_fence_stays_valid(tmp_path):
    """Locks already in the field carry no number and must keep working."""
    lock = tmp_path / "LOCK.traffic.txt"
    stamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    lock.write_text(
        f"owner: claude-code@ASUS-GEI\ncreated: {stamp}\nhost: ASUS-GEI\n"
        "expires_after: 24h\nmode: hard\nscope: traffic\n",
        encoding="utf-8",
    )

    assert lock_utils.lock_fence(lock) is None, "absent is not fence 0"
    assert not lock_utils.is_expired(lock)
    assert lock_utils.active_locks(tmp_path) == [("LOCK.traffic.txt", "traffic", False)]

    # A pre-fencing holder recorded no number -> the old, unchecked behaviour.
    status, _ = lock_utils.fence_status(lock, None)
    assert status == lock_utils.FENCE_UNKNOWN


def test_new_reader_handles_old_file_and_old_reader_handles_new_file(tmp_path):
    """Both directions of the mixed-version case."""
    stamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    old_file = tmp_path / "LOCK.old.txt"
    old_file.write_text(f"owner: a\ncreated: {stamp}\nexpires_after: 24h\n", encoding="utf-8")
    new_file = tmp_path / "LOCK.new.txt"
    new_file.write_text(
        f"owner: b\ncreated: {stamp}\nfence: {lock_utils.new_fence()}\nexpires_after: 24h\n",
        encoding="utf-8",
    )

    # New reader, old file: no crash, no bogus expiry, no invented number.
    assert lock_utils.lock_fence(old_file) is None
    assert not lock_utils.is_expired(old_file)

    # Old reader, new file: 'fence' is just one more key: value line. Every
    # pre-fencing reader parses it the same way and ignores what it does not
    # know -- that is what keeps the format additive.
    data = lock_utils.parse_lock_file(new_file)
    assert data["owner"] == "b"
    assert not lock_utils.is_expired(new_file)
    assert len(lock_utils.active_locks(tmp_path)) == 2


def test_lock_without_fence_is_refused_once_a_fence_was_recorded(tmp_path):
    """Fail-closed: a holder WITH a number meeting a file WITHOUT one."""
    lock = tmp_path / "LOCK.work.txt"
    fence_a = _acquire(tmp_path, "agent-A")
    lock.write_text("owner: someone-else\ncreated: 2026-09-20T10:00\n", encoding="utf-8")
    status, reason = lock_utils.fence_status(lock, fence_a)
    assert status == lock_utils.FENCE_LOST
    assert "no fence" in reason


# --- monotonicity ----------------------------------------------------------

def test_fence_only_goes_up(tmp_path):
    """Successive grants for the same area, each one higher than the last."""
    seen = []
    for owner in ("A", "B", "C"):
        fence = _acquire(tmp_path, owner)
        seen.append(fence)
        (tmp_path / "LOCK.work.txt").unlink()
    assert seen == sorted(seen) and len(set(seen)) == 3, seen


def test_force_overwrite_counts_as_a_new_grant(tmp_path):
    """--force replaces the holder, so the old holder must fall out."""
    lock = tmp_path / "LOCK.work.txt"
    fence_a = _acquire(tmp_path, "agent-A")
    lock_create.main([str(tmp_path), "--scope", "work", "--owner", "agent-B",
                      "--no-contest", "--force"])
    fence_b = lock_utils.lock_fence(lock)
    assert fence_b > fence_a
    assert lock_utils.fence_status(lock, fence_a)[0] == lock_utils.FENCE_LOST


def test_same_clock_tick_still_yields_a_higher_number():
    """A coarse clock must not hand out the same number twice.

    Windows ticks at roughly 15.6 ms, so two grants a few lines apart read the
    same microsecond value -- CI on windows-latest 3.11/3.12 produced exactly
    that, and the displaced holder would have read "held". Pinning `now` here
    reproduces it on every platform instead of leaving it to the scheduler.
    """
    frozen = datetime(2026, 9, 20, 12, 0, 0)
    saved = lock_utils._last_fence
    try:
        lock_utils._last_fence = 0
        first = lock_utils.new_fence(now=frozen)
        second = lock_utils.new_fence(now=frozen)
        assert second > first, "same tick must still move the number"

        # Across processes the in-process floor is gone, so a takeover steps
        # past the number it displaces -- that is what `previous` is for.
        lock_utils._last_fence = 0
        assert lock_utils.new_fence(previous=10**18, now=frozen) > 10**18

        # The floor only ever nudges: a clock that IS ahead still sets the
        # value, so numbers stay comparable between hosts.
        lock_utils._last_fence = 0
        ahead = datetime(2099, 1, 1)
        assert lock_utils.new_fence(now=ahead) == int(ahead.timestamp() * 1_000_000)
    finally:
        lock_utils._last_fence = saved


# --- CLI -------------------------------------------------------------------

def test_verify_fence_cli_exit_codes(tmp_path):
    """lock_scan --verify-fence: 0 = still held, 1 = lost."""
    lock = tmp_path / "LOCK.work.txt"
    fence_a = _acquire(tmp_path, "agent-A")

    def verify(fence: int) -> int:
        return subprocess.run(
            [sys.executable, str(REPO_ROOT / "lock_scan.py"),
             "--verify-fence", str(lock), "--fence", str(fence)],
            capture_output=True, text=True, cwd=str(REPO_ROOT),
        ).returncode

    assert verify(fence_a) == 0
    assert verify(fence_a - 1) == 1
