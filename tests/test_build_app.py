"""Packaging contracts: bundle flags, icons, plist identity and the frozen relaunch path.

Content-only assertions keep this suite runnable on every OS, without building anything.
"""

import importlib.util
import os
from pathlib import Path
import plistlib
import socket
import struct
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT / 'src', ROOT / 'tests'):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

import paths
from platform_support import APP_DISPLAY_NAME
from support import tm


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


packager = load_module('build_app_script', ROOT / 'scripts' / 'build_app.py')


def options(**overrides):
    data = dict(name='SSH Tunnel Manager', output=Path('/tmp/release'), version='1.0')
    data.update(overrides)
    return types.SimpleNamespace(**data)


class FrozenPathsTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {'SSH_TUNNEL_MANAGER_HOME': ''}, clear=False)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_bundle_reads_its_own_data_directory_argument(self):
        requested = Path(tempfile.gettempdir()) / '中文 data'
        with patch.object(paths, 'is_frozen', return_value=True), \
             patch.object(paths.sys, 'argv', ['SSH Tunnel Manager', '--data-dir', str(requested)]):
            self.assertEqual(paths.argument_value('--data-dir'), str(requested))
            self.assertEqual(paths.runtime_home(), requested.resolve())

    def test_command_line_beats_a_stale_environment_variable(self):
        requested = Path(tempfile.gettempdir()) / '命令行 data'
        with patch.object(paths, 'is_frozen', return_value=True), \
             patch.object(paths.sys, 'argv', ['SSH Tunnel Manager', '--data-dir', str(requested)]), \
             patch.dict(os.environ, {'SSH_TUNNEL_MANAGER_HOME': str(Path(tempfile.gettempdir()) / 'env data')}):
            self.assertEqual(paths.runtime_home(), requested.resolve())

    def test_bundle_never_adopts_a_data_directory_next_to_itself(self):
        with tempfile.TemporaryDirectory() as folder:
            checkout = Path(folder)
            (checkout / 'data').mkdir()
            (checkout / 'data' / 'tunnels.json').write_text('[]', encoding='utf-8')
            with patch.object(paths, 'is_frozen', return_value=True), \
                 patch.object(paths.sys, 'argv', ['SSH Tunnel Manager']):
                self.assertNotEqual(paths.runtime_home(checkout), checkout)
            with patch.object(paths, 'is_frozen', return_value=False):
                self.assertEqual(paths.runtime_home(checkout), checkout)

    def test_packaged_resources_and_logo_live_inside_the_payload(self):
        payload = Path(tempfile.gettempdir()) / 'tunnel-payload'
        import platform_support
        with patch.object(platform_support, 'is_frozen', return_value=True), \
             patch.object(sys, '_MEIPASS', str(payload), create=True), \
             patch.object(sys, 'argv', ['SSH Tunnel Manager']):
            frozen = load_module('frozen_paths', ROOT / 'src' / 'paths.py')
            self.assertEqual(frozen.resource_root(), payload)
            self.assertEqual(frozen.LOGO_FILE, payload / 'assets' / 'app_logo.png')
        self.assertEqual(paths.resource_root(), ROOT / 'src')
        self.assertEqual(paths.LOGO_FILE, ROOT / 'src' / 'assets' / 'app_logo.png')


