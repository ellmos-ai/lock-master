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


def _setup(tmp: Path, *, pointer_ok: bool = True, with_lock: bool = True,
           clone_dirname: str = "demo-repo"):
    """Baut ein Klon/Zwilling-Paar wie in der Zwei-Baeume-Regel.

    clone_dirname weicht bewusst vom repo_name ab, wenn der Aufrufer das
    verlangt -- ein Klon darf auf der Platte anders heissen als im Pointer."""
    clone = tmp / "repos" / clone_dirname
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


def test_foreign_absolute_mirror_path_is_not_free(tmp_path):
    clone = tmp_path / "repos" / "lock-master"
    clone.mkdir(parents=True)
    mirror = tmp_path / "User" / "onedrive" / "lock-master"
    mirror.mkdir(parents=True)
    (mirror / lock_utils.REPO_POINTER_NAME).write_text(
        _pointer("lock-master", clone), encoding="utf-8")
    (mirror / "LOCK.user.txt").write_text("user-held\n", encoding="utf-8")
    foreign = tmp_path / "lukas" / "onedrive" / "lock-master"
    index = lock_utils.build_twin_index([mirror])
    index["by_clone_path"][lock_utils._path_key(clone)] = [str(foreign)]
    index["by_repo_name"]["lock-master"] = [str(foreign)]
    index_path = tmp_path / "TWIN-INDEX.json"
    index_path.write_text(json.dumps(index), encoding="utf-8")
    cfg = {"clone_roots": [str(clone.parent)], "index_path": str(index_path)}

    dirs, status = lock_utils.twin_dirs_for_path(clone, cfg)
    assert dirs == []
    assert status == "index-unresolved"


def test_legacy_and_foreign_host_indexes_are_unresolved(tmp_path):
    clone, twin, cfg = _setup(tmp_path)
    index_path = Path(cfg["index_path"])
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["schema"] = "ellmos-lock-twin-index-v1"
    index_path.write_text(json.dumps(index), encoding="utf-8")
    assert lock_utils.twin_dirs_for_path(clone, cfg) == ([], "index-unresolved")

    index["schema"] = lock_utils.TWIN_INDEX_SCHEMA
    index["producer_host"] = "OTHER-HOST"
    index_path.write_text(json.dumps(index), encoding="utf-8")
    assert lock_utils.twin_dirs_for_path(clone, cfg) == ([], "index-unresolved")
    assert twin.is_dir()


def test_missing_mapping_and_mismatched_pointer_are_unresolved(tmp_path):
    clone, twin, cfg = _setup(tmp_path)
    index_path = Path(cfg["index_path"])
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["by_clone_path"].clear()
    index["by_repo_name"].clear()
    index_path.write_text(json.dumps(index), encoding="utf-8")
    assert lock_utils.twin_dirs_for_path(clone, cfg) == ([], "index-unmapped")

    index = lock_utils.build_twin_index([twin])
    index_path.write_text(json.dumps(index), encoding="utf-8")
    (twin / lock_utils.REPO_POINTER_NAME).write_text(
        _pointer("demo-repo", tmp_path / "another-clone"), encoding="utf-8")
    assert lock_utils.twin_dirs_for_path(clone, cfg) == ([], "index-unresolved")


def test_direct_pointer_with_absent_clone_is_unresolved(tmp_path):
    clone, twin, cfg = _setup(tmp_path)
    clone.rmdir()
    assert lock_utils.twin_dirs_for_path(twin, cfg) == ([], "pointer-unresolved")


def test_one_absent_or_invalid_twin_does_not_hide_other_twins(tmp_path):
    clone, twin, cfg = _setup(tmp_path)
    index_path = Path(cfg["index_path"])
    missing = tmp_path / "foreign-home" / "demo-repo"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["by_repo_name"]["demo-repo"].append(str(missing))
    index_path.write_text(json.dumps(index), encoding="utf-8")
    assert lock_utils.twin_dirs_for_path(clone, cfg) == ([], "index-unresolved")
    assert twin.is_dir()

    index["by_repo_name"]["demo-repo"] = "not-a-list"
    index_path.write_text(json.dumps(index), encoding="utf-8")
    assert lock_utils.twin_dirs_for_path(clone, cfg) == ([], "index-unresolved")


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


def test_lock_wird_auch_aus_einem_unterordner_gesehen():
    """Ein Lock am Repo-Root bindet alles darunter -- auch fuer die
    Zwillingsaufloesung. Vorher wurde nur das letzte Pfadsegment im Index
    gesucht: ein Aufruf aus <klon>/src meldete "frei", obwohl der Zwilling
    gesperrt war. Im Review gefunden (T-20260913-715231627)."""
    with tempfile.TemporaryDirectory() as td:
        clone, twin, cfg = _setup(Path(td))
        unterordner = clone / "src" / "tief"
        unterordner.mkdir(parents=True)
        dirs, status = lock_utils.twin_dirs_for_path(unterordner, cfg)
        assert status == "ok", status
        assert twin.resolve() in [Path(d).resolve() for d in dirs], dirs
        assert lock_utils.active_locks(twin), "Lock am Zwilling nicht gefunden"


