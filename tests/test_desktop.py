import importlib.util
import fcntl
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import desktop


class DesktopTests(unittest.TestCase):
    def test_second_window_cannot_enter_first_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with open(root / "control.lock", "a") as existing_window:
                fcntl.flock(existing_window, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch.object(desktop, "SUPPORT", root), \
                     patch.object(sys, "argv", ["desktop.py", "gui"]), \
                     patch.object(desktop, "gui") as gui, \
                     patch.object(desktop, "message") as message:
                    self.assertEqual(desktop.main(), 0)
                    gui.assert_not_called()
                    message.assert_called_once()

    def test_tunnel_ids_require_real_full_shape(self):
        self.assertEqual(desktop.validate_tunnel_id("tunnel_"+"a"*32), "tunnel_"+"a"*32)
        self.assertEqual(desktop.validate_tunnel_id("tunnel_ab12_"+"9"*32), "tunnel_ab12_"+"9"*32)
        for bad in ["tunnel_fake", "https://example.org/mcp", "tunnel_"+"A"*32, "tunnel_"+"a"*32+"\n", "$(touch nope)"]:
            with self.assertRaises(desktop.DesktopError):
                desktop.validate_tunnel_id(bad)

    def test_profile_routes_only_to_its_tunnel_and_pins_native_python(self):
        info = {"arch":"arm64", "python":"/Users/test/Applications/A folder/python"}
        cfg = {"tunnel_id":"tunnel_"+"1"*32, "health_port":8080}
        profile = desktop.render_profile(cfg, info).decode()
        self.assertIn('api_key: "env:CONTROL_PLANE_API_KEY"', profile)
        command_line = next(line for line in profile.splitlines() if "command:" in line)
        command = json.loads(command_line.split("command:",1)[1])
        argv = shlex.split(command)
        self.assertEqual(argv[:3], ["/usr/bin/arch", "-arm64", info["python"]])
        other = desktop.render_profile({**cfg,"tunnel_id":"tunnel_"+"2"*32},info).decode()
        self.assertNotIn(cfg["tunnel_id"],other)

    def test_other_existing_tunnel_is_not_reused_or_restarted(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(desktop,"SUPPORT",Path(directory)), \
                 patch.object(desktop,"load_settings",return_value={"health_port":8080}), \
                 patch.object(desktop,"current_status",return_value={"port_in_use":True,"owned_listener":False}), \
                 patch.object(desktop,"Keychain") as keychain, \
                 patch.object(desktop,"run") as command:
                with self.assertRaises(desktop.DesktopError):
                    desktop.start()
                keychain.assert_not_called()
                command.assert_not_called()

    def test_running_owned_tunnel_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(desktop,"SUPPORT",Path(directory)), \
                 patch.object(desktop,"load_settings",return_value={"health_port":8080}), \
                 patch.object(desktop,"current_status",return_value={"port_in_use":True,"owned_listener":True,"ready":200}), \
                 patch.object(desktop,"Keychain") as keychain:
                self.assertEqual(desktop.start(),"已连接")
                keychain.assert_not_called()

    def test_daemon_does_not_put_key_into_command_arguments(self):
        cfg={"health_port":8080,"keychain_account":"computer-b","development_path":"/tools:/usr/bin","tunnel_id":"tunnel_"+"b"*32}
        info={"tunnel_client":"/tools/tunnel-client","python":"/python","arch":"arm64"}
        with patch.dict(desktop.os.environ, {"CONTROL_PLANE_TUNNEL_ID":"a-different-route", "MCP_COMMAND":"another-server"}), \
             patch.object(desktop,"load_settings",return_value=cfg), \
             patch.object(desktop,"runtime",return_value=info), \
             patch.object(desktop,"health",return_value={"port_in_use":False}), \
             patch.object(desktop,"Keychain") as keychain, \
             patch.object(desktop.os,"chdir"), \
             patch.object(desktop.os,"execve") as execute:
            keychain.return_value.get.return_value="not-a-real-key"
            desktop.daemon()
            binary,args,env=execute.call_args.args
            self.assertNotIn("not-a-real-key"," ".join(args))
            self.assertEqual(env["CONTROL_PLANE_API_KEY"],"not-a-real-key")
            self.assertNotIn("CONTROL_PLANE_TUNNEL_ID",env)
            self.assertNotIn("MCP_COMMAND",env)
            self.assertEqual(args[-2:],["--control-plane.tunnel-id",cfg["tunnel_id"]])
            keychain.assert_called_once_with(interactive=False,account="computer-b")

    def test_atomic_configuration_is_private(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/"settings.json"
            desktop.atomic_private(target,b'{"ok":true}\n')
            self.assertEqual(target.stat().st_mode & 0o777,0o600)
            desktop.atomic_private(target,b'{"ok":false}\n')
            self.assertEqual(json.loads(target.read_text()),{"ok":False})

    def test_login_shell_noise_and_secrets_are_not_saved(self):
        def fake_run(argv,**kwargs):
            marker=__import__("re").search(r"SUGAR_MCP_PATH_[a-f0-9]+",argv[-1]).group()
            return MagicMock(stdout="unrelated-secret-noise\n"+marker+"/custom/bin:/usr/bin"+marker+"\n",returncode=0)
        with patch.object(desktop,"run",side_effect=fake_run):
            path=desktop.development_path()
        self.assertTrue(path.startswith("/custom/bin:/usr/bin"))
        self.assertNotIn("unrelated-secret-noise",path)


class ConnectionRestartTests(unittest.TestCase):
    def setUp(self):
        # Ordinary save/restart flows cannot depend on optional system permissions.
        backend = patch.object(desktop.permissions, "backend_request",
                               side_effect=AssertionError("Restart must not query or request OS permissions"))
        backend.start()
        self.addCleanup(backend.stop)

    def test_restart_confirms_then_rechecks_ownership_and_stops_before_starting(self):
        events = []
        messages = []

        def message(text, buttons=("完成",), default=None):
            messages.append(text)
            if len(buttons) == 2:
                self.assertEqual(default, "取消")
                events.append("confirm")
                return "重启连接"
            events.append("result")
            return "完成"

        def status():
            events.append("status")
            return {"port_in_use": True, "owned_listener": True, "managed_pid": 123, "ready": 200}

        with patch.object(desktop, "message", side_effect=message), \
             patch.object(desktop, "current_status", side_effect=status), \
             patch.object(desktop, "stop", side_effect=lambda: events.append("stop")), \
             patch.object(desktop, "start", side_effect=lambda: events.append("start") or "已连接"):
            self.assertTrue(desktop.restart_connection())
        self.assertEqual(events, ["confirm", "status", "stop", "start", "result"])
        self.assertIn("任务可能中断", messages[0])
        self.assertTrue(messages[1].startswith("本机隧道已就绪"))
        self.assertIn("尚未验证 ChatGPT 工具调用", messages[1])

    def test_cancel_button_or_dismissal_preserves_the_connection(self):
        for reply in ("取消", desktop.Cancelled()):
            with self.subTest(reply=type(reply).__name__), \
                 patch.object(desktop, "message", side_effect=[reply]), \
                 patch.object(desktop, "current_status") as status, \
                 patch.object(desktop, "stop") as stop, \
                 patch.object(desktop, "start") as start, \
                 patch.object(desktop, "atomic_private") as write, \
                 patch.object(desktop, "Keychain") as keychain:
                if isinstance(reply, desktop.Cancelled):
                    with self.assertRaises(desktop.Cancelled):
                        desktop.restart_connection()
                else:
                    self.assertFalse(desktop.restart_connection())
                status.assert_not_called()
                stop.assert_not_called()
                start.assert_not_called()
                write.assert_not_called()
                keychain.assert_not_called()

    def test_restart_does_not_stop_or_adopt_an_unmanaged_listener(self):
        with patch.object(desktop, "message", return_value="重启连接"), \
             patch.object(desktop, "current_status", return_value={
                 "port_in_use": True, "owned_listener": False, "managed_pid": 123}), \
             patch.object(desktop, "stop") as stop, \
             patch.object(desktop, "start") as start:
            with self.assertRaisesRegex(desktop.DesktopError, "其他进程"):
                desktop.restart_connection()
        stop.assert_not_called()
        start.assert_not_called()

    def test_restart_can_start_a_stopped_connection_without_stopping_other_jobs(self):
        with patch.object(desktop, "message", side_effect=["重启连接", "完成"]), \
             patch.object(desktop, "current_status", return_value={
                 "port_in_use": False, "owned_listener": False, "managed_pid": None}), \
             patch.object(desktop, "stop") as stop, \
             patch.object(desktop, "start", return_value="已连接") as start:
            self.assertTrue(desktop.restart_connection())
        stop.assert_not_called()
        start.assert_called_once()

    def test_failed_stop_does_not_start_or_report_a_ready_connection(self):
        with patch.object(desktop, "message", return_value="重启连接") as message, \
             patch.object(desktop, "current_status", return_value={
                 "port_in_use": True, "owned_listener": True, "managed_pid": 123}), \
             patch.object(desktop, "stop", side_effect=desktop.DesktopError("后台尚未停止")), \
             patch.object(desktop, "start") as start:
            with self.assertRaisesRegex(desktop.DesktopError, "后台尚未停止"):
                desktop.restart_connection()
        start.assert_not_called()
        message.assert_called_once()

    def test_menu_exposes_chrome_guide_optional_permissions_and_explicit_restart(self):
        expected = ("管理项目目录", "修改连接设置", "查看日志", "返回", "系统权限检查（可选）",
                    "Chrome 集成使用说明", "Office 环境检测与验证", "重启连接")
        with patch.object(desktop, "choose_list", side_effect=[4, 5, 6, 7, 3]) as choose, \
             patch.object(desktop, "system_permissions") as permissions, \
             patch.object(desktop, "chrome_integration_guide") as chrome_guide, \
             patch.object(desktop, "office_environment") as office, \
             patch.object(desktop, "restart_connection", return_value=False) as restart:
            desktop.configure()
        self.assertEqual(choose.call_args_list[0].args[0], expected)
        for selected in (permissions, chrome_guide, office, restart):
            selected.assert_called_once_with()
        self.assertNotIn("打开本机 Chrome 调试", expected)
        self.assertNotIn("暂停 / 继续自动操作", expected)

    def test_main_gui_distinguishes_local_readiness_and_preserves_upgrade_notice(self):
        for result in ("已连接", "已连接（升级待重启；当前继续使用原后台）", "当前未连接"):
            with self.subTest(result=result), \
                 patch.object(desktop, "load_settings", return_value={"computer_name": "Test Mac", "tunnel_id": "test-tunnel"}), \
                 patch.object(desktop, "load_workspace_document", return_value=({"workspaces": []}, "hash")), \
                 patch.object(desktop, "workspace_summary", return_value="测试项目"), \
                 patch.object(desktop, "start", return_value=result), \
                 patch.object(desktop, "runtime_upgrade_guide", return_value=(result, True)), \
                 patch.object(desktop, "message", return_value="完成") as message, \
                 patch.object(desktop, "stop") as stop:
                desktop.gui()
                text = message.call_args.args[0]
                if result.startswith("已连接"):
                    self.assertTrue(text.startswith("本机隧道已就绪"))
                    self.assertIn("尚未验证 ChatGPT 工具调用", text)
                    self.assertIn(result[len("已连接"):], text)
                else:
                    self.assertTrue(text.startswith(result))
                    self.assertNotIn("本机隧道已就绪", text)
                stop.assert_not_called()


if __name__ == "__main__":
    unittest.main()
