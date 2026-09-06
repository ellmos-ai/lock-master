"""Build-gate helper: install a wheel in isolation and smoke-test Team-Lock."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import venv
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        raise SystemExit("usage: wheel_smoke.py <wheel-directory>")

    wheel_directory = Path(args[0]).resolve(strict=True)
    wheels = sorted(wheel_directory.glob("lock_master-*.whl"))
    if len(wheels) != 1:
        raise SystemExit(f"expected exactly one lock-master wheel, found {len(wheels)}")

    clean_environment = os.environ.copy()
    clean_environment.pop("PYTHONPATH", None)
    clean_environment.pop("PYTHONHOME", None)
    with tempfile.TemporaryDirectory(prefix="lock-master-wheel-smoke-") as temp_name:
        temp = Path(temp_name)
        environment = temp / "venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        scripts = environment / ("Scripts" if os.name == "nt" else "bin")
        python = scripts / ("python.exe" if os.name == "nt" else "python")
        cli = scripts / ("lock-master-team.exe" if os.name == "nt" else "lock-master-team")

        subprocess.run(
            [
                str(python),
                "-X",
                "utf8",
                "-m",
                "pip",
                "install",
                "--no-index",
                "--no-deps",
                str(wheels[0]),
            ],
            check=True,
            cwd=temp,
            env=clean_environment,
        )
        imported = subprocess.run(
            [
                str(python),
                "-X",
                "utf8",
                "-c",
                (
                    "from pathlib import Path; import sys, team_lock, _lock_master_team; "
                    "assert Path(team_lock.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()); "
                    "assert callable(team_lock.update_team_lock); print(team_lock.__file__)"
                ),
            ],
            check=True,
            cwd=temp,
            env=clean_environment,
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE,
        )
        cli_help = subprocess.run(
            [str(cli), "--help"],
            check=True,
            cwd=temp,
            # This invokes the installed console-script .exe directly (not
            # `python -X utf8 ...` like the two calls above), so Python's
            # UTF-8 mode is not on by default here. Without it, a non-ASCII
            # character in the (partly German) argparse help text -- e.g.
            # "für" -- is written using the platform's default locale
            # encoding, which is cp1252 on an English-locale Windows CI
            # runner; the strict `encoding="utf-8"` decode below then fails
            # on that byte (T-20260907-501809205). PYTHONUTF8=1 is the
            # environment-variable equivalent of `-X utf8` (PEP 540) and
            # forces the child's stdout to be UTF-8 regardless of locale.
            env={**clean_environment, "PYTHONUTF8": "1"},
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE,
        )
        if "claim-file" not in cli_help.stdout or "--lock-name" not in cli_help.stdout:
            raise SystemExit("installed lock-master-team help is incomplete")
        print(f"installed import: {imported.stdout.strip()}")
        print("installed CLI: lock-master-team --help OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
