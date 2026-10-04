"""Create the local virtual environment and install pinned dependencies."""
from pathlib import Path
import os
import subprocess
import sys
import venv


def main():
    root = Path(__file__).resolve().parents[1]
    environment = root / '.venv'
    python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.is_file():
        venv.EnvBuilder(with_pip=True).create(environment)
    return subprocess.call([str(python), '-m', 'pip', 'install', '-r', str(root / 'requirements.txt')])


if __name__ == '__main__':
    raise SystemExit(main())
