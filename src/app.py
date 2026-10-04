#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
隧道管家 (SSH Tunnel Manager) - PySide6 + QFluentWidgets 现代化桌面客户端
- 基于 Windows 11 Fluent Design 官方规范
- 纯代码实现，无缝标题栏、行内折叠卡片、原生 Fluent 开关
- 后台自连接自愈重连守护、系统托盘动态指示灯、Windows 开机自启
"""

import os
import sys
import json
import time
import socket
import subprocess
import threading
import atexit
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from queue import SimpleQueue, Empty
from pathlib import Path
from copy import deepcopy

from paths import ROOT, BIN_FILE, CONFIG_FILE as DATA_CONFIG_FILE
from paths import SETTINGS_FILE as DATA_SETTINGS_FILE, LOG_FILE as DATA_LOG_FILE
from paths import LOGO_FILE as ASSET_LOGO_FILE, argument_value, ensure_runtime_dirs
from paths import RUNTIME_HOME
import platform_support
from platform_support import process_creation_flags, is_autostart_enabled, APP_VERSION

from ssh_connections import (discover_hosts, connection_args, load_connections,
                             save_connections, atomic_write_json, validate_connection)
from connection_settings import ConnectionSettingsEditor

from PySide6.QtCore import Qt, QTimer, Signal, QObject, QSize, QPoint, QRect, QRectF
from PySide6.QtGui import QIcon, QColor, QFont, QPixmap, QPainter, QBrush, QPen, QCursor, QPainterPath
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QFrame, QScrollArea, QFileDialog, QSystemTrayIcon, QGridLayout, QMenu,
    QSizePolicy, QProgressBar, QPushButton, QTabWidget, QDoubleSpinBox
)


# 按桌面平台选择对应的字体族，同平台按优先级列出，本机缺失的族名由 Qt 自动跳过。
# 族名不能留空：空字符串在 macOS 上会被解析成 ".Apple Color Emoji UI"，
# 其全角 ASCII 字形会把 PID、端口和 IP 中的数字与冒号拉开。
if sys.platform == 'win32':
    UI_FONT_FAMILIES = ('Microsoft YaHei UI', 'Segoe UI', 'Tahoma')
    MONO_FONT_FAMILIES = ('Consolas', 'Cascadia Mono', 'Courier New')
elif sys.platform == 'darwin':
    UI_FONT_FAMILIES = ('PingFang SC', 'Hiragino Sans GB', 'Helvetica Neue')
    MONO_FONT_FAMILIES = ('Menlo', 'Monaco', 'Courier')
else:
    UI_FONT_FAMILIES = ('Noto Sans CJK SC', 'Source Han Sans SC',
                        'WenQuanYi Micro Hei', 'Sans Serif')
    MONO_FONT_FAMILIES = ('Noto Sans Mono CJK SC', 'DejaVu Sans Mono',
                          'Liberation Mono', 'monospace')

# 当前平台的首选字体族。
UI_FONT = UI_FONT_FAMILIES[0]
MONO_FONT = MONO_FONT_FAMILIES[0]


def ui_font(size: int, weight: int = QFont.Normal) -> QFont:
    """当前平台的界面字体，附带拉丁与中日韩字形回退。"""
    font = QFont(UI_FONT, size, weight)
    font.setFamilies(list(UI_FONT_FAMILIES))
    return font


def mono_font(size: int, weight: int = QFont.Normal) -> QFont:
    """当前平台的等宽字体，用于运行日志。"""
    font = QFont(MONO_FONT, size, weight)
    font.setFamilies(list(MONO_FONT_FAMILIES))
    return font


# Layout baseline in Qt logical pixels. The real window size is clamped to the
# screen so the fixed layout also fits small laptop and low-resolution displays
# on Windows, macOS and Linux.
MAIN_WIDTH = 1100
MAIN_HEIGHT = 660
WORKSPACE_WIDTH = 420
MIN_WORKSPACE_WIDTH = 320

SPLASH_PORT_ARGUMENT = "--splash-port"
SPLASH_BACKLOG_TIMEOUT = 5  # 父进程等待启动页连回来的上限（秒）。
SPLASH_CONNECT_TIMEOUT = 5  # 启动页连回父进程的上限（秒）。


def self_launch_arguments(executable):
    """重新拉起自身的命令前缀：打包后应用自身即入口，源码运行还要带上 bin 入口。"""
    return [executable] if platform_support.is_frozen() else [executable, str(BIN_FILE)]


def splash_channel_port():
    """打包后的启动页通过回环端口接收状态，源码运行返回 0 表示继续用标准输入。"""
    try:
        return max(0, int(argument_value(SPLASH_PORT_ARGUMENT)))
    except ValueError:
        return 0


def layout_widths(expanded=False, screen=None):
    """Main and workspace widths for one layout state on this screen."""
    if screen is None:
        screen = QApplication.primaryScreen()
    area = screen.availableGeometry() if screen is not None else QRect(
        0, 0, MAIN_WIDTH + WORKSPACE_WIDTH, MAIN_HEIGHT)
    if not expanded:
        return min(MAIN_WIDTH, area.width()), 0
    # The workspace never drops below a usable width and the window never grows
    # past the screen; on narrow displays the table area gives way first.
    workspace = min(WORKSPACE_WIDTH, max(MIN_WORKSPACE_WIDTH, area.width() - MAIN_WIDTH))
    return min(MAIN_WIDTH, max(1, area.width() - workspace)), workspace


def window_size(expanded=False, screen=None):
    """Fixed window size for one layout state, clamped to the screen's area."""
    main, workspace = layout_widths(expanded, screen)
    if screen is None:
        screen = QApplication.primaryScreen()
    height = min(MAIN_HEIGHT, (screen.availableGeometry().height() if screen is not None
                               else MAIN_HEIGHT))
    return QSize(main + workspace, height)


class WindowOutline(QWidget):
    """始终绘制在窗口内容上方的细外框，不遮挡鼠标操作。"""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)

    def paintEvent(self, event):
        painter = QPainter(self)
        color = QColor("#94a3b8")
        painter.fillRect(0, 0, self.width(), 1, color)
        painter.fillRect(0, self.height() - 1, self.width(), 1, color)
        painter.fillRect(0, 0, 1, self.height(), color)
        painter.fillRect(self.width() - 1, 0, 1, self.height(), color)


class SmoothBusyProgress(QProgressBar):
    """按实际经过时间移动滑块，避免帧率波动改变动画速度。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRange(0, 0)
        self.setTextVisible(False)
        self.setFixedHeight(6)
        self._phase = 0.32
        self._started_at = time.monotonic()
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._advance)

    def _advance(self):
        self._phase = (0.32 + (time.monotonic() - self._started_at) / 1.6) % 1.0
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        self._started_at = time.monotonic() - (self._phase - 0.32) * 1.6
        self._timer.start()

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        track = QPainterPath()
        radius = self.height() / 2.0
        track.addRoundedRect(QRectF(self.rect()), radius, radius)
        painter.fillPath(track, QColor("#e7eff2"))
        painter.setClipPath(track)
        width = self.width() * 0.28
        left = (self.width() + width) * self._phase - width
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#009ca9"))
        painter.drawRoundedRect(QRectF(left, 0, width, self.height()), radius, radius)


class StartupSplash(QWidget):
    """在加载较重的界面组件前，先提供可见的启动反馈。"""

    def __init__(self):
        super().__init__(None, Qt.SplashScreen | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setObjectName("startupSplash")
        self.setFixedSize(360, 160)
        self.setStyleSheet("""
            QWidget#startupSplash {
                background: #ffffff;
                border: none;
            }
            QLabel { border: none; background: transparent; }
            QProgressBar {
                background: #e7eff2;
                border: none;
                border-radius: 3px;
            }
            QProgressBar::chunk {
                background: #009ca9;
                border-radius: 3px;
            }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(0)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title = QLabel("隧道管家", self)
        title.setFont(ui_font(16, QFont.Bold))
        title.setStyleSheet("color: #0f172a;")
        title_row.addWidget(title)
        version_tag = QLabel(APP_VERSION, self)
        version_tag.setFont(ui_font(10))
        version_tag.setStyleSheet("color: #0891b2;")
        title_row.addWidget(version_tag, 0, Qt.AlignBottom)
        title_row.addStretch()
        layout.addLayout(title_row)
        layout.addSpacing(10)
        self.status = QLabel("正在加载界面…", self)
        self.status.setFont(ui_font(9))
        self.status.setStyleSheet("color: #64748b;")
        layout.addWidget(self.status)
        layout.addStretch()
        self.progress = SmoothBusyProgress(self)
        layout.addWidget(self.progress)

        self.window_outline = WindowOutline(self)
        self.window_outline.setGeometry(self.rect())
        self.window_outline.raise_()

        screen = QApplication.primaryScreen()
        if screen is not None:
            self.move(screen.availableGeometry().center() - self.rect().center())


def show_main_window(app, window, splash):
    """主界面完成首次绘制后再撤掉启动页。"""
    window.show()
    app.processEvents()
    if splash is not None:
        splash.close()


class StartupSplashProcess:
    """启动页独立运行，重型导入和主窗口构造不会阻塞其事件循环。

    源码运行用子进程的标准输入传状态；打包后（Windows 无控制台时子进程没有 stdin）
    改用只监听回环地址的一次性 socket，父进程关闭连接即表示启动页该结束。
    """

    def __init__(self):
        self.socket_channel = platform_support.is_frozen()
        self.listener = None
        self.connection = None
        self.pending = []
        self.closed = False
        self.lock = threading.Lock()
        arguments = self_launch_arguments(sys.executable) + ["--startup-splash"]
        if self.socket_channel:
            self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.listener.bind(("127.0.0.1", 0))
            self.listener.listen(1)
            self.listener.settimeout(SPLASH_BACKLOG_TIMEOUT)
            arguments += [SPLASH_PORT_ARGUMENT, str(self.listener.getsockname()[1])]
        self.process = subprocess.Popen(
            arguments,
            stdin=subprocess.DEVNULL if self.socket_channel else subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", creationflags=process_creation_flags(),
        )
        if self.socket_channel:
            threading.Thread(target=self._accept_channel, daemon=True).start()

    def _accept_channel(self):
        """接受启动页的连接，并补发等待期间产生的状态文本。"""
        try:
            connection, _ = self.listener.accept()
        except OSError:
            return  # 启动页没能连回来，主窗口照常继续启动。
        with self.lock:
            if self.closed:
                pending, abandon = [], True
            else:
                self.connection, pending, abandon = connection, self.pending, False
                self.pending = []
        if abandon:
            connection.close()
            return
        try:
            for line in pending:
                connection.sendall(line)
        except OSError:
            pass

    def set_status(self, text):
        line = text.replace("\n", " ").encode("utf-8") + b"\n"
        try:
            if self.socket_channel:
                with self.lock:
                    if self.connection is None:
                        self.pending.append(line)
                    else:
                        self.connection.sendall(line)
            else:
                self.process.stdin.write(text.replace("\n", " ") + "\n")
                self.process.stdin.flush()
        except (OSError, ValueError):
            pass  # 启动页提前退出不影响主窗口启动。

    def close(self):
        try:
            if self.socket_channel:
                with self.lock:
                    self.closed = True
                    connection, self.connection = self.connection, None
                if connection is not None:
                    connection.close()
                if self.listener is not None:
                    self.listener.close()
            else:
                self.process.stdin.close()
        except OSError:
            pass


def run_startup_splash():
    """管道 EOF 同时处理正常交接和父进程异常退出。"""
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    splash = StartupSplash()

    class StartupMessages(QObject):
        status = Signal(str)
        finished = Signal()

    messages = StartupMessages()
    messages.status.connect(splash.status.setText)
    messages.finished.connect(app.quit)

    def read_parent():
        try:
            with _parent_status_channel() as pipe:
                for line in pipe:
                    messages.status.emit(line.decode("utf-8").rstrip("\r\n"))
        finally:
            messages.finished.emit()

    splash.show()
    reader = threading.Thread(target=read_parent, daemon=True)
    reader.start()
    return app.exec()


def _parent_status_channel():
    """启动页读取父进程状态的通道：打包后连回环端口，源码运行复用标准输入。"""
    port = splash_channel_port()
    if not port:
        # 使用独立无缓冲句柄，提前关闭窗口时后台读取不会锁住 sys.stdin。
        return os.fdopen(os.dup(sys.stdin.fileno()), "rb", buffering=0)
    connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    connection.settimeout(SPLASH_CONNECT_TIMEOUT)
    try:
        connection.connect(("127.0.0.1", port))
    except OSError:
        connection.close()
        raise
    connection.settimeout(None)
    return connection.makefile("rb")


