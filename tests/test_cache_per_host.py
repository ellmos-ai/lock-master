"""Cache files are per host and only rewritten on change.

Several hosts share one synced folder. A cache file with a fixed name was
overwritten by every host every few minutes and the sync service answered each
collision with a conflict copy (~1700 per folder). One file per host plus
write-only-on-change removes the cause."""

from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path

import lock_scan


def _record(path: str = r"C:\ws\p\LOCK.txt") -> dict:
    return {
        "path": path, "scope": "project", "owner": "t",
        "created": "2026-10-04T00:00", "remaining": "23h", "legacy": False,
    }


def test_host_name_is_filename_safe(monkeypatch):
    monkeypatch.setenv("COMPUTERNAME", "My Host/01")
    assert lock_scan.host_name() == "My_Host_01"
    assert lock_scan.host_cache_name() == "LOCK-CACHE.My_Host_01.md"


def test_host_name_falls_back_to_platform_node(monkeypatch):
    monkeypatch.delenv("COMPUTERNAME", raising=False)
    monkeypatch.setattr(lock_scan.platform, "node", lambda: "mac-studio")
    assert lock_scan.host_name() == "mac-studio"


def test_host_name_never_empty(monkeypatch):
    monkeypatch.delenv("COMPUTERNAME", raising=False)
    monkeypatch.setattr(lock_scan.platform, "node", lambda: "")
    assert lock_scan.host_name() == "unknown-host"


def test_placeholders_resolve_on_every_platform(monkeypatch):
    monkeypatch.setenv("COMPUTERNAME", "IDEAPAD-GEI")
    assert lock_scan._expand_path("x/LOCK-CACHE.{host}.md") == "x/LOCK-CACHE.IDEAPAD-GEI.md"
    # %COMPUTERNAME% is resolved by hand, not only where expandvars knows it.
    assert lock_scan._expand_path("x/LOCK-CACHE.%COMPUTERNAME%.md") == "x/LOCK-CACHE.IDEAPAD-GEI.md"
    assert lock_scan._expand_path("x/LOCK-CACHE.%computername%.md") == "x/LOCK-CACHE.IDEAPAD-GEI.md"


def test_two_hosts_write_two_files(tmp_path: Path, monkeypatch):
    cfg = {"caches": [{"name": "all", "path": str(tmp_path / "LOCK-CACHE.%COMPUTERNAME%.md")}]}
    for host in ("HOST-A", "HOST-B"):
        monkeypatch.setenv("COMPUTERNAME", host)
        cfg_h = {"caches": [dict(e, path=lock_scan._expand_path(e["path"])) for e in cfg["caches"]]}
        lock_scan.write_caches([_record()], datetime.now(), cfg_h)
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "LOCK-CACHE.HOST-A.md", "LOCK-CACHE.HOST-B.md",
    ]


def test_unchanged_content_is_not_rewritten(tmp_path: Path):
    target = tmp_path / "c.md"
    cfg = {"caches": [{"name": "all", "path": str(target)}]}
    lock_scan.write_caches([_record()], datetime(2026, 10, 4, 10, 0), cfg)
    old = (time.time() - 600)
    os.utime(target, (old, old))
    before = target.stat().st_mtime

    # Only the timestamp line differs -> no write.
    lock_scan.write_caches([_record()], datetime(2026, 10, 4, 10, 5), cfg)
    assert target.stat().st_mtime == before
    assert "10:00" in target.read_text(encoding="utf-8")


def test_changed_content_is_rewritten(tmp_path: Path):
    target = tmp_path / "c.md"
    cfg = {"caches": [{"name": "all", "path": str(target)}]}
    lock_scan.write_caches([_record()], datetime(2026, 10, 4, 10, 0), cfg)
    lock_scan.write_caches([_record(), _record(r"C:\ws\q\LOCK.txt")], datetime(2026, 10, 4, 10, 5), cfg)
    assert "Active locks: 2" in target.read_text(encoding="utf-8")


def test_stale_unchanged_file_is_refreshed(tmp_path: Path):
    target = tmp_path / "c.md"
    cfg = {"caches": [{"name": "all", "path": str(target)}]}
    lock_scan.write_caches([_record()], datetime(2026, 10, 4, 10, 0), cfg)
    old = time.time() - lock_scan.CACHE_REFRESH_SECONDS - 60
    os.utime(target, (old, old))
    lock_scan.write_caches([_record()], datetime(2026, 10, 4, 11, 0), cfg)
    assert "11:00" in target.read_text(encoding="utf-8")
