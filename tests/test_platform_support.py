"""Platform integration contracts; never touch the real user's startup settings."""
import importlib
import os
from pathlib import Path
import plistlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


class PlatformSupportTests(unittest.TestCase):
    def adapter(self):
        self.assertIsNotNone(importlib.util.find_spec('platform_support'), 'platform adapter is missing')
        return importlib.import_module('platform_support')

    def test_release_version_is_an_integer_starting_at_v1(self):
        platform = self.adapter()
        self.assertIsInstance(platform.APP_VERSION_MAJOR, int)
        self.assertGreaterEqual(platform.APP_VERSION_MAJOR, 1)
        # 发布标签由整数版本推导，递增只改 APP_VERSION_MAJOR 一处。
        self.assertEqual(platform.APP_VERSION, 'v{}'.format(platform.APP_VERSION_MAJOR))
        self.assertEqual(platform.BUNDLE_VERSION, str(platform.APP_VERSION_MAJOR))

    def test_posix_process_flags_and_ssh_lookup(self):
        platform = self.adapter()
        for system in ('darwin', 'linux'):
            with self.subTest(system=system), patch.object(platform.sys, 'platform', system):
                self.assertEqual(platform.process_creation_flags(), 0)
                with patch.object(platform.shutil, 'which', return_value='/usr/bin/ssh'):
                    self.assertEqual(platform.default_ssh(), '/usr/bin/ssh')
                with patch.object(platform.shutil, 'which', return_value=None):
                    self.assertEqual(platform.default_ssh(), 'ssh')

    def test_macos_login_file_round_trip_and_disable(self):
        platform = self.adapter()
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            args = ['/Applications/My Python/python', '/app/main.py', '--data-dir', '/user/My Data']
            with patch.object(platform.sys, 'platform', 'darwin'), patch.object(platform.Path, 'home', return_value=home):
                self.assertFalse(platform.is_autostart_enabled())
                self.assertTrue(platform.set_autostart(True, args))
                files = list((home / 'Library/LaunchAgents').glob('*.plist'))
                self.assertEqual(len(files), 1)
                data = plistlib.loads(files[0].read_bytes())
                self.assertEqual(data['ProgramArguments'], args)
                self.assertTrue(data['RunAtLoad'])
                self.assertTrue(platform.is_autostart_enabled())
                self.assertTrue(platform.set_autostart(False, args))
                self.assertFalse(files[0].exists())

    def test_malformed_macos_login_file_does_not_prevent_app_start(self):
        platform = self.adapter()
        with tempfile.TemporaryDirectory() as folder, patch.object(platform.sys, 'platform', 'darwin'), patch.object(platform.Path, 'home', return_value=Path(folder)):
            file = platform.autostart_file()
            file.parent.mkdir(parents=True)
            file.write_bytes(plistlib.dumps(['unexpected-list']))
            self.assertFalse(platform.is_autostart_enabled())

    def test_linux_login_file_quotes_paths_and_honors_hidden(self):
        platform = self.adapter()
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(platform.sys, 'platform', 'linux'), patch.dict(os.environ, {'XDG_CONFIG_HOME': folder}):
                args = ['/opt/My Python/python', '/app/main.py', '--data-dir', '/data/100% safe/$user']
                self.assertTrue(platform.set_autostart(True, args))
                file = next((Path(folder) / 'autostart').glob('*.desktop'))
                data = file.read_text(encoding='utf-8')
                self.assertIn('"/opt/My Python/python"', data)
                self.assertIn('100%% safe/', data)
                self.assertNotIn('\nExec=/opt/My Python', data)
                self.assertTrue(platform.is_autostart_enabled())
                file.write_text(data + 'Hidden=true\n', encoding='utf-8')
                self.assertFalse(platform.is_autostart_enabled())
                self.assertTrue(platform.set_autostart(False, args))
                self.assertFalse(file.exists())

    def test_linux_rejects_control_characters_without_writing(self):
        platform = self.adapter()
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(platform.sys, 'platform', 'linux'), patch.dict(os.environ, {'XDG_CONFIG_HOME': folder}):
                self.assertFalse(platform.set_autostart(True, ['/python', '/app\nHidden=false']))
                self.assertFalse(list(Path(folder).rglob('*.desktop')))

    def test_runtime_home_preserves_existing_data_and_explicit_override(self):
        import paths
        self.assertTrue(hasattr(paths, 'runtime_home'), 'runtime path selection is missing')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'checkout'
            root.mkdir()
            with patch.dict(os.environ, {}, clear=True), patch.object(paths.Path, 'home', return_value=Path(folder)), patch.object(paths.sys, 'platform', 'linux'):
                self.assertEqual(paths.runtime_home(root), Path(folder) / '.local/share/ssh-tunnel-manager')
                (root / 'data').mkdir()
                (root / 'data/tunnels.json').write_text('[]')
                self.assertEqual(paths.runtime_home(root), root)
                with patch.dict(os.environ, {'SSH_TUNNEL_MANAGER_HOME': str(Path(folder) / 'custom')}):
                    # runtime_home resolves symlinks, which macOS temp directories are made of.
                    self.assertEqual(paths.runtime_home(root), Path(folder).resolve() / 'custom')

    def test_fresh_runtime_homes_and_invalid_xdg_fallback(self):
        import paths
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            for system, expected in [('win32', home / 'AppData/Local/SshTunnelManager'),
                                     ('darwin', home / 'Library/Application Support/SshTunnelManager'),
                                     ('linux', home / '.local/share/ssh-tunnel-manager')]:
                with self.subTest(system=system), patch.dict(os.environ, {'XDG_DATA_HOME': 'relative'}, clear=True), patch.object(paths.sys, 'platform', system), patch.object(paths.Path, 'home', return_value=home):
                    self.assertEqual(paths.runtime_home(home / 'new-checkout'), expected)

    def test_windows_registry_command_preserves_spaces_and_data_home(self):
        from unittest.mock import MagicMock
        platform = self.adapter()
        registry = MagicMock()
        args = ['C:/Python Home/pythonw.exe', 'C:/My App/main.py', '--data-dir', 'C:/My Data']
        with patch.object(platform.sys, 'platform', 'win32'), patch.dict(sys.modules, {'winreg': registry}):
            self.assertTrue(platform.set_autostart(True, args))
            self.assertEqual(registry.SetValueEx.call_args.args[-1], platform.subprocess.list2cmdline(args))
            registry.OpenKey.side_effect = FileNotFoundError()
            self.assertTrue(platform.set_autostart(False, args))

    def test_startup_write_failure_preserves_existing_file(self):
        platform = self.adapter()
        with tempfile.TemporaryDirectory() as folder, patch.object(platform.sys, 'platform', 'linux'), patch.dict(os.environ, {'XDG_CONFIG_HOME': folder}):
            self.assertTrue(platform.set_autostart(True, ['/python', '/old.py']))
            file = platform.autostart_file()
            original = file.read_bytes()
            with patch.object(platform.os, 'replace', side_effect=PermissionError('test')):
                self.assertFalse(platform.set_autostart(True, ['/python', '/new.py']))
            self.assertEqual(file.read_bytes(), original)
            self.assertEqual(len(list(file.parent.iterdir())), 1)

    def test_dock_icon_hidden_only_for_macos_processes(self):
        platform = self.adapter()
        with patch.object(platform.sys, 'platform', 'linux'):
            self.assertFalse(platform.hide_dock_icon())
        with patch.object(platform.sys, 'platform', 'darwin'), patch('ctypes.CDLL') as library:
            library.return_value.sel_registerName.side_effect = lambda selector: selector
            self.assertTrue(platform.hide_dock_icon())
            sent = [call.args for call in library.return_value.objc_msgSend.call_args_list]
            self.assertIn(b'sharedApplication', [args[1] for args in sent])
            # NSApplicationActivationPolicyAccessory keeps the window without a Dock tile.
            self.assertEqual(sent[-1][1], b'setActivationPolicy:')
            self.assertEqual(sent[-1][2], 1)
        with patch.object(platform.sys, 'platform', 'darwin'), patch('ctypes.CDLL', side_effect=OSError('missing')):
            self.assertFalse(platform.hide_dock_icon())

    def test_startup_error_uses_the_native_mechanism_of_each_desktop(self):
        platform = self.adapter()
        for system, launcher in (('darwin', 'osascript'), ('linux', 'notify-send')):
            with self.subTest(system=system), patch.object(platform.sys, 'platform', system), \
                    patch.object(platform.shutil, 'which', return_value='/usr/bin/notify-send'), \
                    patch.object(platform.subprocess, 'run') as run:
                platform.show_error('需要迁移')
                arguments = run.call_args.args[0]
                self.assertEqual(arguments[0], launcher)
                self.assertEqual(arguments[-1], '需要迁移')
        with patch.object(platform.sys, 'platform', 'win32'), \
                patch.object(platform.subprocess, 'run') as run, \
                patch('ctypes.windll.user32.MessageBoxW') as message_box:
            platform.show_error('需要迁移')
            run.assert_not_called()
            message_box.assert_called_once()

    def test_startup_error_without_a_desktop_notifier_only_prints(self):
        platform = self.adapter()
        with patch.object(platform.sys, 'platform', 'linux'), \
                patch.object(platform.shutil, 'which', return_value=None), \
                patch.object(platform.subprocess, 'run') as run:
            platform.show_error('需要迁移')
            run.assert_not_called()