_startup_splash = None
if __name__ == "__main__":
    if "--startup-splash" in sys.argv:
        sys.exit(run_startup_splash())
    try:
        _startup_splash = StartupSplashProcess()
    except OSError:
        pass
    else:
        atexit.register(_startup_splash.close)

from qframelesswindow import FramelessWindow, StandardTitleBar
from qfluentwidgets import (
    setTheme, Theme, isDarkTheme, FluentIcon as FIF,
    SwitchButton, PrimaryPushButton, PushButton, TransparentToolButton,
    LineEdit, ComboBox, SpinBox, TextEdit,
    TitleLabel, SubtitleLabel, StrongBodyLabel, BodyLabel, CaptionLabel,
    RoundMenu, Action, CardWidget, MessageBox, InfoBadgePosition
)

# 常量定义
APP_NAME = "隧道管家"

BASE_DIR = str(ROOT)
CONFIG_FILE = str(DATA_CONFIG_FILE)
SETTINGS_FILE = str(DATA_SETTINGS_FILE)
LOG_FILE = str(DATA_LOG_FILE)
LOGO_FILE = str(ASSET_LOGO_FILE)
STARTUP_TIMEOUT = 30  # 等待 SSH 完成认证和端口监听，避免每次轮询都重启。


def tray_menu_position(cursor, menu_size, screen_rect, available_rect, icon_rect=None):
    """托盘菜单左对齐图标，并限制在屏幕可用区域。"""
    rightmost = max(available_rect.left(), available_rect.right() - menu_size.width() + 1)
    anchor_x = (icon_rect.left() if icon_rect is not None and icon_rect.isValid()
                else cursor.x() - menu_size.width() // 2)
    x = max(available_rect.left(), min(anchor_x, rightmost))
    bottommost = max(available_rect.top(), available_rect.bottom() - menu_size.height() + 1)
    if available_rect.bottom() < screen_rect.bottom():
        y = bottommost
    elif available_rect.top() > screen_rect.top():
        y = available_rect.top()
    else:
        y = max(available_rect.top(), min(cursor.y() - menu_size.height() + 1, bottommost))
    return QPoint(x, y)

# 寻找默认 ssh.exe
DEFAULT_SSH = platform_support.default_ssh()


# ================= 系统与工具函数 =================

def get_pythonw_path():
    return platform_support.gui_python()


def set_autostart(enable: bool):
    return platform_support.set_autostart(enable, self_launch_arguments(get_pythonw_path()) +
                                          ['--data-dir', str(RUNTIME_HOME)])


def client_host(host):
    """通配监听地址不能作为客户端目标，连接时使用相应的回环地址。"""
    host = host.strip().strip("[]")
    return {"0.0.0.0": "127.0.0.1", "*": "127.0.0.1", "::": "::1"}.get(host, host)


def bracket_host(host):
    """在 SSH 转发参数和 host:port 地址中明确 IPv6 的边界。"""
    host = host.strip().strip("[]")
    return f"[{host}]" if ":" in host else host


def test_port_listening(port: int, host="127.0.0.1", timeout=0.3):
    """仅 TCP 连接成功才表示本地端口可用；绑定失败不能证明在线。"""
    try:
        with socket.create_connection((client_host(host), port), timeout=timeout):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def get_known_ssh_hosts(path=None):
    """解析 ~/.ssh/config 获取所有已配置的主机名"""
    return discover_hosts(path or None)


def resolve_connection(item, settings, manual_connections):
    """Resolve one immutable operation snapshot; never execute SSH during discovery."""
    runtime = {key: settings.get(key, default) for key, default in
               (("startupTimeout", 30), ("healthTimeout", 0.3), ("manualTimeout", 2))}
    runtime["args"] = []
    config_path = settings.get("sshConfigPath", "")
    if config_path:
        runtime["args"] = ["-F", os.path.expanduser(config_path)]
    reference = item.connection_id
    if reference.startswith("manual:"):
        record = next((row for row in manual_connections if "manual:" + row["id"] == reference), None)
        if record is None:
            runtime["error"] = f"手动 SSH 连接已缺失：{item.ssh_host}"
        else:
            try:
                runtime["args"].extend(connection_args(record))
                runtime["host"] = record["host"]
                if record.get("connectTimeout", 0) > runtime["startupTimeout"]:
                    runtime["error"] = "等待隧道就绪时间小于此连接的建连超时，请调整系统设置"
            except ValueError as error:
                runtime["error"] = str(error)
    elif reference.startswith("config:"):
        host = reference[len("config:"):]
        runtime["configHost"] = host
        runtime["configPath"] = config_path
        try:
            if host not in get_known_ssh_hosts(config_path):
                runtime["error"] = f"SSH 配置中的连接已缺失：{host}"
            else:
                runtime["host"] = host
        except (ValueError, OSError) as error:
            runtime["error"] = f"SSH 配置读取失败：{error}"
    elif reference:
        runtime["error"] = f"无法识别 SSH 连接引用：{reference}"
    return runtime


def make_circle_pixmap(color_hex: str, size=16):
    """动态生成系统托盘与状态徽章圆形指示图标"""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QBrush(QColor(color_hex)))
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(1, 1, size - 2, size - 2)
    painter.end()
    return pixmap


