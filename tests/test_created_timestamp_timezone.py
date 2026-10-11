"""Timezone-aware created/expiry contract across lock-master readers and writers."""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pure-locking"))
sys.path.insert(0, str(ROOT / "pure-locking" / "watcher"))
sys.path.insert(0, str(ROOT / "reference"))

import bulk_lock  # noqa: E402
import lock_create  # noqa: E402
import lock_push_guard  # noqa: E402
import lock_scan  # noqa: E402
import lock_status  # noqa: E402
import lock_utils  # noqa: E402
import prune_stale_locks  # noqa: E402
import cache_writer  # noqa: E402
import scanner  # noqa: E402
import web_server  # noqa: E402

UTC = timezone.utc


def _write_lock(path: Path, *, created: str | None = None,
                expires: str = "1h", extra: str = "") -> Path:
    lines = ["owner: timezone-test"]
    if created is not None:
        lines.append(f"created: {created}")
    lines.extend([f"expires_after: {expires}", "mode: hard", "fence: 1000"])
    if extra:
        lines.extend(extra.strip().splitlines())
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _args() -> argparse.Namespace:
    return argparse.Namespace(
        owner="timezone-test", host="TESTHOST", fence=1000, user=False,
        condition=False, expires="24h", release_condition="", mode="hard",
        purpose="test", scope="docs", team=False,
    )


@pytest.fixture
def berlin_timezone():
    if not hasattr(time, "tzset"):
        pytest.skip("host has no time.tzset; historical local-zone tests need it")
    previous = os.environ.get("TZ")
    os.environ["TZ"] = "Europe/Berlin"
    time.tzset()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time.tzset()


def test_utc_z_offsets_and_cest_resolve_to_the_same_instant():
    expected = datetime(2026, 10, 26, 10, 0, tzinfo=UTC)
    values = [
        "2026-10-26T10:00:00Z",
        "2026-10-26T10:00:00+00:00",
        "2026-10-26T12:00:00+02:00",
    ]
    parsed = [lock_utils._parse_created(value) for value in values]
    assert parsed == [expected, expected, expected]
    assert all(value.tzinfo is UTC for value in parsed)
    assert lock_utils.format_utc_iso(parsed[2]) == "2026-10-26T10:00:00Z"


def test_relative_expiry_is_elapsed_utc_and_scan_status_agree(tmp_path, monkeypatch):
    lock_path = _write_lock(
        tmp_path / "LOCK.timezone.txt", created="2026-10-26T12:00:00+02:00"
    )
    before = datetime(2026, 10, 26, 10, 59, tzinfo=UTC)
    after = datetime(2026, 10, 26, 11, 1, tzinfo=UTC)
    assert not lock_utils.is_expired(lock_path, before)
    assert lock_utils.is_expired(lock_path, after)

    monkeypatch.setattr(lock_scan, "iter_lock_dirs", lambda _config: [tmp_path])
    rows = lock_scan.collect_locks({}, before)
    assert len(rows) == 1
    assert rows[0]["created"] == "2026-10-26T10:00Z"
    assert rows[0]["expires_at"] == "2026-10-26T11:00Z"
    assert rows[0]["created_source"] == "header"
    status = lock_status.check_project_status(tmp_path, before)
    assert status[0]["created"] == rows[0]["created"]
    assert status[0]["remaining"] == "0h01m"
    assert lock_scan.collect_locks({}, after) == []
    assert lock_status.check_project_status(tmp_path, after) == []


def test_naive_legacy_uses_historical_reader_zone_and_rejects_dst_gaps(berlin_timezone):
    winter = lock_utils._parse_created("2026-01-15T12:00:00")
    summer = lock_utils._parse_created("2026-07-15T12:00:00")
    assert winter == datetime(2026, 1, 15, 11, 0, tzinfo=UTC)
    assert summer == datetime(2026, 7, 15, 10, 0, tzinfo=UTC)
    assert lock_utils._parse_created("2026-03-29T02:30:00") is None
    assert lock_utils._parse_created("2026-10-25T02:30:00") is None
    assert lock_push_guard._created_utc("2026-03-29T02:30:00") is None
    assert lock_push_guard._created_utc("2026-10-25T02:30:00") is None


