"""Real owner CLI/physical-path regressions for the explicit creation mode."""
import errno
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "pure-locking"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import lock_scan  # noqa: E402
import lock_utils  # noqa: E402


def scan(path, config, anchor=None):
    argv = [sys.executable, str(TOOLS / "lock_scan.py"),
            "--check-dir" if anchor is None else "--check-create-dir", str(path),
            "--json", "--strict", "--roots-file", str(config)]
    if anchor is not None:
        argv += ["--creation-anchor", str(anchor)]
    result = subprocess.run(argv, capture_output=True, text=True, timeout=15)
    return result.returncode, json.loads(result.stdout)


def config_at(base, **extra):
    path = base / "roots.json"
    path.write_text(json.dumps({"roots": [], "check_dir_max_descendants": 4, **extra}), encoding="utf-8")
    return path


def lock(path, name="LOCK.txt", protected=None):
    text = ("owner: private-scope-fixture\ncreated: " + datetime.now(timezone.utc).isoformat()
            + "\nexpires_after: 24h\nmode: hard\nscope: fixture\n")
    if protected is not None:
        text += "paths_version: 1\nprotected_paths: " + protected + "\n"
    p = path / name
    p.write_text(text, encoding="utf-8")
    return p


def link(path, target):
    if sys.platform == "win32":
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(path), str(target)],
                                capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            pytest.skip("directory junction unavailable: " + result.stderr)
    else:
        try:
            path.symlink_to(target, target_is_directory=True)
        except OSError as exc:
            pytest.skip("directory symlink unavailable: " + str(exc))


def unlink_dir_redirect(path):
    if sys.platform == "win32":
        os.rmdir(path)
    else:
        path.unlink()


def test_missing_creation_is_typed_and_normal_missing_stays_usage_error(tmp_path):
    base = tmp_path.resolve()
    config = config_at(base)
    for missing in [base / "task-41", base / "not-created" / "worktrees" / "task-41"]:
        assert scan(missing, config)[0] == 2
        code, proof = scan(missing, config, base)
        assert code == 0
        assert proof["check_mode"] == "check-create-dir-v1"
        assert proof["creation_anchor"] == str(base)
        assert proof["missing_suffix"] == list(missing.relative_to(base).parts)
        assert proof["target_exists"] is False and proof["descendants_complete"] is None
        assert proof["target_descendants_state"] == "absent-confirmed"
        assert proof["checks_complete"] and proof["twins_descendants_complete"]
        assert not missing.exists()  # inspection creates nothing
    missing.mkdir(parents=True)
    assert scan(missing, config)[0] == 0
    assert scan(missing, config, base)[0] == 1  # unexpected existing target


def test_sibling_redirect_lock_and_budget_remain_scoped(tmp_path):
    base = tmp_path.resolve()
    root = base / "trees"
    target = root / "task-41"
    sibling = root / "task-42"
    target.mkdir(parents=True)
    sibling.mkdir()
    config = config_at(base)
    for n in range(6):
        (sibling / str(n)).mkdir()
    locked = lock(sibling / "0")
    pointer = sibling / "cycle"
    inside = pointer.parent / "inside"
    inside.mkdir()
    link(pointer, inside)
    try:
        before = (pointer.lstat().st_ino, locked.read_bytes())
        assert scan(target, config)[0] == 0
        assert scan(root / "task-43", config, root)[0] == 0
        assert scan(root, config)[0] == 1
        assert before == (pointer.lstat().st_ino, locked.read_bytes())
    finally:
        unlink_dir_redirect(pointer)


@pytest.mark.parametrize("where,name,protected", [
    ("root", "LOCK.txt", None), ("ancestor", "LOCK.txt", None),
    ("root", "LOCK.user.stop.txt", "other"),
    ("root", "LOCK.condition.stop.txt", "other"),
    ("root", "LOCK.until.stop.txt", "other"),
    ("root", "LOCK.malformed.txt", "../escape"),
])
def test_creation_ancestor_and_protected_locks_block(tmp_path, where, name, protected):
    base = tmp_path.resolve()
    root = base / "trees"
    root.mkdir()
    config = config_at(base)
    lock(root if where == "root" else base, name, protected)
    assert scan(root / "task-41", config, root)[0] == 1


def test_declared_disjoint_lock_stays_disjoint(tmp_path):
    base = tmp_path.resolve()
    root = base / "trees"
    root.mkdir()
    config = config_at(base)
    lock(root, "LOCK.other.txt", "task-42")
    assert scan(root / "task-41", config, root)[0] == 0
    assert scan(root / "task-42", config, root)[0] == 1


def test_creation_rejects_wrong_anchor_escape_file_and_redirect(tmp_path):
    base = tmp_path.resolve()
    root = base / "trees"
    root.mkdir()
    config = config_at(base)
    assert scan(root / "task-41", config, base)[0] == 1  # not last existing parent
    assert scan(root / ".." / "escape", config, root)[0] == 1
    file = root / "file"
    file.write_text("retain", encoding="utf-8")
    assert scan(file / "task-41", config, root)[0] == 1
    pointer = root / "alias"
    link(pointer, root)
    try:
        assert scan(pointer / "task-41", config, pointer)[0] == 1
        assert file.read_text() == "retain"
    finally:
        unlink_dir_redirect(pointer)


