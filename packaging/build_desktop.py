#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a self-contained VSD Local Studio executable")
    parser.add_argument("--clean", action="store_true")
    parser.add_argument("--name", default="vsd-local-studio")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    entry = root / "packaging" / "studio_entrypoint.py"
    separator = ";" if os.name == "nt" else ":"
    data_args = [
        f"{root / 'vsd' / 'local' / 'static'}{separator}vsd/local/static",
        f"{root / 'vsd' / 'local' / 'assets'}{separator}vsd/local/assets",
    ]
    command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--onefile", "--console",
        "--name", args.name,
        "--collect-submodules", "uvicorn",
        "--collect-submodules", "fastapi",
        "--collect-submodules", "pydantic",
    ]
    if args.clean:
        command.append("--clean")
    for item in data_args:
        command.extend(["--add-data", item])
    command.append(str(entry))
    subprocess.check_call(command, cwd=root)
    print(root / "dist" / (args.name + (".exe" if os.name == "nt" else "")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