class BundleCommandTests(unittest.TestCase):
    def test_checkout_launches_the_bin_entry_while_a_bundle_launches_itself(self):
        with patch.object(tm.platform_support, 'is_frozen', return_value=True):
            self.assertEqual(tm.self_launch_arguments('/Applications/App.app/Contents/MacOS/App'),
                             ['/Applications/App.app/Contents/MacOS/App'])
        with patch.object(tm.platform_support, 'is_frozen', return_value=False):
            self.assertEqual(tm.self_launch_arguments('/usr/bin/python3'),
                             ['/usr/bin/python3', str(tm.BIN_FILE)])

    def test_frozen_splash_gets_a_loopback_channel_instead_of_stdin(self):
        received, holder = [], {}

        class FakeProcess:
            def kill(self):
                pass

        def fake_popen(arguments, **kwargs):
            holder['arguments'] = list(arguments)
            holder['stdin'] = kwargs.get('stdin')
            port = int(arguments[arguments.index('--splash-port') + 1])
            connection = socket.create_connection(('127.0.0.1', port), timeout=10)

            def pump():
                try:
                    channel = connection.makefile('rb')
                    for line in channel:
                        received.append(line.decode('utf-8').rstrip('\r\n'))
                    channel.close()
                finally:
                    connection.close()

            threading.Thread(target=pump, daemon=True).start()
            return FakeProcess()

        with patch.object(tm.platform_support, 'is_frozen', return_value=True), \
             patch.object(tm.subprocess, 'Popen', fake_popen):
            splash = tm.StartupSplashProcess()
            arguments = holder['arguments']
            self.assertIn('--startup-splash', arguments)
            self.assertNotIn(str(tm.BIN_FILE), arguments)
            self.assertEqual(arguments[-1], str(splash.listener.getsockname()[1]))
            self.assertEqual(holder['stdin'], tm.subprocess.DEVNULL)
            splash.set_status('正在准备隧道列表…')
            deadline = time.monotonic() + 10
            while not received and time.monotonic() < deadline:
                time.sleep(0.01)
            splash.close()
        self.assertEqual(received, ['正在准备隧道列表…'])


class PyInstallerArgumentsTests(unittest.TestCase):
    def test_entry_is_the_application_itself_and_never_opens_a_console(self):
        command = packager.pyinstaller_command(options(), Path('/tmp/work'), None)
        self.assertEqual(command[-1], str(packager.ENTRY_SCRIPT))
        self.assertIn('--windowed', command)     # double-click must not flash a console
        self.assertNotIn('--onefile', command)   # the splash child would re-extract every launch
        self.assertEqual(command[command.index('--paths') + 1], str(ROOT / 'src'))

    def test_nothing_is_force_collected_beyond_the_application_imports(self):
        command = ' '.join(packager.pyinstaller_command(options(), Path('/tmp/work'), None))
        # collect-submodules on qfluentwidgets drags in QtWebEngine/QtQuick3D (300 MB+).
        self.assertNotIn('--collect-submodules', command)
        self.assertNotIn('--collect-data', command)

    def test_logo_ships_inside_the_bundle_payload(self):
        icon = Path('/tmp/AppIcon.icns')
        command = packager.pyinstaller_command(options(), Path('/tmp/work'), icon)
        source, _, destination = command[command.index('--add-data') + 1].partition(os.pathsep)
        self.assertEqual(destination, 'assets')
        self.assertTrue((Path(source) / 'app_logo.png').is_file())
        self.assertEqual(command[command.index('--icon') + 1], str(icon))

    def test_windows_bundle_drops_collected_icu_that_shadows_the_system_dll(self):
        with tempfile.TemporaryDirectory() as folder:
            bundle = Path(folder)
            internal = bundle / '_internal'
            internal.mkdir()
            conflicting = [internal / 'icuuc.dll', internal / 'icudt78.dll']
            for path in conflicting:
                path.write_bytes(b'not the Windows system ICU')
            unrelated = internal / 'Qt6Core.dll'
            unrelated.write_bytes(b'keep me')

            removed = packager.remove_collected_windows_icu(bundle)

            self.assertEqual(removed, conflicting)
            self.assertTrue(all(not path.exists() for path in conflicting))
            self.assertTrue(unrelated.is_file())


class WindowsIconTests(unittest.TestCase):
    def test_directory_precedes_payloads_and_two_fifty_six_stores_as_zero(self):
        images = [(16, b'aaaa'), (256, b'bbbbbbbb')]
        data = packager.ico_container(images)
        self.assertEqual(struct.unpack('<HHH', data[:6]), (0, 1, 2))
        entries = [struct.unpack('<BBBBHHII', data[6 + 16 * index:22 + 16 * index])
                   for index in range(2)]
        self.assertEqual(entries[0][:4], (16, 16, 0, 0))
        self.assertEqual(entries[1][:4], (0, 0, 0, 0))
        first, second = entries[0][7], entries[1][7]
        self.assertEqual(first, 6 + 16 * 2)
        self.assertEqual(second, first + 4)
        self.assertEqual(data[first:second], b'aaaa')
        self.assertEqual(data[second:second + 8], b'bbbbbbbb')
        self.assertEqual(len(data), second + 8)


