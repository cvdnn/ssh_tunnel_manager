"""Paths for a source checkout of the tunnel manager."""

from pathlib import Path


SRC = Path(__file__).resolve().parent
ROOT = SRC.parent
BIN_FILE = ROOT / "bin" / "ssh-tunnel-manager.pyw"
CONFIG_FILE = ROOT / "data" / "tunnels.json"
SETTINGS_FILE = ROOT / "data" / "settings.json"
LOG_FILE = ROOT / "logs" / "ssh_tunnel.log"
LOGO_FILE = SRC / "assets" / "app_logo.png"


def ensure_runtime_dirs():
    CONFIG_FILE.parent.mkdir(exist_ok=True)
    LOG_FILE.parent.mkdir(exist_ok=True)


def require_migrated(root=ROOT):
    legacy = [name for name in ("settings.json", "tunnels.json", "ssh_connections.json", "ssh_tunnel.log")
              if (root / name).exists()]
    if legacy:
        raise RuntimeError(
            "发现根目录旧数据（" + ", ".join(legacy) +
            "）。请先退出应用并运行 scripts/migrate-layout.ps1 -Apply。"
        )
