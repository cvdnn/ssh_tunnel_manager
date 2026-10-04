"""Threading regressions with controlled I/O; never contacts real SSH hosts."""
import threading
import time
import subprocess
import sys
import unittest
from unittest.mock import patch

from support import tm, tunnel


class AsyncTunnelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = tm.QApplication.instance() or tm.QApplication([])

    def setUp(self):
        self.jobs = tm.TunnelWorkerPool()
        self.closed = False
        self.jobs.finished.connect(lambda: setattr(self, "closed", True))

    def pump_until(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertTrue(predicate(), "后台任务未在期限内完成")

    def tearDown(self):
        self.jobs.shutdown()
        self.pump_until(lambda: self.closed)
        self.jobs.deleteLater()

    def test_slow_starts_run_in_parallel_without_blocking_gui_or_worker_logging(self):
        release = threading.Event()
        entered = [threading.Event(), threading.Event()]
        worker_threads, log_threads = [], []
        self.jobs.logged.connect(lambda *_: log_threads.append(threading.get_ident()))
        def slow_start(runtime, logger, *_):
            worker_threads.append(threading.get_ident())
            entered[int(runtime.name)].set()
            release.wait(2)
            runtime.status = "Connecting"
            logger("started", "INFO")
        with patch.object(tm.TunnelItem, "start_process", slow_start):
            try:
                self.jobs.request(tunnel(name="0"), "start", "ssh.exe")
                self.jobs.request(tunnel(name="1"), "start", "ssh.exe")
                ticks = []
                tm.QTimer.singleShot(0, lambda: ticks.append(True))
                self.pump_until(lambda: all(e.is_set() for e in entered) and ticks)
                self.assertEqual(len(worker_threads), 2)
                self.assertNotIn(threading.get_ident(), worker_threads)
            finally:
                release.set()
            self.pump_until(lambda: len(log_threads) == 2)
            self.assertEqual(set(log_threads), {threading.get_ident()})

    def test_probe_is_async_and_a_slow_tunnel_does_not_hold_up_another(self):
        release, entered = threading.Event(), threading.Event()
        slow, fast = tunnel(name="slow"), tunnel(name="fast")
        def health(runtime, logger, *_):
            if runtime.name == "slow":
                entered.set()
                release.wait(2)
            runtime.status = "Connected"
        with patch.object(tm.TunnelItem, "check_health", health):
            try:
                self.jobs.request(slow, "check", "ssh.exe")
                self.jobs.request(fast, "check", "ssh.exe")
                self.pump_until(lambda: entered.is_set() and fast.status == "Connected")
                self.assertNotEqual(slow.status, "Connected")
            finally:
                release.set()
            self.pump_until(lambda: slow.status == "Connected")

    def test_worker_reports_probe_stage_before_completion_and_discards_stale_progress(self):
        release, entered = threading.Event(), threading.Event()
        item = tunnel()
        def health(runtime, logger, *_):
            runtime.report_progress("probing")
            entered.set()
            release.wait(2)
            runtime.report_progress("retrying")
        with patch.object(tm.TunnelItem, "check_health", health):
            try:
                self.jobs.request(item, "check")
                self.assertEqual(getattr(item, "phase", None), "queued")
                self.pump_until(lambda: entered.is_set() and item.phase == "probing")
                item.enabled = False
                self.jobs.request(item, "stop")
            finally:
                release.set()
            self.pump_until(lambda: not self.jobs.busy)
            self.assertIsNone(item.phase)
            self.assertEqual(item.status, "Stopped")

    def test_row_distinguishes_waiting_retrying_and_connected(self):
        item = tunnel()
        item.activity = None
        item.status = "Connecting"
        row = tm.TunnelRowWidget(0, item)
        try:
            self.assertEqual(row.status_text_lbl.text(), "等待监听")
            item.retry_count = 2
            row._update_status_display()
            self.assertEqual(row.status_text_lbl.text(), "重连等待")
            self.assertIn("2", row.dot_label.toolTip())
            item.status = "Connected"
            row._update_status_display()
            self.assertEqual(row.dot_label._symbol, "check")
            self.assertIn("不代表远端", row.dot_label.toolTip())
        finally:
            row.deleteLater()

    def test_probe_preserves_result_until_completion_and_only_notifies_changes(self):
        for result in ("Connected", "Disconnected", "Connecting", "error"):
            with self.subTest(result=result):
                self._assert_probe_display(result)

    def _assert_probe_display(self, result):
        release = threading.Event()
        item = tunnel()
        item.status = "Connected"
        row = tm.TunnelRowWidget(0, item)
        self.jobs.changed.connect(row._update_status_display)
        notifications = []
        def changed():
            notifications.append(item.status)
        self.jobs.changed.connect(changed)
        def health(runtime, logger, *_):
            runtime.report_progress("probing")
            release.wait(2)
            if result == "error":
                raise OSError("probe failed")
            runtime.status = result
        try:
            with patch.object(tm.TunnelItem, "check_health", health):
                self.jobs.request(item, "check")
                self.pump_until(lambda: item.phase == "probing")
                self.assertEqual(row.status_text_lbl.text(), "已连接")
                self.assertEqual(item.status, "Connected")
                self.assertEqual(notifications, [])
                self.assertFalse(row.dot_label._busy)
                release.set()
                self.pump_until(lambda: not self.jobs.busy)
                expected = "Disconnected" if result == "error" else result
                self.assertEqual(item.status, expected)
                self.assertEqual(notifications, [] if result == "Connected" else [expected])
                self.assertEqual(row.status_text_lbl.text(), tm.TUNNEL_INDICATORS[expected][2])
        finally:
            release.set()
            self.jobs.changed.disconnect(changed)
            self.jobs.changed.disconnect(row._update_status_display)
            row.deleteLater()

    def test_unchanged_row_skips_widget_updates_even_when_another_tunnel_changes(self):
        item = tunnel()
        item.status = "Connected"
        row = tm.TunnelRowWidget(0, item)
        try:
            with patch.object(row.dot_label, "set_state") as icon, \
                 patch.object(row.status_text_lbl, "setText") as label, \
                 patch.object(row.status_text_lbl, "setStyleSheet") as style:
                item.activity, item.phase = "check", "probing"
                row._update_status_display()
                item.activity = item.phase = None
                row._update_status_display()
                icon.assert_not_called()
                label.assert_not_called()
                style.assert_not_called()
        finally:
            row.deleteLater()

    def test_pid_updates_even_when_connection_status_stays_connected(self):
        from unittest.mock import Mock
        item = tunnel()
        item.status = "Connected"
        row = tm.TunnelRowWidget(0, item)
        self.jobs.changed.connect(row._update_status_display)
        process = Mock(pid=12345)
        process.poll.return_value = None
        def health(runtime, logger, *_):
            runtime.process = process
            runtime.status = "Connected"
        try:
            self.assertEqual(row.pid_lbl.text(), "—")
            with patch.object(tm.TunnelItem, "check_health", health):
                for pid in (12345, 23456):
                    process.pid = pid
                    self.jobs.request(item, "check")
                    self.pump_until(lambda: not self.jobs.busy)
                    self.assertEqual(row.pid_lbl.text(), str(pid))
            item.enabled = False
            self.jobs.request(item, "stop")
            self.pump_until(lambda: not self.jobs.busy)
            self.assertEqual(row.pid_lbl.text(), "—")
        finally:
            self.jobs.changed.disconnect(row._update_status_display)
            row.deleteLater()

    def test_manual_probe_is_async_shows_progress_and_reports_result(self):
        from unittest.mock import Mock
        item = tunnel()
        item.status = "Connected"
        runtime = tunnel()
        runtime.status = "Connected"
        runtime.process = Mock(pid=42)
        runtime.process.poll.return_value = None
        self.jobs._entries[item] = dict(runtime=runtime, future=None, pending=None, version=0)
        row = tm.TunnelRowWidget(0, item)
        row.test_requested.connect(lambda _: self.jobs.request(item, "probe"))
        self.jobs.changed.connect(row._update_status_display)
        release, entered = threading.Event(), threading.Event()
        def probe(*args, **kwargs):
            entered.set()
            release.wait(2)
            return False
        try:
            with patch.object(tm, "test_port_listening", side_effect=probe) as port, \
                 patch.object(tm.TunnelItem, "start_process") as start:
                row.test_btn.click()
                self.pump_until(lambda: entered.is_set() and item.phase == "probing")
                self.assertEqual(row.status_text_lbl.text(), "检测端口")
                self.assertEqual(item.status, "Connected")
                self.assertEqual(row.test_btn.text(), "检测")
                self.assertFalse(row.test_spinner.isHidden())
                self.assertTrue(row.test_spinner._busy)
                self.assertFalse(row.test_btn.isEnabled())
                release.set()
                self.pump_until(lambda: not self.jobs.busy)
                self.assertEqual(row.test_btn.text(), "检测")
                self.assertTrue(row.test_spinner.isHidden())
                self.assertFalse(row.test_spinner._timer.isActive())
                self.assertEqual(item.status, "Disconnected")
                self.assertTrue(row.test_btn.isEnabled())
                port.assert_called_once_with(item.local_port, host=item.local_host, timeout=2.0)
                start.assert_not_called()
            with patch.object(tm, "test_port_listening", return_value=True):
                row.test_btn.click()
                self.pump_until(lambda: not self.jobs.busy)
                self.assertEqual(row.test_btn.text(), "检测")
                self.assertTrue(row.test_spinner.isHidden())
                self.assertEqual(item.status, "Connected")
        finally:
            release.set()
            self.jobs.changed.disconnect(row._update_status_display)
            row.deleteLater()

    def test_status_click_reconnects_without_selecting_row_and_shows_progress(self):
        from PySide6.QtTest import QTest
        item = tunnel()
        item.status = "Connected"
        row = tm.TunnelRowWidget(0, item)
        selected, clicked = [], []
        row.selected.connect(selected.append)
        def reconnect(index):
            clicked.append(index)
            self.jobs.request(item, "start")
        row.reconnect.connect(reconnect)
        self.jobs.changed.connect(row._update_status_display)
        release, entered = threading.Event(), threading.Event()
        def start(runtime, logger, *_):
            runtime.report_progress("starting")
            entered.set()
            release.wait(2)
            runtime.status = "Connecting"
        try:
            row.show()
            with patch.object(tm.TunnelItem, "start_process", start):
                QTest.mouseClick(row.status_btn, tm.Qt.LeftButton)
                self.assertEqual(row.status_text_lbl.text(), "排队中")
                self.pump_until(lambda: entered.is_set() and item.phase == "starting")
                self.assertEqual(row.status_text_lbl.text(), "启动连接")
                self.assertFalse(row.status_btn.isEnabled())
                self.assertEqual(clicked, [0])
                self.assertEqual(selected, [])
                release.set()
                self.pump_until(lambda: not self.jobs.busy)
                self.assertEqual(row.status_text_lbl.text(), "等待监听")
                self.assertTrue(row.status_btn.isEnabled())
        finally:
            release.set()
            self.jobs.changed.disconnect(row._update_status_display)
            row.close()
            row.deleteLater()

    def test_disabling_during_manual_probe_discards_late_result(self):
        release, entered = threading.Event(), threading.Event()
        item = tunnel()
        def probe(*args, **kwargs):
            entered.set()
            release.wait(2)
            return True
        with patch.object(tm, "test_port_listening", side_effect=probe):
            try:
                self.jobs.request(item, "probe")
                self.pump_until(entered.is_set)
                item.enabled = False
                self.jobs.request(item, "stop")
            finally:
                release.set()
            self.pump_until(lambda: not self.jobs.busy)
        self.assertEqual(item.status, "Stopped")
        self.assertEqual(item.probe_result, "")

    def test_manual_probe_rejects_unowned_listener_and_handles_exception(self):
        from unittest.mock import Mock
        for outcome in (True, OSError("test failure")):
            with self.subTest(outcome=outcome):
                runtime = tunnel()
                probe = Mock(return_value=outcome) if outcome is True else Mock(side_effect=outcome)
                with patch.object(tm, "test_port_listening", probe):
                    result, logs = self.jobs._execute(runtime, "probe", runtime.to_dict(), "ssh", {})
                self.assertEqual(result.status, "Disconnected")
                self.assertEqual(result.probe_result, "不可用")
                self.assertTrue(logs)

    def test_repeated_probes_coalesce_and_disabling_rejects_stale_success(self):
        release, entered = threading.Event(), threading.Event()
        item = tunnel()
        calls = []
        def health(runtime, logger, *_):
            calls.append(True)
            entered.set()
            release.wait(2)
            runtime.status = "Connected"
        with patch.object(tm.TunnelItem, "check_health", health):
            try:
                self.jobs.request(item, "check", "ssh.exe")
                self.pump_until(entered.is_set)
                for _ in range(10):
                    self.jobs.request(item, "check", "ssh.exe")
                item.enabled = False
                self.jobs.request(item, "stop", "ssh.exe")
            finally:
                release.set()
            self.pump_until(lambda: not self.jobs.busy)
            self.assertEqual(len(calls), 1)
            self.assertEqual(item.status, "Stopped")

    def test_shutdown_during_start_cleans_up_before_finishing(self):
        release, entered, stopped = threading.Event(), threading.Event(), threading.Event()
        def start(runtime, logger, *_):
            entered.set()
            release.wait(2)
            runtime.status = "Connecting"
        def stop(runtime, logger=None):
            stopped.set()
            runtime.status = "Stopped"
        with patch.object(tm.TunnelItem, "start_process", start), \
             patch.object(tm.TunnelItem, "stop_process", stop):
            try:
                self.jobs.request(tunnel(), "start", "ssh.exe")
                self.pump_until(entered.is_set)
                self.jobs.shutdown()
                self.assertFalse(self.closed)
            finally:
                release.set()
            self.pump_until(lambda: self.closed)
            self.assertTrue(stopped.is_set())

    def test_shutdown_cleans_real_child_created_by_an_inflight_start(self):
        release, entered = threading.Event(), threading.Event()
        children = []
        real_popen = subprocess.Popen
        def slow_popen(args, **kwargs):
            entered.set()
            release.wait(2)
            child = real_popen([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)
            children.append(child)
            return child
        with patch.object(tm.subprocess, "Popen", slow_popen):
            try:
                self.jobs.request(tunnel(), "start", "ssh.exe")
                self.pump_until(entered.is_set)
                self.jobs.shutdown()
                self.assertFalse(self.closed)
                release.set()
                self.pump_until(lambda: self.closed, timeout=5)
                self.assertEqual(len(children), 1)
                self.assertIsNotNone(children[0].poll())
                self.assertTrue(children[0].stderr.closed)
            finally:
                release.set()
                for child in children:
                    if child.poll() is None:
                        child.kill()
                        child.wait(timeout=5)

    def test_editing_during_start_uses_latest_snapshot_without_overlapping(self):
        release, entered = threading.Event(), threading.Event()
        calls = []
        item = tunnel(name="old")
        def start(runtime, logger, *_):
            calls.append(runtime.name)
            if runtime.name == "old":
                entered.set()
                release.wait(2)
            runtime.status = "Connecting"
        with patch.object(tm.TunnelItem, "start_process", start):
            try:
                self.jobs.request(item, "start", "ssh.exe")
                self.pump_until(entered.is_set)
                item.name = "intermediate"
                self.jobs.request(item, "start", "ssh.exe")
                item.name = "latest"
                self.jobs.request(item, "start", "ssh.exe")
                self.assertEqual(calls, ["old"])
            finally:
                release.set()
            self.pump_until(lambda: not self.jobs.busy)
            self.assertEqual(calls, ["old", "latest"])


if __name__ == "__main__":
    unittest.main()
