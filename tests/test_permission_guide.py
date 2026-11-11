"""Optional permission diagnostics and external Chrome integration guidance."""
import copy
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import desktop
import permission_guide as guide


def backend_status(**overrides):
    value = {"ok": True, "supported": True, "scope": "current_process",
             "pid": 4321, "executable": "/Applications/Runtime/python3",
             "screen_capture": True, "accessibility": True, "post_event": True,
             "api_errors": []}
    value.update(overrides)
    return value


class PermissionGuideTests(unittest.TestCase):
    def setUp(self):
        self.document = {"workspaces": [], "state_dir": "/tmp/sugar-guide-test-state"}

    def test_state_dir_uses_document_and_core_default(self):
        self.assertEqual(guide.resolve_state_dir(self.document), Path(self.document["state_dir"]).resolve())
        self.assertEqual(guide.resolve_state_dir({}), (Path.home() / ".local/state/sugar-local-mcp").resolve())
        expected = Path("~/sugar-guide-test-state").expanduser().resolve()
        self.assertEqual(guide.resolve_state_dir({"state_dir": "~/sugar-guide-test-state"}), expected)
        for invalid in ("relative/path", "", 17, "a\x00b"):
            with self.subTest(invalid=invalid), self.assertRaises(guide.PermissionGuideError):
                guide.resolve_state_dir({"state_dir": invalid})

    def test_status_queries_actual_backend_and_preserves_its_identity(self):
        with patch.object(guide, "_request_local", return_value=backend_status()) as request:
            status = guide.permission_status(self.document)
        request.assert_called_once_with(Path(self.document["state_dir"]).resolve(), "permissions_status", payload=None, timeout=3)
        self.assertTrue(status["available"])
        self.assertEqual(status["pid"], 4321)
        self.assertEqual(status["executable"], "/Applications/Runtime/python3")
        self.assertTrue(status["screen_capture"])

    def test_unreachable_backend_is_unknown_not_denied(self):
        with patch.object(guide, "_request_local", side_effect=OSError("private exception text")):
            status = guide.permission_status(self.document)
        self.assertFalse(status["available"])
        self.assertTrue(all(status[kind] is None for kind in guide.PERMISSION_KINDS))
        text = guide.format_permission_status(status)
        self.assertIn("未能检查", text)
        self.assertNotIn("尚未授权", text)
        self.assertNotIn("private exception text", text)

    def test_status_requires_the_backend_process_identity(self):
        for change in ({"scope": "ui_process"}, {"pid": None}, {"pid": True},
                       {"executable": ""}, {"supported": "true"}):
            with self.subTest(change=change), patch.object(guide, "_request_local", return_value=backend_status(**change)):
                status = guide.permission_status(self.document)
            self.assertFalse(status["available"])
            self.assertIsNone(status["screen_capture"])

    def test_false_unknown_and_boolean_like_values_are_distinct(self):
        with patch.object(guide, "_request_local", return_value=backend_status(screen_capture=False, accessibility=None, post_event=1)):
            status = guide.permission_status(self.document)
        self.assertFalse(status["screen_capture"])
        self.assertIsNone(status["accessibility"])
        self.assertIsNone(status["post_event"])
        text = guide.format_permission_status(status)
        self.assertIn("屏幕录制：尚未授权", text)
        self.assertIn("辅助功能：未能确定", text)

    def test_unsupported_backend_is_not_reported_as_permission_denial(self):
        with patch.object(guide, "_request_local", return_value=backend_status(supported=False, screen_capture=None, accessibility=None, post_event=None)):
            status = guide.permission_status(self.document)
        self.assertFalse(status["supported"])
        self.assertIn("不支持", guide.format_permission_status(status))

    def test_request_permission_uses_only_explicit_supported_kind(self):
        with patch.object(guide, "_request_local", return_value=backend_status(screen_capture=False, request_sent=True)) as request:
            reply = guide.request_permission(self.document, "screen_capture")
            self.assertFalse(reply["screen_capture"])
            request.assert_called_once_with(Path(self.document["state_dir"]).resolve(), "permissions_request", payload={"kind": "screen_capture"}, timeout=30)
            with self.assertRaises(guide.PermissionGuideError):
                guide.request_permission(self.document, "all")
            self.assertEqual(request.call_count, 1)

    def test_settings_open_uses_fixed_urls_without_shell_or_tcc_changes(self):
        with patch.object(guide.subprocess, "run", return_value=Mock(returncode=0)) as execute:
            guide.open_privacy_settings("screen_capture")
            self.assertEqual(execute.call_args.args[0], ["/usr/bin/open", guide.PRIVACY_URLS["screen_capture"]])
            self.assertNotIn("shell", execute.call_args.kwargs)
            with self.assertRaises(guide.PermissionGuideError):
                guide.open_privacy_settings("https://example.test")
            self.assertEqual(execute.call_count, 1)

