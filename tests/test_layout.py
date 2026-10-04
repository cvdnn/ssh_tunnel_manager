"""Repository layout and local-data migration contracts."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LayoutTests(unittest.TestCase):
    def test_runtime_paths_do_not_depend_on_current_directory(self):
        paths = load_module("layout_paths", ROOT / "src" / "paths.py")
        self.assertEqual(paths.ROOT, ROOT)
        self.assertEqual(paths.CONFIG_FILE, ROOT / "data" / "tunnels.json")
        self.assertEqual(paths.SETTINGS_FILE, ROOT / "data" / "settings.json")
        self.assertEqual(paths.LOG_FILE, ROOT / "logs" / "ssh_tunnel.log")
        self.assertEqual(paths.LOGO_FILE, ROOT / "src" / "assets" / "app_logo.png")

    def test_legacy_data_requires_explicit_migration(self):
        paths = load_module("layout_paths", ROOT / "src" / "paths.py")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "settings.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "migrate-layout.ps1"):
                paths.require_migrated(root)

    def test_migration_copies_bytes_and_removes_legacy_source(self):
        migrate = load_module("layout_migrate", ROOT / "scripts" / "migrate_layout.py")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "settings.json"
            payload = b'{"window": 1}\r\n'
            source.write_bytes(payload)
            result = migrate.migrate(root, apply=True)
            self.assertEqual((root / "data" / "settings.json").read_bytes(), payload)
            self.assertEqual((root / ".review-backup" / "layout" / "settings.json").read_bytes(), payload)
            self.assertFalse(source.exists())
            self.assertIn("settings.json", result)
            self.assertEqual(migrate.migrate(root, apply=True), [])

    def test_migration_refuses_conflict_without_partial_move(self):
        migrate = load_module("layout_migrate", ROOT / "scripts" / "migrate_layout.py")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "settings.json").write_text("{}", encoding="utf-8")
            (root / "tunnels.json").write_text("[]", encoding="utf-8")
            (root / "data").mkdir()
            (root / "data" / "tunnels.json").write_text("[1]", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "tunnels.json"):
                migrate.migrate(root, apply=True)
            self.assertTrue((root / "settings.json").exists())
            self.assertFalse((root / "data" / "settings.json").exists())

    def test_migration_refuses_invalid_json_and_preserves_source(self):
        migrate = load_module("layout_migrate", ROOT / "scripts" / "migrate_layout.py")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "settings.json"
            source.write_text("{broken", encoding="utf-8")
            with self.assertRaises(json.JSONDecodeError):
                migrate.migrate(root, apply=True)
            self.assertTrue(source.exists())
            self.assertFalse((root / "data" / "settings.json").exists())


if __name__ == "__main__":
    unittest.main()
