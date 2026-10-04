"""Paths for a source checkout or a packaged bundle of the tunnel manager."""

from pathlib import Path
import os
import sys

from platform_support import is_frozen, xdg_directory


SRC = Path(__file__).resolve().parent
ROOT = SRC.parent
BIN_FILE = ROOT / "bin" / "ssh-tunnel-manager.pyw"
DATA_DIR_ARGUMENT = '--data-dir'


def resource_root():
    """Directory that carries the bundled resources: the checkout or the bundle payload."""
    if is_frozen():
        return Path(getattr(sys, '_MEIPASS', Path(sys.executable).resolve().parent))
    return SRC


def argument_value(argument, argv=None):
    """Value of a one-value CLI option; packaged builds parse their own command line."""
    argv = sys.argv[1:] if argv is None else argv
    for index, value in enumerate(argv[:-1]):
        if value == argument:
            return argv[index + 1]
    return ''


def configured_runtime_home():
    """Runtime root requested by the caller: command line first, then the environment.

    A source checkout converts ``--data-dir`` into the environment variable inside
    ``bin/ssh-tunnel-manager.pyw``; a bundle has no such wrapper and reads its own
    command line here.
    """
    requested = argument_value(DATA_DIR_ARGUMENT) if is_frozen() else ''
    return requested or os.environ.get('SSH_TUNNEL_MANAGER_HOME', '')


def runtime_home(root=ROOT):
    explicit = configured_runtime_home()
    if explicit:
        return Path(explicit).expanduser().resolve()
    # A bundle is installed, not checked out, so it never adopts a data/ directory next to it.
    if not is_frozen() and any((root / 'data' / name).exists()
                               for name in ('settings.json', 'tunnels.json', 'ssh_connections.json')):
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
LOGO_FILE = resource_root() / "assets" / "app_logo.png"


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
