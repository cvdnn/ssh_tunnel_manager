"""Real entry smoke tests using the splash only, without user runtime data."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class EntryTests(unittest.TestCase):
    def test_portable_help_does_not_create_data(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder) / 'unused'
            result = subprocess.run([sys.executable, str(ROOT / 'bin/ssh-tunnel-manager.py'), '--data-dir', str(home), '--help'],
                                    capture_output=True, text=True, encoding='utf-8', timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('--data-dir', result.stdout)
            self.assertFalse(home.exists())

    def test_opened_document_path_does_not_abort_the_entry(self):
        # Finder hands the double-clicked file to the launcher as a positional argument.
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder) / 'unused'
            env = dict(os.environ, QT_QPA_PLATFORM='cocoa' if sys.platform == 'darwin' else 'offscreen')
            result = subprocess.run([sys.executable, str(ROOT / 'bin/ssh-tunnel-manager.py'),
                                     '--data-dir', str(home), '--startup-splash',
                                     str(ROOT / 'ssh_tunnel_manager.pyw')],
                                    capture_output=True, text=True, encoding='utf-8',
                                    input='', env=env, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn('unrecognized arguments', result.stderr)
            self.assertFalse(home.exists())

    def test_all_entries_accept_data_directory_and_close_splash_cleanly(self):
        for entry in ('bin/ssh-tunnel-manager.py', 'bin/ssh-tunnel-manager.pyw', 'ssh_tunnel_manager.pyw'):
            with self.subTest(entry=entry), tempfile.TemporaryDirectory() as folder:
                home = Path(folder) / '中文 data'
                env = dict(os.environ, QT_QPA_PLATFORM='cocoa' if sys.platform == 'darwin' else 'offscreen')
                child = subprocess.Popen([sys.executable, str(ROOT / entry), '--data-dir', str(home), '--startup-splash'],
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
                try:
                    stdout, stderr = child.communicate(b'', timeout=20)
                    self.assertEqual(child.returncode, 0, (stdout, stderr))
                    # The splash must never initialize/read runtime configuration.
                    self.assertFalse(home.exists())
                finally:
                    if child.poll() is None:
                        child.kill()
                        child.communicate()
