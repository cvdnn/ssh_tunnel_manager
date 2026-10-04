"""Exercise adapter boundaries without emulating an entire operating system."""
import types
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import tm, fixed_screen


class PlatformUiTests(unittest.TestCase):
    def test_layout_sizes_stay_within_the_screen(self):
        # Baseline, MacBook 13", 1366x768 desktop and a narrow Linux screen.
        cases = (
            (1920, 1080, (1100, 660), (1100, 420)),
            (1440, 900, (1100, 660), (1100, 340)),
            (1366, 768, (1100, 660), (1046, 320)),
            (1024, 768, (1024, 660), (704, 320)),
            (1920, 600, (1100, 600), (1100, 420)),
        )
        for width, height, collapsed, expanded in cases:
            screen = fixed_screen(width, height)
            with self.subTest(screen=f"{width}x{height}"):
                self.assertEqual(tm.window_size(False, screen), tm.QSize(*collapsed))
                self.assertEqual(tm.layout_widths(True, screen), expanded)
                self.assertEqual(tm.window_size(True, screen),
                                 tm.QSize(sum(expanded), collapsed[1]))

    def test_layout_sizes_have_a_headless_fallback(self):
        with patch.object(tm.QApplication, "primaryScreen", return_value=None):
            self.assertEqual(tm.window_size(False), tm.QSize(tm.MAIN_WIDTH, tm.MAIN_HEIGHT))
            self.assertEqual(tm.layout_widths(True), (tm.MAIN_WIDTH, tm.WORKSPACE_WIDTH))

    def test_splash_does_not_pass_windows_flags_on_posix(self):
        for system in ('darwin', 'linux'):
            with self.subTest(system=system), patch.object(tm.sys, 'platform', system), patch.object(tm.subprocess, 'Popen') as popen:
                tm.StartupSplashProcess()
                self.assertEqual(popen.call_args.kwargs.get('creationflags', 0), 0)

    def test_close_without_tray_shuts_down_instead_of_hiding(self):
        window = types.SimpleNamespace(settings={'minimizeToTray': True}, hide=Mock(), tray=Mock(), quit_app=Mock())
        with patch.object(tm.QSystemTrayIcon, 'isSystemTrayAvailable', return_value=False):
            tm.MainWindow.closeEvent(window, Mock())
        window.quit_app.assert_called_once()
        window.hide.assert_not_called()

    def test_native_tray_context_does_not_open_duplicate_popup(self):
        window = types.SimpleNamespace(_show_tray_menu=Mock(), _restore_from_tray=Mock())
        for system in ('darwin', 'linux'):
            with patch.object(tm.sys, 'platform', system):
                tm.MainWindow._on_tray_activated(window, tm.QSystemTrayIcon.Context)
        window._show_tray_menu.assert_not_called()
