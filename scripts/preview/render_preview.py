"""Render a local, isolated UI preview without opening any SSH connections."""
from pathlib import Path
import tempfile
from unittest.mock import patch

from support import tm, ROOT
from PySide6.QtCore import QEvent
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest


def main():
    app = tm.QApplication.instance() or tm.QApplication([])
    # The offscreen platform needs fonts registered explicitly on Windows.
    for name in ("segoeui.ttf", "msyh.ttc", "msyhbd.ttc", "consola.ttf"):
        QFontDatabase.addApplicationFont(str(Path("C:/Windows/Fonts") / name))
    output = ROOT / "artifacts" / "ui" / f"scale-{app.devicePixelRatio():g}"
    output.mkdir(parents=True, exist_ok=True)
    splash = tm.StartupSplash()
    splash.show()
    app.processEvents()
    splash.grab().save(str(output / "startup-splash.png"))
    splash.close()
    splash.deleteLater()
    with tempfile.TemporaryDirectory() as directory:
        config = Path(directory) / "tunnels.json"
        config.write_bytes((ROOT / "tests" / "fixtures" / "preview_tunnels.json").read_bytes())
        with patch.object(tm, "CONFIG_FILE", str(config)), \
             patch.object(tm, "SETTINGS_FILE", str(Path(directory) / "settings.json")), \
             patch.object(tm, "LOG_FILE", str(Path(directory) / "preview.log")), \
             patch.object(tm, "is_autostart_enabled", return_value=False), \
             patch.object(tm.QTimer, "singleShot"), \
             patch.object(tm.TunnelItem, "start_process", side_effect=AssertionError("Preview cannot start SSH")):
            window = tm.MainWindow()
            window.supervisor_timer.stop()
            try:
                window.show()
                app.processEvents()
                window.grab().save(str(output / "window.png"))
                rows = window.findChildren(tm.TunnelRowWidget)
                if rows:
                    button = rows[0].status_btn
                    button.setFocus()
                    QTest.mouseMove(button, button.rect().center())
                    app.processEvents()
                    window.grab().save(str(output / "status-focus.png"))
                    button.clearFocus()
                    test_button = rows[0].test_btn
                    test_button.setFocus()
                    QTest.mouseMove(test_button, test_button.rect().center())
                    app.processEvents()
                    window.grab().save(str(output / "connectivity-focus.png"))
                    test_button.clearFocus()
                    rows[0].tunnel.activity, rows[0].tunnel.phase = "probe", "probing"
                    rows[0]._update_status_display()
                    app.processEvents()
                    window.grab().save(str(output / "connectivity-running.png"))
                    rows[0].tunnel.activity = rows[0].tunnel.phase = None
                    rows[0]._update_status_display()
                window.titleBar.grab().save(str(output / "title-bar.png"))
                window.btn_config.grab().save(str(output / "settings-button.png"))
                log_toolbar = window.lbl_log_toggle.parentWidget()
                window.log_card.grab(log_toolbar.geometry()).save(str(output / "log-toolbar.png"))
                window.select_tunnel(0)
                app.processEvents()
                window.grab().save(str(output / "tunnel-details.png"))
                window.btn_new.click()
                app.processEvents()
                window.grab().save(str(output / "new-tunnel-workspace.png"))
                window.btn_config.click()
                app.processEvents()
                window.grab().save(str(output / "settings-workspace.png"))
                window.tray_menu.popup(tm.QPoint(200, 200))
                app.processEvents()
                window.tray_menu.grab().save(str(output / "tray-menu.png"))
                window.tray_menu.hide()
                print(f"Preview: {window.width()} x {window.height()}, max button hidden: {window.titleBar.maxBtn.isHidden()}")
                print(output)
            finally:
                window.tray.hide()
                window.hide()
                window.deleteLater()
                app.sendPostedEvents(None, QEvent.DeferredDelete)


if __name__ == "__main__":
    main()