def create_app_logo_icon():
    """绘制高分辨率矢量质感 App Logo"""
    size = 64
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)

    # 蓝色圆角背景
    painter.setBrush(QBrush(QColor("#1677ff")))
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(0, 0, size, size, 14, 14)

    # 白色立体堆叠图层
    painter.setPen(QPen(QColor("#ffffff"), 3.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    cx, cy = size // 2, size // 2
    w, h, gap = 16, 7, 7

    # 顶层菱形
    painter.setBrush(QBrush(QColor("#ffffff")))
    top_poly = [
        QPoint(cx, cy - gap - h),
        QPoint(cx + w, cy - gap),
        QPoint(cx, cy - gap + h),
        QPoint(cx - w, cy - gap)
    ]
    painter.drawPolygon(top_poly)

    # 中层/底层折线
    painter.setBrush(Qt.NoBrush)
    painter.drawPolyline([QPoint(cx - w, cy), QPoint(cx, cy + h), QPoint(cx + w, cy)])
    painter.drawPolyline([QPoint(cx - w, cy + gap), QPoint(cx, cy + gap + h), QPoint(cx + w, cy + gap)])

    painter.end()
    return QIcon(pixmap)


# ================= 业务模型对象 =================

class TunnelItem:
    """单个 SSH 隧道的数据与生命周期管理"""
    def __init__(self, data: dict):
        self.name = data.get("name", "未命名隧道")
        self.ssh_host = data.get("sshHost", "")
        self.connection_id = data.get("connectionId", "")
        self.connection_runtime = deepcopy(data.get("_connectionRuntime", {}))
        self.local_host = data.get("localHost", "127.0.0.1")
        self.local_port = int(data.get("localPort", 13389))
        self.remote_host = data.get("remoteHost", "")
        self.remote_port = int(data.get("remotePort", 3389))
        self.enabled = bool(data.get("enabled", True))
        self.auto_reconnect = bool(data.get("autoReconnect", True))
        self.description = data.get("description", "")

        self.process = None
        self.pid = None  # 后台返回的进程快照，GUI 不直接访问 Popen。
        self.status = "Stopped" if not self.enabled else "Disconnected"
        self.retry_count = 0
        self.started_at = 0.0
        self._stderr_lines = deque(maxlen=200)
        self._stderr_thread = None
        self._exit_reported = False
        self.activity = None
        self.phase = None
        self.last_error = ""
        self._progress = None
        self.probe_result = ""
        self.probe_detail = ""

    def report_progress(self, phase):
        """后台只发送阶段快照，GUI 在主线程消费。"""
        if self._progress:
            self._progress(phase, self.retry_count)

    @property
    def local_address(self):
        return f"{bracket_host(client_host(self.local_host))}:{self.local_port}"

    @staticmethod
    def _read_stderr(stream, lines):
        """持续排空管道；线程只收集文本，不调用 Qt 或 GUI 日志函数。"""
        try:
            while True:
                line = stream.readline(4096)
                if not line:
                    break
                if line.strip():
                    lines.append(line.rstrip())
        except (OSError, ValueError):
            pass
        finally:
            stream.close()

    def drain_errors(self, logger, report_exit=True):
        """由隧道工作线程消费诊断；进程退出时先收齐尾部错误。"""
        returncode = self.process.poll() if self.process else None
        if (returncode is not None and self._stderr_thread
                and self._stderr_thread.ident is not None):
            self._stderr_thread.join(timeout=0.5)
        # 每次只消费进入此方法时的积压，避免持续输出占用整个 GUI 事件循环。
        for _ in range(len(self._stderr_lines)):
            try:
                line = self._stderr_lines.popleft()
            except IndexError:
                break
            logger(f'SSH [{self.name}]: {line}', "ERROR" if returncode not in (None, 0) else "WARN")
        if report_exit and returncode is not None and not self._exit_reported:
            logger(f'SSH 隧道 "{self.name}" 进程已退出 (退出码 {returncode})。', "ERROR")
            self._exit_reported = True

    def to_dict(self):
        return {
            "name": self.name,
            "sshHost": self.ssh_host,
            "connectionId": self.connection_id,
            "localHost": self.local_host,
            "localPort": self.local_port,
            "remoteHost": self.remote_host,
            "remotePort": self.remote_port,
            "enabled": self.enabled,
            "autoReconnect": self.auto_reconnect,
            "description": self.description,
        }

    def start_process(self, logger, ssh_exe, ssh_aliases=None):
        if not self.enabled:
            self.status = "Stopped"
            return

        self.stop_process(logger)

        if self.connection_runtime.get("configHost"):
            try:
                config_host = self.connection_runtime["configHost"]
                if config_host not in discover_hosts(self.connection_runtime.get("configPath") or None):
                    raise ValueError(f"SSH 配置中的连接已缺失：{config_host}")
                self.connection_runtime["host"] = config_host
                self.connection_runtime.pop("error", None)
            except (ValueError, OSError) as error:
                self.connection_runtime["error"] = str(error)
        if self.connection_runtime.get("error"):
            self.last_error = self.connection_runtime["error"]
            self.status = "Disconnected"
            logger(self.last_error, "ERROR")
            return

        self.report_progress("starting")
        self.last_error = ""
        actual_host = self.ssh_host
        if ssh_aliases and actual_host in ssh_aliases:
            actual_host = ssh_aliases[actual_host]
        actual_host = self.connection_runtime.get("host", actual_host)
        if not actual_host or actual_host.startswith("-") or any(c.isspace() for c in actual_host):
            self.last_error = "SSH 主机为空或格式不合法"
            self.status = "Disconnected"
            logger(self.last_error, "ERROR")
            return

        args = [
            ssh_exe,
            "-N",
            "-T",
            "-o", "ExitOnForwardFailure=yes",
            "-o", "BatchMode=yes",
            *self.connection_runtime.get("args", []),
            "-L", f"{bracket_host(self.local_host)}:{self.local_port}:{bracket_host(self.remote_host)}:{self.remote_port}",
            actual_host
        ]

        logger(f'SSH 连接 [{self.ssh_host}] 正在建立隧道 "{self.name}" {self.local_port} -> {self.remote_host}:{self.remote_port}')
        self.status = "Connecting"
        self.started_at = time.monotonic()
        self._stderr_lines = deque(maxlen=200)
        self._exit_reported = False

        creationflags = process_creation_flags()
        try:
            self.process = subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creationflags
            )
            self._stderr_thread = threading.Thread(
                target=self._read_stderr,
                args=(self.process.stderr, self._stderr_lines),
                daemon=True,
                name=f"ssh-stderr-{self.process.pid}",
            )
            self._stderr_thread.start()
        except Exception as e:
            self.stop_process(logger)
            logger(f'启动隧道 "{self.name}" 失败: {e}', "ERROR")
            self.status = "Disconnected"
            self.last_error = f"启动 SSH 失败：{e}"

    def stop_process(self, logger=None):
        self.report_progress("stopping")
        if self.process:
            try:
                if self.process.poll() is None:
                    if logger:
                        logger(f'已断开隧道 "{self.name}" (PID={self.process.pid})')
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait(timeout=2)
            except Exception:
                pass
            self.drain_errors(logger or (lambda *args: None), report_exit=False)
            if (self.process.stderr is not None
                    and (self._stderr_thread is None or not self._stderr_thread.is_alive())):
                self.process.stderr.close()
            self.process = None
        self._stderr_thread = None
        self.status = "Stopped" if not self.enabled else "Disconnected"
        self.activity = None  # GUI 操作状态，不持久化，也不替代实际连接状态。

    def check_health(self, logger, ssh_exe, ssh_aliases=None):
        """同步 I/O 仅供后台调度器调用，保持单隧道操作串行。"""
        if not self.enabled:
            return
        self.report_progress("checking")
        self.drain_errors(logger)
        proc_dead = self.process is None or self.process.poll() is not None
        if not proc_dead:
            self.report_progress("probing")
        probe_timeout = self.connection_runtime.get("healthTimeout", 0.3)
        port_ok = not proc_dead and test_port_listening(self.local_port, host=self.local_host, timeout=probe_timeout)
        proc_dead = self.process is None or self.process.poll() is not None
        if port_ok and not proc_dead:
            self.last_error = ""
            if self.status != "Connected":
                logger(f'隧道 "{self.name}" 连接成功 (本地端口 {self.local_port} 正常监听)。')
                self.status = "Connected"
                self.retry_count = 0
            return
        if (not proc_dead and self.status == "Connecting"
                and time.monotonic() - self.started_at < self.connection_runtime.get("startupTimeout", STARTUP_TIMEOUT)):
            return
        if self.auto_reconnect:
            self.retry_count += 1
            self.report_progress("retrying")
            logger(f'检测到隧道 "{self.name}" 本地端口 {self.local_port} 异常，正在自动自愈重连 (第 {self.retry_count} 次)...', "WARN")
            self.start_process(logger, ssh_exe, ssh_aliases)
        elif self.status != "Disconnected":
            self.stop_process(logger)
            self.status = "Disconnected"
            self.last_error = "SSH 进程退出或不存在" if proc_dead else "本地端口未就绪或监听超时"


class TunnelWorkerPool(QObject):
    """后台独占运行对象；GUI 只提交配置快照并收取状态和日志。"""
    logged = Signal(str, str)
    changed = Signal()
    finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="tunnel-io")
        self._entries = {}
        self._closing = False
        self._closed = False
        self._progress_events = SimpleQueue()
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._collect)

    @property
    def busy(self):
        return any(e["future"] is not None or e["pending"] is not None
                   for e in self._entries.values())

    def request(self, item, action, ssh_exe=DEFAULT_SSH, ssh_aliases=None):
        if self._closing:
            return
        if action in ("check", "probe") and not item.enabled:
            return
        if action == "start" and not item.enabled:
            action = "stop"
        entry = self._entries.setdefault(item, {
            "runtime": None, "future": None, "pending": None, "version": 0,
        })
        # 慢探测尚未完成时跳过重复心跳，避免积压和重叠重连。
        if action == "check" and (entry["future"] is not None or entry["pending"] is not None):
            return
        if action == "probe" and item.activity not in (None, "check"):
            return
        if action != "check":
            entry["version"] += 1
        if action not in ("check", "probe"):
            item.status = ("Connecting" if action == "start" else
                           "Stopped" if not item.enabled or action == "remove" else "Disconnected")
            item.probe_result = item.probe_detail = ""
        if action == "probe":
            item.probe_result = item.probe_detail = ""
        snapshot = item.to_dict()
        snapshot["_connectionRuntime"] = deepcopy(item.connection_runtime)
        entry["pending"] = (action, snapshot, ssh_exe, dict(ssh_aliases or {}), entry["version"])
        item.activity = action
        item.phase = "queued"
        if action == "start":
            item.last_error = ""
            item.retry_count = 0
        if entry["future"] is None:
            self._dispatch(entry)
        self._timer.start()
        if action != "check":
            self.changed.emit()

    def _dispatch(self, entry):
        action, data, exe, aliases, version = entry["pending"]
        entry["pending"] = None
        entry["action"], entry["submitted_version"] = action, version
        def progress(phase, retries):
            self._progress_events.put((entry, version, phase, retries))
        entry["future"] = self._executor.submit(self._execute, entry["runtime"], action, data, exe, aliases, progress)

    @staticmethod
    def _execute(runtime, action, data, exe, aliases, progress=None):
        logs = []
        def log(message, level="INFO"):
            logs.append((message, level))
        if runtime is None:
            runtime = TunnelItem(data)
        runtime.connection_runtime = deepcopy(data.get("_connectionRuntime", runtime.connection_runtime))
        runtime._progress = progress
        try:
            if action == "start":
                runtime.stop_process(log)
                runtime = TunnelItem(data)
                runtime._progress = progress
                runtime.start_process(log, exe, aliases)
            elif action == "check":
                runtime.check_health(log, exe, aliases)
            elif action == "probe":
                # 手动测试只验证端口和进程，不停止进程、不触发自动重连。
                runtime.report_progress("probing")
                try:
                    port_ok = test_port_listening(runtime.local_port, host=runtime.local_host,
                                                  timeout=runtime.connection_runtime.get("manualTimeout", 2.0))
                    alive = runtime.process is not None and runtime.process.poll() is None
                    ok = port_ok and alive
                    reason = ("本地 TCP 端口可连接，SSH 进程存活" if ok else
                              "SSH 进程未运行，无法确认端口属于此隧道" if port_ok else
                              "本地 TCP 端口连接失败或超时")
                except Exception as error:
                    ok, reason = False, f"测试异常：{error}"
                runtime.probe_result = "可用" if ok else "不可用"
                runtime.probe_detail = f"{runtime.local_address}：{reason}。仅验证本地转发入口。"
                runtime.status = "Connected" if ok else "Disconnected"
                runtime.last_error = "" if ok else reason
                if ok:
                    runtime.retry_count = 0
                log(f'隧道 "{runtime.name}" 连通性测试{runtime.probe_result}：{runtime.probe_detail}',
                    "INFO" if ok else "WARN")
            else:
                runtime.enabled = data["enabled"] if action == "stop" else False
                runtime.stop_process(log)
        except Exception as error:
            runtime.stop_process(log)
            runtime.last_error = f"后台操作失败：{error}"
            log(f'隧道 "{runtime.name}" 后台操作失败: {error}', "ERROR")
        finally:
            runtime._progress = None
        runtime.pid = (runtime.process.pid if runtime.process is not None
                       and runtime.process.poll() is None else None)
        return runtime, logs

    def _collect(self):
        changed = False
        items_by_entry = {id(entry): item for item, entry in self._entries.items()}
        while True:
            try:
                entry, version, phase, retries = self._progress_events.get_nowait()
            except Empty:
                break
            item = items_by_entry.get(id(entry))
            if item is not None and version == entry["version"] and entry["future"] is not None:
                item.phase = phase
                # 健康检查保留上一次结果，重试次数也随最终结果一起提交。
                if entry["action"] != "check":
                    item.retry_count = retries
                    changed = True
        for item, entry in list(self._entries.items()):
            future = entry["future"]
            if future is None or not future.done():
                continue
            runtime, logs = future.result()
            entry["runtime"], entry["future"] = runtime, None
            if entry["pending"] is None:
                item.activity = None
                item.phase = None
                changed |= entry["action"] != "check"
            # 旧任务仍负责交回进程以便清理，但不得覆盖编辑/停用后的界面。
            if entry["submitted_version"] == entry["version"]:
                changed |= ((item.status, item.retry_count, item.last_error, item.pid) !=
                            (runtime.status, runtime.retry_count, runtime.last_error, runtime.pid))
                item.status, item.retry_count = runtime.status, runtime.retry_count
                item.last_error = runtime.last_error
                item.pid = runtime.pid
                if entry["action"] == "probe":
                    item.probe_result, item.probe_detail = runtime.probe_result, runtime.probe_detail
                for message, level in logs:
                    self.logged.emit(message, level)
                if entry["action"] == "remove":
                    del self._entries[item]
                    continue
            if entry["pending"] is not None:
                self._dispatch(entry)
        if changed and not self._closing:
            self.changed.emit()
        if not self.busy:
            self._timer.stop()
        if self._closing and not self._entries and not self._closed:
            self._closed = True
            self._executor.shutdown(wait=False)
            self.finished.emit()

    def shutdown(self):
        if self._closing:
            return
        # 清理操作排在各自的运行任务后；GUI 不 join、不 wait。
        for item in list(self._entries):
            self.request(item, "remove")
        self._closing = True
        self._collect()


# ================= 隧道行控件与行内展开卡片 =================

# 图形、颜色、短标签、说明集中定义；不依赖字体符号或外部图片。
TUNNEL_INDICATORS = {
    "queued": ("hourglass", "#64748b", "排队中", "等待后台工作线程或上一操作结束"),
    "starting": ("link", "#d97706", "启动连接", "正在创建 SSH 转发进程"),
    "checking": ("pulse", "#0891b2", "检查进程", "检查 SSH 进程是否存活并收集诊断"),
    "probing": ("search", "#0891b2", "检测端口", "正在测试本地 TCP 转发入口"),
    "retrying": ("retry", "#d97706", "重连中", "检测失败，正在自动重连"),
    "stopping": ("stop", "#64748b", "停止中", "正在终止 SSH 进程并回收资源"),
    "removing": ("cross", "#64748b", "移除中", "正在清理待移除隧道的进程"),
    "Connected": ("check", "#16a34a", "已连接", "SSH 进程存活且本地端口可连接；不代表远端服务可用"),
    "Connecting": ("clock", "#d97706", "等待监听", "SSH 已启动，等待后续检查确认本地端口就绪"),
    "reconnect_wait": ("retry", "#d97706", "重连等待", "重连进程已启动，等待本地端口就绪"),
    "Stopped": ("pause", "#94a3b8", "已停用", "隧道已停用"),
    "Disconnected": ("cross", "#dc2626", "已断开", "本地转发入口尚未确认可用；查看日志了解详情"),
    "unknown": ("question", "#64748b", "状态未知", "尚无可识别的状态"),
}


def tunnel_indicator(item):
    """主动操作显示进度；后台检查始终显示上一次已确认的结果。"""
    activity = item.activity if item.activity != "check" else None
    key = item.status if item.status in TUNNEL_INDICATORS else "unknown"
    if key == "Connecting" and item.retry_count:
        key = "reconnect_wait"
    if activity:
        key = item.phase or {"start": "starting", "check": "checking", "probe": "probing",
                             "stop": "stopping", "remove": "removing"}.get(item.activity, "unknown")
        if item.activity == "remove" and key == "stopping":
            key = "removing"
        elif key == "starting" and item.retry_count:
            key = "retrying"
    symbol, color, text, detail = TUNNEL_INDICATORS.get(key, TUNNEL_INDICATORS["unknown"])
    busy = bool(activity) or item.status == "Connecting"
    if activity:
        action = {"start": "连接", "check": "状态测试", "probe": "连通性测试", "stop": "停止", "remove": "移除"}.get(item.activity, item.activity)
        actual = TUNNEL_INDICATORS.get(item.status, TUNNEL_INDICATORS["unknown"])[2]
        detail += f"\n当前操作：{action} · 连接状态：{actual}"
    if item.retry_count:
        detail += f"\n自动重连：第 {item.retry_count} 次"
    if item.last_error:
        detail += f"\n最近异常：{item.last_error}"
    return symbol, color, text, detail, busy


