"""Launch the application from a source checkout."""

import runpy
import sys
import os

from paths import SRC, ensure_runtime_dirs, require_migrated
import platform_support


def launch():
    try:
        if '--startup-splash' not in sys.argv and not os.environ.get('SSH_TUNNEL_MANAGER_HOME'):
            require_migrated()
    except RuntimeError as error:
        platform_support.show_error(str(error))
        raise
    if '--startup-splash' not in sys.argv:
        ensure_runtime_dirs()
    runpy.run_path(str(SRC / "app.py"), run_name="__main__")
