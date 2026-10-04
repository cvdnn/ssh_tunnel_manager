import codecs
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import ssh_connections as sc


class ConnectionsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        home = Path(self.temp.name)
        self.root = home / '.ssh'
        self.root.mkdir()
        home_patch = patch.object(Path, 'home', return_value=home)
        home_patch.start()
        self.addCleanup(home_patch.stop)

    def record(self, **fields):
        return dict(name='测试连接', host='example.com', **fields)

    def test_discovery_includes_aliases_cycles_and_missing(self):
        root = self.root / 'config'
        root.write_text('hOsT first second *.test !omit\nInclude parts/*\nHost first\n', encoding='utf-8')
        (self.root / 'parts').mkdir()
        (self.root / 'parts' / 'one').write_text('Host third\nInclude config\n', encoding='utf-8')
        self.assertEqual(sc.discover_hosts(root), ['first', 'second', 'third'])
        self.assertEqual(sc.discover_hosts(self.root / 'missing'), [])

    def test_default_include_is_relative_to_ssh_directory(self):
        ssh = self.root / '.ssh'
        (ssh / 'parts').mkdir(parents=True)
        (ssh / 'config').write_text('Include parts/one\n')
        (ssh / 'parts' / 'one').write_text('Include two\n')
        (ssh / 'two').write_text('Host nested\n')
        with patch.object(Path, 'home', return_value=self.root):
            self.assertEqual(sc.discover_hosts(), ['nested'])

    def test_malformed_config_raises(self):
        path = self.root / 'config'
        path.write_text('Host "unclosed\n')
        with self.assertRaises(ValueError):
            sc.discover_hosts(path)

    def test_validation_and_args(self):
        value = sc.validate_connection(self.record(port='2222', user='alice', identityFile='C:/my keys/id', proxyJump='jump', connectTimeout='9', serverAliveInterval=0, serverAliveCountMax=3))
        self.assertEqual(value['port'], 2222)
        self.assertEqual(sc.validate_connection(value)['id'], value['id'])
        self.assertEqual(sc.connection_args(value), ['-p', '2222', '-l', 'alice', '-i', 'C:/my keys/id', '-J', 'jump', '-o', 'ConnectTimeout=9', '-o', 'ServerAliveInterval=0', '-o', 'ServerAliveCountMax=3'])
        for fields in ({'host': '-oProxyCommand=evil'}, {'host': 'a\nb'}, {'host': 'a;cmd'}, {'port': 65536}, {'port': True}, {'user': 'a b'}, {'proxyJump': 'jump;cmd'}, {'identityFile': 'a\nb'}, {'connectTimeout': -1}):
            bad = self.record()
            bad.update(fields)
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                sc.validate_connection(bad)

    def test_json_roundtrip_duplicates_and_corruption(self):
        path = self.root / 'connections.json'
        self.assertEqual(sc.load_connections(path), [])
        value = sc.validate_connection(self.record())
        sc.save_connections(path, [value])
        self.assertEqual(sc.load_connections(path), [value])
        with self.assertRaises(ValueError):
            sc.save_connections(path, [value, value])
        self.assertEqual(sc.load_connections(path), [value])
        path.write_text('{bad')
        with self.assertRaises(ValueError):
            sc.load_connections(path)

    def test_export_preserves_bytes_scope_backup_and_mode(self):
        path = self.root / 'config'
        original = codecs.BOM_UTF8 + b'# existing\r\nHost old\r\n  HostName old.example\r\nMatch all\r\n'
        path.write_bytes(original)
        mode = path.stat().st_mode
        preview = sc.preview_export(path, [self.record(exportAlias='new', identityFile='C:/my keys/id')])
        self.assertEqual(path.read_bytes(), original)
        self.assertIn('Host new\r\n', preview['text'])
        backup = sc.export_connections(preview)
        self.assertEqual(Path(backup).read_bytes(), original)
        result = path.read_bytes()
        self.assertTrue(result.startswith(codecs.BOM_UTF8 + b'Host new\r\n'))
        self.assertTrue(result.endswith(original[len(codecs.BOM_UTF8):]))
        self.assertEqual(path.stat().st_mode, mode)

    def test_export_conflicts_include_and_external_edit(self):
        path = self.root / 'config'
        include = self.root / 'other'
        path.write_text('Include other\nHost *\n')
        include.write_text('Host old\n')
        with self.assertRaises(ValueError):
            sc.preview_export(path, [self.record(exportAlias='old')])
        preview = sc.preview_export(path, [self.record(exportAlias='new')])
        include.write_text('Host new\n')
        with self.assertRaises(ValueError):
            sc.export_connections(preview)
        include.write_text('Host old\n')
        path.write_text('Host externally-edited\n')
        with self.assertRaises(ValueError):
            sc.export_connections(preview)

    def test_export_new_and_invalid_alias(self):
        path = self.root / 'newconfig'
        with self.assertRaises(ValueError):
            sc.preview_export(path, [self.record()])
        preview = sc.preview_export(path, [self.record(exportAlias='safe')])
        self.assertIsNone(sc.export_connections(preview))
        self.assertEqual(sc.discover_hosts(path), ['safe'])

    def test_atomic_write_failure_preserves_original(self):
        path = self.root / 'data.json'
        path.write_text('original')
        with patch.object(sc.os, 'replace', side_effect=OSError('denied')):
            with self.assertRaises(OSError):
                sc.atomic_write_json(path, {'new': True})
        self.assertEqual(path.read_text(), 'original')

    def test_export_restores_original_global_scope(self):
        path = self.root / 'config'
        path.write_bytes(b'User shared-user\nIdentityFile ~/.ssh/shared\nHost old\n')
        preview = sc.preview_export(path, [self.record(exportAlias='new')])
        self.assertIn('Host *\nUser shared-user\nIdentityFile ~/.ssh/shared\n', preview['text'])

    def test_discovery_equals_quoted_paths_and_depth(self):
        path = self.root / 'config'
        path.write_text('Host=one two\nInclude="with spaces"\n')
        (self.root / 'with spaces').write_text('Host three # comment\n')
        self.assertEqual(sc.discover_hosts(path), ['one', 'two', 'three'])
        for i in range(35):
            (self.root / str(i)).write_text(f'Include {i + 1}\n')
        with self.assertRaises(ValueError):
            sc.discover_hosts(self.root / '0')

    def test_symlink_export_is_refused(self):
        with patch.object(Path, 'is_symlink', return_value=True):
            with self.assertRaises(ValueError):
                sc.preview_export(self.root / 'config', [self.record(exportAlias='new')])

    def test_read_failure_is_not_empty_success(self):
        with patch.object(Path, 'read_bytes', side_effect=PermissionError('denied')):
            with self.assertRaises(PermissionError):
                sc.discover_hosts(self.root / 'config')

    def test_custom_config_relative_include_uses_user_ssh_directory(self):
        custom_dir = self.root.parent / 'custom'
        custom_dir.mkdir()
        config = custom_dir / 'config'
        config.write_text('Include servers\n')
        (custom_dir / 'servers').write_text('Host wrong-sibling\n')
        (self.root / 'servers').write_text('Host real-user-ssh\n')
        self.assertEqual(sc.discover_hosts(config), ['real-user-ssh'])
        with self.assertRaises(ValueError):
            sc.preview_export(config, [self.record(exportAlias='real-user-ssh')])

    def test_include_static_tokens_and_environment(self):
        config = self.root / 'config'
        (self.root / 'part').write_text('Host from-home-token\n')
        (self.root / 'percent%').write_text('Host from-percent\n')
        (self.root / 'env-part').write_text('Host from-environment\n')
        config.write_text('Include %d/.ssh/part percent%% ${SSH_TEST_INCLUDE}\n')
        with patch.dict(os.environ, {'SSH_TEST_INCLUDE': 'env-part'}):
            self.assertEqual(sc.discover_hosts(config), ['from-home-token', 'from-percent', 'from-environment'])

    def test_unresolved_include_tokens_refuse_discovery_and_export(self):
        config = self.root / 'config'
        with patch.dict(os.environ, {}, clear=True):
            for token in ('${SSH_UNDEFINED}', '%h.conf', 'trailing%', '${UNCLOSED'):
                config.write_text(f'Include {token}\n')
                with self.subTest(token=token), self.assertRaises(ValueError):
                    sc.discover_hosts(config)
                with self.subTest(export_token=token), self.assertRaises(ValueError):
                    sc.preview_export(config, [self.record(exportAlias='safe')])


if __name__ == '__main__':
    unittest.main()
