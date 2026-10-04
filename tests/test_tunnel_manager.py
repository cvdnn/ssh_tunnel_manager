"""Regression tests: no real SSH hosts, user configs, registry writes or visible windows."""
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from support import tm, tunnel, controller, patch_screen, ROOT
from PySide6.QtCore import QEvent, QRect
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMenu, QWidget


class TunnelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = tm.QApplication.instance() or tm.QApplication([])
        # Layout sizes follow the display, so pin one for reproducible geometry.
        cls.screen = patch_screen()
        cls.screen.start()

    @classmethod
    def tearDownClass(cls):
        cls.screen.stop()
        # There is no app.exec() in offscreen tests; explicitly process deferred Qt deletes.
        cls.app.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_startup_splash_shows_busy_progress_before_main_window(self):
        splash = tm.StartupSplash()
        try:
            splash.show()
            self.app.processEvents()
            self.assertTrue(splash.isVisible())
            self.assertEqual((splash.progress.minimum(), splash.progress.maximum()), (0, 0))
            self.assertIn("加载", splash.status.text())
        finally:
            splash.close()
            splash.deleteLater()

    def test_startup_splash_has_complete_outer_border(self):
        splash = tm.StartupSplash()
        try:
            splash.show()
            self.app.processEvents()
            image = splash.grab().toImage()
            expected = QColor("#94a3b8")
            for x, y in ((image.width() // 2, 0), (0, image.height() // 2),
                         (image.width() // 2, image.height() - 1),
                         (image.width() - 1, image.height() // 2)):
                with self.subTest(x=x, y=y):
                    self.assertEqual(image.pixelColor(x, y), expected)
        finally:
            splash.close()
            splash.deleteLater()

    def test_startup_progress_moves_and_stops_when_hidden(self):
        splash = tm.StartupSplash()
        try:
            splash.show()
            self.app.processEvents()
            self.assertTrue(splash.progress._timer.isActive())
            first = splash.progress.grab().toImage()
            deadline = time.monotonic() + 1
            second = first
            while second == first and time.monotonic() < deadline:
                QTest.qWait(30)
                second = splash.progress.grab().toImage()
            self.assertNotEqual(first, second)
            splash.hide()
            frozen = splash.progress.grab().toImage()
            QTest.qWait(100)
            self.assertEqual(frozen, splash.progress.grab().toImage())
        finally:
            splash.close()
            splash.deleteLater()

    def test_startup_process_exits_when_parent_closes_pipe(self):
        executables = [Path(sys.executable)]
        if sys.platform == 'win32':
            executables.append(Path(sys.executable).with_name("pythonw.exe"))
        for executable in executables:
            with self.subTest(executable=executable), patch.object(tm.sys, "executable", str(executable)):
                splash = tm.StartupSplashProcess()
                try:
                    self.assertIsNone(splash.process.poll())
                    splash.set_status("正在准备隧道列表…")
                    splash.close()
                    self.assertEqual(splash.process.wait(timeout=15), 0)
                    splash.close()  # Cleanup can run again at interpreter exit.
                finally:
                    if splash.process.poll() is None:
                        splash.process.kill()
                        splash.process.wait(timeout=5)

    def test_startup_animation_keeps_running_while_parent_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            frames = Path(directory) / "frames.txt"
            code = f"""
import runpy, sys, time
from PySide6.QtWidgets import QProgressBar
original_update = QProgressBar.update
def record_frame(self):
    with open({str(frames)!r}, 'a') as output:
        output.write(str(time.monotonic()) + '\\n')
    original_update(self)
QProgressBar.update = record_frame
sys.argv = [{str(ROOT / 'bin' / 'ssh-tunnel-manager.pyw')!r}, '--startup-splash']
runpy.run_path(sys.argv[0], run_name='__main__')
"""
            proc = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic() + 10
                while not frames.exists() and proc.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(frames.exists(), "启动页未开始动画")
                blocked_at = time.monotonic()
                time.sleep(0.35)  # 主进程完全不处理 Qt 事件，模拟阻塞加载。
                resumed_at = time.monotonic()
                proc.stdin.close()
                self.assertEqual(proc.wait(timeout=10), 0)
                timestamps = [float(line) for line in frames.read_text().splitlines()]
                self.assertGreaterEqual(sum(blocked_at <= t <= resumed_at for t in timestamps), 6)
                self.assertEqual(proc.stderr.read(), b"")
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5)
                proc.stdin.close()
                proc.stderr.close()

    def test_main_window_outline_tracks_collapsed_and_expanded_sizes(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "tunnels.json"
            config.write_text("[]", encoding="utf-8")
            with patch.object(tm, "CONFIG_FILE", str(config)), \
                 patch.object(tm, "SETTINGS_FILE", str(Path(directory) / "settings.json")), \
                 patch.object(tm, "LOG_FILE", str(Path(directory) / "test.log")), \
                 patch.object(tm, "is_autostart_enabled", return_value=False), \
                 patch.object(tm, "get_known_ssh_hosts", return_value=[]), \
                 patch.object(tm.QTimer, "singleShot"):
                window = tm.MainWindow()
                try:
                    window.supervisor_timer.stop()
                    window.show()
                    self.app.processEvents()
                    for width in (1100, 1520):
                        with self.subTest(width=width):
                            if width == 1520:
                                window.open_system_settings_workspace()
                                self.app.processEvents()
                            image = window.grab().toImage()
                            self.assertEqual(image.width(), round(width * window.devicePixelRatioF()))
                            expected = QColor("#94a3b8")
                            for x, y in ((image.width() // 2, 0), (0, image.height() // 2),
                                         (image.width() // 2, image.height() - 1),
                                         (image.width() - 1, image.height() // 2)):
                                self.assertEqual(image.pixelColor(x, y), expected)
                finally:
                    window.tray.hide()
                    window.hide()
                    window.deleteLater()

    def test_startup_splash_closes_after_main_window_is_visible(self):
        splash = tm.StartupSplash()
        window = QWidget()
        try:
            splash.show()
            self.app.processEvents()
            tm.show_main_window(self.app, window, splash)
            self.assertTrue(window.isVisible())
            self.assertFalse(splash.isVisible())
        finally:
            window.close()
            window.deleteLater()
            splash.close()
            splash.deleteLater()

    def start_fake(self, item):
        proc = Mock()
        proc.poll.return_value = None
        proc.stderr = io.StringIO("")
        with patch.object(tm.subprocess, "Popen", return_value=proc) as popen:
            item.start_process(Mock(), "ssh.exe", {"jump": "actual-host"})
        self.addCleanup(item.stop_process)
        return proc, popen.call_args

    def test_forward_uses_configured_bind_address(self):
        _, call = self.start_fake(tunnel())
        args = call.args[0]
        self.assertEqual(args[args.index("-L") + 1], "127.0.0.2:13389:10.0.0.2:3389")
        self.assertEqual(args[-1], "actual-host")

    def test_ipv6_forward_brackets_both_addresses(self):
        _, call = self.start_fake(tunnel(localHost="::1", remoteHost="2001:db8::2"))
        args = call.args[0]
        self.assertEqual(args[args.index("-L") + 1], "[::1]:13389:[2001:db8::2]:3389")

    def test_copy_uses_configured_address(self):
        row = types.SimpleNamespace(tunnel=tunnel())
        tm.TunnelRowWidget._copy_address(row)
        self.assertEqual(self.app.clipboard().text(), "127.0.0.2:13389")

    def test_rdp_uses_configured_address(self):
        row = types.SimpleNamespace(tunnel=tunnel())
        with patch.object(tm.sys, 'platform', 'win32'), patch.object(tm.subprocess, "Popen") as popen:
            tm.TunnelRowWidget._launch_rdp(row)
        popen.assert_called_once_with(["mstsc.exe", "/v:127.0.0.2:13389"])

    def test_copy_ipv6_and_wildcard_addresses(self):
        for host, expected in [("::1", "[::1]:13389"), ("::", "[::1]:13389"),
                               ("0.0.0.0", "127.0.0.1:13389"), ("*", "127.0.0.1:13389")]:
            with self.subTest(host=host):
                tm.TunnelRowWidget._copy_address(types.SimpleNamespace(tunnel=tunnel(localHost=host)))
                self.assertEqual(self.app.clipboard().text(), expected)

    def test_failed_connection_is_not_a_listening_port(self):
        with patch.object(tm.socket, "create_connection", side_effect=OSError("unreachable")), \
             patch.object(tm.socket, "socket") as sock:
            sock.return_value.__enter__.return_value.bind.side_effect = OSError("invalid address")
            self.assertFalse(tm.test_port_listening(12345, "192.0.2.1"))

    def test_probe_real_listener_and_closed_port(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            listener.listen()
            self.assertTrue(tm.test_port_listening(port))
        self.assertFalse(tm.test_port_listening(port))

    def test_probe_wildcard_uses_loopback(self):
        for host, expected in [("0.0.0.0", "127.0.0.1"), ("::", "::1"), ("*", "127.0.0.1")]:
            with self.subTest(host=host), patch.object(tm.socket, "create_connection") as connect:
                tm.test_port_listening(12345, host)
                self.assertEqual(connect.call_args.args[0], (expected, 12345))

    def test_startup_does_not_claim_success(self):
        item = tunnel()
        item.start_process = Mock()
        window = controller(item, tunnel(enabled=False))
        with patch.object(tm.QTimer, "singleShot"):
            tm.MainWindow.initial_startup(window)
        messages = "\n".join(c.args[0] for c in window.log.call_args_list)
        self.assertNotIn("连接成功", messages)
        self.assertNotIn("已连接", messages)
        item.start_process.assert_not_called()
        window.tunnel_jobs.request.assert_called_once_with(item, "start", "ssh.exe", {})

    def test_summary_counts_enabled_and_does_not_claim_completion(self):
        window = controller(tunnel(), tunnel(enabled=False))
        window.check_tunnels_health = Mock()
        window._report_online_summary()
        message = window.log.call_args.args[0]
        self.assertIn("0/1", message)
        self.assertNotIn("连接完成", message)

    def test_live_process_without_listener_waits_during_startup(self):
        item = tunnel()
        proc, _ = self.start_fake(item)
        window = controller(item)
        with patch.object(tm, "test_port_listening", return_value=False), \
             patch.object(item, "start_process") as restart:
            item.check_health(window.log, "ssh.exe")
        restart.assert_not_called()
        self.assertIs(item.process, proc)
        proc.terminate.assert_not_called()
        self.assertEqual(item.status, "Connecting")

    def test_startup_timeout_retries(self):
        item = tunnel()
        proc, _ = self.start_fake(item)
        item.started_at = time.monotonic() - 60
        item.start_process = Mock()
        with patch.object(tm, "test_port_listening", return_value=False):
            item.check_health(Mock(), "ssh.exe")
        item.start_process.assert_called_once()

    def test_live_process_and_listener_mark_connected(self):
        item = tunnel()
        self.start_fake(item)
        window = controller(item)
        with patch.object(tm, "test_port_listening", return_value=True):
            item.check_health(window.log, "ssh.exe")
        self.assertEqual(item.status, "Connected")
        self.assertIn("连接成功", window.log.call_args.args[0])

    def test_dead_process_is_not_online_even_if_port_is_occupied(self):
        item = tunnel(autoReconnect=False)
        proc, _ = self.start_fake(item)
        proc.poll.return_value = 255
        with patch.object(tm, "test_port_listening", return_value=True):
            item.check_health(Mock(), "ssh.exe")
        self.assertEqual(item.status, "Disconnected")

    def test_real_child_stderr_reaches_application_log(self):
        item = tunnel(autoReconnect=False)
        window = controller(item)
        real_popen = subprocess.Popen
        def failing_child(args, **kwargs):
            return real_popen([sys.executable, "-c",
                              "import sys; sys.stderr.write('Permission denied (publickey).\\n'); sys.exit(23)"],
                              **kwargs)
        with patch.object(tm.subprocess, "Popen", side_effect=failing_child):
            item.start_process(window.log, "ssh.exe")
        self.addCleanup(item.stop_process)
        item.process.wait(timeout=10)
        with patch.object(tm, "test_port_listening", return_value=False):
            item.check_health(window.log, "ssh.exe")
        messages = "\n".join(c.args[0] for c in window.log.call_args_list)
        self.assertIn("Permission denied (publickey).", messages)
        self.assertEqual(item.status, "Disconnected")

    def test_start_failure_reports_error_and_disconnects(self):
        item = tunnel()
        logger = Mock()
        with patch.object(tm.subprocess, "Popen", side_effect=FileNotFoundError("missing ssh.exe")):
            item.start_process(logger, "missing.exe")
        self.assertEqual(item.status, "Disconnected")
        self.assertEqual(logger.call_args.args[1], "ERROR")

    def test_reader_start_failure_cleans_up_child_and_pipe(self):
        item = tunnel()
        log = Mock()
        real_popen = subprocess.Popen
        children = []
        def sleeping_child(args, **kwargs):
            child = real_popen([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)
            children.append(child)
            return child
        with patch.object(tm.subprocess, "Popen", side_effect=sleeping_child), \
             patch.object(tm.threading.Thread, "start", side_effect=RuntimeError("cannot start new thread")):
            item.start_process(log, "ssh.exe")
        self.assertIsNotNone(children[0].poll())
        self.assertTrue(children[0].stderr.closed)
        self.assertIsNone(item.process)
        self.assertEqual(item.status, "Disconnected")
        self.assertIn("cannot start new thread", log.call_args.args[0])

    def test_disabled_tunnel_does_not_start(self):
        with patch.object(tm.subprocess, "Popen") as popen:
            item = tunnel(enabled=False)
            item.start_process(Mock(), "ssh.exe")
        popen.assert_not_called()
        self.assertEqual(item.status, "Stopped")

    def test_real_child_stop_closes_stderr_and_reader(self):
        item = tunnel()
        real_popen = subprocess.Popen
        def sleeping_child(args, **kwargs):
            return real_popen([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)
        with patch.object(tm.subprocess, "Popen", side_effect=sleeping_child):
            item.start_process(Mock(), "ssh.exe")
        self.addCleanup(item.stop_process)
        proc, reader = item.process, item._stderr_thread
        item.stop_process()
        self.assertIsNotNone(proc.poll())
        self.assertTrue(proc.stderr.closed)
        self.assertFalse(reader.is_alive())
        self.assertIsNone(item.process)

    def test_stop_keeps_pending_diagnostics(self):
        item = tunnel()
        self.start_fake(item)
        item._stderr_lines.append("channel open failed: Connection refused")
        log = Mock()
        item.stop_process(log)
        self.assertTrue(any("Connection refused" in c.args[0] for c in log.call_args_list))

    def test_stderr_does_not_touch_gui_log_from_reader_thread(self):
        item = tunnel()
        log = Mock()
        real_popen = subprocess.Popen
        def noisy_child(args, **kwargs):
            return real_popen([sys.executable, "-c",
                              "import sys; sys.stderr.write('diagnostic\\n' * 10000)"], **kwargs)
        with patch.object(tm.subprocess, "Popen", side_effect=noisy_child):
            item.start_process(log, "ssh.exe")
        self.addCleanup(item.stop_process)
        item.process.wait(timeout=10)
        item._stderr_thread.join(timeout=2)
        self.assertFalse(item._stderr_thread.is_alive())
        self.assertEqual(log.call_count, 1)  # Only the main-thread startup message.
        self.assertLessEqual(len(item._stderr_lines), 200)
        item.drain_errors(log)
        self.assertTrue(any("diagnostic" in c.args[0] for c in log.call_args_list))

    def test_drain_limits_work_when_diagnostics_keep_arriving(self):
        item = tunnel()
        item._stderr_lines.extend(["first batch"] * 200)
        messages = []
        def log_and_receive_more(message, level):
            messages.append(message)
            if len(messages) < 300:
                item._stderr_lines.append("next batch")
        item.drain_errors(log_and_receive_more)
        self.assertEqual(len(messages), 200)
        self.assertEqual(len(item._stderr_lines), 200)

    def test_no_auto_reconnect_stops_timed_out_attempt(self):
        item = tunnel(autoReconnect=False)
        proc, _ = self.start_fake(item)
        item.started_at = time.monotonic() - 60
        with patch.object(tm, "test_port_listening", return_value=False):
            item.check_health(Mock(), "ssh.exe")
        proc.terminate.assert_called_once()
        self.assertIsNone(item.process)
        self.assertEqual(item.status, "Disconnected")

    def test_window_and_dialogs_construct_with_isolated_config(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "tunnels.json"
            config.write_text(json.dumps([tunnel(enabled=False).to_dict()]), encoding="utf-8")
            with patch.object(tm, "CONFIG_FILE", str(config)), \
                 patch.object(tm, "SETTINGS_FILE", str(Path(directory) / "settings.json")), \
                 patch.object(tm, "LOG_FILE", str(Path(directory) / "test.log")), \
                 patch.object(tm, "is_autostart_enabled", return_value=False), \
                 patch.object(tm, "get_known_ssh_hosts", return_value=[]), \
                 patch.object(tm.QTimer, "singleShot"), \
                 patch.object(tm.subprocess, "Popen") as popen:
                window = tm.MainWindow()
                try:
                    window.supervisor_timer.stop()
                    self.assertTrue(window.tunnel_workspace.isHidden())
                    self.assertTrue(window.settings_workspace.isHidden())
                    self.assertEqual(window.rows_layout.count(), 2)
                    self.assertEqual(window.settings_workspace.spin_int.value(), 4)
                    self.assertEqual(window.minimumSize(), window.maximumSize())
                    self.assertEqual(window.size(), tm.QSize(1100, 660))
                    self.assertTrue(window.titleBar.maxBtn.isHidden())
                    self.assertFalse(window.titleBar._isDoubleClickEnabled)
                    self.assertFalse(window._isResizeEnabled)
                    self.assertFalse(window.windowFlags() & tm.Qt.WindowMaximizeButtonHint)
                    window.resize(1400, 900)
                    self.assertEqual(window.size(), tm.QSize(1100, 660))
                    QTest.mouseDClick(window.titleBar, tm.Qt.LeftButton, pos=tm.QPoint(100, 15))
                    self.assertFalse(window.isMaximized())
                    window.show()
                    self.app.processEvents()
                    header_widgets = [window.lbl_active_count, window.btn_config,
                                      window.titleBar.minBtn, window.titleBar.closeBtn]
                    header_widgets += [w for w in window.findChildren(tm.QLabel)
                                       if w.text() == "端口隧道列表"]
                    header_widgets += [w for w in window.findChildren(tm.PushButton)
                                       if w.text() == "新建隧道"]
                    centers = [w.mapTo(window, w.rect().center()).y() for w in header_widgets]
                    # Each desktop font stack measures its own line height.
                    self.assertLessEqual(max(centers) - min(centers), 2)
                    popen.assert_not_called()
                finally:
                    window.supervisor_timer.stop()
                    window.tray.hide()
                    window.deleteLater()

    def test_system_settings_uses_workspace_and_saves_only_on_submit(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "tunnels.json"
            settings_file = Path(directory) / "settings.json"
            config.write_text("[]", encoding="utf-8")
            with patch.object(tm, "CONFIG_FILE", str(config)), \
                 patch.object(tm, "SETTINGS_FILE", str(settings_file)), \
                 patch.object(tm, "LOG_FILE", str(Path(directory) / "test.log")), \
                 patch.object(tm, "is_autostart_enabled", return_value=False), \
                 patch.object(tm, "get_known_ssh_hosts", return_value=["jump"]), \
                 patch.object(tm, "set_autostart") as set_autostart, \
                 patch.object(tm.QTimer, "singleShot"):
                window = tm.MainWindow()
                try:
                    window.supervisor_timer.stop()
                    window.show()
                    self.app.processEvents()
                    window.btn_config.click()
                    self.app.processEvents()
                    workspace = window.settings_workspace
                    self.assertEqual(window.size(), tm.QSize(1520, 660))
                    self.assertTrue(workspace.isVisible())
                    self.assertTrue(window.tunnel_workspace.isHidden())
                    self.assertEqual(window.workspace_title.text(), "系统配置")
                    self.assertIs(workspace.parentWidget(), window.content_row)
                    self.assertFalse(workspace.isWindow())
                    workspace.spin_int.setValue(10)
                    workspace.btn_cancel.click()
                    self.assertEqual(window.size(), tm.QSize(1100, 660))
                    self.assertFalse(settings_file.exists())
                    set_autostart.assert_not_called()

                    window.btn_new.click()
                    window.btn_config.click()
                    self.assertTrue(window.tunnel_workspace.isHidden())
                    self.assertTrue(workspace.isVisible())
                    self.assertEqual(workspace.spin_int.value(), 4)
                    workspace.sw_tray.setChecked(False)
                    workspace.edit_ssh.setText("C:/ssh/ssh.exe")
                    workspace.spin_int.setValue(8)
                    workspace.btn_save.click()
                    self.assertTrue(workspace.isHidden())
                    self.assertEqual(window.size(), tm.QSize(1100, 660))
                    saved = json.loads(settings_file.read_text(encoding="utf-8"))
                    self.assertFalse(saved["minimizeToTray"])
                    self.assertEqual(saved["sshPath"], "C:/ssh/ssh.exe")
                    self.assertEqual(saved["checkInterval"], 8)
                    self.assertEqual(window.supervisor_timer.interval(), 8000)
                    set_autostart.assert_called_once_with(False)
                finally:
                    window.supervisor_timer.stop()
                    window.tray.hide()
                    window.deleteLater()

    def test_clicking_tunnel_row_opens_details_and_saves_without_inline_expansion(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "tunnels.json"
            config.write_text(json.dumps([
                tunnel(name="one", enabled=True).to_dict(),
                tunnel(name="two", enabled=False, localPort=15555).to_dict(),
            ]), encoding="utf-8")
            with patch.object(tm, "CONFIG_FILE", str(config)), \
                 patch.object(tm, "SETTINGS_FILE", str(Path(directory) / "settings.json")), \
                 patch.object(tm, "LOG_FILE", str(Path(directory) / "test.log")), \
                 patch.object(tm, "is_autostart_enabled", return_value=False), \
                 patch.object(tm, "get_known_ssh_hosts", return_value=["jump"]), \
                 patch.object(tm.QTimer, "singleShot"), \
                 patch.object(tm.TunnelWorkerPool, "request") as request:
                window = tm.MainWindow()
                try:
                    window.supervisor_timer.stop()
                    window.show()
                    self.app.processEvents()
                    first_row = window.rows_layout.itemAt(0).widget()
                    self.assertEqual(first_row.height(), 50)
                    unselected_color = first_row.grab().toImage().pixelColor(8, 8)
                    QTest.mousePress(first_row.name_lbl, tm.Qt.LeftButton)
                    self.app.processEvents()
                    self.assertIsNone(window.selected_index)
                    self.assertTrue(window.tunnel_workspace.isHidden())
                    QTest.mouseRelease(first_row.name_lbl, tm.Qt.LeftButton)
                    self.app.processEvents()
                    self.assertEqual(window.size(), tm.QSize(1520, 660))
                    self.assertEqual(window.workspace_title.text(), "隧道详情")
                    self.assertTrue(window.tunnel_workspace.isVisible())
                    self.assertEqual(window.tunnel_workspace.edit_name.text(), "one")
                    self.app.processEvents()
                    self.assertEqual(window.rows_layout.itemAt(0).widget().height(), 50)

                    selected_row = window.rows_layout.itemAt(0).widget()
                    selected_image = selected_row.grab().toImage()
                    selected_color = selected_image.pixelColor(8, 8)
                    self.assertNotEqual(selected_color, unselected_color)
                    self.assertEqual(selected_image.pixelColor(selected_image.width() - 8, 8), selected_color)

                    # 再次点击同一行的端口区域，应取消选择并收起详情。
                    QTest.mousePress(selected_row.lport_lbl, tm.Qt.LeftButton)
                    self.app.processEvents()
                    self.assertEqual(window.selected_index, 0)
                    self.assertTrue(window.tunnel_workspace.isVisible())
                    QTest.mouseRelease(selected_row.lport_lbl, tm.Qt.LeftButton)
                    self.app.processEvents()
                    self.assertIsNone(window.selected_index)
                    self.assertTrue(window.tunnel_workspace.isHidden())
                    self.assertTrue(window.workspace_title_bar.isHidden())
                    self.assertEqual(window.size(), tm.QSize(1100, 660))
                    first_row = window.rows_layout.itemAt(0).widget()
                    self.assertEqual(first_row.grab().toImage().pixelColor(8, 8), unselected_color)

                    # 第三次点击重新展开；切换其他行不需要额外点击。
                    QTest.mouseClick(first_row.name_lbl, tm.Qt.LeftButton)
                    self.app.processEvents()
                    self.assertEqual(window.selected_index, 0)
                    self.assertTrue(window.tunnel_workspace.isVisible())

                    second_row = window.rows_layout.itemAt(1).widget()
                    QTest.mouseClick(second_row.name_lbl, tm.Qt.LeftButton)
                    self.app.processEvents()
                    self.assertEqual(window.tunnel_workspace.edit_name.text(), "two")
                    self.assertEqual(window.tunnel_workspace.edit_lport.text(), "15555")
                    self.assertEqual(window.rows_layout.itemAt(0).widget().grab().toImage().pixelColor(8, 8), unselected_color)
                    QTest.mouseClick(window.rows_layout.itemAt(0).widget().name_lbl, tm.Qt.LeftButton)
                    self.app.processEvents()
                    window.tunnel_workspace.edit_name.setText("renamed")
                    window.tunnel_workspace.edit_rhost.setText("10.0.0.9")
                    window.tunnel_workspace.btn_create.click()
                    self.app.processEvents()
                    self.assertEqual(window.tunnels[0].name, "renamed")
                    self.assertEqual(window.tunnels[0].remote_host, "10.0.0.9")
                    self.assertEqual(json.loads(config.read_text(encoding="utf-8"))[0]["name"], "renamed")
                    request.assert_called_once()
                    self.assertEqual(request.call_args.args[:2], (window.tunnels[0], "start"))
                    self.assertEqual(window.rows_layout.itemAt(0).widget().height(), 50)
                    row = window.rows_layout.itemAt(0).widget()
                    window.tunnels[0].status = "Connected"
                    window.tunnel_jobs.changed.emit()
                    self.assertIs(window.rows_layout.itemAt(0).widget(), row)
                    self.assertEqual(row.status_text_lbl.text(), "已连接")
                    window.btn_new.click()
                    self.assertEqual(window.workspace_title.text(), "新建隧道")
                    self.assertEqual(window.tunnel_workspace.edit_name.text(), "新端口隧道")
                    self.assertIsNone(window.tunnel_workspace.edit_index)
                    QTest.mouseClick(window.rows_layout.itemAt(0).widget().name_lbl, tm.Qt.LeftButton)
                    self.assertEqual(window.workspace_title.text(), "隧道详情")
                    self.assertEqual(window.tunnel_workspace.edit_name.text(), "renamed")
                finally:
                    window.supervisor_timer.stop()
                    window.tray.hide()
                    window.deleteLater()

    def test_new_tunnel_opens_in_right_workspace_and_can_be_cancelled_or_created(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "tunnels.json"
            config.write_text("[]", encoding="utf-8")
            with patch.object(tm, "CONFIG_FILE", str(config)), \
                 patch.object(tm, "SETTINGS_FILE", str(Path(directory) / "settings.json")), \
                 patch.object(tm, "LOG_FILE", str(Path(directory) / "test.log")), \
                 patch.object(tm, "is_autostart_enabled", return_value=False), \
                 patch.object(tm, "get_known_ssh_hosts", return_value=["jump"]), \
                 patch.object(tm.QTimer, "singleShot"), \
                 patch.object(tm.TunnelWorkerPool, "request") as request:
                window = tm.MainWindow()
                try:
                    window.supervisor_timer.stop()
                    window.show()
                    self.app.processEvents()
                    workspace = window.tunnel_workspace
                    self.assertTrue(workspace.isHidden())
                    original_positions = {
                        name: widget.mapTo(window, tm.QPoint(0, 0))
                        for name, widget in (
                            ("new", window.btn_new),
                            ("config", window.btn_config),
                            ("min", window.titleBar.minBtn),
                            ("close", window.titleBar.closeBtn),
                            ("table", window.table_card),
                        )
                    }
                    window.btn_new.click()
                    self.app.processEvents()
                    self.assertTrue(workspace.isVisible())
                    self.assertEqual(window.size(), tm.QSize(1520, 660))
                    self.assertEqual(window.minimumSize(), window.maximumSize())
                    self.assertIs(workspace.parentWidget(), window.content_row)
                    self.assertEqual(window.central.width(), 1100)
                    self.assertEqual(workspace.geometry().left(), window.central.geometry().right() + 1)
                    self.assertEqual(workspace.height(), window.central.height())
                    self.assertFalse(workspace.isWindow())
                    for name, widget in (
                        ("new", window.btn_new),
                        ("config", window.btn_config),
                        ("min", window.titleBar.minBtn),
                        ("close", window.titleBar.closeBtn),
                        ("table", window.table_card),
                    ):
                        self.assertEqual(widget.mapTo(window, tm.QPoint(0, 0)), original_positions[name])
                    self.assertTrue(window.workspace_title_bar.isVisible())
                    self.assertIs(window.workspace_title_bar.parentWidget(), window.titleBar)
                    self.assertEqual(window.workspace_title_bar.geometry().left(), 1100)
                    self.assertEqual(window.workspace_title_bar.height(), window.titleBar.height())
                    self.assertEqual(window.workspace_title.text(), "新建隧道")
                    window.workspace_close_btn.click()
                    self.assertTrue(workspace.isHidden())
                    self.assertTrue(window.workspace_title_bar.isHidden())
                    self.assertEqual(window.size(), tm.QSize(1100, 660))

                    window.btn_new.click()
                    workspace.btn_cancel.click()
                    self.assertTrue(workspace.isHidden())
                    self.assertTrue(window.workspace_title_bar.isHidden())
                    self.assertEqual(window.size(), tm.QSize(1100, 660))

                    window.btn_new.click()
                    workspace.edit_name.setText("新隧道")
                    workspace.edit_lport.setText("15555")
                    workspace.edit_rhost.setText("10.0.0.8")
                    workspace.edit_rport.setText("8080")
                    workspace.btn_create.click()
                    self.assertTrue(workspace.isHidden())
                    self.assertEqual(window.size(), tm.QSize(1100, 660))
                    self.assertEqual(len(window.tunnels), 1)
                    self.assertEqual(window.tunnels[0].name, "新隧道")
                    self.assertEqual(json.loads(config.read_text(encoding="utf-8"))[0]["remotePort"], 8080)
                    request.assert_called_once()
                    self.assertEqual(request.call_args.args[:2], (window.tunnels[0], "start"))
                finally:
                    window.supervisor_timer.stop()
                    window.tray.hide()
                    window.deleteLater()

    def test_new_tunnel_workspace_rejects_invalid_fields_with_feedback(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "tunnels.json"
            config.write_text("[]", encoding="utf-8")
            with patch.object(tm, "CONFIG_FILE", str(config)), \
                 patch.object(tm, "SETTINGS_FILE", str(Path(directory) / "settings.json")), \
                 patch.object(tm, "LOG_FILE", str(Path(directory) / "test.log")), \
                 patch.object(tm, "is_autostart_enabled", return_value=False), \
                 patch.object(tm, "get_known_ssh_hosts", return_value=["jump"]), \
                 patch.object(tm.QTimer, "singleShot"), \
                 patch.object(tm.TunnelWorkerPool, "request") as request:
                window = tm.MainWindow()
                try:
                    window.supervisor_timer.stop()
                    window.show()
                    window.btn_new.click()
                    workspace = window.tunnel_workspace
                    workspace.edit_lport.setText("invalid")
                    workspace.btn_create.click()
                    self.assertTrue(workspace.isVisible())
                    self.assertTrue(workspace.error_label.isVisible())
                    self.assertIn("端口", workspace.error_label.text())
                    self.assertEqual(window.tunnels, [])
                    self.assertEqual(json.loads(config.read_text(encoding="utf-8")), [])
                    request.assert_not_called()
                finally:
                    window.supervisor_timer.stop()
                    window.tray.hide()
                    window.deleteLater()

    def test_log_toolbar_uses_single_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "tunnels.json"
            config.write_text(json.dumps([tunnel(enabled=False).to_dict()]), encoding="utf-8")
            with patch.object(tm, "CONFIG_FILE", str(config)), \
                 patch.object(tm, "SETTINGS_FILE", str(Path(directory) / "settings.json")), \
                 patch.object(tm, "LOG_FILE", str(Path(directory) / "test.log")), \
                 patch.object(tm, "is_autostart_enabled", return_value=False), \
                 patch.object(tm, "get_known_ssh_hosts", return_value=[]), \
                 patch.object(tm.QTimer, "singleShot"), \
                 patch.object(tm.subprocess, "Popen") as popen:
                window = tm.MainWindow()
                try:
                    window.supervisor_timer.stop()
                    window.show()
                    self.app.processEvents()

                    clear_button = window.findChild(tm.PushButton, "clearLogButton")
                    with self.subTest("clear button has a stable object name"):
                        self.assertIsNotNone(clear_button)

                    if clear_button is None:
                        clear_button = next(
                            button for button in window.findChildren(tm.PushButton)
                            if button.text() == "清空日志"
                        )

                    with self.subTest("clear button retains its content and dimensions"):
                        self.assertEqual(clear_button.text(), "清空日志")
                        self.assertFalse(clear_button.icon().isNull())
                        self.assertEqual(clear_button.height(), 30)

                    with self.subTest("clear button retains its font"):
                        if sys.platform == 'win32':
                            self.assertIn("Microsoft YaHei UI", clear_button.font().family())
                        self.assertEqual(clear_button.font().pointSize(), 9)

                    clear_button_style = clear_button.styleSheet()
                    with self.subTest("clear button styles are scoped and lightweight"):
                        self.assertIn("PushButton#clearLogButton", clear_button_style)
                        self.assertIn("background-color: transparent", clear_button_style)
                        self.assertIn("padding: 4px 10px 4px 30px", clear_button_style)
                        self.assertIn("PushButton#clearLogButton:hover", clear_button_style)
                        self.assertIn("PushButton#clearLogButton:pressed", clear_button_style)
                        self.assertIn("#edf3f8", clear_button_style)
                        self.assertIn("#dbe7f1", clear_button_style)

                    with self.subTest("clear button does not draw another boundary"):
                        self.assertIn("border: none", clear_button_style)

                    log_style = window.log_text.styleSheet()
                    with self.subTest("log text uses the card boundary"):
                        self.assertIn("border: none", log_style)
                        self.assertNotIn("border-top", log_style)

                    window.log_text.setPlainText("diagnostic")
                    QTest.mouseClick(clear_button, tm.Qt.LeftButton)
                    self.assertEqual(window.log_text.toPlainText(), "")
                    popen.assert_not_called()
                finally:
                    window.supervisor_timer.stop()
                    window.tray.hide()
                    window.deleteLater()

    def test_tray_menu_bottom_aligns_to_taskbar_top(self):
        full = QRect(0, 0, 1920, 1080)
        available = QRect(0, 0, 1920, 1040)
        menu_size = tm.QSize(220, 150)
        position = tm.tray_menu_position(tm.QPoint(1880, 1055), menu_size, full, available)
        self.assertEqual(position.y() + menu_size.height(), available.bottom() + 1)
        self.assertGreaterEqual(position.x(), available.left())
        self.assertLessEqual(position.x() + menu_size.width(), available.right() + 1)
        icon = QRect(1600, 1043, 24, 24)
        aligned = tm.tray_menu_position(
            tm.QPoint(1612, 1055), menu_size, full, available, icon
        )
        self.assertEqual(aligned.x(), icon.left())

        top_taskbar = QRect(0, 40, 1920, 1040)
        top_position = tm.tray_menu_position(tm.QPoint(100, 20), menu_size, full, top_taskbar)
        self.assertEqual(top_position.y(), top_taskbar.top())

        secondary_screen = QRect(-1920, 0, 1920, 1080)
        secondary_available = QRect(-1920, 0, 1920, 1040)
        secondary_position = tm.tray_menu_position(
            tm.QPoint(-20, 1055), menu_size, secondary_screen, secondary_available
        )
        self.assertEqual(
            secondary_position.y() + menu_size.height(), secondary_available.bottom() + 1
        )
        self.assertLessEqual(
            secondary_position.x() + menu_size.width(), secondary_available.right() + 1
        )

    def test_tray_menu_is_shown_from_context_activation_with_all_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "tunnels.json"
            config.write_text("[]", encoding="utf-8")
            with patch.object(tm, "CONFIG_FILE", str(config)), \
                 patch.object(tm, "SETTINGS_FILE", str(Path(directory) / "settings.json")), \
                 patch.object(tm, "LOG_FILE", str(Path(directory) / "test.log")), \
                 patch.object(tm, "is_autostart_enabled", return_value=False), \
                 patch.object(tm.QTimer, "singleShot"):
                window = tm.MainWindow()
                try:
                    window.supervisor_timer.stop()
                    menu = window.tray_menu
                    self.assertIs(type(menu), QMenu)
                    self.assertEqual(
                        [action.text() for action in menu.actions() if not action.isSeparator()],
                        ["打开主界面", "重新连接全部隧道", "彻底退出"],
                    )
                    if sys.platform == 'win32':
                        self.assertIsNone(window.tray.contextMenu())
                    else:
                        self.assertIs(window.tray.contextMenu(), menu)
                    menu.popup(tm.QPoint(50, 50))
                    self.app.processEvents()
                    self.assertGreaterEqual(menu.actionGeometry(menu.actions()[0]).height(), 38)
                    self.assertGreaterEqual(menu.actionGeometry(menu.actions()[0]).left(), 8)
                    menu.hide()
                    with patch.object(window, "_show_tray_menu") as show_menu:
                        window._on_tray_activated(tm.QSystemTrayIcon.Context)
                        if sys.platform == 'win32':
                            show_menu.assert_called_once_with()
                        else:
                            show_menu.assert_not_called()
                finally:
                    window.supervisor_timer.stop()
                    window.tray.hide()
                    window.deleteLater()

    def test_tray_colors(self):
        for statuses, enabled, expected in [(["Disconnected"], True, "#ef4444"),
                                             (["Connecting"], True, "#eab308"),
                                             (["Connected"], True, "#22c55e"),
                                             (["Connected", "Disconnected"], True, "#eab308"),
                                             (["Stopped"], False, "#94a3b8")]:
            with self.subTest(statuses=statuses):
                items = [tunnel(enabled=enabled) for _ in statuses]
                for item, status in zip(items, statuses):
                    item.status = status
                window = controller(*items)
                with patch.object(tm, "make_circle_pixmap", wraps=tm.make_circle_pixmap) as pixmap:
                    tm.MainWindow.update_tray_state(window)
                self.assertEqual(pixmap.call_args.args[0], expected)


if __name__ == "__main__":
    unittest.main()
