"""Operating-system integration. No Qt imports or changes at import time."""
import configparser
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile

APP_ID = 'ssh-tunnel-manager'
APP_DISPLAY_NAME = '隧道管家'
# 版本号规则：从 v1 开始，每次发布整数递增（v1、v2、v3…）。发布时只改 APP_VERSION_MAJOR。
APP_VERSION_MAJOR = 1
APP_VERSION = 'v{}'.format(APP_VERSION_MAJOR)  # 界面、日志和产物文件名使用的版本标签
BUNDLE_VERSION = str(APP_VERSION_MAJOR)  # Info.plist 与 Inno Setup 的版本字段只接受数字
LAUNCH_LABEL = 'local.ssh-tunnel-manager'
REG_RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
REG_ITEM_NAME = 'SshTunnelManager'


def is_frozen():
    """True inside a packaged bundle: no source checkout, and no console on Windows."""
    return bool(getattr(sys, 'frozen', False))


def process_creation_flags():
    return subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0


def default_ssh():
    if sys.platform == 'win32':
        candidate = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/OpenSSH/ssh.exe'
        if candidate.is_file():
            return str(candidate)
    return shutil.which('ssh') or 'ssh'


def gui_python():
    if sys.platform == 'win32':
        candidate = Path(sys.executable).with_name('pythonw.exe')
        if candidate.is_file():
            return str(candidate)
    return sys.executable


def xdg_directory(variable, fallback):
    value = os.environ.get(variable, '')
    return Path(value) if value and Path(value).is_absolute() else Path.home() / fallback


def autostart_file():
    if sys.platform == 'darwin':
        return Path.home() / 'Library/LaunchAgents' / (LAUNCH_LABEL + '.plist')
    if sys.platform.startswith('linux'):
        return xdg_directory('XDG_CONFIG_HOME', '.config') / 'autostart' / (APP_ID + '.desktop')
    raise OSError('当前系统不支持登录自启')


def is_autostart_enabled():
    try:
        if sys.platform == 'win32':
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_RUN_KEY, 0, winreg.KEY_READ) as key:
                value, _ = winreg.QueryValueEx(key, REG_ITEM_NAME)
                return bool(value)
        file = autostart_file()
        if sys.platform == 'darwin':
            data = plistlib.loads(file.read_bytes())
            return isinstance(data, dict) and bool(data.get('RunAtLoad') and not data.get('Disabled', False) and data.get('ProgramArguments'))
        parser = configparser.ConfigParser(interpolation=None)
        parser.read_string(file.read_text(encoding='utf-8'))
        entry = parser['Desktop Entry']
        return bool(entry.get('Exec')) and not entry.getboolean('Hidden', fallback=False)
    except (OSError, ValueError, KeyError, configparser.Error, plistlib.InvalidFileException):
        return False


def _desktop_argument(value):
    # Desktop Entry string escaping is applied BEFORE Exec argument unquoting.
    escaped = ''.join('\\' + c if c in '\\"`$' else c for c in value)
    escaped = escaped.replace('\\', '\\\\').replace('%', '%%')
    return '"' + escaped + '"'


def set_autostart(enable, command):
    """Configure next-login startup only; never launch/stop the current GUI."""
    try:
        if not command or any(not isinstance(arg, str) or any(ord(c) < 32 for c in arg) for arg in command):
            raise ValueError('启动命令含非法字符')
        if sys.platform == 'win32':
            import winreg
            if enable:
                with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, REG_RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                    winreg.SetValueEx(key, REG_ITEM_NAME, 0, winreg.REG_SZ, subprocess.list2cmdline(command))
            else:
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                        winreg.DeleteValue(key, REG_ITEM_NAME)
                except FileNotFoundError:
                    pass
            return True
        file = autostart_file()
        if not enable:
            file.unlink(missing_ok=True)
            return True
        if sys.platform == 'darwin':
            payload = plistlib.dumps({'Label': LAUNCH_LABEL, 'ProgramArguments': command,
                                      'RunAtLoad': True, 'LimitLoadToSessionType': 'Aqua'})
        else:
            if '=' in command[0]:
                raise ValueError('Desktop Entry 不支持可执行文件路径中包含等号')
            payload = ('[Desktop Entry]\nType=Application\nName=SSH Tunnel Manager\n'
                       'Terminal=false\nExec=' + ' '.join(map(_desktop_argument, command)) + '\n').encode('utf-8')
        file.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.' + APP_ID, dir=file.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, file)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return True
    except (OSError, ValueError) as error:
        print(f'设置登录自启失败：{error}')
        return False


def show_error(message):
    """Show a startup error with the native dialog of each desktop system."""
    try:
        if sys.platform == 'win32':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, APP_DISPLAY_NAME, 0x10)
        elif sys.platform == 'darwin':
            script = ('on run argv\n'
                      '  display alert (item 1 of argv) message (item 2 of argv)\n'
                      'end run')
            subprocess.run([
                'osascript', '-e', script, APP_DISPLAY_NAME, message],
                creationflags=process_creation_flags(), check=False)
        elif shutil.which('notify-send'):
            subprocess.run(['notify-send', APP_DISPLAY_NAME, message],
                           creationflags=process_creation_flags(), check=False)
    except (OSError, ImportError, AttributeError) as error:
        print(f'显示错误提示失败：{error}')
