"""Launch the application from a source checkout."""

import runpy
import sys

from paths import SRC, ensure_runtime_dirs, require_migrated


def launch():
    try:
        require_migrated()
    except RuntimeError as error:
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, str(error), "隧道管家", 0x10)
        raise
    ensure_runtime_dirs()
    runpy.run_path(str(SRC / "app.py"), run_name="__main__")
