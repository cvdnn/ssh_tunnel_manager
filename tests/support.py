"""Shared offscreen application and test object factories."""

import os
from pathlib import Path
import sys
import types
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "cocoa" if sys.platform == 'darwin' else "offscreen")
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

import app as tm


def tunnel(**overrides):
    data = dict(name="test", sshHost="jump", localHost="127.0.0.2", localPort=13389,
                remoteHost="10.0.0.2", remotePort=3389, enabled=True, autoReconnect=True)
    data.update(overrides)
    return tm.TunnelItem(data)


def controller(*items):
    window = types.SimpleNamespace(
        tunnels=list(items), settings={"sshPath": "ssh.exe", "sshAliases": {}},
        is_paused=False, running=True, log=Mock(), refresh_table=Mock(), tray=Mock(),
        tunnel_jobs=Mock())
    for name in ("check_tunnels_health", "_report_online_summary", "_queue_tunnel"):
        setattr(window, name, types.MethodType(getattr(tm.MainWindow, name), window))
    return window