def test_klon_ordner_darf_anders_heissen_als_repo_name():
    """Der Klon auf der Platte traegt nicht zwingend den repo_name. Die
    Aufloesung ueber den DEKLARIERTEN Klonpfad muss trotzdem greifen -- sonst
    meldet der Check "frei", waehrend der Zwilling gesperrt ist. Im Review
    gefunden (T-20260913-715231627)."""
    with tempfile.TemporaryDirectory() as td:
        clone, twin, cfg = _setup(Path(td), clone_dirname="anders-benannt")
        assert clone.name != "demo-repo"
        dirs, status = lock_utils.twin_dirs_for_path(clone, cfg)
        assert status == "ok", status
        assert twin.resolve() in [Path(d).resolve() for d in dirs], dirs


def test_andere_pointer_schemata_gelten_als_lesbar():
    """REPO.pointer.json existiert im Bestand in mehreren Formen. Am lebenden
    Baum gemessen: 3 von 29 Pointern nannten weder local_locator.repo_name noch
    repo_id, sondern top-level repo_name/canonical_path bzw. project/local_clone.
    Sie als "unlesbar" zu behandeln haette drei gesunde Repos fail-closed
    blockiert -- ein Waechter, der grundlos Alarm schlaegt, wird nicht mehr
    gelesen. (T-20260913-715231627)"""
    varianten = [
        {"local_locator": {"repo_name": "demo", "windows_default": "C:/x/demo"}},
        {"repo_name": "demo", "canonical_path": "C:/x/demo"},
        {"project": "demo", "local_clone": "C:/x/demo"},
        {"canonical_remote": "https://github.com/org/demo.git", "local_clone": "C:/x/demo"},
        {"module": "demo", "source_of_truth": "C:/x/demo"},
    ]
    for v in varianten:
        assert lock_utils._pointer_repo_name(v) == "demo", v
        assert lock_utils._pointer_clone_path(v) is not None, v
    # Ein Pointer, der ueber seinen Klon nichts sagt, bleibt unlesbar.
    assert lock_utils._pointer_repo_name({"schema": "x"}) is None


def test_legacy_pointer_dateien_bleiben_lesbar(tmp_path):
    clone, twin, cfg = _setup(tmp_path)
    for pointer in (
        {"repo_name": "demo-repo", "canonical_path": str(clone)},
        {"project": "demo-repo", "local_clone": str(clone)},
        {"local_locator": None, "repo_id": "ellmos-ai/demo-repo",
         "source_of_truth": str(clone)},
    ):
        (twin / lock_utils.REPO_POINTER_NAME).write_text(
            json.dumps(pointer), encoding="utf-8")
        index = lock_utils.build_twin_index([twin])
        Path(cfg["index_path"]).write_text(json.dumps(index), encoding="utf-8")
        assert lock_utils.twin_dirs_for_path(clone, cfg) == ([twin], "ok")
        assert lock_utils.twin_dirs_for_path(twin, cfg) == ([clone], "ok")


def test_check_dir_nichtexistenter_pfad_ist_kein_frei():
    """Regressionsfall T-20260922-626871087: ein vertippter/veralteter Pfad
    wurde als 'free' (Exit 0) gemeldet -- Path.resolve() normalisiert einen
    nicht existierenden Pfad ohne Fehler, ohne expliziten Existenz-Check sieht
    das wie ein echtes 'frei' aus. Live-Fall: --check-dir bekam
    '.../.HACKATHONS/2026-roshambo' (ohne '.TOPICS') und meldete Exit 0,
    obwohl der echte Ordner ein aktives LOCK.user.txt trug. Das ist KEIN
    Zwillings-/Zwei-Baeume-Problem -- die Zwillingsaufloesung selbst
    funktioniert (siehe alle Tests oben); der fehlende Pfad wurde nur nie
    als Fehler erkannt."""
    with tempfile.TemporaryDirectory() as td:
        roots = Path(td) / "lock_roots.json"
        roots.write_text(json.dumps({
            "default_max_depth": 4, "shallow_depth": 2, "skip_dirs": [],
            # On macOS tempfile may start with /var -> /private/var. Declare
            # the fixture root so this prefix alias stays outside the project.
            "roots": [{"path": str(Path(td))}],
        }, ensure_ascii=False), encoding="utf-8")
        missing = Path(td) / "does-not-exist" / "2026-roshambo"

        result = subprocess.run(
            [sys.executable, str(CODE_DIR / "lock_scan.py"),
             "--check-dir", str(missing), "--roots-file", str(roots)],
            capture_output=True, text=True)
        assert result.returncode == 2, (result.returncode, result.stdout, result.stderr)
        assert "does not exist" in (result.stdout + result.stderr)

        result_json = subprocess.run(
            [sys.executable, str(CODE_DIR / "lock_scan.py"),
             "--check-dir", str(missing), "--roots-file", str(roots), "--json"],
            capture_output=True, text=True)
        assert result_json.returncode == 2, result_json.returncode
        payload = json.loads(result_json.stdout)
        assert payload["error"] == "target-not-found"


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


