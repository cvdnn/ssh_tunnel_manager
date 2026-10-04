"""macOS launcher bundle contracts (content only, so the suite runs on every OS)."""

import importlib.util
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'src') not in sys.path:
    sys.path.insert(0, str(ROOT / 'src'))


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUILDER = load_module('macos_app_builder', ROOT / 'scripts' / 'make_macos_app.py')


class MacOSAppTests(unittest.TestCase):
    def test_plist_binds_executable_without_claiming_any_extension(self):
        payload = BUILDER.bundle_plist(with_icon=True)
        self.assertEqual(payload['CFBundleExecutable'], BUILDER.EXECUTABLE)
        self.assertEqual(payload['CFBundleIconFile'], 'AppIcon')
        # The bundle is the double-click target; claiming .pyw would redirect every such file.
        self.assertNotIn('CFBundleDocumentTypes', payload)
        self.assertNotIn('CFBundleIconFile', BUILDER.bundle_plist(with_icon=False))

    def test_launcher_quotes_paths_and_validates_the_interpreter(self):
        interpreter = Path('/tmp/中文 venv/bin/python')
        text = BUILDER.launcher_text(interpreter, Path('/tmp/pro j/ssh-tunnel-manager.pyw'))
        self.assertIn("'{}'".format(interpreter), text)
        self.assertIn('-x "$INTERPRETER"', text)
        self.assertIn('osascript', text)
        self.assertIn('scripts/dev.py', text)
        self.assertIn('exec "$INTERPRETER" "$ENTRY" "$@"', text)

    @unittest.skipIf(sys.platform == 'win32', 'POSIX shell syntax check')
    def test_launcher_is_valid_posix_shell(self):
        with tempfile.NamedTemporaryFile('w', suffix='.sh', delete=False, encoding='utf-8') as handle:
            handle.write(BUILDER.launcher_text(sys.executable, ROOT / 'bin' / 'ssh-tunnel-manager.pyw'))
            path = Path(handle.name)
        try:
            self.assertEqual(subprocess.run(['sh', '-n', str(path)]).returncode, 0)
        finally:
            path.unlink()

    def test_write_bundle_creates_a_readable_plist_and_executable_launcher(self):
        with tempfile.TemporaryDirectory() as folder:
            app = Path(folder) / (BUILDER.APP_NAME + '.app')
            launcher = BUILDER.write_bundle(app, sys.executable, ROOT / 'bin' / 'ssh-tunnel-manager.pyw')
            self.assertEqual(launcher, app / 'Contents' / 'MacOS' / BUILDER.EXECUTABLE)
            self.assertTrue(launcher.is_file())
            with (app / 'Contents' / 'Info.plist').open('rb') as stream:
                self.assertEqual(plistlib.load(stream)['CFBundleIdentifier'], BUILDER.BUNDLE_IDENTIFIER)
            if sys.platform != 'win32':
                self.assertTrue(launcher.stat().st_mode & 0o111)


if __name__ == '__main__':
    unittest.main()
