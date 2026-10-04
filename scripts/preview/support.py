"""Shared isolated preview setup; never reads local runtime data."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import app as tm


def tunnel(**overrides):
    data = dict(name="Example", sshHost="example.invalid", localHost="127.0.0.1",
                localPort=13389, remoteHost="127.0.0.1", remotePort=3389,
                enabled=False, autoReconnect=False)
    data.update(overrides)
    return tm.TunnelItem(data)
