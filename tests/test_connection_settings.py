import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault('QT_QPA_PLATFORM', 'cocoa' if sys.platform == 'darwin' else 'offscreen')
import unittest
from unittest.mock import patch
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from connection_settings import ConnectionSettingsEditor


class ConnectionSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.identifier = '18e304c4-fdd4-4d39-8515-52f92fca3ef7'
        self.source = [dict(id=self.identifier, name='dev', host='server', port=22)]
        self.editor = ConnectionSettingsEditor()
        self.editor.load(self.source, ['dev'], 'unused-test-config')
        self.addCleanup(self.editor.close)

    def test_draft_cancel_and_auto_apply_preserve_identity(self):
        self.editor.sources.setCurrentRow(1)
        self.editor.fields['host'].setText('changed')
        self.assertEqual(self.editor.records()[0]['host'], 'changed')
        self.assertEqual(self.editor.records()[0]['id'], self.identifier)
        self.assertEqual(self.source[0]['host'], 'server')
        self.editor.load(self.source, ['dev'], 'unused-test-config')
        self.assertEqual(self.editor.records()[0]['host'], 'server')

    def test_only_manual_sources_have_checkboxes(self):
        config, manual = self.editor.sources.item(0), self.editor.sources.item(1)
        self.assertFalse(config.flags() & Qt.ItemFlag.ItemIsUserCheckable)
        self.assertTrue(manual.flags() & Qt.ItemFlag.ItemIsUserCheckable)
        self.assertNotEqual(config.text(), manual.text())
        self.editor.sources.setCurrentRow(0)
        self.assertFalse(self.editor.fields['host'].isEnabled())
        self.assertFalse(self.editor.delete_button.isEnabled())

    def test_referenced_manual_cannot_be_deleted(self):
        self.editor.load(self.source, [], 'unused', {'manual:' + self.identifier})
        self.editor.sources.setCurrentRow(0)
        self.editor.delete_selected()
        self.assertEqual(len(self.editor.records()), 1)
        self.assertIn('引用', self.editor.error.text())

    def test_export_requires_separate_write_click(self):
        self.editor.sources.item(1).setCheckState(Qt.CheckState.Checked)
        preview = dict(path='unused', text='Host dev\n', original=b'', existed=False)
        with patch('connection_settings.backend.preview_export', return_value=preview), patch('connection_settings.backend.export_connections') as write, patch('connection_settings.backend.discover_hosts', return_value=['dev']):
            dialog = self.editor.open_export()
            self.assertIsNotNone(dialog)
            write.assert_not_called()
            dialog.reject()
            write.assert_not_called()
            dialog = self.editor.open_export()
            dialog.write_button.click()
            write.assert_called_once_with(preview)

    def test_invalid_dirty_form_blocks_outer_save(self):
        self.editor.sources.setCurrentRow(1)
        self.editor.fields['host'].setText('')
        with self.assertRaises(ValueError):
            self.editor.records()

    def test_new_form_is_included_in_save_without_apply(self):
        self.editor.new_button.click()
        self.editor.fields['name'].setText('new')
        self.editor.fields['host'].setText('new.example')
        records = self.editor.records()
        self.assertEqual(len(records), 2)
        self.assertEqual(records[1]['host'], 'new.example')
        self.assertEqual(records[1]['id'], self.editor.records()[1]['id'])

    def test_selection_preserves_dirty_edit_and_rejects_invalid_edit(self):
        self.editor.sources.setCurrentRow(1)
        self.editor.fields['host'].setText('other.example')
        self.editor.sources.setCurrentRow(0)
        self.assertEqual(self.editor.records()[0]['host'], 'other.example')
        self.editor.sources.setCurrentRow(1)
        self.editor.fields['host'].clear()
        self.editor.sources.setCurrentRow(0)
        self.assertEqual(self.editor.sources.currentRow(), 1)
        self.assertTrue(self.editor.error.text())

    def test_failed_export_keeps_dialog_open_and_does_not_emit(self):
        self.editor.sources.item(1).setCheckState(Qt.CheckState.Checked)
        emissions = []
        self.editor.exported.connect(lambda: emissions.append(True))
        preview = dict(path='unused', text='Host dev\n', original=b'', existed=False)
        with patch('connection_settings.backend.preview_export', return_value=preview), patch('connection_settings.backend.export_connections', side_effect=ValueError('changed externally')):
            dialog = self.editor.open_export()
            dialog.write_button.click()
            self.assertTrue(dialog.isVisible())
            self.assertIn('changed externally', dialog.error.text())
            self.assertEqual(emissions, [])
            dialog.reject()

    def test_editor_fits_sidebar_width(self):
        self.assertLessEqual(self.editor.minimumSizeHint().width(), 370)

    def test_reference_mapping_shows_tunnel_names(self):
        self.editor.load(self.source, [], 'unused', {'manual:' + self.identifier: ['开发数据库', '服务面板']})
        self.editor.sources.setCurrentRow(0)
        self.editor.delete_selected()
        self.assertIn('开发数据库', self.editor.error.text())
        self.assertIn('服务面板', self.editor.error.text())
        self.assertEqual(len(self.editor.records()), 1)

    def test_refresh_uses_current_path_and_shows_source_path(self):
        path = 'C:/test/deep/config'
        self.editor.load(self.source, ['dev'], path)
        self.assertIn('入口文件', self.editor.config_path_label.text())
        self.assertIn(path, self.editor.config_path_label.toolTip())
        with patch('connection_settings.backend.discover_hosts', return_value=['refreshed']) as discover:
            self.editor.refresh_button.click()
            discover.assert_called_once_with(path)
        self.assertIn('refreshed', self.editor.sources.item(0).text())

    def test_export_success_reports_backup_path(self):
        self.editor.sources.item(1).setCheckState(Qt.CheckState.Checked)
        preview = dict(path='unused', text='Host dev\n', original=b'', existed=False)
        backup = 'C:/test/config.backup-123'
        with patch('connection_settings.backend.preview_export', return_value=preview), patch('connection_settings.backend.export_connections', return_value=backup), patch('connection_settings.backend.discover_hosts', return_value=['dev']):
            self.editor.open_export().write_button.click()
        self.assertIn(backup, self.editor.status.text())


if __name__ == '__main__':
    unittest.main()
