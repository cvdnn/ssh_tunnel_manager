"""Launch the application from a source checkout."""

import runpy
import sys
import os

from paths import SRC, ensure_runtime_dirs, require_migrated


def launch():
    try:
        if '--startup-splash' not in sys.argv and not os.environ.get('SSH_TUNNEL_MANAGER_HOME'):
            require_migrated()
    except RuntimeError as error:
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, str(error), "隧道管家", 0x10)
        raise
    if '--startup-splash' not in sys.argv:
        ensure_runtime_dirs()
    runpy.run_path(str(SRC / "app.py"), run_name="__main__")
