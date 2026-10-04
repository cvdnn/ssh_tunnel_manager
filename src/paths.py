"""Paths for a source checkout of the tunnel manager."""

from pathlib import Path
import os
import sys

from platform_support import xdg_directory


SRC = Path(__file__).resolve().parent
ROOT = SRC.parent
BIN_FILE = ROOT / "bin" / "ssh-tunnel-manager.pyw"


def runtime_home(root=ROOT):
    explicit = os.environ.get('SSH_TUNNEL_MANAGER_HOME')
    if explicit:
        return Path(explicit).expanduser().resolve()
    if any((root / 'data' / name).exists() for name in ('settings.json', 'tunnels.json', 'ssh_connections.json')):
        return root
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData/Local') / 'SshTunnelManager'
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Application Support/SshTunnelManager'
    return xdg_directory('XDG_DATA_HOME', '.local/share') / 'ssh-tunnel-manager'


RUNTIME_HOME = runtime_home()
CONFIG_FILE = RUNTIME_HOME / "data" / "tunnels.json"
SETTINGS_FILE = RUNTIME_HOME / "data" / "settings.json"
LOG_FILE = RUNTIME_HOME / "logs" / "ssh_tunnel.log"
LOGO_FILE = SRC / "assets" / "app_logo.png"


def ensure_runtime_dirs():
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)


def require_migrated(root=ROOT):
    legacy = [name for name in ("settings.json", "tunnels.json", "ssh_connections.json", "ssh_tunnel.log")
              if (root / name).exists()]
    if legacy:
        raise RuntimeError(
            "发现根目录旧数据（" + ", ".join(legacy) +
            "）。请先退出应用并运行 python scripts/migrate_layout.py --apply（Windows 也可用 scripts/migrate-layout.ps1 -Apply）。"
        )
