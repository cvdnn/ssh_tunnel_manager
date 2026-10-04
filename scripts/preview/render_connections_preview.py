"""Render connection settings with temporary fixtures and no SSH execution."""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from support import tm, ROOT, register_preview_fonts
from PySide6.QtCore import Qt, QEvent


def main():
    app = tm.QApplication.instance() or tm.QApplication([])
    register_preview_fonts()
    output = ROOT / "artifacts" / "ui" / "connections"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        config = root / "config"
        config.write_text("# Existing SSH settings\nHost development\n    HostName dev.example.com\n", encoding="utf-8")
        (root / "tunnels.json").write_text("[]", encoding="utf-8")
        (root / "settings.json").write_text(json.dumps({"sshConfigPath": str(config)}), encoding="utf-8")
        records = [{"id": "df82cecb-8ad1-418d-a741-8dcb53dd5ac9", "name": "测试服务器",
                    "host": "server.example.com", "port": 2222, "user": "developer", "exportAlias": "test-server"}]
        (root / "ssh_connections.json").write_text(json.dumps(records), encoding="utf-8")
        with patch.object(tm, "CONFIG_FILE", str(root / "tunnels.json")), \
                patch.object(tm, "SETTINGS_FILE", str(root / "settings.json")), \
                patch.object(tm, "LOG_FILE", str(root / "test.log")), \
                patch.object(tm, "is_autostart_enabled", return_value=False), \
                patch.object(tm.QTimer, "singleShot"), \
                patch.object(tm.subprocess, "Popen", side_effect=AssertionError("No subprocess during preview")):
            window = tm.MainWindow()
            window.supervisor_timer.stop()
            try:
                window.show()
                window.open_system_settings_workspace()
                app.processEvents()
                window.grab().save(str(output / "general.png"))
                workspace = window.settings_workspace
                workspace.tabs.setCurrentIndex(1)
                editor = workspace.connections_editor
                for index in range(editor.sources.count()):
                    item = editor.sources.item(index)
                    if str(item.data(Qt.UserRole)).startswith("manual:"):
                        item.setCheckState(Qt.Checked)
                        editor.sources.setCurrentItem(item)
                        break
                app.processEvents()
                window.grab().save(str(output / "connections.png"))
                preview = editor.open_export()
                app.processEvents()
                preview.grab().save(str(output / "export-preview.png"))
                preview.reject()
                assert "test-server" not in config.read_text(encoding="utf-8")
                print(output)
            finally:
                window.tray.hide()
                window.hide()
                window.deleteLater()
                app.sendPostedEvents(None, QEvent.DeferredDelete)


if __name__ == "__main__":
    main()
