"""Room mapping, room configuration, path validation and the host overlay (names/colors/icons)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("LOCK_MASTER_WATCHER_DATA", tempfile.mkdtemp(prefix="lockmaster-watcher-rooms-"))
sys.path.insert(0, str(ROOT / "pure-locking" / "watcher"))

import config  # noqa: E402
import rooms  # noqa: E402
import web_server  # noqa: E402


def _fake_rooms(base: Path) -> list[dict]:
    items = [
        {"key": "work_research", "abs_path": str(base / "work" / "research")},
        {"key": "work_software", "abs_path": str(base / "work" / "software")},
        {"key": "work", "abs_path": str(base / "work")},
        {"key": "scripts", "abs_path": str(base / "scripts")},
    ]
    return sorted(items, key=lambda r: len(r["abs_path"]), reverse=True)


def test_key_from_relpath():
    assert rooms._key_from_relpath("work") == "work"
    assert rooms._key_from_relpath(".work/.research") == "work_research"
    assert rooms._key_from_relpath(".work\\.ai") == "work_ai"
    assert rooms._key_from_relpath("/work/") == "work"


def test_room_for_path_uses_the_longest_prefix(tmp_path):
    with patch.object(rooms, "_get_rooms", return_value=_fake_rooms(tmp_path)):
        assert rooms.room_for_path(str(tmp_path / "work" / "research" / "p1" / "LOCK.txt")) == "work_research"
        assert rooms.room_for_path(str(tmp_path / "work" / "hardware" / "LOCK.txt")) == "work"
        assert rooms.room_for_path(str(tmp_path / "scripts")) == "scripts"
        assert rooms.room_for_path(str(tmp_path / "elsewhere" / "LOCK.txt")) == "other"


def test_room_for_path_has_no_prefix_confusion(tmp_path):
    with patch.object(rooms, "_get_rooms", return_value=[{"key": "r", "abs_path": str(tmp_path / "root")}]):
        assert rooms.room_for_path(str(tmp_path / "root2" / "LOCK.txt")) == "other"
        assert rooms.is_path_in_roots(str(tmp_path / "root" / "sub" / "LOCK.txt"))
        assert not rooms.is_path_in_roots(str(tmp_path / "root2" / "LOCK.txt"))
        assert not rooms.is_path_in_roots(str(tmp_path / "other" / "LOCK.txt"))


def test_junction_or_symlink_out_of_the_root_is_not_inside(tmp_path):
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    link = root / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        # Windows without symlink rights: a junction needs none.
        import subprocess

        import pytest
        made = os.name == "nt" and subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True
        ).returncode == 0
        if not made:
            pytest.skip("neither symlinks nor junctions available here")
    with patch.object(rooms, "_get_rooms", return_value=[{"key": "r", "abs_path": str(root)}]):
        assert rooms.is_path_in_roots(str(root / "plain"))
        assert not rooms.is_path_in_roots(str(link))
        assert not rooms.is_path_in_roots(str(link / "sub"))


def test_is_path_in_roots_rejects_nul_and_realpath_errors(monkeypatch, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    with patch.object(rooms, "_get_rooms", return_value=[{"key": "r", "abs_path": str(root)}]):
        assert not rooms.is_path_in_roots(str(root / "sub\x00evil"))
        assert not rooms.is_path_in_roots(str(root) + "\x00evil")

        # Simulate POSIX os.path.realpath raising ValueError on NUL bytes (Windows returns it unchanged).
        orig_realpath = os.path.realpath

        def _posix_realpath(path):
            if "\x00" in str(path):
                raise ValueError("embedded null byte")
            return orig_realpath(path)

        monkeypatch.setattr(os.path, "realpath", _posix_realpath)
        assert not rooms.is_path_in_roots(str(root) + "\x00evil")
        assert not rooms.is_path_in_roots(str(root / "sub\x00evil"))

        # realpath of the target works, but the configured root cannot be resolved: not inside.
        def _failing_root_realpath(path):
            if os.path.normcase(str(path)) == os.path.normcase(str(root)):
                raise OSError("cannot resolve root")
            return orig_realpath(path)

        monkeypatch.setattr(os.path, "realpath", _failing_root_realpath)
        assert not rooms.is_path_in_roots(str(root / "sub"))


def test_safe_filename_regex():
    for name in ["USER-NOTES.md", "CLAUDE.md", "my_file.md", "Test 1.md"]:
        assert rooms._SAFE_FILENAME_RE.match(name), name
    for name in ["../evil.md", "..\\evil.md", "/etc/passwd.md", "LOCK\x00.md"]:
        assert not rooms._SAFE_FILENAME_RE.match(name), name


def _use_rooms_file(monkeypatch, tmp_path):
    monkeypatch.setattr(rooms, "_ROOMS_FILE", tmp_path / "rooms.json")
    rooms.invalidate_cache()


def test_room_config_roundtrip_and_allowed_fields(monkeypatch, tmp_path):
    _use_rooms_file(monkeypatch, tmp_path)
    fake = [{"key": "test", "abs_path": str(tmp_path), "dir_name": "test", "label": "Old", "color": "#000",
             "notes": "", "goals": "", "notes_target": "own_file", "notes_filename": "USER-NOTES.md",
             "icon": "", "objects": [], "rel_path": "test"}]
    assert rooms._load_rooms_config() == {}
    with patch.object(rooms, "_get_rooms", return_value=fake):
        assert rooms.update_room("test", label="New Label", color="#ff0000", evil_field="pwned")
        assert not rooms.update_room("nonexistent", label="x")
    cfg = rooms._load_rooms_config()
    assert cfg["test"] == {"label": "New Label", "color": "#ff0000"}
    rooms.invalidate_cache()


# --- host overlay -------------------------------------------------------------------------


def _build(monkeypatch, tmp_path, roots: list[Path], overlay: dict | None):
    """Build the rooms for ``roots`` with ``overlay`` as the host overlay file."""
    _use_rooms_file(monkeypatch, tmp_path)
    overlay_file = tmp_path / "overlay" / "rooms.overlay.json"
    monkeypatch.setattr(config, "OVERLAY_FILE", overlay_file)
    if overlay is not None:
        overlay_file.parent.mkdir(parents=True, exist_ok=True)
        overlay_file.write_text(json.dumps(overlay), encoding="utf-8")
    monkeypatch.setattr(config, "load_scan_config", lambda: {"roots": [{"path": str(r)} for r in roots]})
    return {r["rel_path"]: r for r in rooms._build_rooms()}


def test_without_an_overlay_rooms_get_neutral_defaults(monkeypatch, tmp_path):
    built = _build(monkeypatch, tmp_path, [tmp_path / "docs", tmp_path / "private-project"], None)
    assert built["docs"]["label"] == "Docs" and built["docs"]["color"] == "#0ea5e9"
    # unknown root: label is the directory name, neutral grey
    assert built["private-project"]["label"] == "private-project" and built["private-project"]["color"] == "#6b7280"


def test_overlay_supplies_labels_colors_and_workspace_root(monkeypatch, tmp_path):
    work = tmp_path / "workspace"
    roots = [work / "alpha", work / "alpha" / "beta"]
    built = _build(monkeypatch, tmp_path, roots, {
        "workspace_root": str(work),
        "labels": {"alpha": "Alpha Room", "alpha/beta": "Beta Room"},
        "colors": {"alpha": "#112233"},
    })
    assert set(built) == {"alpha", "alpha/beta"}
    assert built["alpha"]["label"] == "Alpha Room" and built["alpha"]["color"] == "#112233"
    assert built["alpha/beta"]["label"] == "Beta Room" and built["alpha/beta"]["key"] == "alpha_beta"


def test_the_workspace_root_keeps_room_keys_stable(monkeypatch, tmp_path):
    # A single root: without an overlay the common path IS the root and the key would be its
    # name only; pinning the workspace root keeps the key of a larger setup (and with it the
    # saved rooms.json entries and icons).
    work = tmp_path / "workspace"
    built = _build(monkeypatch, tmp_path, [work / "alpha" / "beta"], {"workspace_root": str(work)})
    assert [r["key"] for r in built.values()] == ["alpha_beta"]


def test_user_room_config_beats_the_overlay(monkeypatch, tmp_path):
    work = tmp_path / "workspace"
    _use_rooms_file(monkeypatch, tmp_path)
    (tmp_path / "rooms.json").write_text(json.dumps({"alpha": {"label": "Mine"}}), encoding="utf-8")
    built = _build(monkeypatch, tmp_path, [work / "alpha"], {"workspace_root": str(work), "labels": {"alpha": "Overlay"}})
    # _build() points _ROOMS_FILE at tmp_path/rooms.json again, which still holds the user entry
    assert built["alpha"]["label"] == "Mine"


def test_unusable_overlay_content_is_ignored(monkeypatch, tmp_path):
    overlay_file = tmp_path / "overlay.json"
    monkeypatch.setattr(config, "OVERLAY_FILE", overlay_file)
    for raw in ("not json", "[1, 2]", json.dumps({"labels": "x", "colors": 3, "workspace_root": 5})):
        overlay_file.write_text(raw, encoding="utf-8")
        assert config.load_rooms_overlay() == {"workspace_root": None, "labels": {}, "colors": {}}
    monkeypatch.setattr(config, "OVERLAY_FILE", tmp_path / "missing.json")
    assert config.load_rooms_overlay()["labels"] == {}


def test_overlay_values_are_validated(monkeypatch, tmp_path):
    overlay_file = tmp_path / "overlay.json"
    monkeypatch.setattr(config, "OVERLAY_FILE", overlay_file)
    overlay_file.write_text(json.dumps({
        "labels": {"a\\b/": "  Spaced  ", "c": "", "d": 7, "e": "x" * 200},
        "colors": {"a": "#abc", "b": "red", "c": "#12345g", "d": "url(javascript:1)", "e": "#AABBCCDD"},
    }), encoding="utf-8")
    loaded = config.load_rooms_overlay()
    assert loaded["labels"]["a/b"] == "Spaced" and len(loaded["labels"]["e"]) == 80
    assert "c" not in loaded["labels"] and "d" not in loaded["labels"]
    assert loaded["colors"] == {"a": "#abc", "e": "#AABBCCDD"}


# --- icons from the overlay ---------------------------------------------------------------


def test_overlay_icons_are_listed_and_served_outside_the_repo(monkeypatch, tmp_path):
    icons = tmp_path / "overlay" / "icons"
    icons.mkdir(parents=True)
    (icons / "private_room.svg").write_text("<svg/>", encoding="utf-8")
    (icons / "notes.txt").write_text("not an icon", encoding="utf-8")
    monkeypatch.setattr(config, "OVERLAY_ICONS_DIR", icons)

    assert web_server._overlay_icon("icons/private_room.svg") == (icons / "private_room.svg").resolve()
    assert web_server._overlay_icon("icons/notes.txt") is None  # only svg/png
    assert web_server._overlay_icon("icons/missing.svg") is None  # falls back to the shipped icons
    assert web_server._overlay_icon("icons/../app.js") is None
    assert web_server._overlay_icon("app.js") is None
    assert icons in web_server._icon_dirs()


def test_the_repo_ships_no_private_room_assets():
    shipped = ROOT / "pure-locking" / "watcher" / "static" / "icons"
    assert not shipped.exists() or not list(shipped.iterdir())


def test_cache_dir_defaults_to_the_watcher_directory_and_follows_the_env(monkeypatch, tmp_path):
    assert config._dir_from_env("LOCK_MASTER_TEST_UNSET_DIR", config.WATCHER_DIR) == config.WATCHER_DIR
    monkeypatch.setenv("LOCK_MASTER_TEST_DIR", "  ")
    assert config._dir_from_env("LOCK_MASTER_TEST_DIR", config.WATCHER_DIR) == config.WATCHER_DIR
    monkeypatch.setenv("LOCK_MASTER_TEST_DIR", str(tmp_path / "cache"))
    assert config._dir_from_env("LOCK_MASTER_TEST_DIR", config.WATCHER_DIR) == tmp_path / "cache"


def test_detail_cache_is_written_to_the_cache_dir(monkeypatch, tmp_path):
    import cache_writer
    from datetime import datetime, timezone

    target = tmp_path / "cache" / "nested"
    monkeypatch.setattr(config, "CACHE_DIR", target)
    cache_writer._write_detail_cache([], datetime.now(timezone.utc))
    assert len(list(target.glob("LOCK-CACHE*.md"))) == 1
