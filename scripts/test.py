"""Run isolated tests, syntax compilation and dependency validation on any OS."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='tunnel-manager-tests-') as folder:
        # MacFramelessWindow requires a real Cocoa NSWindow.
        env = dict(os.environ, QT_QPA_PLATFORM='cocoa' if sys.platform == 'darwin' else 'offscreen',
                   SSH_TUNNEL_MANAGER_HOME=folder)
        for arguments in (
            ['-m', 'unittest', 'discover', '-s', str(root / 'tests'), '-v'],
            ['-m', 'compileall', '-q', str(root / 'src'), str(root / 'bin'), str(root / 'scripts')],
            ['-m', 'pip', 'check'],
        ):
            result = subprocess.run([sys.executable, *arguments], cwd=root, env=env)
            if result.returncode:
                return result.returncode
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
