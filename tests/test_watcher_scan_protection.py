"""Protection against runaway full scans.

A full scan took 5608 s on one host (about 60 s expected). With the idle factor 4 the next scan
started only after about six hours. Now: a time budget per root, a cap on the pause after a
scan, and a visible warning (daemon status, API state, UI text).
"""
from __future__ import annotations

import atexit
import shutil
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if "LOCK_MASTER_WATCHER_DATA" not in os.environ:
    _WATCHER_TMP = tempfile.mkdtemp(prefix="lockmaster-scan-protection-")
    atexit.register(shutil.rmtree, _WATCHER_TMP, True)
    os.environ["LOCK_MASTER_WATCHER_DATA"] = _WATCHER_TMP
sys.path.insert(0, str(ROOT / "pure-locking" / "watcher"))

import config  # noqa: E402
import dir_stats  # noqa: E402
import lock_scan  # noqa: E402
import lock_utils  # noqa: E402
import lock_watcher  # noqa: E402
import scanner  # noqa: E402
import web_server  # noqa: E402

RT = {
    "full_scan_interval": 60,
    "full_scan_idle_factor": 4.0,
    "full_scan_max_gap_seconds": 1800,
    "scan_root_budget_seconds": 300,
}


def _young_lock(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "LOCK.txt"
    path.write_text(f"owner: test\ncreated: {lock_utils.created_utc_iso()}\nexpires_after: 24h\n", encoding="utf-8")
    return path


# --- cap on the pause ----------------------------------------------------------------------


def test_the_gap_is_capped_for_an_extremely_slow_scan():
    # 5608 s measured * factor 4 = 22432 s without a cap
    assert lock_watcher.next_full_scan_gap(5608, interval=60, idle_factor=4, max_gap=1800) == 1800
    assert lock_watcher.next_full_scan_gap(300, interval=60, idle_factor=4, max_gap=1800) == 1200
    assert lock_watcher.next_full_scan_gap(5, interval=60, idle_factor=4, max_gap=1800) == 60


def test_the_interval_wins_over_a_smaller_cap():
    assert lock_watcher.next_full_scan_gap(5608, interval=600, idle_factor=4, max_gap=60) == 600


def test_the_default_cap_comes_from_the_runtime_settings(monkeypatch):
    monkeypatch.setattr(config, "load_runtime_settings", lambda: dict(config.default_runtime_settings()))
    assert config.default_runtime_settings()["full_scan_max_gap_seconds"] == config.FULL_SCAN_MAX_GAP_SECONDS
    assert lock_watcher.next_full_scan_gap(5608) == config.FULL_SCAN_MAX_GAP_SECONDS


def test_new_runtime_settings_are_clamped(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "RUNTIME_SETTINGS_PATH", tmp_path / "runtime_settings.json")
    monkeypatch.setattr(config, "_LOCAL_DATA_DIR", tmp_path)
    saved = config.save_runtime_settings({"scan_root_budget_seconds": 1, "full_scan_max_gap_seconds": 10**9})
    assert saved["scan_root_budget_seconds"] == 10
    assert saved["full_scan_max_gap_seconds"] == 86400


# --- time budget per root --------------------------------------------------------------------


def _config(*roots: Path) -> dict:
    return {"default_max_depth": 4, "shallow_depth": 2, "skip_dirs": [], "roots": [{"path": str(r)} for r in roots]}


def test_every_root_is_reported_and_locks_are_found(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    lock = _young_lock(a / "proj")
    b.mkdir()
    report: dict = {}
    found = scanner.full_scan(_config(a, b), root_budget_s=0, report=report)
    assert [r["path"] for r in found] == [str(lock)]
    assert [(r["path"], r["timed_out"]) for r in report["roots"]] == [(str(a), False), (str(b), False)]
    assert report["roots"][0]["dirs"] >= 2 and report["roots"][0]["seconds"] >= 0


def test_a_root_over_budget_is_cut_off_and_the_others_still_run(monkeypatch, tmp_path):
    slow, fast = tmp_path / "slow", tmp_path / "fast"
    for i in range(5):
        (slow / f"d{i}").mkdir(parents=True)
    fast_lock = _young_lock(fast / "proj")

    real_iter = lock_scan.iter_lock_dirs

    def slow_iter(cfg):
        for directory in real_iter(cfg):
            if Path(cfg["roots"][0]["path"]) == slow:
                time.sleep(0.05)  # every directory of the slow root costs 50 ms
            yield directory

    monkeypatch.setattr(lock_scan, "iter_lock_dirs", slow_iter)
    report: dict = {}
    found = scanner.full_scan(_config(slow, fast), root_budget_s=0.12, report=report)

    by_path = {r["path"]: r for r in report["roots"]}
    assert by_path[str(slow)]["timed_out"] is True and by_path[str(slow)]["dirs"] < 6
    assert by_path[str(fast)]["timed_out"] is False
    assert [r["path"] for r in found] == [str(fast_lock)]


def test_the_budget_defaults_to_the_runtime_setting(monkeypatch, tmp_path):
    root = tmp_path / "r"
    for i in range(4):
        (root / f"d{i}").mkdir(parents=True)
    real_iter = lock_scan.iter_lock_dirs

    def slow_iter(cfg):
        for directory in real_iter(cfg):
            time.sleep(0.05)
            yield directory

    monkeypatch.setattr(lock_scan, "iter_lock_dirs", slow_iter)
    monkeypatch.setattr(config, "load_runtime_settings",
                        lambda: {**config.default_runtime_settings(), "scan_root_budget_seconds": 0.08})
    report: dict = {}
    scanner.full_scan(_config(root), report=report)  # no explicit budget: the runtime setting applies
    assert report["roots"][0]["timed_out"] is True


def test_a_cut_off_scan_never_deletes_a_known_lock_without_looking_at_the_file(monkeypatch, tmp_path):
    """_run_full_scan re-checks every known lock missing from the scan before calling it deleted."""
    import storage

    root = tmp_path / "root"
    lock = _young_lock(root / "proj")
    db = storage.LockDB(tmp_path / "w.db")
    try:
        cfg = _config(root)
        lock_watcher._run_full_scan(db, cfg, update_cache=False)
        assert str(lock) in {a["path"] for a in db.get_active_locks()}

        monkeypatch.setattr(config, "load_runtime_settings",
                            lambda: {**config.default_runtime_settings(), "scan_root_budget_seconds": 10})
        monkeypatch.setattr(scanner, "full_scan", lambda c, report=None: report.update(roots=[
            {"path": str(root), "dirs": 0, "seconds": 11.0, "timed_out": True}]) or [])
        stats = lock_watcher._run_full_scan(db, cfg, update_cache=False)
        assert stats["deleted"] == 0 and stats["roots"][0]["timed_out"] is True
        assert str(lock) in {a["path"] for a in db.get_active_locks()}
    finally:
        db.close()


# --- visible warning ---------------------------------------------------------------------------


def test_a_clean_scan_has_no_warning():
    stats = {"roots": [{"path": "/r", "dirs": 3, "seconds": 2.0, "timed_out": False}]}
    assert lock_watcher.scan_warnings(stats, 2.0, 60.0, RT) == []


def test_warnings_name_the_cut_off_root_the_slow_roots_and_the_capped_pause():
    stats = {"roots": [
        {"path": "/slow", "dirs": 10, "seconds": 301.0, "timed_out": True},
        {"path": "/other", "dirs": 10, "seconds": 5.0, "timed_out": False},
    ]}
    warnings = lock_watcher.scan_warnings(stats, 5608.0, 1800.0, RT)
    text = "\n".join(warnings)
    assert "/slow" in text and "abgebrochen" in text and "unvollstaendig" in text
    assert "5608s" in text and "langsamste Roots" in text and text.index("/slow") < text.index("/other")
    assert "Pause auf 1800s begrenzt" in text


def _status(**extra):
    now = datetime.now().isoformat(timespec="seconds")
    return {"pid": os.getpid(), "host": "h", "last_seen": now, "scan_in_progress": False, **extra}


def test_a_partial_scan_makes_the_daemon_degraded_despite_a_fresh_heartbeat(monkeypatch):
    monkeypatch.setattr(lock_watcher, "_daemon_status_is_fresh", lambda _s: True)
    monkeypatch.setattr(lock_watcher, "load_daemon_status",
                        lambda: _status(scan_partial=True, scan_warnings=["Root /slow abgebrochen"]))
    api = web_server._daemon_status_for_api(None)
    assert api["state"] == "degraded" and api["scan_partial"] is True
    assert api["scan_warnings"] == ["Root /slow abgebrochen"]


def test_a_slow_but_complete_scan_warns_without_degrading(monkeypatch):
    monkeypatch.setattr(lock_watcher, "_daemon_status_is_fresh", lambda _s: True)
    monkeypatch.setattr(lock_watcher, "load_daemon_status",
                        lambda: _status(scan_partial=False, scan_warnings=["Full-Scan dauerte 400s"]))
    api = web_server._daemon_status_for_api(None)
    assert api["state"] == "running" and api["scan_warnings"] == ["Full-Scan dauerte 400s"]


def test_old_status_files_without_the_new_fields_still_work(monkeypatch):
    monkeypatch.setattr(lock_watcher, "_daemon_status_is_fresh", lambda _s: True)
    monkeypatch.setattr(lock_watcher, "load_daemon_status", lambda: _status(
        last_scan_finished_at=(datetime.now() - timedelta(seconds=5)).isoformat(timespec="seconds")))
    api = web_server._daemon_status_for_api(None)
    assert api["state"] == "running" and api["scan_partial"] is False and api["scan_warnings"] == []


def test_the_settings_endpoint_exposes_the_new_knobs():
    source = (ROOT / "pure-locking" / "watcher" / "web_server.py").read_text(encoding="utf-8")
    assert '"scan_root_budget_seconds": rt["scan_root_budget_seconds"]' in source
    assert '"full_scan_max_gap_seconds": rt["full_scan_max_gap_seconds"]' in source


# --- pruning and fast lock discovery -----------------------------------------------------------


def test_per_root_max_depth_limits_clone_trees_without_shrinking_shallow_roots(tmp_path):
    repos_root = tmp_path / "repos"
    topics_root = tmp_path / "topics"
    repo_lock = _young_lock(repos_root / "repo-a")
    _young_lock(repos_root / "repo-a" / "nested")  # depth 2: skipped when max_depth=1
    topic_lock = _young_lock(topics_root / "cat" / "proj")  # depth 2: kept by shallow_depth=2

    cfg = {
        "default_max_depth": 4,
        "shallow_depth": 2,
        "skip_dirs": [],
        "roots": [
            {"path": str(repos_root), "shallow": True, "max_depth": 1},
            {"path": str(topics_root), "shallow": True},
        ],
    }
    report: dict = {}
    found = scanner.full_scan(cfg, root_budget_s=0, report=report)
    assert {r["path"] for r in found} == {str(repo_lock), str(topic_lock)}
    by_path = {r["path"]: r for r in report["roots"]}
    assert by_path[str(repos_root)]["dirs"] == 2  # root + repo-a only


def test_per_root_max_depth_zero_and_invalid_values_behave_conservatively(tmp_path):
    root = tmp_path / "r"
    top_lock = _young_lock(root)
    sub_lock = _young_lock(root / "sub")

    cfg_zero = {"default_max_depth": 4, "shallow_depth": 2, "skip_dirs": [], "roots": [{"path": str(root), "max_depth": 0}]}
    assert [r["path"] for r in scanner.full_scan(cfg_zero, root_budget_s=0)] == [str(top_lock)]

    for bad_depth in (-1, True, "1", None):
        cfg_bad = {
            "default_max_depth": 2,
            "shallow_depth": 1,
            "skip_dirs": [],
            "roots": [{"path": str(root), "shallow": True, "max_depth": bad_depth}],
        }
        found = {r["path"] for r in scanner.full_scan(cfg_bad, root_budget_s=0)}
        assert found == {str(top_lock), str(sub_lock)}


def test_skip_dirs_supports_glob_patterns_case_insensitively_and_honors_empty_list(tmp_path):
    root = tmp_path / "r"
    kept_lock = _young_lock(root / "repo" / "src")
    _young_lock(root / "repo" / ".pytest_tmp_run123")
    _young_lock(root / "repo" / "Pkg.EGG-INFO")

    cfg_pruned = {
        "default_max_depth": 4,
        "shallow_depth": 2,
        "skip_dirs": [".pytest_tmp*", "*.egg-info"],
        "roots": [{"path": str(root)}],
    }
    assert [r["path"] for r in scanner.full_scan(cfg_pruned, root_budget_s=0)] == [str(kept_lock)]

    cfg_unpruned = {
        "default_max_depth": 4,
        "shallow_depth": 2,
        "skip_dirs": [],
        "roots": [{"path": str(root)}],
    }
    assert len(scanner.full_scan(cfg_unpruned, root_budget_s=0)) == 3


def test_find_lock_files_scandir_preserves_contract_and_handles_missing_dir(tmp_path):
    assert lock_utils.find_lock_files(tmp_path / "does-not-exist") == []

    (tmp_path / "README.md").write_text("x", encoding="utf-8")
    (tmp_path / "LOCK.dir.txt").mkdir()  # directory with a lock name must not count as a file
    _young_lock(tmp_path)
    (tmp_path / "LOCK.api.txt").write_text("owner: a\n", encoding="utf-8")
    (tmp_path / "TEST.txt").write_text("legacy\n", encoding="utf-8")

    with_legacy = lock_utils.find_lock_files(tmp_path, include_legacy=True)
    assert with_legacy == [
        ("LOCK.api.txt", "api", False),
        ("LOCK.txt", "project", False),
        ("TEST.txt", "project", True),
    ]
    without_legacy = lock_utils.find_lock_files(tmp_path, include_legacy=False)
    assert without_legacy == [
        ("LOCK.api.txt", "api", False),
        ("LOCK.txt", "project", False),
    ]


def test_dir_stats_scan_room_applies_glob_skips_case_insensitively(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print(1)\n", encoding="utf-8")
    (tmp_path / ".pytest_tmp_run1").mkdir()
    (tmp_path / ".pytest_tmp_run1" / "junk.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "Pkg.EGG-INFO").mkdir()
    (tmp_path / "Pkg.EGG-INFO" / "PKG-INFO").write_text("Name: p\n", encoding="utf-8")
    (tmp_path / ".GIT").mkdir()
    (tmp_path / ".GIT" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    stats = dir_stats.scan_room(str(tmp_path), {".git", ".pytest_tmp*", "*.egg-info"})
    assert stats["folder_count"] == 1
    assert stats["file_count"] == 1
    assert stats["type_code"] == 1
