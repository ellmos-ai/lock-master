r"""Test der Zwillingsaufloesung Klon <-> OneDrive-Spiegel (T-20260913-785936980).

Der Regressionsfall in einem Satz: Ein Lock am OneDrive-Zwilling muss auch
sichtbar sein, wenn man den KLON prueft. `LOCK.user.*` ist absichtlich nicht
versioniert, ein frisch geklontes Repo kann ihn also gar nicht enthalten --
ohne Zwillingsaufloesung liest man dort aus einer leeren Stelle ein "frei".
Am 2026-09-10 hat genau das einen Judging-Hold ueberschritten.

Lauf:  PYTHONIOENCODING=utf-8 python test_twin_resolution.py
       (oder pytest; beides funktioniert, keine Fixtures noetig)
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

# The implementation lives under pure-locking/ in the repository and next to
# the test in a deployment. Support both so the same file runs in either place.
HERE = Path(__file__).resolve().parent
CODE_DIR = HERE if (HERE / "lock_scan.py").is_file() else HERE.parent / "pure-locking"
sys.path.insert(0, str(CODE_DIR))

import lock_utils  # noqa: E402

B = chr(92)  # Backslash, bewusst nicht als Literal (Windows-Pfade in JSON)


def _pointer(repo_name: str, clone: Path) -> str:
    return json.dumps({
        "schema": "ellmos-repo-pointer-v1",
        "repo_id": f"ellmos-ai/{repo_name}",
        "local_locator": {"repo_name": repo_name, "windows_default": str(clone)},
    }, ensure_ascii=False, indent=2)


def _setup(tmp: Path, *, pointer_ok: bool = True, with_lock: bool = True):
    """Baut ein Klon/Zwilling-Paar wie in der Zwei-Baeume-Regel."""
    clone = tmp / "repos" / "demo-repo"
    twin = tmp / "onedrive" / "demo-repo"
    clone.mkdir(parents=True)
    twin.mkdir(parents=True)
    if with_lock:
        # Der Lock liegt NUR am Zwilling -- wie im echten Fall.
        (twin / "LOCK.user.txt").write_text(
            "TEST-Lock, nur am OneDrive-Zwilling.\n", encoding="utf-8")
    (twin / lock_utils.REPO_POINTER_NAME).write_text(
        _pointer("demo-repo", clone) if pointer_ok else "{ kaputt",
        encoding="utf-8")

    index_path = tmp / "TWIN-INDEX.json"
    index = lock_utils.build_twin_index([clone, twin])
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    cfg = {"clone_roots": [str(tmp / "repos")], "index_path": str(index_path)}
    return clone, twin, cfg


def test_lock_am_zwilling_ist_vom_klon_aus_sichtbar():
    with tempfile.TemporaryDirectory() as td:
        clone, twin, cfg = _setup(Path(td))
        dirs, status = lock_utils.twin_dirs_for_path(clone, cfg)
        assert status == "ok", status
        assert [Path(d).resolve() for d in dirs] == [twin.resolve()], dirs
        # der eigentliche Regressionspunkt: der Lock ist ueber den Zwilling erreichbar
        assert lock_utils.active_locks(dirs[0]), "Lock am Zwilling nicht gefunden"


def test_richtung_zwilling_zu_klon():
    """Wer den OneDrive-Pfad prueft, soll den Klon mitbekommen."""
    with tempfile.TemporaryDirectory() as td:
        clone, twin, cfg = _setup(Path(td))
        dirs, status = lock_utils.twin_dirs_for_path(twin, cfg)
        assert status == "ok", status
        assert [Path(d).resolve() for d in dirs] == [clone.resolve()], dirs


def test_fehlender_index_ist_fail_closed():
    with tempfile.TemporaryDirectory() as td:
        clone, _twin, cfg = _setup(Path(td))
        cfg = dict(cfg, index_path=str(Path(td) / "gibtsnicht.json"))
        dirs, status = lock_utils.twin_dirs_for_path(clone, cfg)
        assert status == "index-missing", status
        assert dirs == []


def test_unlesbarer_pointer_ist_fail_closed():
    """Ein kaputter Pointer darf das Repo nicht lautlos aus dem Index fallen
    lassen -- sonst meldet der Klon wieder 'frei'. Real passiert: der erste
    Testpointer dieses Tickets war selbst ungueltiges JSON."""
    with tempfile.TemporaryDirectory() as td:
        clone, _twin, cfg = _setup(Path(td), pointer_ok=False)
        dirs, status = lock_utils.twin_dirs_for_path(clone, cfg)
        assert status == "pointer-unreadable", status
        assert dirs == []


def test_ohne_konfiguration_kein_fehler():
    """Host ohne Spiegel: 'nicht anwendbar' ist kein Defekt (unavailable != empty)."""
    with tempfile.TemporaryDirectory() as td:
        clone, _twin, _cfg = _setup(Path(td))
        assert lock_utils.twin_dirs_for_path(clone, None) == ([], "not-applicable")


def test_pfad_ausserhalb_der_clone_roots():
    with tempfile.TemporaryDirectory() as td:
        _clone, _twin, cfg = _setup(Path(td))
        fremd = Path(td) / "woanders"
        fremd.mkdir()
        assert lock_utils.twin_dirs_for_path(fremd, cfg) == ([], "not-applicable")


def test_check_dir_exitcodes_end_to_end():
    """lock_scan --check-dir: Exit 1 ueber den Zwilling, Exit 0 ohne Lock.
    Exitcode OHNE Pipe messen -- sonst misst man die Pipe (LOCK-SYSTEM.md)."""
    with tempfile.TemporaryDirectory() as td:
        clone, twin, cfg = _setup(Path(td))
        roots = Path(td) / "lock_roots.json"
        roots.write_text(json.dumps({
            "default_max_depth": 4, "shallow_depth": 2, "skip_dirs": [],
            "roots": [{"path": str(Path(td) / "repos")}],
            "twin_resolution": cfg,
        }, ensure_ascii=False), encoding="utf-8")

        def run(target: Path) -> int:
            return subprocess.run(
                [sys.executable, str(CODE_DIR / "lock_scan.py"),
                 "--check-dir", str(target), "--roots-file", str(roots)],
                capture_output=True, text=True).returncode

        assert run(clone) == 1, "Lock am Zwilling wurde vom Klon aus nicht gesehen"
        (twin / "LOCK.user.txt").unlink()
        assert run(clone) == 0, "ohne Lock muss der Klon frei sein"


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"  OK   {name}")
        except AssertionError as exc:
            fails += 1
            print(f"  FAIL {name}: {exc}")
    print(f"\n{'ALLE GRUEN' if not fails else str(fails) + ' FEHLGESCHLAGEN'}")
    sys.exit(1 if fails else 0)
