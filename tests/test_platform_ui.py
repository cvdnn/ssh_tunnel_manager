"""Exercise adapter boundaries without emulating an entire operating system."""
import types
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import tm


class PlatformUiTests(unittest.TestCase):
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
