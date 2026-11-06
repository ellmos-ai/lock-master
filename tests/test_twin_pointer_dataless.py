"""A cloud placeholder (dataless) twin pointer gets its own fail-closed status.

Measured 06.11.2026 on the Mac Studio: OneDrive had dehydrated the mirror's
REPO.pointer.json (st_flags 0x40000060, read -> EDEADLK). The scanner reported
the generic "index-unresolved"; the BACH write guard turned that into
"twin lock status unknown" and two workers returned every task (~65,000 lease
returns, PB-1 / BACH #3985). Nobody could see that one "keep on this device"
click was the repair. The status stays fail-closed; only its name and hint change.
"""
from __future__ import annotations

import errno
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import lock_scan
import lock_utils

_MAC_DATALESS_FLAGS = 0x40000060  # SF_DATALESS | UF_TRACKED | UF_COMPRESSED, as measured


def _pointer(repo_name: str, clone: Path) -> str:
    return json.dumps({
        "schema": "ellmos-repo-pointer-v1",
        "repo_id": f"ellmos-ai/{repo_name}",
        "local_locator": {"repo_name": repo_name, "windows_default": str(clone)},
    })


def _setup(tmp: Path):
    clone = tmp / "repos" / "demo-repo"
    twin = tmp / "onedrive" / "demo-repo"
    clone.mkdir(parents=True)
    twin.mkdir(parents=True)
    (twin / lock_utils.REPO_POINTER_NAME).write_text(_pointer("demo-repo", clone), encoding="utf-8")
    index_path = tmp / "TWIN-INDEX.json"
    index_path.write_text(json.dumps(lock_utils.build_twin_index([clone, twin])), encoding="utf-8")
    cfg = {"clone_roots": [str(tmp / "repos")], "index_path": str(index_path)}
    return clone, twin, cfg


def _make_dataless(monkeypatch, pointer: Path, *, flags=_MAC_DATALESS_FLAGS, attributes=0):
    """Reading fails like the File Provider does; stat reports the placeholder."""
    real_read_text, real_stat = Path.read_text, lock_utils.os.stat

    def read_text(self, *args, **kwargs):
        if self == pointer:
            raise OSError(errno.EDEADLK, "Resource deadlock avoided")
        return real_read_text(self, *args, **kwargs)

    def stat(path, *args, **kwargs):
        real = real_stat(path, *args, **kwargs)
        if Path(path) == pointer:
            return SimpleNamespace(st_mode=real.st_mode, st_size=real.st_size,
                                   st_flags=flags, st_file_attributes=attributes)
        return real

    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(lock_utils.os, "stat", stat)


def test_dataless_twin_pointer_seen_from_clone_reports_pointer_dataless(tmp_path, monkeypatch):
    clone, twin, cfg = _setup(tmp_path)
    _make_dataless(monkeypatch, twin / lock_utils.REPO_POINTER_NAME)
    assert lock_utils.twin_dirs_for_path(clone, cfg) == ([], "pointer-dataless")


def test_dataless_pointer_of_checked_mirror_reports_pointer_dataless(tmp_path, monkeypatch):
    _, twin, cfg = _setup(tmp_path)
    _make_dataless(monkeypatch, twin / lock_utils.REPO_POINTER_NAME)
    assert lock_utils.twin_dirs_for_path(twin, cfg) == ([], "pointer-dataless")


def test_windows_recall_on_data_access_counts_as_dataless(tmp_path, monkeypatch):
    clone, twin, cfg = _setup(tmp_path)
    _make_dataless(monkeypatch, twin / lock_utils.REPO_POINTER_NAME, flags=0, attributes=0x00400000)
    assert lock_utils.twin_dirs_for_path(clone, cfg) == ([], "pointer-dataless")


def test_unreadable_pointer_without_placeholder_flag_keeps_old_status(tmp_path, monkeypatch):
    clone, twin, cfg = _setup(tmp_path)
    _make_dataless(monkeypatch, twin / lock_utils.REPO_POINTER_NAME, flags=0, attributes=0)
    assert lock_utils.twin_dirs_for_path(clone, cfg) == ([], "index-unresolved")
    assert lock_utils.twin_dirs_for_path(twin, cfg) == ([], "pointer-unreadable")


def test_scan_stays_closed_and_names_the_repair(tmp_path):
    """End to end in a child process: exit 1, JSON status, human hint."""
    clone, twin, cfg = _setup(tmp_path)
    roots = tmp_path / "lock_roots.json"
    roots.write_text(json.dumps({"roots": [], "twin_resolution": cfg}), encoding="utf-8")
    pointer = twin / lock_utils.REPO_POINTER_NAME
    driver = tmp_path / "driver.py"
    driver.write_text(
        "import errno, os, sys\n"
        "from pathlib import Path\n"
        "from types import SimpleNamespace\n"
        f"sys.path.insert(0, {str(Path(lock_scan.__file__).parent)!r})\n"
        "import lock_utils, lock_scan\n"
        f"P = Path({str(pointer)!r})\n"
        "rt, st = Path.read_text, os.stat\n"
        "def read_text(self, *a, **k):\n"
        "    if self == P: raise OSError(errno.EDEADLK, 'Resource deadlock avoided')\n"
        "    return rt(self, *a, **k)\n"
        "def stat(p, *a, **k):\n"
        "    r = st(p, *a, **k)\n"
        f"    return SimpleNamespace(st_mode=r.st_mode, st_size=r.st_size, st_flags={_MAC_DATALESS_FLAGS}, st_file_attributes=0) if Path(p) == P else r\n"
        "Path.read_text = read_text\n"
        "os.stat = stat\n"
        "sys.exit(lock_scan.main())\n",
        encoding="utf-8")
    for json_output in (True, False):
        command = [sys.executable, "-X", "utf8", str(driver), "--check-dir", str(clone),
                   "--roots-file", str(roots)] + (["--json"] if json_output else [])
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=30)
        assert result.returncode == 1, result.stdout + result.stderr
        assert "Traceback" not in result.stderr
        if json_output:
            assert json.loads(result.stdout)["twin_status"] == "pointer-dataless"
        else:
            assert "UNDETERMINED" in result.stdout
            assert "cloud placeholder" in result.stdout
            assert "keep" in result.stdout.lower() and "device" in result.stdout.lower()
