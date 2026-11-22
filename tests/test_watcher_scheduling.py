"""Full scans must not run back to back when a scan takes longer than the interval.

LANGLAUF-PROTOKOLL on ASUS-GEI measured full scans of 55-617 s against a 60 s
interval, so the daemon scanned continuously (about 45 % of one core and
3,900 stat calls per second on 2026-10-17). The gap after a scan now scales
with the measured duration.
"""
from __future__ import annotations

import atexit
import importlib
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WATCHER = ROOT / "pure-locking" / "watcher"
if "LOCK_MASTER_WATCHER_DATA" not in os.environ:
    _WATCHER_TMP = tempfile.mkdtemp(prefix="lockmaster-scheduling-")
    atexit.register(shutil.rmtree, _WATCHER_TMP, True)
    os.environ["LOCK_MASTER_WATCHER_DATA"] = _WATCHER_TMP
sys.path.insert(0, str(WATCHER))

import config  # noqa: E402
import lock_watcher  # noqa: E402


def test_gap_never_drops_below_the_interval():
    assert lock_watcher.next_full_scan_gap(5, interval=60, idle_factor=4) == 60
    assert lock_watcher.next_full_scan_gap(0, interval=60, idle_factor=4) == 60
    assert lock_watcher.next_full_scan_gap(-3, interval=60, idle_factor=4) == 60


def test_gap_scales_with_a_slow_scan():
    # 127 s measured on 2026-10-02 -> about 10.5 minutes of pause, i.e. < 20 % scan share.
    assert lock_watcher.next_full_scan_gap(127, interval=60, idle_factor=4) == 508
    # uncapped here; the default cap (full_scan_max_gap_seconds) is covered in test_watcher_scan_protection
    assert lock_watcher.next_full_scan_gap(617, interval=60, idle_factor=4, max_gap=1e9) == 2468


def test_zero_factor_restores_the_fixed_interval():
    assert lock_watcher.next_full_scan_gap(617, interval=60, idle_factor=0) == 60


def test_defaults_come_from_config_and_env_override(monkeypatch):
    assert config.FULL_SCAN_IDLE_FACTOR == 4
    assert lock_watcher.next_full_scan_gap(100) == max(config.FULL_SCAN_INTERVAL, 400)
    monkeypatch.setenv("LOCK_MASTER_FULL_SCAN_IDLE_FACTOR", "2.5")
    try:
        reloaded = importlib.reload(config)
        assert reloaded.FULL_SCAN_IDLE_FACTOR == 2.5
        monkeypatch.setenv("LOCK_MASTER_FULL_SCAN_IDLE_FACTOR", "-1")
        assert importlib.reload(config).FULL_SCAN_IDLE_FACTOR == 0
    finally:
        monkeypatch.delenv("LOCK_MASTER_FULL_SCAN_IDLE_FACTOR", raising=False)
        importlib.reload(config)


def test_unusable_env_values_fall_back_to_the_default():
    parse = config._idle_factor_from_env
    assert parse(None) == 4 and parse("") == 4 and parse("abc") == 4
    assert parse("inf") == 4 and parse("nan") == 4
    assert parse("-2") == 0 and parse("2.5") == 2.5


def test_stale_threshold_follows_the_announced_gap():
    base = lock_watcher.SCAN_OVERRUN_WARN_SECONDS
    assert lock_watcher.scan_stale_after_seconds(None) == base + config.FULL_SCAN_INTERVAL
    assert lock_watcher.scan_stale_after_seconds({}) == base + config.FULL_SCAN_INTERVAL
    status = {"next_full_scan_gap_s": 508.0, "last_scan_duration_s": 127.0}
    assert lock_watcher.scan_stale_after_seconds(status) == base + 635.0
    # A healthy daemon in its deliberate pause is not degraded.
    assert 400 < lock_watcher.scan_stale_after_seconds(status)


def test_daemon_status_reports_the_next_gap():
    source = (WATCHER / "lock_watcher.py").read_text(encoding="utf-8")
    assert '"next_full_scan_gap_s"' in source
    assert "now - last_full_scan >= full_scan_gap" in source
    assert "now - last_full_scan >= config.FULL_SCAN_INTERVAL" not in source


def test_runtime_settings_persist_clamp_and_reset(monkeypatch, tmp_path: Path):
    tmp_dir = tmp_path / "runtime-settings"
    tmp_dir.mkdir()
    settings_file = tmp_dir / "runtime_settings.json"
    monkeypatch.setattr(config, "RUNTIME_SETTINGS_PATH", settings_file)
    monkeypatch.setattr(config, "_LOCAL_DATA_DIR", tmp_dir)

    defaults = config.load_runtime_settings()
    assert defaults["heartbeat_interval"] == 5
    assert defaults["check_interval"] == 20
    assert defaults["full_scan_interval"] == 60
    assert defaults["full_scan_idle_factor"] == 4.0
    assert defaults["stale_daemon_seconds"] == 180

    saved = config.save_runtime_settings({
        "heartbeat_interval": 10,
        "check_interval": 1,  # clamped to min 5
        "full_scan_interval": 120,
        "full_scan_idle_factor": 2.5,
        "stale_daemon_seconds": 300,
    })
    assert saved["heartbeat_interval"] == 10
    assert saved["check_interval"] == 5
    assert saved["full_scan_interval"] == 120
    assert saved["full_scan_idle_factor"] == 2.5
    assert saved["stale_daemon_seconds"] == 300
    assert settings_file.is_file()

    # Dynamic lookup in next_full_scan_gap uses runtime_settings when arguments are omitted
    assert lock_watcher.next_full_scan_gap(100) == 250.0

    # Reset removes file and restores defaults
    reset_vals = config.save_runtime_settings({"reset": True})
    assert reset_vals == defaults
    assert not settings_file.exists()
