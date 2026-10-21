"""File-link metadata cannot hide child locks or authorize query redirects."""
import errno
import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

import lock_scan
import lock_utils


def make_link(link, target, *, directory=False):
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314 or exc.errno in {
                errno.EPERM, errno.ENOSYS, getattr(errno, "EOPNOTSUPP", -1)}:
            pytest.skip("Unprivileged symlinks are unavailable on this host")
        raise


def query(root, capsys):
    code = lock_scan._check_dir(root, True, False)
    return code, json.loads(capsys.readouterr().out)


def file_tree(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    data = tmp_path / "data.db"
    data.write_bytes(b"metadata fixture")
    link = root / "a-data.db"
    make_link(link, data)
    return root, data, link


def test_regular_file_link_does_not_make_directory_scan_incomplete(tmp_path, capsys):
    root, data, link = file_tree(tmp_path)
    (root / "ordinary-directory").mkdir()
    before = data.read_bytes()
    code, payload = query(root, capsys)
    assert code == 0
    assert payload["descendants_complete"] is True
    assert payload["locks"] == []
    assert data.read_bytes() == before and link.is_symlink()


@pytest.mark.parametrize("marker", ["LOCK.txt", "LOCK.user.txt", "LOCK.condition.publish.txt",
                                  "LOCK.until.release.txt", "TEST.txt"])
def test_file_link_does_not_hide_real_lock_in_later_subtree(tmp_path, capsys, marker):
    root, _, _ = file_tree(tmp_path)
    child = root / "z-locked"
    child.mkdir()
    (child / marker).write_text("owner: fixture\n", encoding="utf-8")
    code, payload = query(root, capsys)
    assert code == 1
    assert payload["descendants_complete"] is True
    assert any(Path(row["path"]).name == marker for row in payload["locks"])


def test_file_link_named_user_lock_is_still_a_real_lock(tmp_path, capsys):
    root = tmp_path / "project"
    root.mkdir()
    marker = tmp_path / "marker-data"
    marker.write_text("owner: fixture\n", encoding="utf-8")
    make_link(root / "LOCK.user.txt", marker)
    code, payload = query(root, capsys)
    assert code == 1
    assert payload["descendants_complete"] is True
    assert any(Path(row["path"]).name == "LOCK.user.txt" for row in payload["locks"])


def test_twin_file_link_does_not_hide_later_real_lock(tmp_path, capsys):
    clone = tmp_path / "repos" / "demo-repo"
    twin = tmp_path / "mirror" / "demo-repo"
    clone.mkdir(parents=True)
    twin.mkdir(parents=True)
    pointer = {"schema": "ellmos-repo-pointer-v1", "repo_id": "ellmos-ai/demo-repo",
               "local_locator": {"repo_name": "demo-repo", "windows_default": str(clone)}}
    (twin / lock_utils.REPO_POINTER_NAME).write_text(json.dumps(pointer), encoding="utf-8")
    data = tmp_path / "data.db"
    data.write_bytes(b"twin metadata fixture")
    make_link(twin / "a-data.db", data)
    child = twin / "z-locked"
    child.mkdir()
    marker = child / "LOCK.user.txt"
    marker.write_text("owner: fixture\n", encoding="utf-8")
    index_path = tmp_path / "twins.json"
    index_path.write_text(json.dumps(lock_utils.build_twin_index([clone, twin])), encoding="utf-8")
    roots = tmp_path / "roots.json"
    roots.write_text(json.dumps({"roots": [{"path": str(tmp_path)}], "twin_resolution": {
        "clone_roots": [str(tmp_path / "repos")], "index_path": str(index_path)}}), encoding="utf-8")
    code = lock_scan._check_dir(clone, True, False, roots)
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["twin_status"] == "ok"
    assert payload["descendants_complete"] is True
    assert any(Path(row["path"]) == marker and row["from_twin"] is True for row in payload["locks"])
    assert str(twin) in payload["twins_checked"]


@pytest.mark.parametrize("changed", [None, "link", "target"])
def test_file_link_identity_checks_do_not_require_host_symlink_privilege(monkeypatch, changed):
    link = Path("metadata-only-file-link")
    before = SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_dev=1, st_ino=11,
                             st_size=8, st_mtime_ns=2, st_ctime_ns=3)
    after = SimpleNamespace(**vars(before))
    first = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_dev=1, st_ino=21)
    last = SimpleNamespace(**vars(first))
    if changed == "link":
        after.st_ino += 1
    if changed == "target":
        last.st_ino += 1
    link_stats = iter([before, after])
    target_stats = iter([first, last])
    monkeypatch.setattr(Path, "lstat", lambda path: next(link_stats))
    monkeypatch.setattr(Path, "stat", lambda path: next(target_stats))
    assert lock_scan._verified_regular_file_link(link) is (changed is None)