class TunnelStatusIcon(QWidget):
    """语义线条图标配活动圆弧；隐藏时暂停动画。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(20, 20)
        self._color = QColor("#94a3b8")
        self._symbol = "pause"
        self._busy = False
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self.update)

    def set_state(self, color, busy, symbol="pause"):
        self._color, self._busy, self._symbol = QColor(color), busy, symbol
        if busy and self.isVisible():
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        if self._busy:
            self._timer.start()

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(self._color, 1.7)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        if self._busy:
            ring_rect = (QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
                         if self._symbol == "spinner" else QRectF(1.5, 1.5, 17, 17))
            painter.setPen(QPen(QColor("#e2e8f0"), 1.3))
            painter.drawEllipse(ring_rect)
            painter.setPen(pen)
            angle = -int(time.monotonic() * 270) % 360
            painter.drawArc(ring_rect, angle * 16, 90 * 16)
        symbol = self._symbol
        if symbol == "spinner":
            return
        if symbol == "check":
            painter.drawEllipse(QRectF(2, 2, 16, 16))
            painter.drawLine(6, 10, 9, 13)
            painter.drawLine(9, 13, 14, 7)
        elif symbol == "cross":
            if not self._busy:
                painter.drawEllipse(QRectF(2, 2, 16, 16))
            painter.drawLine(7, 7, 13, 13)
            painter.drawLine(7, 13, 13, 7)
        elif symbol == "pause":
            painter.drawEllipse(QRectF(2, 2, 16, 16))
            painter.drawLine(8, 7, 8, 13)
            painter.drawLine(12, 7, 12, 13)
        elif symbol == "stop":
            painter.drawRoundedRect(QRectF(6, 6, 8, 8), 1, 1)
        elif symbol == "hourglass":
            path = QPainterPath()
            path.moveTo(7, 6)
            for x, y in ((13, 6), (7, 14), (13, 14), (7, 6)):
                path.lineTo(x, y)
            painter.drawPath(path)
        elif symbol == "search":
            painter.drawEllipse(QRectF(5, 5, 7, 7))
            painter.drawLine(11, 11, 15, 15)
        elif symbol == "retry":
            painter.drawArc(QRectF(5.5, 5.5, 9, 9), 40 * 16, 285 * 16)
            painter.drawLine(14, 5, 14, 9)
            painter.drawLine(14, 9, 10, 9)
        elif symbol == "clock":
            painter.drawEllipse(QRectF(5, 5, 10, 10))
            painter.drawLine(10, 7, 10, 10)
            painter.drawLine(10, 10, 13, 11)
        elif symbol == "link":
            painter.drawRoundedRect(QRectF(5, 8, 7, 5), 2, 2)
            painter.drawRoundedRect(QRectF(9, 6, 6, 5), 2, 2)
        elif symbol == "pulse":
            path = QPainterPath()
            path.moveTo(5, 10)
            for x, y in ((8, 10), (9, 6), (11, 14), (12, 10), (15, 10)):
                path.lineTo(x, y)
            painter.drawPath(path)
        else:
            painter.drawArc(QRectF(7, 5, 6, 6), -90 * 16, 260 * 16)
            painter.drawPoint(10, 14)


class DetectionButton(QPushButton):
    """固定画布：焦点与加载仅重绘，避免样式重套引起描边跳变。"""

    def __init__(self, parent=None):
        super().__init__("检测", parent)
        self.setFixedSize(76, 34)
        self.setFont(ui_font(9))
        self.setCursor(Qt.PointingHandCursor)

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        testing = bool(self.property("testing"))
        highlighted = testing or (self.isEnabled() and (self.underMouse() or self.hasFocus()))
        if highlighted:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#cffafe" if self.isDown() and not testing else "#ecfeff"))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 5, 5)
        painter.setPen(QColor("#334155" if self.isEnabled() or testing else "#94a3b8"))
        painter.setFont(self.font())
        painter.drawText(self.rect(), Qt.AlignCenter, self.text())


class TunnelRowWidget(QFrame):
    """单条隧道摘要行。"""
    toggled = Signal(int, bool)
    selected = Signal(int)
    deleted = Signal(int)
    reconnect = Signal(int)
    test_requested = Signal(int)

    def __init__(self, index: int, tunnel: TunnelItem, parent=None, is_selected=False):
        super().__init__(parent)
        self.index = index
        self.tunnel = tunnel

        self.setObjectName("tunnelRow")
        self.setProperty("selected", is_selected)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet("""
            QFrame#tunnelRow {
                background-color: #ffffff;
                border-bottom: 1px solid #f1f5f9;
            }
            QFrame#tunnelRow[selected="true"] {
                background-color: #e6f4f7;
                border-bottom: 1px solid #b6dfe6;
            }
            QFrame#tunnelRow QWidget {
                background-color: transparent;
            }
        """)

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        # 1. 摘要行容器 (Height: 50)
        self.summary_widget = QWidget(self)
        self.summary_widget.setFixedHeight(50)
        if is_selected:
            selection_bar = QFrame(self.summary_widget)
            selection_bar.setGeometry(0, 0, 3, 50)
            selection_bar.setStyleSheet("background-color: #0891b2; border: none;")
            selection_bar.raise_()
        self.summary_layout = QHBoxLayout(self.summary_widget)
        self.summary_layout.setContentsMargins(16, 0, 16, 0)
        self.summary_layout.setSpacing(0)

        # 1.1 SSH 进程 PID
        self.pid_lbl = QLabel("—", self.summary_widget)
        self.pid_lbl.setFixedWidth(70)
        self.pid_lbl.setFont(ui_font(9))
        self.pid_lbl.setStyleSheet("color: #64748b;")
        self.pid_lbl.setToolTip("当前 SSH 进程 PID；无运行进程时显示 —")
        self.summary_layout.addWidget(self.pid_lbl)

        # 状态 Badge 在 SSH 连接列之后显示。
        status_column = QWidget(self.summary_widget)
        status_column.setFixedWidth(110)
        status_layout = QHBoxLayout(status_column)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(0)
        self.status_btn = QPushButton(status_column)
        status_layout.addWidget(self.status_btn, 0, Qt.AlignLeft | Qt.AlignVCenter)
        status_layout.addStretch()
        status_box = self.status_btn
        status_box.setObjectName("reconnectStatus")
        status_box.setFixedHeight(34)
        status_box.setCursor(Qt.PointingHandCursor)
        status_box.setFocusPolicy(Qt.StrongFocus)
        status_box.setAccessibleName(f"重新连接隧道 {self.tunnel.name}")
        status_box.setStyleSheet("""
            QPushButton#reconnectStatus {
                background-color: transparent;
                border: 1px solid transparent;
                border-radius: 5px;
            }
            QPushButton#reconnectStatus:hover:enabled,
            QPushButton#reconnectStatus:focus:enabled {
                background-color: #ecfeff;
                border: 1px solid #67cbd8;
            }
            QPushButton#reconnectStatus:pressed:enabled {
                background-color: #cffafe;
                border: 1px solid #0891b2;
            }
        """)
        status_box.clicked.connect(lambda: self.reconnect.emit(self.index))
        s_layout = QHBoxLayout(status_box)
        s_layout.setContentsMargins(8, 0, 8, 0)
        s_layout.setSpacing(6)

        self.dot_label = TunnelStatusIcon(status_box)
        self.dot_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        s_layout.addWidget(self.dot_label)

        self.status_text_lbl = QLabel(status_box)
        self.status_text_lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.status_text_lbl.setFont(ui_font(9))
        s_layout.addWidget(self.status_text_lbl)

        # 1.2 隧道名称
        self.name_lbl = QLabel(self.tunnel.name, self.summary_widget)
        self.name_lbl.setFixedWidth(180)
        self.name_lbl.setFont(ui_font(9, QFont.Bold if self.tunnel.enabled else QFont.Normal))
        self.name_lbl.setStyleSheet("color: #0f172a;" if self.tunnel.enabled else "color: #94a3b8;")
        self.summary_layout.addWidget(self.name_lbl)

        # 1.3 本地端口
        self.lport_lbl = QLabel(str(self.tunnel.local_port) if self.tunnel.enabled else "-", self.summary_widget)
        self.lport_lbl.setFixedWidth(90)
        self.lport_lbl.setAlignment(Qt.AlignCenter)
        self.lport_lbl.setFont(ui_font(9))
        self.lport_lbl.setStyleSheet("color: #334155;" if self.tunnel.enabled else "color: #94a3b8;")
        self.summary_layout.addWidget(self.lport_lbl)

        # 1.4 远程目标映射
        target_str = f"{self.tunnel.remote_host}:{self.tunnel.remote_port}" if self.tunnel.enabled else "-"
        self.target_lbl = QLabel(target_str, self.summary_widget)
        self.target_lbl.setFixedWidth(180)
        self.target_lbl.setFont(ui_font(9))
        self.target_lbl.setStyleSheet("color: #334155;" if self.tunnel.enabled else "color: #94a3b8;")
        self.summary_layout.addWidget(self.target_lbl)

        # 1.5 SSH 连接
        self.ssh_lbl = QLabel(self.tunnel.ssh_host, self.summary_widget)
        self.ssh_lbl.setFixedWidth(110)
        self.ssh_lbl.setFont(ui_font(9))
        self.ssh_lbl.setStyleSheet("color: #334155;")
        self.summary_layout.addWidget(self.ssh_lbl)
        self.summary_layout.addWidget(status_column)

        # 1.6 启用 Switch 开关
        switch_box = QWidget(self.summary_widget)
        switch_box.setFixedWidth(80)
        sw_layout = QHBoxLayout(switch_box)
        sw_layout.setContentsMargins(0, 0, 0, 0)
        sw_layout.setAlignment(Qt.AlignCenter)
        self.switch_btn = SwitchButton(switch_box)
        self.switch_btn.setOnText("")
        self.switch_btn.setOffText("")
        self.switch_btn.setAccessibleName(f"启用隧道 {self.tunnel.name}")
        self.switch_btn.setChecked(self.tunnel.enabled)
        self.switch_btn.checkedChanged.connect(lambda v: self.toggled.emit(self.index, v))
        sw_layout.addWidget(self.switch_btn)
        self.summary_layout.addWidget(switch_box)

        test_box = QWidget(self.summary_widget)
        test_box.setFixedWidth(80)
        test_layout = QHBoxLayout(test_box)
        test_layout.setContentsMargins(2, 0, 2, 0)
        self.test_btn = DetectionButton(test_box)
        self.test_btn.setObjectName("connectivityTest")
        # 文字始终居中；小图标独立叠放在右侧，不参与文字或按钮布局。
        self.test_spinner = TunnelStatusIcon(self.test_btn)
        self.test_spinner.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.test_spinner.setFixedSize(16, 16)
        self.test_spinner.move(54, 9)
        self.test_spinner.hide()
        self.test_btn.setAccessibleName(f"检测隧道 {self.tunnel.name} 连通性")
        self.test_btn.clicked.connect(lambda: self.test_requested.emit(self.index))
        test_layout.addWidget(self.test_btn)
        self.summary_layout.addWidget(test_box)

        # 1.7 操作菜单按钮 (⋮)
        action_box = QWidget(self.summary_widget)
        action_box.setFixedWidth(50)
        ac_layout = QHBoxLayout(action_box)
        ac_layout.setContentsMargins(0, 0, 0, 0)
        ac_layout.setAlignment(Qt.AlignCenter)

        self.btn_more = TransparentToolButton(FIF.MORE, action_box)
        self.btn_more.clicked.connect(self._show_action_menu)
        ac_layout.addWidget(self.btn_more)
        self.summary_layout.addWidget(action_box)

        self.main_layout.addWidget(self.summary_widget)

        # 更新状态指示灯与文字
        self._update_status_display()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.selected.emit(self.index)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _update_status_display(self):
        self.status_btn.setEnabled(self.tunnel.activity in (None, "check"))
        testing = self.tunnel.activity == "probe"
        if self.test_btn.property("testing") != testing:
            self.test_btn.setProperty("testing", testing)
            self.test_btn.update()
            self.test_spinner.set_state("#0891b2", testing, "spinner")
            self.test_spinner.setVisible(testing)
        self.test_btn.setEnabled(self.tunnel.enabled and self.tunnel.activity in (None, "check"))
        self.test_btn.setToolTip(self.tunnel.probe_detail or "测试本地 TCP 转发端口（超时 2 秒），不会触发重连；不验证远端应用协议。")
        pid_text = str(self.tunnel.pid) if self.tunnel.pid is not None else "—"
        if self.pid_lbl.text() != pid_text:
            self.pid_lbl.setText(pid_text)
        indicator = tunnel_indicator(self.tunnel)
        if indicator == getattr(self, "_displayed_indicator", None):
            return
        self._displayed_indicator = indicator
        symbol, color, text, detail, busy = indicator
        tooltip = f"{text}\n{detail}"
        self.status_btn.setToolTip(f"{tooltip}\n点击重新连接此隧道（已停用时将启用）")
        self.dot_label.set_state(color, busy, symbol)
        self.dot_label.setToolTip(tooltip)
        self.dot_label.setAccessibleName(text)
        self.dot_label.setAccessibleDescription(detail)
        self.status_text_lbl.setToolTip(tooltip)
        self.status_text_lbl.setText(text)
        self.status_text_lbl.setStyleSheet(f"color: {color};")
        # 列宽保持不变；高亮边框只包住图标、文字及对称内边距。
        self.status_btn.setFixedWidth(
            self.dot_label.width() + 6 + self.status_text_lbl.sizeHint().width() + 18)

    def _show_action_menu(self):
        menu = RoundMenu(parent=self)

        act_rdp = Action(FIF.CLOUD, "打开远程桌面 (RDP)", self)
        if sys.platform != 'win32':
            act_rdp.setEnabled(False)
            act_rdp.setText("远程桌面：请复制地址到客户端")
        act_rdp.triggered.connect(self._launch_rdp)
        menu.addAction(act_rdp)

        act_copy = Action(FIF.COPY, "复制本地连接地址", self)
        act_copy.triggered.connect(self._copy_address)
        menu.addAction(act_copy)

        menu.addSeparator()

        act_del = Action(FIF.DELETE, "删除此隧道", self)
        act_del.triggered.connect(lambda: self.deleted.emit(self.index))
        menu.addAction(act_del)

        pos = self.btn_more.mapToGlobal(QPoint(0, self.btn_more.height()))
        menu.exec(pos)

    def _launch_rdp(self):
        if sys.platform != 'win32':
            return
        addr = self.tunnel.local_address
        subprocess.Popen(["mstsc.exe", f"/v:{addr}"])

    def _copy_address(self):
        addr = self.tunnel.local_address
        QApplication.clipboard().setText(addr)

# ================= 隧道辅助工作区 =================

class TunnelWorkspace(QFrame):
    """嵌在主窗口右侧的新建与详情编辑表单。"""
    create_requested = Signal(dict)
    save_requested = Signal(int, dict)
    close_requested = Signal()

    def __init__(self, parent=None, ssh_options=None):
        super().__init__(parent)
        self.edit_index = None
        self.setObjectName("tunnelWorkspace")
        self.setStyleSheet("""
            QFrame#tunnelWorkspace {
                background: #ffffff;
                border-left: 1px solid #cbd5e1;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        self.intro_label = BodyLabel("填写连接信息后创建隧道", self)
        layout.addWidget(self.intro_label)

        grid = QGridLayout()
        grid.setVerticalSpacing(14)
        grid.setHorizontalSpacing(14)

        grid.addWidget(BodyLabel("隧道名称:", self), 0, 0)
        self.edit_name = LineEdit(self)
        self.edit_name.setText("新端口隧道")
        grid.addWidget(self.edit_name, 0, 1)

        grid.addWidget(BodyLabel("SSH 连接:", self), 1, 0)
        self.combo_ssh = ComboBox(self)
        self.set_ssh_options(ssh_options or [])
        grid.addWidget(self.combo_ssh, 1, 1)

        grid.addWidget(BodyLabel("本地地址:", self), 2, 0)
        self.edit_lhost = LineEdit(self)
        self.edit_lhost.setText("127.0.0.1")
        grid.addWidget(self.edit_lhost, 2, 1)

        grid.addWidget(BodyLabel("本地端口:", self), 3, 0)
        self.edit_lport = LineEdit(self)
        self.edit_lport.setText("13389")
        grid.addWidget(self.edit_lport, 3, 1)

        grid.addWidget(BodyLabel("目标地址:", self), 4, 0)
        self.edit_rhost = LineEdit(self)
        self.edit_rhost.setPlaceholderText("目标服务的地址")
        grid.addWidget(self.edit_rhost, 4, 1)

        grid.addWidget(BodyLabel("目标端口:", self), 5, 0)
        self.edit_rport = LineEdit(self)
        self.edit_rport.setText("3389")
        grid.addWidget(self.edit_rport, 5, 1)

        layout.addLayout(grid)
        self.error_label = CaptionLabel("", self)
        self.error_label.setStyleSheet("color: #dc2626; background: transparent;")
        self.error_label.hide()
        layout.addWidget(self.error_label)
        layout.addStretch()

        btn_box = QHBoxLayout()
        btn_box.addStretch()
        self.btn_cancel = PushButton("取消", self)
        self.btn_cancel.clicked.connect(self.close_requested)
        btn_box.addWidget(self.btn_cancel)

        self.btn_create = PrimaryPushButton("立即创建", self)
        self.btn_create.clicked.connect(self._submit)
        btn_box.addWidget(self.btn_create)

        layout.addLayout(btn_box)

    def get_data(self):
        index = self.combo_ssh.currentIndex()
        selected = self._connection_options[index] if 0 <= index < len(self._connection_options) else {}
        return {
            "name": self.edit_name.text().strip(),
            "sshHost": selected.get("host", self.combo_ssh.currentText().strip()),
            "connectionId": selected.get("id", ""),
            "localHost": self.edit_lhost.text().strip() or "127.0.0.1",
            "localPort": int(self.edit_lport.text().strip() or "13389"),
            "remoteHost": self.edit_rhost.text().strip(),
            "remotePort": int(self.edit_rport.text().strip() or "3389"),
            "enabled": True,
            "autoReconnect": True
        }

    def set_ssh_options(self, options):
        self.combo_ssh.clear()
        self._connection_options = []
        seen = set()
        for option in options:
            record = option if isinstance(option, dict) else {"id": "", "name": option, "host": option}
            key = record.get("id") or record["name"]
            if key in seen:
                continue
            seen.add(key)
            self._connection_options.append(record)
            label = record["name"]
            if record.get("source"):
                label += " · " + record["source"]
            self.combo_ssh.addItem(label)

    def reset_form(self):
        self.edit_index = None
        self.intro_label.setText("填写连接信息后创建隧道")
        self.btn_create.setText("立即创建")
        self.edit_name.setText("新端口隧道")
        self.edit_lhost.setText("127.0.0.1")
        self.edit_lport.setText("13389")
        self.edit_rhost.clear()
        self.edit_rport.setText("3389")
        self.error_label.hide()

    def load_tunnel(self, index, tunnel):
        self.edit_index = index
        self.intro_label.setText("查看并修改隧道连接信息")
        self.btn_create.setText("保存修改")
        self.edit_name.setText(tunnel.name)
        selected = next((i for i, row in enumerate(self._connection_options)
                         if (row.get("id") == tunnel.connection_id if tunnel.connection_id
                             else not row.get("id") and row.get("host") == tunnel.ssh_host)), None)
        if selected is None:
            selected = len(self._connection_options)
            self._connection_options.append({"id": tunnel.connection_id, "name": tunnel.ssh_host, "host": tunnel.ssh_host})
            self.combo_ssh.addItem(tunnel.ssh_host + (" · 连接缺失" if tunnel.connection_id else " · 旧配置"))
        self.combo_ssh.setCurrentIndex(selected)
        self.edit_lhost.setText(tunnel.local_host)
        self.edit_lport.setText(str(tunnel.local_port))
        self.edit_rhost.setText(tunnel.remote_host)
        self.edit_rport.setText(str(tunnel.remote_port))
        self.error_label.hide()

    def _show_error(self, message):
        self.error_label.setText(message)
        self.error_label.show()

    def _submit(self):
        try:
            data = self.get_data()
        except ValueError:
            self._show_error("端口必须是 1–65535 之间的数字")
            return
        if not data["name"]:
            self._show_error("请输入隧道名称")
            return
        if not data["sshHost"]:
            self._show_error("请选择 SSH 连接")
            return
        if not data["remoteHost"]:
            self._show_error("请输入目标地址")
            return
        if not (1 <= data["localPort"] <= 65535 and 1 <= data["remotePort"] <= 65535):
            self._show_error("端口必须是 1–65535 之间的数字")
            return
        self.error_label.hide()
        if self.edit_index is None:
            self.create_requested.emit(data)
        else:
            self.save_requested.emit(self.edit_index, data)


