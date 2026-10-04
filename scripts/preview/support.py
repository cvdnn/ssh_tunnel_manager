"""Shared isolated preview setup; never reads local runtime data."""

from pathlib import Path
import os
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import app as tm


def register_preview_fonts():
    """Windows font families are font files; macOS/Linux resolve them already."""
    if sys.platform != "win32":
        return []
    from PySide6.QtGui import QFontDatabase
    font_dir = Path(os.environ.get("SystemRoot", "C:/Windows")) / "Fonts"
    return [QFontDatabase.addApplicationFont(str(font_dir / name))
            for name in ("segoeui.ttf", "msyh.ttc", "msyhbd.ttc", "consola.ttf")]


def tunnel(**overrides):
    data = dict(name="Example", sshHost="example.invalid", localHost="127.0.0.1",
                localPort=13389, remoteHost="127.0.0.1", remotePort=3389,
                enabled=False, autoReconnect=False)
    data.update(overrides)
    return tm.TunnelItem(data)