def test_creation_rechecks_anchor_identity_after_owner_probe(tmp_path, monkeypatch, capsys):
    base = tmp_path.resolve()
    root = base / "trees"
    root.mkdir()
    config = config_at(base)
    spec = importlib.util.spec_from_file_location("create_race_owner", TOOLS / "lock_scan.py")
    owner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(owner)
    original = owner._creation_chain
    calls = 0

    def change_parent(target, anchor):
        nonlocal calls
        calls += 1
        if calls == 2:
            root.rename(base / "original-retained")
            root.mkdir()
        return original(target, anchor)

    monkeypatch.setattr(owner, "_creation_chain", change_parent)
    assert owner._check_create_dir(root / "task-41", root, True, True, config) == 1
    assert json.loads(capsys.readouterr().out)["checks_complete"] is False
    assert not (root / "task-41").exists()


def test_creation_missing_twin_index_stays_blocked(tmp_path):
    base = tmp_path.resolve()
    root = base / "clones" / "repo"
    root.mkdir(parents=True)
    config = config_at(base, twin_resolution={"clone_roots": [str(base / "clones")],
                                             "index_path": str(base / "missing-index.json")})
    assert scan(root / "new", config, root)[0] == 1


def test_creation_allows_configured_prefix_alias_on_twin_and_rejects_unconfigured():
    checkout_parent = Path(__file__).resolve().parents[1].parent
    with tempfile.TemporaryDirectory(prefix="lock-master-create-twin-", dir=checkout_parent) as td:
        base = Path(td).resolve()
        clone_root = base / "clones"
        repo = clone_root / "demo"
        repo.mkdir(parents=True)
        real_cloud = base / "cloud-storage"
        mirror = real_cloud / "topics" / "demo"
        mirror.mkdir(parents=True)
        alias_cloud = base / "OneDrive"
        link(alias_cloud, real_cloud)
        try:
            alias_mirror = alias_cloud / "topics" / "demo"
            (alias_mirror / lock_utils.REPO_POINTER_NAME).write_text(json.dumps({
                "schema": "ellmos-repo-pointer-v1",
                "repo_name": "demo",
                "canonical_path": str(repo),
            }), encoding="utf-8")
            index_path = base / "TWIN-INDEX.json"
            index_path.write_text(json.dumps(lock_utils.build_twin_index([alias_mirror])), encoding="utf-8")
            configured = base / "roots-configured.json"
            configured.write_text(json.dumps({
                "roots": [{"path": str(alias_cloud / "topics")}],
                "check_dir_max_descendants": 16,
                "twin_resolution": {"clone_roots": [str(clone_root)], "index_path": str(index_path)},
            }), encoding="utf-8")
            code, payload = scan(repo / ".cache" / ".worker-check", configured, repo)
            assert code == 0
            assert payload["checks_complete"] is True
            assert payload["twin_status"] == "ok"

            unconfigured = base / "roots-unconfigured.json"
            unconfigured.write_text(json.dumps({
                "roots": [],
                "check_dir_max_descendants": 16,
                "twin_resolution": {"clone_roots": [str(clone_root)], "index_path": str(index_path)},
            }), encoding="utf-8")
            # Even without roots, REPO.pointer.json at alias_mirror is a project boundary above which alias_cloud sits.
            # Now test an internal redirect inside alias_mirror:
            internal_target = base / "internal-target"
            internal_target.mkdir()
            internal_link = alias_mirror / ".cache"
            link(internal_link, internal_target)
            try:
                code_internal, payload_internal = scan(repo / ".cache" / ".worker-check", configured, repo)
                assert code_internal == 1
                assert payload_internal["checks_complete"] is False
            finally:
                unlink_dir_redirect(internal_link)
        finally:
            unlink_dir_redirect(alias_cloud)


def test_render_cache_handles_none_created_and_build_twin_index_handles_edeadlk(tmp_path, monkeypatch):
    locks = [{
        "path": str(tmp_path / "LOCK.txt"),
        "scope": "project",
        "legacy": False,
        "owner": "agent",
        "created": None,
        "created_source": "invalid",
        "remaining": "invalid created timestamp -> active (fail-closed)",
        "expires_at": "",
    }]
    rendered = lock_scan.render_cache(locks, datetime.now(timezone.utc), "LOCK-CACHE")
    assert "| unknown |" in rendered

    healthy = tmp_path / "healthy"
    healthy.mkdir()
    (healthy / lock_utils.REPO_POINTER_NAME).write_text(json.dumps({
        "schema": "ellmos-repo-pointer-v1",
        "repo_name": "healthy",
        "canonical_path": str(healthy),
    }), encoding="utf-8")
    deadlocked = tmp_path / ".TRANSLATOR-JOBS"
    deadlocked.mkdir()
    bad_pointer = deadlocked / lock_utils.REPO_POINTER_NAME
    bad_pointer.write_text("{}", encoding="utf-8")

    orig_is_file = Path.is_file

    def fake_is_file(self):
        if self == bad_pointer:
            raise OSError(errno.EDEADLK, "Resource deadlock avoided")
        return orig_is_file(self)

    monkeypatch.setattr(Path, "is_file", fake_is_file)
    idx = lock_utils.build_twin_index([healthy, deadlocked])
    assert "healthy" in idx["by_repo_name"]
    assert ".TRANSLATOR-JOBS" in idx["unreadable_pointers_by_dirname"]