def test_present_invalid_created_does_not_fall_back_to_mtime(tmp_path, monkeypatch):
    invalid = _write_lock(tmp_path / "LOCK.timezone.txt", created="bad timestamp")
    old = (datetime.now(UTC) - timedelta(days=3)).timestamp()
    os.utime(invalid, (old, old))
    created, _expires, source = lock_utils.lock_created_and_expiry(invalid)
    assert source == "invalid"
    assert created.tzinfo is UTC  # diagnostic mtime only
    assert not lock_utils.is_expired(invalid)
    assert lock_utils.compute_expires_at(invalid) is None

    monkeypatch.setattr(lock_scan, "iter_lock_dirs", lambda _config: [tmp_path])
    row = lock_scan.collect_locks({})[0]
    assert row["created_source"] == "invalid"
    assert row["created"] is None
    assert row["expires_at"] == ""
    assert "fail-closed" in row["remaining"]
    status = lock_status.check_project_status(tmp_path)[0]
    assert status["created_source"] == "invalid"
    assert status["created"] is None
    assert "fail-closed" in status["remaining"]


def test_missing_created_retains_mtime_fallback(tmp_path):
    lock_path = _write_lock(tmp_path / "LOCK.timezone.txt", created=None)
    old = (datetime.now(UTC) - timedelta(days=3)).timestamp()
    os.utime(lock_path, (old, old))
    _created, _expires, source = lock_utils.lock_created_and_expiry(lock_path)
    assert source == "mtime"
    assert lock_utils.is_expired(lock_path)


def test_prune_keeps_present_invalid_created_and_removes_expired_valid(tmp_path, monkeypatch):
    invalid = _write_lock(tmp_path / "LOCK.invalid.txt", created="invalid")
    expired = _write_lock(
        tmp_path / "LOCK.expired.txt",
        created=(datetime.now(UTC) - timedelta(days=2)).isoformat().replace("+00:00", "Z"),
        expires="1h",
    )
    monkeypatch.setattr(prune_stale_locks, "iter_lock_dirs", lambda _config: [tmp_path])
    removed = prune_stale_locks.prune({}, dry_run=False)
    assert removed == 1
    assert invalid.exists()
    assert not expired.exists()


def test_until_condition_and_user_lock_contracts_remain_separate(tmp_path):
    before = datetime(2026, 10, 26, 9, 59, tzinfo=UTC)
    after = datetime(2026, 10, 26, 10, 1, tzinfo=UTC)
    until = _write_lock(
        tmp_path / "LOCK.until.release.txt",
        created="bad-but-irrelevant-for-until",
        extra="not_before: 2026-10-26T12:00:00+02:00",
    )
    assert not lock_utils.is_expired(until, before)
    assert lock_utils.is_expired(until, after)

    all_condition = _write_lock(
        tmp_path / "LOCK.until.all.txt",
        created="bad-but-irrelevant-for-until",
        extra=("not_before: 2026-10-26T12:00:00+02:00\n"
               "release_condition: evidence required\nrelease_mode: all"),
    )
    any_condition = _write_lock(
        tmp_path / "LOCK.until.any.txt",
        created="bad-but-irrelevant-for-until",
        extra=("not_before: 2026-10-26T12:00:00+02:00\n"
               "release_condition: evidence required\nrelease_mode: any"),
    )
    assert not lock_utils.is_expired(all_condition, after)
    assert lock_utils.is_expired(any_condition, after)

    user_lock = _write_lock(
        tmp_path / "LOCK.user.txt",
        created="2020-01-01T00:00:00Z",
        expires="1s",
    )
    condition_lock = _write_lock(
        tmp_path / "LOCK.condition.review.txt",
        created="2020-01-01T00:00:00Z",
        expires="1s",
    )
    far_future = datetime(2030, 1, 1, tzinfo=UTC)
    assert not lock_utils.is_expired(user_lock, far_future)
    assert not lock_utils.is_expired(condition_lock, far_future)


