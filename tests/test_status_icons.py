"""Status semantics and real Qt rendering, without external network access."""
import unittest
from unittest.mock import Mock, patch
from support import tm, tunnel


class StatusIconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = tm.QApplication.instance() or tm.QApplication([])

    def test_probe_reports_stages_and_success_clears_error(self):
        item = tunnel()
        item.process = Mock()
        item.process.poll.return_value = None
        item.last_error = "old failure"
        seen = []
        item._progress = lambda phase, retry: seen.append(phase)
        with patch.object(tm, "test_port_listening", return_value=True):
            item.check_health(Mock(), "ssh")
        self.assertEqual(seen, ["checking", "probing"])
        self.assertEqual(item.status, "Connected")
        self.assertEqual(item.last_error, "")

    def test_start_failure_keeps_reason_for_tooltip(self):
        item = tunnel()
        with patch.object(tm.subprocess, "Popen", side_effect=OSError("missing ssh")):
            item.start_process(Mock(), "missing")
        self.assertEqual(item.status, "Disconnected")
        self.assertIn("missing ssh", tm.tunnel_indicator(item)[3])

    def test_all_symbols_render_and_hidden_animations_stop(self):
        icon = tm.TunnelStatusIcon()
        try:
            icon.show()
            for key, (symbol, color, text, detail) in tm.TUNNEL_INDICATORS.items():
                with self.subTest(state=key):
                    icon.set_state(color, True, symbol)
                    self.app.processEvents()
                    self.assertFalse(icon.grab().isNull())
                    self.assertTrue(icon._timer.isActive())
                    icon.hide()
                    self.assertFalse(icon._timer.isActive())
                    icon.show()
                    icon.set_state(color, False, symbol)
                    self.assertFalse(icon._timer.isActive())
        finally:
            icon.close()
            icon.deleteLater()

    def test_transient_state_is_not_saved_to_config(self):
        item = tunnel()
        item.phase, item.activity = "probing", "check"
        self.assertNotIn("phase", item.to_dict())
        self.assertNotIn("activity", item.to_dict())

    def test_status_highlight_fits_content_with_balanced_padding(self):
        item = tunnel()
        item.status = "Connected"
        row = tm.TunnelRowWidget(0, item)
        try:
            row.show()
            self.app.processEvents()
            button = row.status_btn
            self.assertLess(button.width(), 110)
            left = row.dot_label.geometry().left()
            right = button.width() - row.status_text_lbl.geometry().right() - 1
            self.assertLessEqual(abs(left - right), 2)
        finally:
            row.close()
            row.deleteLater()

    def test_detection_spinner_does_not_resize_or_move_focus_frame(self):
        item = tunnel()
        row = tm.TunnelRowWidget(0, item)
        try:
            row.show()
            row.test_btn.setFocus()
            self.app.processEvents()
            button_rect = row.test_btn.geometry()
            initial_border = row.test_btn.grab().toImage().pixelColor(0, 15)
            text_bounds = None
            for activity in ("probe", None, "probe", None):
                item.activity = activity
                row._update_status_display()
                self.app.processEvents()
                self.assertEqual(row.test_btn.geometry(), button_rect)
                self.assertEqual(row.test_btn.text(), "检测")
                self.assertEqual(row.test_spinner.isVisible(), activity == "probe")
                self.assertTrue(row.test_btn.rect().contains(row.test_spinner.geometry()))
                picture = row.test_btn.grab().toImage()
                # 深色文字与青色边框、加载环分离，直接验证实际绘制位置。
                xs = [x for x in range(8, picture.width() - 8) for y in range(8, picture.height() - 8)
                      if picture.pixelColor(x, y).red() < 100
                      and picture.pixelColor(x, y).alpha() > 200
                      and picture.pixelColor(x, y).green() < 100
                      and picture.pixelColor(x, y).blue() < 150]
                self.assertTrue(xs)
                bounds = (min(xs), max(xs))
                self.assertLessEqual(abs(sum(bounds) / 2 - (picture.width() - 1) / 2), 2)
                if text_bounds is not None:
                    self.assertEqual(bounds, text_bounds)
                text_bounds = bounds
                if activity == "probe":
                    self.assertEqual(row.test_btn.grab().toImage().pixelColor(0, 15), initial_border)
        finally:
            row.close()
            row.deleteLater()

    def test_detection_focus_has_soft_fill_without_outline_and_busy_keeps_text(self):
        item = tunnel()
        row = tm.TunnelRowWidget(0, item)
        try:
            row.show()
            row.test_btn.setFocus()
            self.app.processEvents()
            before = row.test_btn.grab().toImage()
            self.assertEqual(before.pixelColor(0, 15).alpha(), 0)
            self.assertEqual(before.pixelColor(3, 15), tm.QColor("#ecfeff"))
            for activity in ("probe", None, "probe", None):
                item.activity = activity
                row._update_status_display()
                if activity is None:
                    row.test_btn.setFocus()
                self.app.processEvents()
                after = row.test_btn.grab().toImage()
                # 左侧包含文字和背景，右侧预留给唯一变化的加载环。
                self.assertEqual(before.copy(0, 0, 54, 34), after.copy(0, 0, 54, 34))
        finally:
            row.close()
            row.deleteLater()