# ================= 系统配置工作区 =================

class SystemSettingsWorkspace(QFrame):
    """嵌在主窗口右侧的系统配置表单。"""
    save_requested = Signal(dict)
    close_requested = Signal()
    quit_requested = Signal()

    def __init__(self, settings: dict, parent=None):
        super().__init__(parent)
        self.setObjectName("settingsWorkspace")
        self.setFont(ui_font(9))
        self.setStyleSheet("""
            QFrame#settingsWorkspace {
                background: #ffffff;
                border-left: 1px solid #cbd5e1;
            }
            QTabWidget::pane { border: none; background: white; }
            QTabBar::tab { padding: 8px 14px; background: #f1f5f9; color: #475569; }
            QTabBar::tab:selected { background: white; color: #008b98; border-bottom: 2px solid #00a4af; }
            QScrollArea, QScrollArea > QWidget > QWidget { background: white; }
            QLineEdit, QDoubleSpinBox { background: white; border: 1px solid #cbd5e1; border-radius: 4px; padding: 5px; }
            QLineEdit:focus { border: 1px solid #00a4af; }
            QListWidget { background: white; border: 1px solid #dbe3ec; border-radius: 4px; }
            QListWidget::item { padding: 7px 4px; }
            QListWidget::item:selected { background: #dff5f6; color: #075963; }
            QPushButton { padding: 6px 8px; border: 1px solid #d0dae5; border-radius: 4px; background: #fff; }
            QPushButton:hover { background: #f0f9fa; }
            QPushButton:disabled { color: #94a3b8; background: #f8fafc; }
        """)
        self.settings = dict(settings)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(12, 12, 12, 12)
        self.tabs = QTabWidget(self)
        root_layout.addWidget(self.tabs)
        general = QWidget(self)
        general_scroll = QScrollArea(self)
        general_scroll.setWidgetResizable(True)
        general_scroll.setFrameShape(QFrame.NoFrame)
        general_scroll.setWidget(general)
        self.tabs.addTab(general_scroll, "常规")
        layout = QVBoxLayout(general)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(16)

        # 1. 开机自启
        f_auto = QHBoxLayout()
        lbl_auto = StrongBodyLabel("用户登录时自动启动", self)
        f_auto.addWidget(lbl_auto)
        f_auto.addStretch()
        self.sw_auto = SwitchButton(self)
        self.sw_auto.setToolTip("保存后在下次登录桌面时生效")
        self.sw_auto.setChecked(is_autostart_enabled())
        f_auto.addWidget(self.sw_auto)
        layout.addLayout(f_auto)

        # 2. 最小化到托盘
        f_tray = QHBoxLayout()
        lbl_tray = BodyLabel("窗口关闭后最小化到系统托盘", self)
        f_tray.addWidget(lbl_tray)
        f_tray.addStretch()
        self.sw_tray = SwitchButton(self)
        self.sw_tray.setChecked(self.settings.get("minimizeToTray", True))
        f_tray.addWidget(self.sw_tray)
        layout.addLayout(f_tray)

        # 3. SSH 路径
        layout.addWidget(BodyLabel("SSH 客户端可执行文件路径:", self))
        f_ssh = QHBoxLayout()
        self.edit_ssh = LineEdit(self)
        self.edit_ssh.setText(self.settings.get("sshPath", DEFAULT_SSH))
        f_ssh.addWidget(self.edit_ssh)
        btn_br = PushButton("浏览...", self)
        btn_br.clicked.connect(self._browse_ssh)
        f_ssh.addWidget(btn_br)
        layout.addLayout(f_ssh)

        # 4. 心跳频率
        f_int = QHBoxLayout()
        f_int.addWidget(BodyLabel("断线心跳检测探活频率 (秒):", self))
        f_int.addStretch()
        self.spin_int = SpinBox(self)
        self.spin_int.setRange(2, 60)
        self.spin_int.setValue(self.settings.get("checkInterval", 4))
        f_int.addWidget(self.spin_int)
        layout.addLayout(f_int)

        layout.addWidget(BodyLabel("SSH 配置文件（留空使用 ~/.ssh/config）:", self))
        self.edit_config = LineEdit(self)
        self.edit_config.setPlaceholderText(str(Path.home() / ".ssh" / "config"))
        layout.addWidget(self.edit_config)
        self.sw_connect = SwitchButton(self)
        layout.addWidget(BodyLabel("启动时连接已启用隧道", self))
        layout.addWidget(self.sw_connect)
        self.timeout_spins = {}
        for key, label, low, high, default in (
            ("startupTimeout", "等待隧道就绪（秒）", 1, 600, 30),
            ("healthTimeout", "自动端口检测超时（秒）", 0.1, 60, 0.3),
            ("manualTimeout", "手动端口检测超时（秒）", 0.1, 60, 2),
        ):
            row = QHBoxLayout()
            row.addWidget(BodyLabel(label, self))
            spin = QDoubleSpinBox(self)
            spin.setRange(low, high)
            spin.setDecimals(1)
            spin.setValue(settings.get(key, default))
            row.addWidget(spin)
            layout.addLayout(row)
            self.timeout_spins[key] = spin
        self.connections_editor = ConnectionSettingsEditor(self)
        connection_scroll = QScrollArea(self)
        connection_scroll.setWidgetResizable(True)
        connection_scroll.setFrameShape(QFrame.NoFrame)
        connection_scroll.setWidget(self.connections_editor)
        self.tabs.addTab(connection_scroll, "SSH 连接")
        self.edit_config.editingFinished.connect(
            lambda: self.connections_editor.set_config_path(self.edit_config.text().strip() or str(Path.home() / ".ssh" / "config")))

        # 彻底退出程序按钮
        self.btn_quit = PushButton("彻底退出隧道管家程序", self)
        self.btn_quit.setFixedHeight(34)
        self.btn_quit.setStyleSheet("color: #b91c1c; border: 1px solid #fca5a5; border-radius: 6px; background-color: #fef2f2;")
        self.btn_quit.clicked.connect(self.quit_requested.emit)
        layout.addWidget(self.btn_quit)

        version_label = CaptionLabel(f"版本 {APP_VERSION}", self)
        version_label.setStyleSheet("color: #64748b;")
        layout.addWidget(version_label)

        layout.addStretch()

        btn_box = QHBoxLayout()
        btn_box.addStretch()
        self.btn_cancel = PushButton("取消", self)
        self.btn_cancel.clicked.connect(self.close_requested.emit)
        btn_box.addWidget(self.btn_cancel)

        self.btn_save = PrimaryPushButton("保存配置", self)
        self.btn_save.clicked.connect(self._save_settings)
        btn_box.addWidget(self.btn_save)

        self.error_label = CaptionLabel("", self)
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #b91c1c;")
        root_layout.addWidget(self.error_label)
        root_layout.addLayout(btn_box)
        self.load_settings(settings)

    def load_settings(self, settings: dict):
        self.settings = dict(settings)
        self.sw_auto.setChecked(is_autostart_enabled())
        self.sw_tray.setChecked(settings.get("minimizeToTray", True))
        self.edit_ssh.setText(settings.get("sshPath", DEFAULT_SSH))
        self.spin_int.setValue(settings.get("checkInterval", 4))
        self.edit_config.setText(settings.get("sshConfigPath", ""))
        self.sw_connect.setChecked(settings.get("connectOnStartup", True))
        for key, default in (("startupTimeout", 30), ("healthTimeout", 0.3), ("manualTimeout", 2)):
            self.timeout_spins[key].setValue(settings.get(key, default))
        self.error_label.clear()

    def _browse_ssh(self):
        file_filter = "Executable (*.exe);;All Files (*)" if sys.platform == 'win32' else "All Files (*)"
        f, _ = QFileDialog.getOpenFileName(self, "选择 SSH 可执行文件", filter=file_filter)
        if f:
            self.edit_ssh.setText(f)

    def _save_settings(self):
        try:
            records = self.connections_editor.records()
            wait = self.timeout_spins["startupTimeout"].value()
            if any(float(record.get("connectTimeout", 0) or 0) > wait for record in records):
                raise ValueError("等待隧道就绪时间不能小于手动连接的建连超时")
        except (ValueError, OSError) as error:
            self.error_label.setText(str(error))
            return
        self.save_requested.emit({
            "autostart": self.sw_auto.isChecked(),
            "minimizeToTray": self.sw_tray.isChecked(),
            "sshPath": self.edit_ssh.text().strip() or DEFAULT_SSH,
            "checkInterval": self.spin_int.value(),
            "sshConfigPath": self.edit_config.text().strip(),
            "connectOnStartup": self.sw_connect.isChecked(),
            **{key: spin.value() for key, spin in self.timeout_spins.items()},
            "_manualConnections": records,
        })


