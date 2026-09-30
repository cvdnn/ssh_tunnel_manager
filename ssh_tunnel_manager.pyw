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
import winreg
import subprocess
import threading
import ctypes
import atexit
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from queue import SimpleQueue, Empty

from PySide6.QtCore import Qt, QTimer, Signal, QObject, QSize, QPoint, QRectF
from PySide6.QtGui import QIcon, QColor, QFont, QPixmap, QPainter, QBrush, QPen, QCursor, QPainterPath
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
    QFrame, QScrollArea, QFileDialog, QSystemTrayIcon, QGridLayout, QMenu,
    QSizePolicy, QProgressBar, QPushButton
)


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
        title = QLabel("隧道管家", self)
        title.setFont(QFont("Microsoft YaHei UI", 16, QFont.Bold))
        title.setStyleSheet("color: #0f172a;")
        layout.addWidget(title)
        layout.addSpacing(10)
        self.status = QLabel("正在加载界面…", self)
        self.status.setFont(QFont("Microsoft YaHei UI", 9))
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
    """启动页独立运行，重型导入和主窗口构造不会阻塞其事件循环。"""

    def __init__(self):
        self.process = subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), "--startup-splash"],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW,
        )

    def set_status(self, text):
        try:
            self.process.stdin.write(text.replace("\n", " ") + "\n")
            self.process.stdin.flush()
        except (OSError, ValueError):
            pass  # 启动页提前退出不影响主窗口启动。

    def close(self):
        try:
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
            # 使用独立无缓冲句柄，提前关闭窗口时后台读取不会锁住 sys.stdin。
            with os.fdopen(os.dup(sys.stdin.fileno()), "rb", buffering=0) as pipe:
                for line in pipe:
                    messages.status.emit(line.decode("utf-8").rstrip("\r\n"))
        finally:
            messages.finished.emit()

    splash.show()
    reader = threading.Thread(target=read_parent, daemon=True)
    reader.start()
    return app.exec()


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
REG_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
REG_ITEM_NAME = "SshTunnelManager"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "tunnels.json")
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
LOG_FILE = os.path.join(BASE_DIR, "ssh_tunnel.log")
LOGO_FILE = os.path.join(BASE_DIR, "app_logo.png")
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
DEFAULT_SSH = r"C:\Windows\System32\OpenSSH\ssh.exe"
if not os.path.exists(DEFAULT_SSH):
    import shutil
    found_ssh = shutil.which("ssh")
    if found_ssh:
        DEFAULT_SSH = found_ssh


# ================= 系统与工具函数 =================

def get_pythonw_path():
    """获取 pythonw.exe 的绝对路径，用于无黑框静默运行"""
    exe_dir = os.path.dirname(sys.executable)
    pyw_candidate = os.path.join(exe_dir, "pythonw.exe")
    if os.path.exists(pyw_candidate):
        return pyw_candidate
    return sys.executable


