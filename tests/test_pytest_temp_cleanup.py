"""Tests for .pytest_temp cleanup and OneDrive basetemp guard (T-20261107-815315772)."""
from __future__ import annotations

import configparser
from pathlib import Path
from types import SimpleNamespace

import pytest

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent.parent


def test_pyproject_defines_retention_policy_none():
    with (ROOT / "pyproject.toml").open("rb") as handle:
        pyproject = tomllib.load(handle)
    ini_options = pyproject.get("tool", {}).get("pytest", {}).get("ini_options", {})
    assert ini_options.get("tmp_path_retention_policy") == "none", (
        "pyproject.toml must define tmp_path_retention_policy = 'none' to avoid retaining lock files"
    )

    ini_file = ROOT / "pytest.ini"
    if ini_file.is_file():
        cfg = configparser.ConfigParser()
        cfg.read(ini_file, encoding="utf-8")
        assert cfg.get("pytest", "tmp_path_retention_policy", fallback=None) == "none", (
            "pytest.ini must define tmp_path_retention_policy = none"
        )


def test_sessionfinish_cleans_up_all_lock_files_in_basetemp(tmp_path: Path):
    from tests import conftest

    basetemp = tmp_path / ".pytest_temp"
    basetemp.mkdir()
    (basetemp / "subfolder1").mkdir()
    (basetemp / "subfolder2").mkdir()

    # Stamp various active lock files inside basetemp
    (basetemp / "LOCK.txt").write_text("owner: test\n", encoding="utf-8")
    (basetemp / "subfolder1" / "LOCK.user.txt").write_text("owner: user\n", encoding="utf-8")
    (basetemp / "subfolder2" / "LOCK.team.HOST.txt").write_text("owner: team\n", encoding="utf-8")
    (basetemp / "subfolder2" / "other.log").write_text("log content\n", encoding="utf-8")

    # Construct mock session
    config = SimpleNamespace(option=SimpleNamespace(basetemp=str(basetemp)), rootpath=ROOT)
    session = SimpleNamespace(config=config)

    conftest.pytest_sessionfinish(session, 0)

    # All LOCK* files MUST be removed
    leftover_locks = list(basetemp.rglob("LOCK*")) if basetemp.is_dir() else []
    assert leftover_locks == [], f"Found leftover lock files after sessionfinish: {leftover_locks}"


def test_onedrive_basetemp_guard_protects_cloud_storage(tmp_path: Path):
    from tests import conftest

    # 1. Git-less mirror with REPO.pointer.json must raise UsageError
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    (mirror / "REPO.pointer.json").write_text("{}", encoding="utf-8")
    mirror_config = SimpleNamespace(
        rootpath=mirror,
        option=SimpleNamespace(basetemp=".pytest_temp"),
        getoption=lambda opt: ".pytest_temp" if opt == "basetemp" else None,
    )
    with pytest.raises(pytest.UsageError):
        conftest.pytest_configure(mirror_config)

    # 2. Path in OneDrive / CloudStorage redirects basetemp away from cloud sync
    fake_onedrive = tmp_path / "Library" / "CloudStorage" / "OneDrive-Personal" / "project"
    fake_onedrive.mkdir(parents=True)
    od_config = SimpleNamespace(
        rootpath=fake_onedrive,
        option=SimpleNamespace(basetemp=".pytest_temp"),
        getoption=lambda opt: ".pytest_temp" if opt == "basetemp" else None,
    )
    conftest.pytest_configure(od_config)
    assert "onedrive" not in str(od_config.option.basetemp).lower()
    assert "cloudstorage" not in str(od_config.option.basetemp).lower()