def test_lock_create_bulk_and_web_writers_emit_utc_z(tmp_path, monkeypatch):
    body = lock_create.build_lock_body(_args(), "docs")
    created = next(line.split(": ", 1)[1] for line in body.splitlines()
                   if line.startswith("created: "))
    assert created.endswith("Z")
    assert lock_utils._parse_created(created) is not None
    assert bulk_lock._now_iso().endswith("Z")

    root = tmp_path / "bulk-root"
    root.mkdir()
    manifest = tmp_path / "bulk-session.json"
    bulk_lock.bulk_lock(
        [root], owner="timezone-test", reason="test",
        created="2026-10-26T12:00:00+02:00", commit=True,
        manifest_path=manifest,
    )
    bulk_created = lock_utils.parse_lock_file(root / "LOCK.txt")["created"]
    assert bulk_created == "2026-10-26T10:00:00Z"

    handler_class = next(
        value for value in vars(web_server).values()
        if isinstance(value, type) and "_create_lock" in value.__dict__
    )
    handler = object.__new__(handler_class)
    responses = []
    errors = []
    handler._json_response = lambda payload, status=200: responses.append((payload, status))
    handler._json_error = lambda status, message: errors.append((status, message))
    monkeypatch.setattr(web_server.rooms, "is_path_in_roots", lambda _path: True)
    monkeypatch.setattr(web_server, "_load_user_profile", lambda: {})
    handler._create_lock({"project_dir": str(tmp_path), "scope": "docs", "owner": "test"})
    handler._create_user_lock({"project_dir": str(tmp_path), "scope": "review", "owner": "test"})
    assert not errors
    for name in ("LOCK.docs.txt", "LOCK.user.review.txt"):
        web_created = lock_utils.parse_lock_file(tmp_path / name)["created"]
        assert web_created.endswith("Z")
        assert lock_utils._parse_created(web_created) is not None


def test_watcher_serialization_cache_and_api_keep_offset_information(tmp_path):
    lock_path = _write_lock(
        tmp_path / "LOCK.timezone.txt", created="2026-10-26T12:00:00+02:00"
    )
    record = scanner.lock_to_record(lock_path, lock_path.name, "timezone", False)
    assert record["created_at"] == "2026-10-26T10:00:00Z"
    assert record["expires_at"] == "2026-10-26T11:00:00Z"
    scan_record = cache_writer._db_lock_to_scan_format(record)
    assert scan_record["created"] == "2026-10-26T10:00Z"
    assert scan_record["expires_at"] == "2026-10-26T11:00Z"
    assert cache_writer._format_remaining("2099-01-01T12:00:00Z") == "2099-01-01T12:00Z"
    assert cache_writer._format_remaining("not a timestamp") == "?"

    handler_class = next(
        value for value in vars(web_server).values()
        if isinstance(value, type) and "_calc_remaining" in value.__dict__
    )
    assert handler_class._calc_remaining("2099-01-01T12:00:00Z") is not None
    assert handler_class._calc_remaining("2099-01-01T14:00:00+02:00") is not None
    assert handler_class._calc_remaining("not a timestamp") is None


def test_push_guard_uses_offsets_and_invalid_present_created_fails_closed(tmp_path, berlin_timezone):
    now = datetime.now(UTC)
    fresh = _write_lock(
        tmp_path / "LOCK.push.txt",
        created=(now - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        expires="24h",
    )
    data = lock_push_guard._parse_lock_text(fresh.read_text(encoding="utf-8"))
    assert not lock_push_guard._lock_expired(data, fresh)

    offset_value = (now - timedelta(hours=1)).astimezone(
        __import__("datetime").timezone(timedelta(hours=2))
    ).isoformat()
    assert lock_push_guard._created_utc(offset_value) == now - timedelta(hours=1)

    invalid = _write_lock(
        tmp_path / "LOCK.push-invalid.txt", created="not-a-date", expires="24h"
    )
    future = (now + timedelta(days=2)).timestamp()
    os.utime(invalid, (future, future))
    invalid_data = lock_push_guard._parse_lock_text(invalid.read_text(encoding="utf-8"))
    assert lock_push_guard._lock_expired(invalid_data, invalid)

    missing = _write_lock(tmp_path / "LOCK.push-missing.txt", created=None, expires="1h")
    old = (now - timedelta(days=2)).timestamp()
    os.utime(missing, (old, old))
    missing_data = lock_push_guard._parse_lock_text(missing.read_text(encoding="utf-8"))
    assert lock_push_guard._lock_expired(missing_data, missing)
