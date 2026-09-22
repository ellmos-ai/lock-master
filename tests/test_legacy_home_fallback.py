r"""A root that hardcodes another machine's home directory must still resolve.

Why this matters more than it looks: older lock_roots.json entries wrote a home
directory out literally (`C:\Users\<somebody>\OneDrive\...`). On a host whose
user is named differently that path exists nowhere, `Path.exists()` is False,
and iter_lock_dirs() skips the root **without a word**. The scan then reports
fewer locks than exist -- and "fewer locks" reads as an all-clear. A missing
root is the dangerous direction of this failure, which is why the fallback is
worth a test of its own.

The fallback lived only in the deployed copy until the drift merge
T-20260913-633163908; every host reading the repository fassung lost it.

Run:  python -m pytest tests/test_legacy_home_fallback.py -q
      (or: python tests/test_legacy_home_fallback.py)
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
CODE_DIR = HERE if (HERE / "lock_scan.py").is_file() else HERE.parent / "pure-locking"
sys.path.insert(0, str(CODE_DIR))

import lock_scan  # noqa: E402


def test_a_literal_foreign_home_is_re_anchored(monkeypatch=None):
    """A path under a legacy home marker resolves onto the real home."""
    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        marker = lock_scan._LEGACY_HOME_MARKERS[0]
        target = home / "OneDrive" / "somewhere"
        target.mkdir(parents=True)

        old = os.environ.get("USERPROFILE"), os.environ.get("HOME")
        os.environ["USERPROFILE"] = str(home)
        os.environ["HOME"] = str(home)
        try:
            raw = marker + os.sep + os.path.join("OneDrive", "somewhere")
            resolved = lock_scan._expand_path(raw)
            assert Path(resolved) == target, f"{resolved} != {target}"
        finally:
            for key, value in zip(("USERPROFILE", "HOME"), old):
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def test_an_existing_path_is_left_alone():
    """The fallback must not rewrite paths that already resolve."""
    with tempfile.TemporaryDirectory() as td:
        assert Path(lock_scan._expand_path(td)) == Path(td)


def test_environment_variables_still_expand():
    """The added branch must not break the ordinary expansion.

    Both spellings are checked, because both occur in real configurations and
    the code's own docstring names both. %VAR% is Windows-only -- POSIX
    expandvars leaves it untouched -- so it is asserted only there. Written
    with %VAR% alone, the test asserted something Linux and macOS cannot do and
    failed on both for every Python version, while the code under test was
    correct all along.
    """
    with tempfile.TemporaryDirectory() as td:
        os.environ["LOCK_TEST_ROOT"] = td
        try:
            assert Path(lock_scan._expand_path("$LOCK_TEST_ROOT")) == Path(td)
            if os.name == "nt":
                assert Path(lock_scan._expand_path("%LOCK_TEST_ROOT%")) == Path(td)
        finally:
            os.environ.pop("LOCK_TEST_ROOT", None)


def test_unknown_prefix_is_returned_unchanged():
    """Nothing is invented for a path that matches no marker and does not exist."""
    raw = os.path.join("Z:" + os.sep, "nowhere", "at", "all")
    assert lock_scan._expand_path(raw) == raw


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
    print("ALLE GRUEN" if not fails else f"{fails} FEHLGESCHLAGEN")
    sys.exit(1 if fails else 0)