# ================= 主窗口程序 =================

class MainWindow(FramelessWindow):
    """固定尺寸的隧道管家主窗口，保留拖动、最小化及关闭到托盘。"""
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        self.setWindowIcon(create_app_logo_icon())
        self._main_width, _ = layout_widths(False)
        self._workspace_width = layout_widths(True)[1]
        self.setFixedSize(window_size(False))
        self.setResizeEnabled(False)
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, False)
        if sys.platform == 'win32':
            self.windowEffect.disableMaximizeButton(self.winId())

        setTheme(Theme.LIGHT)
        self.setStyleSheet("MainWindow { background-color: #f8fafc; }")

        # 数据模型
        self.tunnels = []
        self.manual_connections = []
        self._startup_errors = []
        self._config_load_error = False
        self._manual_load_error = False
        self.settings = {
            "autostart": False,
            "minimizeToTray": True,
            "sshPath": DEFAULT_SSH,
            "checkInterval": 4,
            "sshAliases": {},
            "sshConfigPath": "",
            "connectOnStartup": True,
            "startupTimeout": 30,
            "healthTimeout": 0.3,
            "manualTimeout": 2,
        }
        self.selected_index = None
        self.running = True
        self.is_paused = False
        self.tray = None

        self.load_settings()
        self.connections_file = str(Path(SETTINGS_FILE).with_name("ssh_connections.json"))
        try:
            self.manual_connections = load_connections(self.connections_file)
        except (ValueError, OSError) as error:
            self._manual_load_error = True
            self._startup_errors.append(f"手动连接配置读取失败：{error}")
        self.load_config()

        # 系统托盘
        self._init_system_tray()

        # 初始化界面
        self._setup_title_bar()
        self._setup_ui()
        for error in self._startup_errors:
            self.log(error, "ERROR")
        self.window_outline = WindowOutline(self)
        self.window_outline.setGeometry(self.rect())
        self.window_outline.raise_()

        # 后台探活重连守护定时器
        self.tunnel_jobs = TunnelWorkerPool(self)
        self.tunnel_jobs.logged.connect(self.log)
        self.tunnel_jobs.changed.connect(self._refresh_tunnel_statuses)
        self.tunnel_jobs.finished.connect(QApplication.quit)
        self.supervisor_timer = QTimer(self)
        self.supervisor_timer.timeout.connect(self.check_tunnels_health)
        self.supervisor_timer.start(self.settings.get("checkInterval", 4) * 1000)

        # 启动初始化隧道
        QTimer.singleShot(200, self.initial_startup)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "window_outline"):
            self.window_outline.setGeometry(self.rect())
            self.window_outline.raise_()

    def load_settings(self):
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if not isinstance(loaded, dict):
                    raise ValueError("全局配置必须是 JSON 对象")
                for key, low, high in (("checkInterval", 2, 60), ("startupTimeout", 1, 600),
                                       ("healthTimeout", 0.1, 60), ("manualTimeout", 0.1, 60)):
                    if key in loaded and (isinstance(loaded[key], bool) or not isinstance(loaded[key], (int, float))
                                          or not low <= loaded[key] <= high):
                        raise ValueError(f"{key} 必须在 {low}–{high} 之间")
                for key in ("sshPath", "sshConfigPath"):
                    if key in loaded and not isinstance(loaded[key], str):
                        raise ValueError(f"{key} 必须是字符串")
                aliases = loaded.get("sshAliases", {})
                if not isinstance(aliases, dict) or any(not isinstance(v, str) for v in aliases.values()):
                    raise ValueError("sshAliases 必须是名称到 SSH 主机的映射")
                loaded["checkInterval"] = int(loaded.get("checkInterval", 4))
                self.settings.update(loaded)
            except (ValueError, OSError) as error:
                self._startup_errors.append(f"系统设置读取失败，使用默认设置：{error}")
        self.settings["autostart"] = is_autostart_enabled()

    def save_settings(self):
        atomic_write_json(SETTINGS_FILE, self.settings)

    def load_config(self):
        if not os.path.exists(CONFIG_FILE):
            self.tunnels = []
            return

        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                raise ValueError("隧道配置必须是 JSON 数组")
            for item in data:
                if not isinstance(item, dict) or not item.get("sshHost") or not item.get("remoteHost"):
                    raise ValueError("隧道缺少 SSH 主机或远程目标")
                for key in ("connectionId", "sshHost", "remoteHost", "localHost", "name"):
                    if key in item and not isinstance(item[key], str):
                        raise ValueError(f"隧道字段 {key} 必须是字符串")
                if any(not 1 <= int(item.get(key, 0)) <= 65535 for key in ("localPort", "remotePort")):
                    raise ValueError("隧道端口必须在 1–65535 之间")
            self.tunnels = [TunnelItem(item) for item in data]
        except (ValueError, TypeError, OSError) as error:
            self.tunnels = []
            self._config_load_error = True
            self._startup_errors.append(f"隧道配置读取失败，未启动任何隧道：{error}")

    def save_config(self):
        try:
            if self._config_load_error:
                raise ValueError("原隧道配置读取失败，请修复文件并重新启动，避免覆盖原内容")
            data = [t.to_dict() for t in self.tunnels]
            atomic_write_json(CONFIG_FILE, data)
            return True
        except Exception as e:
            self.log(f"保存配置文件异常: {e}", "ERROR")
            return False

    # ================= 标题栏定制 =================

    def _setup_title_bar(self):
        tb = self.titleBar
        tb.setFixedHeight(56)
        tb.maxBtn.hide()
        tb.setDoubleClickEnabled(False)
        # 标题、计数、操作与窗口控制共用一行；中间的伸缩空间仍可拖动窗口。
        self.lbl_list_title = SubtitleLabel("端口隧道列表", tb)
        self.lbl_list_title.setFont(ui_font(13, QFont.Bold))
        self.lbl_list_title.setStyleSheet("color: #0f172a;")
        self.lbl_list_title.setAttribute(Qt.WA_TransparentForMouseEvents)

        self.lbl_active_count = QLabel("(0/0)", tb)
        self.lbl_active_count.setFont(ui_font(13, QFont.Bold))
        self.lbl_active_count.setStyleSheet("color: #16a34a; margin-left: 16px;")
        self.lbl_active_count.setAttribute(Qt.WA_TransparentForMouseEvents)
        tb.hBoxLayout.insertSpacing(0, 24)
        tb.hBoxLayout.insertWidget(1, self.lbl_list_title, 0, Qt.AlignVCenter)
        tb.hBoxLayout.insertWidget(2, self.lbl_active_count, 0, Qt.AlignVCenter)

        self.btn_new = PrimaryPushButton(FIF.ADD, "新建隧道", tb)
        self.btn_new.setFont(ui_font(9, QFont.Bold))
        self.btn_new.setFixedHeight(32)
        self.btn_new.clicked.connect(self.toggle_new_tunnel_workspace)

        # 保留 Fluent 的 hasIcon 样式，为图标和文字预留独立空间。
        self.btn_config = PushButton(FIF.SETTING, "系统配置", tb)
        self.btn_config.setFont(ui_font(9))
        self.btn_config.setFixedHeight(32)
        self.btn_config.clicked.connect(self.open_system_settings_workspace)

        idx = tb.hBoxLayout.indexOf(tb.minBtn)
        tb.hBoxLayout.insertWidget(idx, self.btn_new, 0, Qt.AlignVCenter)
        tb.hBoxLayout.insertSpacing(idx + 1, 10)
        tb.hBoxLayout.insertWidget(idx + 2, self.btn_config, 0, Qt.AlignVCenter)
        tb.hBoxLayout.insertSpacing(idx + 3, 12)

        self.workspace_title_bar = QFrame(tb)
        self.workspace_title_bar.setObjectName("workspaceTitleBar")
        self.workspace_title_bar.setFixedSize(self._workspace_width, tb.height())
        self.workspace_title_bar.setStyleSheet("""
            QFrame#workspaceTitleBar {
                background-color: #f8fafc;
                border-left: 1px solid #cbd5e1;
            }
        """)
        workspace_title_layout = QHBoxLayout(self.workspace_title_bar)
        workspace_title_layout.setContentsMargins(24, 0, 8, 0)
        workspace_title_layout.setSpacing(0)
        self.workspace_title = SubtitleLabel("新建隧道", self.workspace_title_bar)
        self.workspace_title.setFont(ui_font(13, QFont.Bold))
        self.workspace_title.setStyleSheet("color: #0f172a; background: transparent;")
        workspace_title_layout.addWidget(self.workspace_title)
        workspace_title_layout.addStretch()
        self.workspace_close_btn = TransparentToolButton(FIF.CLOSE, self.workspace_title_bar)
        self.workspace_close_btn.setFixedSize(40, 32)
        self.workspace_close_btn.setToolTip("关闭工作区")
        self.workspace_close_btn.clicked.connect(self._collapse_workspace)
        workspace_title_layout.addWidget(self.workspace_close_btn)
        tb.hBoxLayout.addWidget(self.workspace_title_bar)
        self.workspace_title_bar.hide()

    # ================= 核心界面布局 =================

    def _setup_ui(self):
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, self.titleBar.height(), 0, 0)
        self.main_layout.setSpacing(0)

        self.content_row = QWidget(self)
        content_layout = QHBoxLayout(self.content_row)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        self.main_layout.addWidget(self.content_row)

        central = QWidget(self.content_row)
        self.central = central
        central.setFixedWidth(self._main_width)
        central.setStyleSheet("background-color: #f8fafc;")
        content_layout.addWidget(central)

        layout = QVBoxLayout(central)
        layout.setContentsMargins(24, 12, 24, 16)
        layout.setSpacing(12)

        # 2. 列表卡片容器
        self.table_card = CardWidget(central)
        self.table_card.setStyleSheet("""
            CardWidget {
                background-color: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
            }
        """)
        card_layout = QVBoxLayout(self.table_card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)

        # 2.1 表头
        header = QFrame(self.table_card)
        header.setFixedHeight(38)
        header.setStyleSheet("QFrame { background-color: #f8fafc; border-bottom: 1px solid #e2e8f0; border-top-left-radius: 8px; border-top-right-radius: 8px; }")
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(16, 0, 16, 0)
        h_layout.setSpacing(0)

        col_defs = [
            ("PID", 70, Qt.AlignLeft | Qt.AlignVCenter),
            ("隧道名称", 180, Qt.AlignLeft | Qt.AlignVCenter),
            ("本地端口", 90, Qt.AlignCenter),
            ("远程目标映射", 180, Qt.AlignLeft | Qt.AlignVCenter),
            ("SSH 连接", 110, Qt.AlignLeft | Qt.AlignVCenter),
            ("状态", 110, Qt.AlignLeft | Qt.AlignVCenter),
            ("启用", 80, Qt.AlignCenter),
            ("连通性", 80, Qt.AlignCenter),
            ("操作", 50, Qt.AlignCenter),
        ]
        for name, w, align in col_defs:
            lbl = QLabel(name, header)
            lbl.setFixedWidth(w)
            lbl.setAlignment(align)
            lbl.setFont(ui_font(9, QFont.Bold))
            lbl.setStyleSheet("color: #64748b; background: transparent; border: none;")
            h_layout.addWidget(lbl)

        card_layout.addWidget(header)

        # 2.2 滚动行容器
        self.scroll_area = QScrollArea(self.table_card)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.NoFrame)
        self.scroll_area.setStyleSheet("QScrollArea { background-color: transparent; border: none; }")

        self.rows_container = QWidget()
        self.rows_container.setStyleSheet("QWidget { background-color: #ffffff; }")
        self.rows_layout = QVBoxLayout(self.rows_container)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(0)
        self.rows_layout.addStretch()

        self.scroll_area.setWidget(self.rows_container)
        card_layout.addWidget(self.scroll_area)
        layout.addWidget(self.table_card, stretch=1)

        # 3. 底部可折叠控制台日志区
        self.log_card = CardWidget(central)
        self.log_card.setStyleSheet("""
            CardWidget {
                background-color: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
            }
        """)
        log_layout = QVBoxLayout(self.log_card)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.setSpacing(0)

        # 日志头部操作栏
        log_bar = QFrame(self.log_card)
        log_bar.setFixedHeight(36)
        log_bar.setStyleSheet("QFrame { background-color: #ffffff; border-radius: 8px; }")
        lb_layout = QHBoxLayout(log_bar)
        lb_layout.setContentsMargins(16, 0, 16, 0)

        self.lbl_log_toggle = PushButton("∨  运行日志", log_bar)
        self.lbl_log_toggle.setStyleSheet("color: #0f172a; font-weight: bold; border: none; background: transparent; font-size: 13px;")
        self.lbl_log_toggle.setCursor(Qt.PointingHandCursor)
        self.lbl_log_toggle.clicked.connect(self.toggle_log_fold)
        lb_layout.addWidget(self.lbl_log_toggle)
        lb_layout.addStretch()

        self.btn_clear_log = PushButton(FIF.DELETE, "清空日志", log_bar)
        self.btn_clear_log.setObjectName("clearLogButton")
        self.btn_clear_log.setFont(ui_font(9))
        self.btn_clear_log.setFixedHeight(30)
        self.btn_clear_log.setStyleSheet("""
            PushButton#clearLogButton {
                color: #334155;
                background-color: transparent;
                border: none;
                border-radius: 6px;
                padding: 4px 10px 4px 30px;
            }
            PushButton#clearLogButton:hover {
                background-color: #edf3f8;
            }
            PushButton#clearLogButton:pressed {
                background-color: #dbe7f1;
            }
        """)
        self.btn_clear_log.clicked.connect(self.clear_log)
        lb_layout.addWidget(self.btn_clear_log)

        log_layout.addWidget(log_bar)

        # 日志输出文本框
        self.log_text = TextEdit(self.log_card)
        self.log_text.setReadOnly(True)
        self.log_text.setFixedHeight(120)
        self.log_text.setFont(mono_font(9))
        self.log_text.setStyleSheet("""
            TextEdit {
                background-color: #ffffff;
                border: none;
                border-bottom-left-radius: 8px;
                border-bottom-right-radius: 8px;
                padding: 8px;
            }
        """)
        log_layout.addWidget(self.log_text)
        layout.addWidget(self.log_card)

        self.tunnel_workspace = TunnelWorkspace(self.content_row)
        self.tunnel_workspace.setFixedWidth(self._workspace_width)
        content_layout.addWidget(self.tunnel_workspace)
        self.tunnel_workspace.close_requested.connect(self._collapse_workspace)
        self.tunnel_workspace.create_requested.connect(self._create_tunnel_from_workspace)
        self.tunnel_workspace.save_requested.connect(self._save_tunnel_from_workspace)
        self.tunnel_workspace.hide()

        self.settings_workspace = SystemSettingsWorkspace(self.settings, self.content_row)
        self.settings_workspace.setFixedWidth(self._workspace_width)
        content_layout.addWidget(self.settings_workspace)
        self.settings_workspace.close_requested.connect(self._collapse_workspace)
        self.settings_workspace.save_requested.connect(self._save_settings_from_workspace)
        self.settings_workspace.quit_requested.connect(self.quit_app)
        self.settings_workspace.hide()

        self.refresh_table()

    def toggle_log_fold(self):
        is_visible = self.log_text.isVisible()
        self.log_text.setVisible(not is_visible)
        self.lbl_log_toggle.setText("∨  运行日志" if not is_visible else ">  运行日志")

    def clear_log(self):
        self.log_text.clear()

    def log(self, message: str, level="INFO"):
        t_str = time.strftime("%H:%M:%S")
        level_colors = {
            "INFO": "#0284c7",
            "WARN": "#d97706",
            "ERROR": "#dc2626"
        }
        col = level_colors.get(level, "#0284c7")

        # 格式化 HTML 着色
        msg_html = message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        if "连接成功" in msg_html:
            msg_html = msg_html.replace("连接成功", '<span style="color:#16a34a; font-weight:bold;">连接成功</span>')
        if "已停用，跳过" in msg_html:
            msg_html = msg_html.replace("已停用，跳过", '<span style="color:#94a3b8;">已停用，跳过</span>')

        line = f'<span style="color:#94a3b8;">{t_str}</span> &nbsp;<span style="color:{col}; font-weight:bold;">[{level}]</span> &nbsp;{msg_html}'
        self.log_text.append(line)

        # 写入文件
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"[{t_str}] [{level}] {message}\n")
        except Exception:
            pass

    # ================= 列表渲染与交互 =================

    def _refresh_tunnel_statuses(self):
        """后台结果只更新状态，避免重新创建行造成布局抖动和焦点丢失。"""
        for index in range(self.rows_layout.count()):
            row = self.rows_layout.itemAt(index).widget()
            if isinstance(row, TunnelRowWidget):
                row._update_status_display()
        connected = sum(t.enabled and t.status == "Connected" for t in self.tunnels)
        self.lbl_active_count.setText(f"({connected}/{len(self.tunnels)})")
        self.update_tray_state()

    def refresh_table(self):
        # 清空现有行
        while self.rows_layout.count() > 1:
            item = self.rows_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        total = len(self.tunnels)
        connected = sum(1 for t in self.tunnels if t.enabled and t.status == "Connected")
        self.lbl_active_count.setText(f"({connected}/{total})")

        for idx, t in enumerate(self.tunnels):
            row = TunnelRowWidget(
                index=idx,
                tunnel=t,
                parent=self.rows_container,
                is_selected=(self.selected_index == idx)
            )
            row.toggled.connect(self.on_tunnel_toggle)
            row.selected.connect(self.select_tunnel)
            row.deleted.connect(self.on_tunnel_deleted)
            row.reconnect.connect(self.on_tunnel_reconnect)
            row.test_requested.connect(self.on_tunnel_test)
            self.rows_layout.insertWidget(idx, row)

        self.update_tray_state()

    def on_tunnel_test(self, idx: int):
        if self.running and not self.is_paused and 0 <= idx < len(self.tunnels):
            self._queue_tunnel(self.tunnels[idx], "probe")

    def on_tunnel_toggle(self, idx: int, enabled: bool):
        t = self.tunnels[idx]
        t.enabled = enabled
        if t.enabled:
            self.log(f'启用隧道 "{t.name}" {t.local_port} -> {t.remote_host}:{t.remote_port}')
            self._queue_tunnel(t, "start")
        else:
            self.log(f'隧道 "{t.name}" 已停用，跳过')
            self._queue_tunnel(t, "stop")

        self.save_config()
        self.refresh_table()

    def on_tunnel_saved(self, idx: int):
        t = self.tunnels[idx]
        if self.save_config() is False:
            self.tunnel_workspace._show_error("保存隧道失败，请检查文件权限或磁盘空间后重试")
            return False
        self.log(f'隧道 "{t.name}" 配置修改已保存。')
        if t.enabled and not self.is_paused:
            self._queue_tunnel(t, "start")
        self.refresh_table()
        return True

    def on_tunnel_deleted(self, idx: int):
        t = self.tunnels[idx]
        self._queue_tunnel(t, "remove")
        del self.tunnels[idx]
        if self.selected_index == idx:
            self._collapse_workspace()
        elif self.selected_index is not None and self.selected_index > idx:
            self.selected_index -= 1
            if self.tunnel_workspace.edit_index is not None:
                self.tunnel_workspace.edit_index -= 1
        self.save_config()
        self.log(f'隧道 "{t.name}" 已删除。')
        self.refresh_table()

    def on_tunnel_reconnect(self, idx: int):
        t = self.tunnels[idx]
        t.enabled = True
        self.log(f'手动触发重新连接: "{t.name}"')
        self._queue_tunnel(t, "start")
        self.refresh_table()

    def _apply_layout(self, expanded):
        """Recompute every fixed width from the screen the window sits on."""
        self._main_width, workspace = layout_widths(expanded)
        if workspace:
            self._workspace_width = workspace
        self.central.setFixedWidth(self._main_width)
        self.tunnel_workspace.setFixedWidth(self._workspace_width)
        self.settings_workspace.setFixedWidth(self._workspace_width)
        self.workspace_title_bar.setFixedSize(self._workspace_width, self.titleBar.height())
        self.setFixedSize(window_size(expanded))

    def _collapse_workspace(self):
        self.tunnel_workspace.hide()
        self.settings_workspace.hide()
        self.workspace_title_bar.hide()
        self._apply_layout(False)
        if self.selected_index is not None:
            self.selected_index = None
            self.refresh_table()

    def _show_workspace(self, title):
        """Expand by the workspace width the current screen can actually show."""
        self._apply_layout(True)
        self.workspace_title.setText(title)
        self.workspace_title_bar.show()

    def select_tunnel(self, idx: int):
        if not 0 <= idx < len(self.tunnels):
            return
        if self.selected_index == idx:
            self._collapse_workspace()
            return
        self.selected_index = idx
        workspace = self.tunnel_workspace
        self.settings_workspace.hide()
        ssh_opts = self.connection_options()
        workspace.set_ssh_options(ssh_opts)
        workspace.load_tunnel(idx, self.tunnels[idx])
        self._show_workspace("隧道详情")
        workspace.show()
        self.refresh_table()

    def toggle_new_tunnel_workspace(self):
        workspace = self.tunnel_workspace
        if workspace.isVisible() and workspace.edit_index is None:
            self._collapse_workspace()
            return
        if self.selected_index is not None:
            self.selected_index = None
            self.refresh_table()
        self.settings_workspace.hide()
        ssh_opts = self.connection_options()
        workspace.set_ssh_options(ssh_opts)
        workspace.reset_form()
        self._show_workspace("新建隧道")
        workspace.show()
        workspace.edit_name.setFocus()
        workspace.edit_name.selectAll()

    def _create_tunnel_from_workspace(self, data):
        if self._config_load_error:
            self.tunnel_workspace._show_error("请先修复无法读取的 tunnels.json 并重新启动")
            return
        new_t = TunnelItem(data)
        self.tunnels.append(new_t)
        if self.save_config() is False:
            self.tunnels.remove(new_t)
            self.tunnel_workspace._show_error("保存隧道失败，请检查文件权限或磁盘空间后重试")
            return
        self.log(f'成功创建隧道 "{new_t.name}"')
        self._queue_tunnel(new_t, "start")
        self.refresh_table()
        self._collapse_workspace()

    def _save_tunnel_from_workspace(self, idx: int, data: dict):
        if not 0 <= idx < len(self.tunnels):
            return
        t = self.tunnels[idx]
        previous = t.to_dict()
        t.name = data["name"]
        t.ssh_host = data["sshHost"]
        t.connection_id = data.get("connectionId", "")
        t.local_host = data["localHost"]
        t.local_port = data["localPort"]
        t.remote_host = data["remoteHost"]
        t.remote_port = data["remotePort"]
        if self.on_tunnel_saved(idx) is False:
            for attr, key in (("name", "name"), ("ssh_host", "sshHost"), ("connection_id", "connectionId"),
                              ("local_host", "localHost"), ("local_port", "localPort"),
                              ("remote_host", "remoteHost"), ("remote_port", "remotePort")):
                setattr(t, attr, previous[key])

    def open_system_settings_workspace(self):
        workspace = self.settings_workspace
        if workspace.isVisible():
            self._collapse_workspace()
            return
        self.tunnel_workspace.hide()
        if self.selected_index is not None:
            self.selected_index = None
            self.refresh_table()
        workspace.load_settings(self.settings)
        self._load_connection_editor()
        self._show_workspace("系统配置")
        workspace.show()

    def _save_settings_from_workspace(self, data: dict):
        data = dict(data)
        records = data.pop("_manualConnections", self.manual_connections)
        previous = self.settings
        old_records = self.manual_connections
        try:
            if self._manual_load_error:
                raise ValueError("手动连接文件读取失败，请修复文件并重新启动后再保存")
            records = [validate_connection(record) for record in records]
            retained = {"manual:" + record["id"] for record in records}
            removed = [t.name for t in self.tunnels if t.connection_id.startswith("manual:")
                       and t.connection_id not in retained]
            if removed:
                raise ValueError("无法移除被隧道引用的连接：" + "、".join(removed))
            updated = {**previous, **data}
            # Validate/write manual file first; on a later failure restore its old data.
            manual_changed = records != old_records
            if manual_changed:
                save_connections(self.connections_file, records)
            try:
                atomic_write_json(SETTINGS_FILE, updated)
            except (OSError, ValueError):
                if manual_changed:
                    save_connections(self.connections_file, old_records)
                raise
        except (OSError, ValueError) as error:
            self.settings_workspace.error_label.setText(f"保存失败：{error}")
            self.log(f"系统配置保存失败：{error}", "ERROR")
            return
        autostart_ok = set_autostart(data["autostart"])
        self.settings = updated
        self.manual_connections = records
        self.supervisor_timer.setInterval(self.settings.get("checkInterval", 4) * 1000)
        old_by_id = {record["id"]: record for record in old_records}
        changed_ids = {"manual:" + record["id"] for record in records
                       if old_by_id.get(record["id"]) != record}
        ssh_changed = any(previous.get(key) != updated.get(key) for key in ("sshPath", "sshConfigPath"))
        for item in self.tunnels:
            if item.enabled and (ssh_changed or item.connection_id in changed_ids):
                self._queue_tunnel(item, "start")
        if autostart_ok is False:
            self.settings_workspace.error_label.setText("连接与设置已保存，但开机自启更新失败，请检查权限")
            self.log("连接与设置已保存，但开机自启更新失败", "ERROR")
            return
        self.log("系统配置已更新。")
        self._collapse_workspace()

    def connection_options(self):
        options = []
        try:
            hosts = get_known_ssh_hosts(self.settings.get("sshConfigPath"))
        except (ValueError, OSError) as error:
            hosts = []
            self.log(f"SSH 配置读取失败：{error}", "ERROR")
        for host in hosts:
            options.append({"id": "config:" + host, "name": host, "host": host, "source": "SSH 配置"})
        for name, host in self.settings.get("sshAliases", {}).items():
            # Retain legacy display names without hijacking a same-named config entry.
            options.append({"id": "", "name": name, "host": name, "source": "旧别名 → " + host})
        options.extend({"id": "manual:" + row["id"], "name": row["name"],
                        "host": row["host"], "source": "手动"} for row in self.manual_connections)
        return options

    def _load_connection_editor(self):
        editor = self.settings_workspace.connections_editor
        path = self.settings.get("sshConfigPath") or str(Path.home() / ".ssh" / "config")
        error_text = ""
        try:
            hosts = get_known_ssh_hosts(self.settings.get("sshConfigPath"))
        except (ValueError, OSError) as error:
            hosts = []
            error_text = str(error)
        editor.load(self.manual_connections, hosts, path,
                    {reference: [t.name for t in self.tunnels if t.connection_id == reference]
                     for reference in {t.connection_id for t in self.tunnels if t.connection_id}})
        if error_text:
            editor.error.setText(error_text)

    # ================= 状态栏 / 系统托盘 =================

    def _init_system_tray(self):
        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(QIcon(make_circle_pixmap("#eab308", 16)))
        self.tray.setToolTip(f"{APP_NAME} [初始化中...]")

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #ffffff;
                border: 1px solid #dbe3ea;
                padding: 6px 8px 6px 16px;
            }
            QMenu::item {
                color: #0f172a;
                padding: 11px 18px 11px 10px;
            }
            QMenu::item:selected {
                background-color: #eaf4f6;
            }
            QMenu::separator {
                height: 1px;
                background-color: #e2e8f0;
                margin: 5px 10px;
            }
        """)

        act_open = Action(FIF.APPLICATION, "打开主界面", self)
        act_open.triggered.connect(self._restore_from_tray)
        menu.addAction(act_open)

        act_recon = Action(FIF.SYNC, "重新连接全部隧道", self)
        act_recon.triggered.connect(self.reconnect_all)
        menu.addAction(act_recon)

        menu.addSeparator()

        act_quit = Action(FIF.CLOSE, "彻底退出", self)
        act_quit.triggered.connect(self.quit_app)
        menu.addAction(act_quit)

        self.tray_menu = menu
        if sys.platform != 'win32':
            self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.Context and sys.platform == 'win32':
            self._show_tray_menu()
        elif reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._restore_from_tray()

    def _show_tray_menu(self):
        cursor = QCursor.pos()
        screen = (QApplication.screenAt(cursor)
                  or QApplication.screenAt(self.tray.geometry().center())
                  or QApplication.primaryScreen())
        if screen is None:
            return
        menu = self.tray_menu
        menu.ensurePolished()
        position = tray_menu_position(
            cursor, menu.sizeHint(), screen.geometry(), screen.availableGeometry(),
            self.tray.geometry()
        )
        menu.popup(position)

    def _restore_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def update_tray_state(self):
        if not getattr(self, "tray", None):
            return
        total_enabled = sum(1 for t in self.tunnels if t.enabled)
        connected_count = sum(1 for t in self.tunnels if t.enabled and t.status == "Connected")

        if self.is_paused or total_enabled == 0:
            self.tray.setIcon(QIcon(make_circle_pixmap("#94a3b8", 16)))
            self.tray.setToolTip(f"{APP_NAME} [服务暂停/无运行]")
        elif connected_count == total_enabled:
            self.tray.setIcon(QIcon(make_circle_pixmap("#22c55e", 16)))
            self.tray.setToolTip(f"{APP_NAME}: 全部在线 ({connected_count}/{total_enabled})")
        elif connected_count == 0 and not any(t.enabled and t.status == "Connecting" for t in self.tunnels):
            self.tray.setIcon(QIcon(make_circle_pixmap("#ef4444", 16)))
            self.tray.setToolTip(f"{APP_NAME}: 全部断开 (0/{total_enabled} 在线)")
        else:
            self.tray.setIcon(QIcon(make_circle_pixmap("#eab308", 16)))
            self.tray.setToolTip(f"{APP_NAME}: 连接中/部分断开 ({connected_count}/{total_enabled} 在线)")

    def closeEvent(self, event):
        """拦截窗口关闭按钮"""
        if self.settings.get("minimizeToTray", True) and QSystemTrayIcon.isSystemTrayAvailable():
            event.ignore()
            self.hide()
            self.tray.showMessage(APP_NAME, "已最小化至任务栏状态栏托盘，随时可点击图标打开。", QSystemTrayIcon.Information, 2000)
        else:
            event.ignore()
            self.quit_app()

    # ================= 业务生命周期与自连接 =================

    def _queue_tunnel(self, item, action):
        if action == "start":
            self.is_paused = False
            getattr(self, "_startup_held", set()).discard(item)
        if action == "start" or not item.connection_runtime:
            item.connection_runtime = resolve_connection(item, self.settings, getattr(self, "manual_connections", []))
        else:
            for key, default in (("startupTimeout", 30), ("healthTimeout", 0.3), ("manualTimeout", 2)):
                item.connection_runtime[key] = self.settings.get(key, default)
        self.tunnel_jobs.request(item, action, self.settings.get("sshPath", DEFAULT_SSH),
                                 self.settings.get("sshAliases"))

    def initial_startup(self):
        if not self.running:
            return
        if not self.settings.get("connectOnStartup", True):
            self.is_paused = True
            self._startup_held = {t for t in self.tunnels if t.enabled}
            self.log("已关闭启动自动连接；手动重连可启动隧道。")
            return
        self.log(f"隧道管家 {APP_VERSION} 服务已启动，开始建立 SSH 连接…")
        total = len(self.tunnels)
        for idx, t in enumerate(self.tunnels):
            num_tag = f"[{idx+1}/{total}]"
            if t.enabled:
                self._queue_tunnel(t, "start")
            else:
                t.status = "Stopped"
                self.log(f'{num_tag} 隧道 "{t.name}" 已停用，跳过')

        self.refresh_table()
        QTimer.singleShot(2500, self._report_online_summary)

    def _report_online_summary(self):
        if not self.running:
            return
        self.check_tunnels_health()
        total = sum(1 for t in self.tunnels if t.enabled)
        connected = sum(1 for t in self.tunnels if t.enabled and t.status == "Connected")
        connecting = sum(1 for t in self.tunnels if t.enabled and t.status == "Connecting")
        self.log(f"当前启用隧道：{connected}/{total} 条在线，{connecting} 条连接中。")

    def reconnect_all(self):
        self.is_paused = False
        self.log("用户触发: 重新连接所有隧道...")
        for t in self.tunnels:
            self._queue_tunnel(t, "start" if t.enabled else "stop")
        self.refresh_table()

    def check_tunnels_health(self):
        """仅投递心跳任务，所有网络和进程等待都在后台执行。"""
        if self.is_paused or not self.running:
            return
        for t in self.tunnels:
            if t.enabled and t not in getattr(self, "_startup_held", set()):
                self._queue_tunnel(t, "check")

    def quit_app(self):
        if not self.running:
            return
        self.running = False
        self.supervisor_timer.stop()
        self.hide()
        if hasattr(self, "tray"):
            self.tray.hide()
        self.tunnel_jobs.shutdown()


def main():
    if platform_support.is_frozen():
        # 打包入口不经过 bootstrap.launch()，运行目录在这里准备；只读安装位置要提示用户。
        try:
            ensure_runtime_dirs()
        except OSError as error:
            platform_support.show_error('无法创建运行目录：\n' + str(error) +
                                        '\n\n请用 --data-dir 指定一个可写目录。')
            return 1
    app = QApplication.instance()
    if app is None:
        QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
        app = QApplication(sys.argv)
    if _startup_splash is not None:
        _startup_splash.set_status("正在准备隧道列表…")
    w = MainWindow()
    show_main_window(app, w, _startup_splash)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