def test_klon_ohne_spiegel_mit_und_ohne_deklaration():
    """T-20261031-777535853: Ein Klon unter clone_root ohne OneDrive-Zwilling
    bleibt ohne Deklaration fail-closed UNDETERMINED (Exit 1), wird aber mit
    ausdruecklicher no_mirror-Deklaration (im Slot-Manifest repos.json, in
    twin_resolution.no_mirror oder im Index) als 'ok' / frei (Exit 0) aufgeloest.
    Ein echter Spiegel-Lock hat weiterhin Vorrang vor einer no_mirror-Deklaration."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        repos_root = tmp / "repos"
        unmapped_clone = repos_root / "unmapped-repo"
        declared_clone = repos_root / "declared-no-mirror"
        manifest_clone = repos_root / "manifest-no-mirror"
        mirrored_clone = repos_root / "mirrored-repo"
        mirror_dir = tmp / "onedrive" / "mirrored-repo"
        for d in (unmapped_clone, declared_clone / "src", manifest_clone, mirrored_clone, mirror_dir):
            d.mkdir(parents=True, exist_ok=True)

        (mirror_dir / lock_utils.REPO_POINTER_NAME).write_text(
            _pointer("mirrored-repo", mirrored_clone), encoding="utf-8")
        (mirror_dir / "LOCK.user.txt").write_text("user lock on mirror\n", encoding="utf-8")

        manifest_path = tmp / "repos.json"
        manifest_path.write_text(json.dumps({
            "schema": "repos-manifest-v1",
            "repos": [
                {"name": "manifest-no-mirror", "path": str(manifest_clone), "no_mirror": True},
                {"name": "unmapped-repo", "path": str(unmapped_clone)},
                {"name": "mirrored-repo", "path": str(mirrored_clone), "no_mirror": True},
            ],
        }, ensure_ascii=False, indent=2), encoding="utf-8")

        index_path = tmp / "TWIN-INDEX.json"
        cfg = {
            "clone_roots": [str(repos_root)],
            "index_path": str(index_path),
            "no_mirror": ["declared-no-mirror"],
            "no_mirror_manifests": [str(manifest_path)],
        }
        index = lock_utils.build_twin_index([mirror_dir], cfg)
        index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")

        # 1. Ohne Deklaration -> weiter fail-closed index-unmapped
        assert lock_utils.twin_dirs_for_path(unmapped_clone, cfg) == ([], "index-unmapped")

        # 2. Mit Deklaration (via no_mirror bzw. repos.json) -> ([], 'ok'), auch aus Unterordner
        assert lock_utils.twin_dirs_for_path(declared_clone, cfg) == ([], "ok")
        assert lock_utils.twin_dirs_for_path(declared_clone / "src", cfg) == ([], "ok")
        assert lock_utils.twin_dirs_for_path(manifest_clone, cfg) == ([], "ok")

        # 3. Echter Spiegel gewinnt trotz no_mirror-Eintrag
        dirs, status = lock_utils.twin_dirs_for_path(mirrored_clone, cfg)
        assert status == "ok"
        assert [Path(d).resolve() for d in dirs] == [mirror_dir.resolve()]

        # 4. End-to-End ueber lock_scan.py --check-dir
        roots = tmp / "lock_roots.json"
        roots.write_text(json.dumps({
            "default_max_depth": 4, "shallow_depth": 2, "skip_dirs": [],
            "roots": [{"path": str(repos_root)}, {"path": str(tmp / "onedrive")}],
            "twin_resolution": cfg,
        }, ensure_ascii=False), encoding="utf-8")

        def check_dir(target: Path) -> tuple[int, dict]:
            res = subprocess.run(
                [sys.executable, str(CODE_DIR / "lock_scan.py"),
                 "--check-dir", str(target), "--roots-file", str(roots), "--json"],
                capture_output=True, text=True)
            return res.returncode, json.loads(res.stdout)

        rc_unmapped, p_unmapped = check_dir(unmapped_clone)
        assert rc_unmapped == 1 and p_unmapped["twin_status"] == "index-unmapped"

        rc_declared, p_declared = check_dir(declared_clone)
        assert rc_declared == 0 and p_declared["twin_status"] == "ok"

        rc_manifest, p_manifest = check_dir(manifest_clone)
        assert rc_manifest == 0 and p_manifest["twin_status"] == "ok"

        rc_mirrored, p_mirrored = check_dir(mirrored_clone)
        assert rc_mirrored == 1 and len(p_mirrored["locks"]) == 1

        # 5. Korruptes no_mirror-Feld im Index ist fail-closed index-unresolved
        bad_index = dict(index, no_mirror="invalid-not-a-list")
        index_path.write_text(json.dumps(bad_index), encoding="utf-8")
        assert lock_utils.twin_dirs_for_path(declared_clone, cfg) == ([], "index-unresolved")


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
