"""Tests for hooks/run.sh interpreter resolution and fail-closed handling."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RUN_SH = ROOT / "hooks" / "run.sh"


def _find_sh() -> str:
    sh = shutil.which("sh")
    if sh:
        return sh
    candidates = [
        Path("C:/Program Files/Git/bin/sh.exe"),
        Path("C:/Program Files/Git/usr/bin/sh.exe"),
        Path("C:/Program Files (x86)/Git/bin/sh.exe"),
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
    pytest.skip("sh interpreter not available on this host")


@pytest.fixture
def no_python_env(tmp_path: Path):
    """Environment with a dummy PATH where python, python3, and py fail or simulate python < 3.10."""
    sh_path = _find_sh()
    sh_dir = str(Path(sh_path).parent)

    # Create dummy failing scripts in a high-priority bin directory
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    for cmd in ("python", "python3", "py"):
        # On POSIX / Git Bash
        script = bin_dir / cmd
        script.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        script.chmod(0o755)  # must be executable, else POSIX `command -v` skips the stub
        # On Windows CMD / batch
        bat = bin_dir / f"{cmd}.bat"
        bat.write_text("@exit /b 1\n", encoding="utf-8")

    env = {
        "PATH": f"{bin_dir}{os.path.pathsep}{sh_dir}",
        "CLAUDE_PLUGIN_ROOT": str(ROOT),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", "C:\\Windows"),
        "WINDIR": os.environ.get("WINDIR", "C:\\Windows"),
    }
    return env


def test_run_sh_warns_on_stderr_when_no_python_310_found(no_python_env):
    sh = _find_sh()
    proc = subprocess.run(
        [sh, str(RUN_SH), "pretooluse"],
        env=no_python_env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.returncode == 0
    assert "lock-master: warning: Python 3.10+ not found" in proc.stderr


def test_run_sh_fails_closed_when_lock_master_fail_closed_is_set(no_python_env):
    sh = _find_sh()
    env = dict(no_python_env)
    env["LOCK_MASTER_FAIL_CLOSED"] = "1"
    proc = subprocess.run(
        [sh, str(RUN_SH), "pretooluse"],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.returncode != 0
    assert "lock-master: warning: Python 3.10+ not found" in proc.stderr