@pytest.mark.parametrize("kind", ["missing", "directory", "cycle"])
def test_unverified_link_target_keeps_scan_incomplete(tmp_path, capsys, kind):
    root = tmp_path / "project"
    root.mkdir()
    link = root / "alias"
    target = tmp_path / "target"
    if kind == "directory":
        target.mkdir()
    elif kind == "cycle":
        target = link
    make_link(link, target, directory=kind == "directory")
    code, payload = query(root, capsys)
    assert code == 1
    assert payload["descendants_complete"] is False


def test_unreadable_target_during_classification_keeps_scan_incomplete(tmp_path, capsys, monkeypatch):
    root, _, link = file_tree(tmp_path)
    original = Path.stat
    original_classifier = lock_scan._verified_regular_file_link
    def denied(path, *args, **kwargs):
        if path == link and kwargs.get("follow_symlinks", True):
            raise PermissionError("synthetic unreadable target")
        return original(path, *args, **kwargs)
    def classify(path):
        if path != link:
            return original_classifier(path)
        with monkeypatch.context() as scoped:
            scoped.setattr(Path, "stat", denied)
            return original_classifier(path)
    monkeypatch.setattr(lock_scan, "_verified_regular_file_link", classify)
    code, payload = query(root, capsys)
    assert code == 1
    assert payload["descendants_complete"] is False


def test_target_becoming_directory_during_classification_is_incomplete(tmp_path, capsys, monkeypatch):
    root, data, link = file_tree(tmp_path)
    original = Path.stat
    original_classifier = lock_scan._verified_regular_file_link
    observations = []
    metadata_errors = []
    swapped_target = []
    def swapped(path, *args, **kwargs):
        try:
            result = original(path, *args, **kwargs)
        except OSError as exc:
            if path == link and swapped_target:
                metadata_errors.append((type(exc).__name__, exc.errno,
                                        getattr(exc, "winerror", None),
                                        kwargs.get("follow_symlinks", True)))
            raise
        if path == link and kwargs.get("follow_symlinks", True):
            observations.append(result)
            if len(observations) == 1:
                data.unlink()
                data.mkdir()
                swapped_target.append(True)
        return result
    def classify(path):
        if path != link:
            return original_classifier(path)
        with monkeypatch.context() as scoped:
            scoped.setattr(Path, "stat", swapped)
            return original_classifier(path)
    monkeypatch.setattr(lock_scan, "_verified_regular_file_link", classify)
    code, payload = query(root, capsys)
    assert code == 1
    assert payload["descendants_complete"] is False
    assert swapped_target == [True] and data.is_dir()
    assert stat.S_ISREG(observations[0].st_mode)
    if len(observations) == 2:
        assert stat.S_ISDIR(observations[1].st_mode)
    else:
        # A Windows file-type link can fail metadata resolution as soon as
        # its target becomes a directory. That must reject the scan too.
        assert len(observations) == 1 and len(metadata_errors) == 1, metadata_errors


def test_file_link_is_not_an_authorized_query_path(tmp_path, capsys):
    _, _, link = file_tree(tmp_path)
    code, payload = query(link, capsys)
    assert code == 1
    assert payload["descendants_complete"] is False


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX special-file contract")
def test_special_file_link_target_is_incomplete_without_opening_it(tmp_path, capsys):
    root = tmp_path / "project"
    root.mkdir()
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    make_link(root / "alias", fifo)
    code, payload = query(root, capsys)
    assert code == 1
    assert payload["descendants_complete"] is False