def is_autostart_enabled():
    """检查是否已开启开机自启动"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_RUN_KEY, 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, REG_ITEM_NAME)
            return bool(val)
    except OSError:
        return False


def set_autostart(enable: bool):
    """设置或取消开机自启动"""
    pyw = get_pythonw_path()
    script_path = os.path.abspath(__file__)
    cmd = f'"{pyw}" "{script_path}"'
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enable:
                winreg.SetValueEx(key, REG_ITEM_NAME, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(key, REG_ITEM_NAME)
                except OSError:
                    pass
        return True
    except Exception as e:
        print(f"设置开机启动失败: {e}")
        return False


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


def get_known_ssh_hosts():
    """解析 ~/.ssh/config 获取所有已配置的主机名"""
    hosts = []
    ssh_config_path = os.path.expanduser("~/.ssh/config")
    if os.path.exists(ssh_config_path):
        try:
            with open(ssh_config_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("Host ") or line.startswith("host "):
                        parts = line.split()[1:]
                        for p in parts:
                            if "*" not in p and "?" not in p and p not in hosts:
                                hosts.append(p)
        except Exception:
            pass
    return hosts


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
        self.ssh_host = data.get("sshHost", "g")
        self.local_host = data.get("localHost", "127.0.0.1")
        self.local_port = int(data.get("localPort", 13389))
        self.remote_host = data.get("remoteHost", "192.168.122.200")
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

        self.report_progress("starting")
        self.last_error = ""
        actual_host = self.ssh_host
        if ssh_aliases and actual_host in ssh_aliases:
            actual_host = ssh_aliases[actual_host]

        args = [
            ssh_exe,
            "-N",
            "-T",
            "-o", "ExitOnForwardFailure=yes",
            "-o", "ServerAliveInterval=15",
            "-o", "ServerAliveCountMax=3",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=15",
            "-o", "StrictHostKeyChecking=no",
            "-L", f"{bracket_host(self.local_host)}:{self.local_port}:{bracket_host(self.remote_host)}:{self.remote_port}",
            actual_host
        ]

        logger(f'SSH 连接 [{self.ssh_host}] 正在建立隧道 "{self.name}" {self.local_port} -> {self.remote_host}:{self.remote_port}')
        self.status = "Connecting"
        self.started_at = time.monotonic()
        self._stderr_lines = deque(maxlen=200)
        self._exit_reported = False

        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
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
        port_ok = not proc_dead and test_port_listening(self.local_port, host=self.local_host)
        proc_dead = self.process is None or self.process.poll() is not None
        if port_ok and not proc_dead:
            self.last_error = ""
            if self.status != "Connected":
                logger(f'隧道 "{self.name}" 连接成功 (本地端口 {self.local_port} 正常监听)。')
                self.status = "Connected"
                self.retry_count = 0
            return
        if (not proc_dead and self.status == "Connecting"
                and time.monotonic() - self.started_at < STARTUP_TIMEOUT):
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
        entry["pending"] = (action, item.to_dict(), ssh_exe, dict(ssh_aliases or {}), entry["version"])
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
                    port_ok = test_port_listening(runtime.local_port, host=runtime.local_host, timeout=2.0)
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
        self.setFont(QFont("Microsoft YaHei UI", 9))
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
        self.pid_lbl.setFont(QFont("Microsoft YaHei UI", 9))
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
        self.status_text_lbl.setFont(QFont("Microsoft YaHei UI", 9))
        s_layout.addWidget(self.status_text_lbl)

        # 1.2 隧道名称
        self.name_lbl = QLabel(self.tunnel.name, self.summary_widget)
        self.name_lbl.setFixedWidth(180)
        self.name_lbl.setFont(QFont("Microsoft YaHei UI", 9, QFont.Bold if self.tunnel.enabled else QFont.Normal))
        self.name_lbl.setStyleSheet("color: #0f172a;" if self.tunnel.enabled else "color: #94a3b8;")
        self.summary_layout.addWidget(self.name_lbl)

        # 1.3 本地端口
        self.lport_lbl = QLabel(str(self.tunnel.local_port) if self.tunnel.enabled else "-", self.summary_widget)
        self.lport_lbl.setFixedWidth(90)
        self.lport_lbl.setAlignment(Qt.AlignCenter)
        self.lport_lbl.setFont(QFont("Microsoft YaHei UI", 9))
        self.lport_lbl.setStyleSheet("color: #334155;" if self.tunnel.enabled else "color: #94a3b8;")
        self.summary_layout.addWidget(self.lport_lbl)

        # 1.4 远程目标映射
        target_str = f"{self.tunnel.remote_host}:{self.tunnel.remote_port}" if self.tunnel.enabled else "-"
        self.target_lbl = QLabel(target_str, self.summary_widget)
        self.target_lbl.setFixedWidth(180)
        self.target_lbl.setFont(QFont("Microsoft YaHei UI", 9))
        self.target_lbl.setStyleSheet("color: #334155;" if self.tunnel.enabled else "color: #94a3b8;")
        self.summary_layout.addWidget(self.target_lbl)

        # 1.5 SSH 连接
        self.ssh_lbl = QLabel(self.tunnel.ssh_host, self.summary_widget)
        self.ssh_lbl.setFixedWidth(110)
        self.ssh_lbl.setFont(QFont("Microsoft YaHei UI", 9))
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
        self.edit_rhost.setText("192.168.122.200")
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
        return {
            "name": self.edit_name.text().strip(),
            "sshHost": self.combo_ssh.currentText().strip(),
            "localHost": self.edit_lhost.text().strip() or "127.0.0.1",
            "localPort": int(self.edit_lport.text().strip() or "13389"),
            "remoteHost": self.edit_rhost.text().strip(),
            "remotePort": int(self.edit_rport.text().strip() or "3389"),
            "enabled": True,
            "autoReconnect": True
        }

    def set_ssh_options(self, options):
        self.combo_ssh.clear()
        self.combo_ssh.addItems(list(dict.fromkeys(options)))

    def reset_form(self):
        self.edit_index = None
        self.intro_label.setText("填写连接信息后创建隧道")
        self.btn_create.setText("立即创建")
        self.edit_name.setText("新端口隧道")
        self.edit_lhost.setText("127.0.0.1")
        self.edit_lport.setText("13389")
        self.edit_rhost.setText("192.168.122.200")
        self.edit_rport.setText("3389")
        self.error_label.hide()

    def load_tunnel(self, index, tunnel):
        self.edit_index = index
        self.intro_label.setText("查看并修改隧道连接信息")
        self.btn_create.setText("保存修改")
        self.edit_name.setText(tunnel.name)
        if tunnel.ssh_host not in [self.combo_ssh.itemText(i) for i in range(self.combo_ssh.count())]:
            self.combo_ssh.addItem(tunnel.ssh_host)
        self.combo_ssh.setCurrentText(tunnel.ssh_host)
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
        self.setStyleSheet("""
            QFrame#settingsWorkspace {
                background: #ffffff;
                border-left: 1px solid #cbd5e1;
            }
        """)
        self.settings = dict(settings)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        # 1. 开机自启
        f_auto = QHBoxLayout()
        lbl_auto = StrongBodyLabel("开机自动启动本服务", self)
        f_auto.addWidget(lbl_auto)
        f_auto.addStretch()
        self.sw_auto = SwitchButton(self)
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

        # 彻底退出程序按钮
        self.btn_quit = PushButton("彻底退出隧道管家程序", self)
        self.btn_quit.setFixedHeight(34)
        self.btn_quit.setStyleSheet("color: #b91c1c; border: 1px solid #fca5a5; border-radius: 6px; background-color: #fef2f2;")
        self.btn_quit.clicked.connect(self.quit_requested.emit)
        layout.addWidget(self.btn_quit)

        layout.addStretch()

        btn_box = QHBoxLayout()
        btn_box.addStretch()
        self.btn_cancel = PushButton("取消", self)
        self.btn_cancel.clicked.connect(self.close_requested.emit)
        btn_box.addWidget(self.btn_cancel)

        self.btn_save = PrimaryPushButton("保存配置", self)
        self.btn_save.clicked.connect(self._save_settings)
        btn_box.addWidget(self.btn_save)

        layout.addLayout(btn_box)

    def load_settings(self, settings: dict):
        self.settings = dict(settings)
        self.sw_auto.setChecked(is_autostart_enabled())
        self.sw_tray.setChecked(settings.get("minimizeToTray", True))
        self.edit_ssh.setText(settings.get("sshPath", DEFAULT_SSH))
        self.spin_int.setValue(settings.get("checkInterval", 4))

    def _browse_ssh(self):
        f, _ = QFileDialog.getOpenFileName(self, "选择 ssh.exe", filter="Executable (*.exe);;All Files (*.*)")
        if f:
            self.edit_ssh.setText(f)

    def _save_settings(self):
        self.save_requested.emit({
            "autostart": self.sw_auto.isChecked(),
            "minimizeToTray": self.sw_tray.isChecked(),
            "sshPath": self.edit_ssh.text().strip(),
            "checkInterval": self.spin_int.value(),
        })


# ================= 主窗口程序 =================

class MainWindow(FramelessWindow):
    """固定尺寸的隧道管家主窗口，保留拖动、最小化及关闭到托盘。"""
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(create_app_logo_icon())
        self.setFixedSize(1100, 660)
        self.setResizeEnabled(False)
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, False)
        self.windowEffect.disableMaximizeButton(self.winId())

        setTheme(Theme.LIGHT)
        self.setStyleSheet("MainWindow { background-color: #f8fafc; }")

        # 数据模型
        self.tunnels = []
        self.settings = {
            "autostart": False,
            "minimizeToTray": True,
            "sshPath": DEFAULT_SSH,
            "checkInterval": 4,
            "sshAliases": {
                "生产环境跳板机": "g",
                "测试跳板机": "g"
            }
        }
        self.selected_index = None
        self.running = True
        self.is_paused = False
        self.tray = None

        self.load_settings()
        self.load_config()

        # 系统托盘
        self._init_system_tray()

        # 初始化界面
        self._setup_title_bar()
        self._setup_ui()
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
                    self.settings.update(json.load(f))
            except Exception:
                pass
        self.settings["autostart"] = is_autostart_enabled()

    def save_settings(self):
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def load_config(self):
        default_config = [
            {
                "name": "Windows远程桌面 (RDP)",
                "sshHost": "生产环境跳板机",
                "localHost": "127.0.0.1",
                "localPort": 13389,
                "remoteHost": "192.168.122.200",
                "remotePort": 3389,
                "enabled": True,
                "autoReconnect": True,
                "description": "生产环境 Windows 远程桌面转发"
            },
            {
                "name": "数据库",
                "sshHost": "测试跳板机",
                "localHost": "127.0.0.1",
                "localPort": 15432,
                "remoteHost": "10.0.1.20",
                "remotePort": 5432,
                "enabled": True,
                "autoReconnect": True,
                "description": "数据库内网映射"
            },
            {
                "name": "VNC远程桌面",
                "sshHost": "测试跳板机",
                "localHost": "127.0.0.1",
                "localPort": 45900,
                "remoteHost": "127.0.0.1",
                "remotePort": 45900,
                "enabled": False,
                "autoReconnect": True,
                "description": "VNC 远程桌面管理"
            }
        ]

        if not os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                    json.dump(default_config, f, indent=2, ensure_ascii=False)
            except Exception:
                pass

        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.tunnels = [TunnelItem(item) for item in data]
        except Exception:
            self.tunnels = [TunnelItem(item) for item in default_config]

    def save_config(self):
        try:
            data = [t.to_dict() for t in self.tunnels]
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.log(f"保存配置文件异常: {e}", "ERROR")

    # ================= 标题栏定制 =================

    def _setup_title_bar(self):
        tb = self.titleBar
        tb.setFixedHeight(56)
        tb.maxBtn.hide()
        tb.setDoubleClickEnabled(False)
        # 标题、计数、操作与窗口控制共用一行；中间的伸缩空间仍可拖动窗口。
        self.lbl_list_title = SubtitleLabel("端口隧道列表", tb)
        self.lbl_list_title.setFont(QFont("Microsoft YaHei UI", 13, QFont.Bold))
        self.lbl_list_title.setStyleSheet("color: #0f172a;")
        self.lbl_list_title.setAttribute(Qt.WA_TransparentForMouseEvents)

        self.lbl_active_count = QLabel("(0/0)", tb)
        self.lbl_active_count.setFont(QFont("Microsoft YaHei UI", 13, QFont.Bold))
        self.lbl_active_count.setStyleSheet("color: #16a34a; margin-left: 16px;")
        self.lbl_active_count.setAttribute(Qt.WA_TransparentForMouseEvents)
        tb.hBoxLayout.insertSpacing(0, 24)
        tb.hBoxLayout.insertWidget(1, self.lbl_list_title, 0, Qt.AlignVCenter)
        tb.hBoxLayout.insertWidget(2, self.lbl_active_count, 0, Qt.AlignVCenter)

        self.btn_new = PrimaryPushButton(FIF.ADD, "新建隧道", tb)
        self.btn_new.setFont(QFont("Microsoft YaHei UI", 9, QFont.Bold))
        self.btn_new.setFixedHeight(32)
        self.btn_new.clicked.connect(self.toggle_new_tunnel_workspace)

        # 保留 Fluent 的 hasIcon 样式，为图标和文字预留独立空间。
        self.btn_config = PushButton(FIF.SETTING, "系统配置", tb)
        self.btn_config.setFont(QFont("Microsoft YaHei UI", 9))
        self.btn_config.setFixedHeight(32)
        self.btn_config.clicked.connect(self.open_system_settings_workspace)

        idx = tb.hBoxLayout.indexOf(tb.minBtn)
        tb.hBoxLayout.insertWidget(idx, self.btn_new, 0, Qt.AlignVCenter)
        tb.hBoxLayout.insertSpacing(idx + 1, 10)
        tb.hBoxLayout.insertWidget(idx + 2, self.btn_config, 0, Qt.AlignVCenter)
        tb.hBoxLayout.insertSpacing(idx + 3, 12)

        self.workspace_title_bar = QFrame(tb)
        self.workspace_title_bar.setObjectName("workspaceTitleBar")
        self.workspace_title_bar.setFixedSize(420, tb.height())
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
        self.workspace_title.setFont(QFont("Microsoft YaHei UI", 13, QFont.Bold))
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
        central.setFixedWidth(1100)
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
            lbl.setFont(QFont("Microsoft YaHei UI", 9, QFont.Bold))
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
        self.btn_clear_log.setFont(QFont("Microsoft YaHei UI", 9))
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
        self.log_text.setFont(QFont("Consolas", 9))
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
        self.tunnel_workspace.setFixedWidth(420)
        content_layout.addWidget(self.tunnel_workspace)
        self.tunnel_workspace.close_requested.connect(self._collapse_workspace)
        self.tunnel_workspace.create_requested.connect(self._create_tunnel_from_workspace)
        self.tunnel_workspace.save_requested.connect(self._save_tunnel_from_workspace)
        self.tunnel_workspace.hide()

        self.settings_workspace = SystemSettingsWorkspace(self.settings, self.content_row)
        self.settings_workspace.setFixedWidth(420)
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
        self.save_config()
        self.log(f'隧道 "{t.name}" 配置修改已保存。')
        if t.enabled and not self.is_paused:
            self._queue_tunnel(t, "start")
        self.refresh_table()

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

    def _collapse_workspace(self):
        self.tunnel_workspace.hide()
        self.settings_workspace.hide()
        self.workspace_title_bar.hide()
        self.setFixedSize(1100, 660)
        if self.selected_index is not None:
            self.selected_index = None
            self.refresh_table()

    def select_tunnel(self, idx: int):
        if not 0 <= idx < len(self.tunnels):
            return
        if self.selected_index == idx:
            self._collapse_workspace()
            return
        self.selected_index = idx
        workspace = self.tunnel_workspace
        self.settings_workspace.hide()
        ssh_opts = list(self.settings.get("sshAliases", {}).keys()) + get_known_ssh_hosts()
        workspace.set_ssh_options(ssh_opts)
        workspace.load_tunnel(idx, self.tunnels[idx])
        self.workspace_title.setText("隧道详情")
        self.setFixedSize(1520, 660)
        self.workspace_title_bar.show()
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
        ssh_opts = list(self.settings.get("sshAliases", {}).keys()) + get_known_ssh_hosts()
        workspace.set_ssh_options(ssh_opts)
        workspace.reset_form()
        self.workspace_title.setText("新建隧道")
        self.setFixedSize(1520, 660)
        self.workspace_title_bar.show()
        workspace.show()
        workspace.edit_name.setFocus()
        workspace.edit_name.selectAll()

    def _create_tunnel_from_workspace(self, data):
        new_t = TunnelItem(data)
        self.tunnels.append(new_t)
        self.save_config()
        self.log(f'成功创建隧道 "{new_t.name}"')
        self._queue_tunnel(new_t, "start")
        self.refresh_table()
        self._collapse_workspace()

    def _save_tunnel_from_workspace(self, idx: int, data: dict):
        if not 0 <= idx < len(self.tunnels):
            return
        t = self.tunnels[idx]
        t.name = data["name"]
        t.ssh_host = data["sshHost"]
        t.local_host = data["localHost"]
        t.local_port = data["localPort"]
        t.remote_host = data["remoteHost"]
        t.remote_port = data["remotePort"]
        self.on_tunnel_saved(idx)

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
        self.workspace_title.setText("系统配置")
        self.setFixedSize(1520, 660)
        self.workspace_title_bar.show()
        workspace.show()

    def _save_settings_from_workspace(self, data: dict):
        set_autostart(data["autostart"])
        self.settings.update(data)
        self.save_settings()
        self.supervisor_timer.setInterval(self.settings.get("checkInterval", 4) * 1000)
        self.log("系统配置已更新。")
        self._collapse_workspace()

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
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.Context:
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
        if self.settings.get("minimizeToTray", True):
            event.ignore()
            self.hide()
            self.tray.showMessage(APP_NAME, "已最小化至任务栏状态栏托盘，随时可点击图标打开。", QSystemTrayIcon.Information, 2000)
        else:
            event.ignore()
            self.quit_app()

    # ================= 业务生命周期与自连接 =================

    def _queue_tunnel(self, item, action):
        self.tunnel_jobs.request(item, action, self.settings.get("sshPath", DEFAULT_SSH),
                                 self.settings.get("sshAliases"))

    def initial_startup(self):
        if not self.running:
            return
        self.log("隧道管家服务已启动，开始建立 SSH 连接…")
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
        self.log("用户触发: 重新连接所有隧道...")
        for t in self.tunnels:
            self._queue_tunnel(t, "start" if t.enabled else "stop")
        self.refresh_table()

    def check_tunnels_health(self):
        """仅投递心跳任务，所有网络和进程等待都在后台执行。"""
        if self.is_paused or not self.running:
            return
        for t in self.tunnels:
            if t.enabled:
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