class PermissionGuideFlowTests(unittest.TestCase):
    def setUp(self):
        self.document = {"workspaces": [{"id": "project", "root": "/tmp/project", "allow_exec": True}],
                         "state_dir": "/tmp/sugar-guide-test-state"}
        self.addCleanup(patch.stopall)
        patch.object(desktop, "load_workspace_document", return_value=(self.document, "test-fingerprint")).start()

    @staticmethod
    def normalized(**changes):
        return {**backend_status(**changes), "available": True, "error": None}

    def test_startup_does_not_check_or_request_optional_permissions(self):
        for allow_exec, upgrade_pending in ((False, False), (True, False), (True, True)):
            self.document["workspaces"][0]["allow_exec"] = allow_exec
            with self.subTest(allow_exec=allow_exec, upgrade_pending=upgrade_pending), \
                 patch.object(desktop, "load_settings", return_value={"computer_name": "Test Mac", "tunnel_id": "test-tunnel"}), \
                 patch.object(desktop, "start", return_value="已连接"), \
                 patch.object(desktop, "runtime_upgrade_guide", return_value=("已连接", upgrade_pending)), \
                 patch.object(desktop, "message", return_value="完成") as message, \
                 patch.object(desktop, "system_permissions") as manager, \
                 patch.object(guide, "permission_status") as status, \
                 patch.object(guide, "backend_request") as backend, \
                 patch.object(guide, "request_permission") as request, \
                 patch.object(guide, "open_privacy_settings") as settings:
                desktop.gui()
                manager.assert_not_called()
                status.assert_not_called()
                backend.assert_not_called()
                request.assert_not_called()
                settings.assert_not_called()
                message.assert_called_once()
                self.assertIn("ChatGPT 的 Chrome 集成", message.call_args.args[0])

    def test_returning_from_manager_never_requests_permission(self):
        with patch.object(guide, "permission_status", return_value=self.normalized(screen_capture=False)), \
             patch.object(desktop, "choose_list", return_value=None) as choose, \
             patch.object(guide, "request_permission") as request, \
             patch.object(guide, "open_privacy_settings") as settings:
            desktop.system_permissions()
        request.assert_not_called()
        settings.assert_not_called()
        self.assertIn("可选检查", choose.call_args.args[1])
        self.assertIn("无需给 Sugar 授予屏幕录制或辅助功能权限", choose.call_args.args[1])

    def test_explicit_request_calls_only_the_selected_permission(self):
        with patch.object(guide, "permission_status", return_value=self.normalized(screen_capture=False)), \
             patch.object(desktop, "choose_list", side_effect=[1, 7]), \
             patch.object(desktop, "message"), \
             patch.object(guide, "request_permission", return_value=backend_status(screen_capture=False, request_sent=True)) as request:
            desktop.system_permissions()
        request.assert_called_once_with(self.document, "screen_capture")

    def test_cancelled_restart_does_not_stop_or_start_anything(self):
        with patch.object(guide, "permission_status", return_value=self.normalized()), \
             patch.object(desktop, "choose_list", side_effect=[6, 7]), \
             patch.object(desktop, "message", return_value="取消"), \
             patch.object(desktop, "stop") as stop, patch.object(desktop, "start") as start:
            desktop.system_permissions()
        stop.assert_not_called()
        start.assert_not_called()

    def test_restart_rechecks_new_backend_and_never_stops_unmanaged_job(self):
        with patch.object(guide, "permission_status", side_effect=[self.normalized(pid=4321), self.normalized(pid=5678)]) as status, \
             patch.object(desktop, "choose_list", side_effect=[6, 7]) as choose, \
             patch.object(desktop, "message", side_effect=["重启并检查", "完成"]), \
             patch.object(desktop, "managed_pid", return_value=None), \
             patch.object(desktop, "stop") as stop, \
             patch.object(desktop, "start", return_value="已连接") as start:
            desktop.system_permissions()
        stop.assert_not_called()
        start.assert_called_once()
        self.assertEqual(status.call_count, 2)
        self.assertIn("5678", choose.call_args.args[1])