class MacOSBundleTests(unittest.TestCase):
    def test_release_label_becomes_a_numeric_bundle_version(self):
        with tempfile.TemporaryDirectory() as folder:
            app = Path(folder) / 'App.app'
            contents = app / 'Contents'
            contents.mkdir(parents=True)
            (contents / 'Info.plist').write_bytes(plistlib.dumps({'CFBundleExecutable': 'App'}))
            packager.patch_macos_plist(app, 'App', 'v3')
            payload = plistlib.loads((contents / 'Info.plist').read_bytes())
            self.assertEqual(payload['CFBundleShortVersionString'], '3')
            self.assertEqual(payload['CFBundleVersion'], '3')

    def test_plist_carries_the_application_identity_and_no_extension_claims(self):
        with tempfile.TemporaryDirectory() as folder:
            app = Path(folder) / 'App.app'
            contents = app / 'Contents'
            (contents / 'Resources').mkdir(parents=True)
            (contents / 'Resources' / 'AppIcon.icns').write_bytes(b'icns')
            (contents / 'Info.plist').write_bytes(plistlib.dumps({'CFBundleExecutable': 'App'}))
            packager.patch_macos_plist(app, 'App', '9.9')
            payload = plistlib.loads((contents / 'Info.plist').read_bytes())
            self.assertEqual(payload['CFBundleExecutable'], 'App')
            self.assertEqual(payload['CFBundleIdentifier'], packager.BUNDLE_IDENTIFIER)
            self.assertEqual(payload['CFBundleDisplayName'], APP_DISPLAY_NAME)
            self.assertEqual(payload['CFBundleShortVersionString'], '9.9')
            self.assertEqual(payload['CFBundleIconFile'], 'AppIcon')
            self.assertTrue(payload['NSHighResolutionCapable'])
            self.assertNotIn('CFBundleDocumentTypes', payload)
            self.assertNotIn('LSUIElement', payload)  # the window still owns a Dock tile

    def test_missing_icon_file_is_not_referenced(self):
        with tempfile.TemporaryDirectory() as folder:
            app = Path(folder) / 'App.app'
            contents = app / 'Contents'
            contents.mkdir(parents=True)
            (contents / 'Info.plist').write_bytes(plistlib.dumps({'CFBundleExecutable': 'App'}))
            packager.patch_macos_plist(app, 'App', '9.9')
            payload = plistlib.loads((contents / 'Info.plist').read_bytes())
            self.assertNotIn('CFBundleIconFile', payload)


class WindowsInstallerTests(unittest.TestCase):
    def test_script_ships_the_whole_bundle_and_launches_the_bundled_exe(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            bundle = output / 'SSH Tunnel Manager'
            bundle.mkdir()
            calls = []

            def record(command, note, env=None):
                calls.append([str(part) for part in command])
                return 0

            with patch.object(packager, 'find_inno_setup', return_value=output / 'iscc'), \
                 patch.object(packager, 'execute', record):
                self.assertTrue(packager.windows_installer(bundle, options(output=output), 'base-name'))
            script = (output / 'SSH Tunnel Manager.iss').read_text(encoding='utf-8-sig')
            self.assertIn('SSH Tunnel Manager.exe', script)
            self.assertIn('recursesubdirs', script)
            self.assertIn(str(bundle), script)
            self.assertEqual(calls[0][-2:], ['/Qp', str(output / 'SSH Tunnel Manager.iss')])

    def test_installer_is_skipped_without_inno_setup(self):
        with patch.object(packager, 'find_inno_setup', return_value=None):
            self.assertFalse(packager.windows_installer(Path('/tmp/bundle'), options(), 'base-name'))


if __name__ == '__main__':
    unittest.main()
