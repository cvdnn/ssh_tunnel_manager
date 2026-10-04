"""Connection management contracts; no user config or network access."""
import tempfile
import json
from contextlib import contextmanager, ExitStack
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from support import tm, tunnel, controller


class ConnectionIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = tm.QApplication.instance() or tm.QApplication([])

    @contextmanager
    def window(self, settings=None, records=None):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            (root / "tunnels.json").write_text("[]", encoding="utf-8")
            (root / "settings.json").write_text(json.dumps(settings or {}), encoding="utf-8")
            (root / "ssh_connections.json").write_text(json.dumps(records or []), encoding="utf-8")
            for name, filename in (("CONFIG_FILE", "tunnels.json"), ("SETTINGS_FILE", "settings.json"), ("LOG_FILE", "test.log")):
                stack.enter_context(patch.object(tm, name, str(root / filename)))
            stack.enter_context(patch.object(tm, "get_known_ssh_hosts", return_value=["dev"]))
            stack.enter_context(patch.object(tm, "is_autostart_enabled", return_value=False))
            stack.enter_context(patch.object(tm, "set_autostart", return_value=True))
            stack.enter_context(patch.object(tm.QTimer, "singleShot"))
            window = tm.MainWindow()
            window.supervisor_timer.stop()
            try:
                yield window, root
            finally:
                window.supervisor_timer.stop()
                window.tray.hide()
                window.hide()
                window.deleteLater()

    def test_settings_manual_create_save_cancel_and_rename_preserves_reference(self):
        with self.window() as (window, root):
            window.show()
            window.open_system_settings_workspace()
            editor = window.settings_workspace.connections_editor
            editor.new_record()
            editor.fields["name"].setText("开发")
            editor.fields["host"].setText("server.example")
            window.settings_workspace._save_settings()
            stored = json.loads((root / "ssh_connections.json").read_text(encoding="utf-8"))
            self.assertEqual(stored[0]["host"], "server.example")
            reference = "manual:" + stored[0]["id"]
            self.assertIn(reference, [row["id"] for row in window.connection_options()])
            window.open_system_settings_workspace()
            editor.new_record()
            editor.fields["name"].setText("不保存")
            editor.fields["host"].setText("discard.example")
            window.settings_workspace.btn_cancel.click()
            window.open_system_settings_workspace()
            self.assertEqual(len(editor.records()), 1)
            self.assertEqual(editor.records()[0]["id"], stored[0]["id"])

    def test_settings_failure_keeps_draft_and_does_not_report_success(self):
        with self.window() as (window, root):
            window.show()
            window.open_system_settings_workspace()
            old = dict(window.settings)
            workspace = window.settings_workspace
            workspace.spin_int.setValue(12)
            with patch.object(tm, "atomic_write_json", side_effect=OSError("disk full")):
                workspace._save_settings()
            self.assertIn("保存失败", workspace.error_label.text())
            self.assertEqual(window.settings, old)
            self.assertTrue(workspace.isVisible())

    def test_missing_manual_reference_never_falls_back_to_host(self):
        item = tunnel(connectionId="manual:missing")
        snapshot = tm.resolve_connection(item, {}, [])
        self.assertIn("error", snapshot)
        item.connection_runtime = snapshot
        with patch.object(tm.subprocess, "Popen") as popen:
            item.start_process(Mock(), "ssh.exe")
        popen.assert_not_called()

    def test_manual_start_resumes_health_checks_after_no_auto_start(self):
        item = tunnel()
        window = controller(item)
        window.is_paused = True
        window._queue_tunnel(item, "start")
        self.assertFalse(window.is_paused)

    def test_worker_snapshot_does_not_mutate_after_settings_change(self):
        item = tunnel()
        item.connection_runtime = {"host": "before.example", "args": ["-p", "2222"]}
        jobs = tm.TunnelWorkerPool()
        try:
            with patch.object(jobs, "_dispatch"):
                jobs.request(item, "start")
            item.connection_runtime["args"][1] = "3333"
            self.assertEqual(jobs._entries[item]["pending"][1]["_connectionRuntime"]["args"], ["-p", "2222"])
        finally:
            jobs._timer.stop()
            jobs._executor.shutdown(wait=False)
            jobs.deleteLater()

    def test_legacy_alias_edit_does_not_switch_to_same_named_config(self):
        with self.window(settings={"sshAliases": {"dev": "production"}}) as (window, _):
            item = tunnel(sshHost="dev")
            workspace = window.tunnel_workspace
            workspace.set_ssh_options(window.connection_options())
            workspace.load_tunnel(0, item)
            data = workspace.get_data()
            self.assertEqual(data["connectionId"], "")
            self.assertEqual(data["sshHost"], "dev")

    def test_config_reference_rechecked_at_actual_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config"
            config.write_text("Host dev\n  HostName server.example\n", encoding="utf-8")
            item = tunnel(sshHost="dev", connectionId="config:dev")
            item.connection_runtime = tm.resolve_connection(item, {"sshConfigPath": str(config)}, [])
            config.write_text("", encoding="utf-8")
            with patch.object(tm.subprocess, "Popen") as popen, patch.object(tm.threading.Thread, "start"):
                item.start_process(Mock(), "ssh.exe")
            popen.assert_not_called()
            self.assertIn("缺失", item.last_error)

    def test_restored_config_reference_cannot_fall_back_to_legacy_alias(self):
        item = tunnel(sshHost="dev", connectionId="config:dev")
        with patch.object(tm, "get_known_ssh_hosts", return_value=[]):
            item.connection_runtime = tm.resolve_connection(item, {}, [])
        proc = Mock(pid=100, stderr=None)
        with patch.object(tm, "discover_hosts", return_value=["dev"]), \
                patch.object(tm.subprocess, "Popen", return_value=proc) as popen, \
                patch.object(tm.threading.Thread, "start"):
            item.start_process(Mock(), "ssh.exe", {"dev": "production"})
        self.assertEqual(popen.call_args.args[0][-1], "dev")

    def test_starting_one_held_tunnel_does_not_start_others(self):
        first, second = tunnel(), tunnel(name="second", localPort=15432)
        window = controller(first, second)
        window.is_paused = True
        window._startup_held = {first, second}
        window._queue_tunnel(first, "start")
        window.tunnel_jobs.reset_mock()
        window.check_tunnels_health()
        self.assertEqual([call.args[0] for call in window.tunnel_jobs.request.call_args_list], [first])

    def test_bad_reference_type_is_reported_during_load(self):
        with self.window() as (window, root):
            (root / "tunnels.json").write_text(json.dumps([tunnel(connectionId=123).to_dict()]), encoding="utf-8")
            window.load_config()
            self.assertTrue(window._config_load_error)
            self.assertEqual(window.tunnels, [])

    def test_failed_new_tunnel_save_keeps_form_and_does_not_start(self):
        with self.window() as (window, _):
            window.show()
            window.toggle_new_tunnel_workspace()
            with patch.object(tm, "atomic_write_json", side_effect=OSError("disk full")), \
                    patch.object(window, "_queue_tunnel") as queue:
                window._create_tunnel_from_workspace(tunnel().to_dict())
            queue.assert_not_called()
            self.assertEqual(window.tunnels, [])
            self.assertTrue(window.tunnel_workspace.isVisible())
            self.assertIn("保存", window.tunnel_workspace.error_label.text())

    def test_reference_survives_serialization(self):
        item = tunnel(connectionId="manual:abc")
        self.assertEqual(item.to_dict().get("connectionId"), "manual:abc")

    def test_missing_config_starts_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            window = Mock(tunnels=[])
            with patch.object(tm, "CONFIG_FILE", str(Path(directory) / "missing.json")):
                tm.MainWindow.load_config(window)
            self.assertEqual(window.tunnels, [])

    def test_manual_connection_resolves_into_worker_snapshot(self):
        item = tunnel(connectionId="manual:abc")
        window = controller(item)
        identifier = "2e4b047b-e36b-4b3a-9c1a-fb2fb9978ff7"
        item.connection_id = "manual:" + identifier
        window.manual_connections = [dict(id=identifier, name="开发", host="server.example", port=2222, user="alice")]
        window.settings["sshConfigPath"] = "C:/test/config"
        window._queue_tunnel(item, "start")
        self.assertEqual(item.connection_runtime["host"], "server.example")
        self.assertIn("2222", item.connection_runtime["args"])

    def test_dropdown_preserves_distinct_sources_with_same_label(self):
        workspace = tm.TunnelWorkspace(ssh_options=[])
        try:
            workspace.set_ssh_options([
                {"id": "config:dev", "name": "dev", "host": "dev", "source": "SSH 配置"},
                {"id": "manual:abc", "name": "dev", "host": "server", "source": "手动"},
            ])
            workspace.combo_ssh.setCurrentIndex(1)
            self.assertEqual(workspace.get_data()["connectionId"], "manual:abc")
            self.assertEqual(workspace.get_data()["sshHost"], "server")
        finally:
            workspace.deleteLater()

    def test_ssh_security_and_keepalive_inherit_config(self):
        item = tunnel()
        proc = Mock(pid=10, stderr=None)
        proc.poll.return_value = None
        with patch.object(tm.subprocess, "Popen", return_value=proc) as popen, \
                patch.object(tm.threading.Thread, "start"):
            item.start_process(Mock(), "ssh.exe")
        args = popen.call_args.args[0]
        self.assertNotIn("StrictHostKeyChecking=no", args)
        self.assertNotIn("ConnectTimeout=15", args)
        self.assertNotIn("ServerAliveInterval=15", args)


if __name__ == "__main__":
    unittest.main()