class ChromeIntegrationGuideTests(unittest.TestCase):
    def test_guide_explains_same_mac_workflow_without_control_or_secret_access(self):
        cfg = {"computer_name": '办公 Mac "A"', "tunnel_id": "private-tunnel-id",
               "keychain_account": "private-account", "future_setting": {"secret": "never-display-this"}}
        original = copy.deepcopy(cfg)
        with patch.object(desktop, "load_settings", return_value=cfg) as settings, \
             patch.object(desktop, "message", return_value="完成") as message, \
             patch.object(desktop, "Keychain") as keychain, \
             patch.object(guide, "backend_request") as backend, \
             patch.object(desktop, "run") as run, \
             patch.object(desktop, "atomic_private") as write, \
             patch.object(desktop, "save_document") as save:
            desktop.chrome_integration_guide()
        settings.assert_called_once_with()
        message.assert_called_once()
        text = message.call_args.args[0]
        for expected in (cfg["computer_name"], "Chrome 集成使用说明", "启动开发服务",
                         "http://127.0.0.1:3000", "同一台 Mac", "连接状态请在 ChatGPT 中确认"):
            self.assertIn(expected, text)
        for sensitive in ("private-tunnel-id", "private-account", "never-display-this"):
            self.assertNotIn(sensitive, text)
        self.assertNotIn("已连接", text)
        self.assertEqual(cfg, original)
        for untouched in (keychain, backend, run, write, save):
            untouched.assert_not_called()

    def test_guide_can_be_read_before_connection_settings_exist(self):
        with patch.object(desktop, "load_settings", return_value=None), \
             patch.object(desktop.socket, "gethostname", return_value="Local-Mac.local"), \
             patch.object(desktop, "message", return_value="完成") as message:
            desktop.chrome_integration_guide()
        self.assertIn("本机：Local-Mac", message.call_args.args[0])


class RuntimeUpgradeGuideTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.support = Path(temporary.name)
        self.profile = self.support / "tunnel-profile.yaml"
        self.profile.write_bytes(b"old runtime profile")
        self.status = {"owned_listener": True, "port_in_use": True, "ready": 200, "health": 200}
        self.addCleanup(patch.stopall)
        patch.object(desktop, "SUPPORT", self.support).start()
        patch.object(desktop, "load_settings", return_value={"computer_name": "Test Mac", "tunnel_id": "test-tunnel"}).start()
        patch.object(desktop, "runtime", return_value={"python": "/new/runtime/python"}).start()
        patch.object(desktop, "render_profile", return_value=b"new runtime profile").start()

    def test_new_runtime_restarts_only_after_explicit_confirmation(self):
        events = []
        def confirm(*args, **kwargs):
            events.append("confirmed")
            return "重启并应用"
        with patch.object(desktop, "current_status", return_value=self.status), \
             patch.object(desktop, "message", side_effect=confirm), \
             patch.object(desktop, "stop", side_effect=lambda: events.append("stop")), \
             patch.object(desktop, "start", side_effect=lambda: events.append("start") or "已连接"):
            result, pending = desktop.runtime_upgrade_guide("已连接")
        self.assertEqual(events, ["confirmed", "stop", "start"])
        self.assertEqual(result, "已连接")
        self.assertFalse(pending)

    def test_declining_upgrade_keeps_old_backend_and_profile_alive(self):
        with patch.object(desktop, "current_status", return_value=self.status), \
             patch.object(desktop, "message", return_value="稍后") as message, \
             patch.object(desktop, "stop") as stop, patch.object(desktop, "start") as start:
            result, pending = desktop.runtime_upgrade_guide("已连接")
        self.assertTrue(pending)
        self.assertIn("升级待重启", result)
        self.assertEqual(self.profile.read_bytes(), b"old runtime profile")
        self.assertNotIn("old runtime profile", message.call_args.args[0])
        stop.assert_not_called()
        start.assert_not_called()

    def test_unmanaged_listener_is_never_prompted_or_restarted(self):
        with patch.object(desktop, "current_status", return_value={**self.status, "owned_listener": False}), \
             patch.object(desktop, "message") as message, \
             patch.object(desktop, "stop") as stop, patch.object(desktop, "start") as start:
            result, pending = desktop.runtime_upgrade_guide("当前未连接")
        self.assertEqual(result, "当前未连接")
        self.assertFalse(pending)
        message.assert_not_called()
        stop.assert_not_called()
        start.assert_not_called()

    def test_configuration_return_preserves_pending_upgrade_until_confirmed_restart(self):
        for restart in (False, True):
            with self.subTest(restart=restart):
                self.profile.write_bytes(b"old runtime profile")
                summaries, events, upgrade_prompts = [], [], []

                def started():
                    events.append("start")
                    if events.count("start") > 1:
                        self.profile.write_bytes(b"new runtime profile")
                    return "已连接"

                def message(text, buttons=("完成",), default=None):
                    if buttons == ("稍后", "重启并应用"):
                        upgrade_prompts.append(text)
                        return "稍后"
                    if buttons == ("停止连接", "配置", "完成"):
                        summaries.append(text)
                        return "配置" if len(summaries) == 1 else "完成"
                    if buttons == ("取消", "重启连接"):
                        events.append("restart-confirmed")
                        return "重启连接"
                    return "完成"

                # Walk the real menu and Chrome guide; only native dialogs and
                # process lifecycle are faked. The profile comparison is real.
                choices = [5, 7, 3] if restart else [5, 3]
                with patch.object(desktop, "current_status", return_value=self.status), \
                     patch.object(desktop, "load_workspace_document", return_value=({"workspaces": []}, "hash")), \
                     patch.object(desktop, "choose_list", side_effect=choices), \
                     patch.object(desktop, "message", side_effect=message), \
                     patch.object(desktop, "start", side_effect=started), \
                     patch.object(desktop, "stop", side_effect=lambda: events.append("stop")), \
                     patch.object(desktop, "Keychain") as keychain, \
                     patch.object(guide, "backend_request") as backend:
                    desktop.gui()
                self.assertEqual(len(summaries), 2)
                self.assertIn("升级待重启", summaries[0])
                self.assertEqual("升级待重启" in summaries[1], not restart)
                self.assertEqual(len(upgrade_prompts), 1)
                expected_events = ["start", "restart-confirmed", "stop", "start"] if restart else ["start"]
                self.assertEqual(events, expected_events)
                self.assertEqual(self.profile.read_bytes(), b"new runtime profile" if restart else b"old runtime profile")
                keychain.assert_not_called()
                backend.assert_not_called()


if __name__ == "__main__":
    unittest.main()
