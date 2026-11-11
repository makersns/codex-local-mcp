"""Retained permission/Office tools and the GUI's private backend IPC."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp import Client

from sugar_local_mcp.config import Config, Workspace
from sugar_local_mcp.server import build_server


def data(result):
    return result.structured_content or json.loads(result.content[0].text)


class DesktopServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        (self.project / "hello.txt").write_text("project stays usable", encoding="utf-8")

    async def asyncTearDown(self):
        self.temp.cleanup()

    def config(self, execute=True, write=False):
        return Config({"demo": Workspace("demo", self.project, write, execute)}, self.root / "state")

    async def test_full_tool_set_is_local_development_with_external_chrome_guidance(self):
        expected = {
            "workspace_list", "list_directory", "read_file", "search_text", "write_file", "edit_file",
            "run_command", "process_list", "read_process", "send_process_input", "stop_process",
            "macos_permissions_status", "macos_permissions_request", "office_status", "office_verify",
        }
        with patch("sugar_local_mcp.server.RuntimeDiagnostics"):
            async with Client(build_server(self.config(write=True))) as client:
                tools = {tool.name: tool for tool in (await client.list_tools()).tools}
                self.assertEqual(set(tools), expected)
                for name in ("macos_permissions_status", "office_status", "office_verify"):
                    self.assertTrue(tools[name].annotations.read_only_hint)
                self.assertFalse(tools["macos_permissions_request"].annotations.read_only_hint)
                workspaces = data(await client.call_tool("workspace_list", {}))
                self.assertEqual(workspaces["server_version"], "0.5.0")
                self.assertFalse(workspaces["execution_is_os_sandboxed"])
                self.assertFalse(workspaces["local_browser_available"])
                self.assertIn("Chrome integration", workspaces["local_browser_note"])
                self.assertIn("same Mac", workspaces["local_browser_note"])
                self.assertIn("cannot determine", workspaces["local_browser_note"])
                # Old clients cannot accidentally invoke removed control paths.
                for name in ("browser_start", "desktop_click", "automation_pause"):
                    self.assertTrue((await client.call_tool(name, {})).is_error)

    async def test_permission_check_never_requests_and_bad_kind_is_rejected(self):
        status = {"ok": True, "supported": True, "scope": "current_process",
                  "pid": os.getpid(), "executable": sys.executable,
                  "screen_capture": False, "accessibility": True,
                  "post_event": True, "api_errors": []}
        with patch("sugar_local_mcp.server.macos_permissions.status", return_value=status) as check, \
                patch("sugar_local_mcp.server.macos_permissions.request", return_value={**status, "request_sent": True}) as request, \
                patch("sugar_local_mcp.server.RuntimeDiagnostics"):
            async with Client(build_server(self.config())) as client:
                result = data(await client.call_tool("macos_permissions_status", {}))
                self.assertEqual(result["pid"], os.getpid())
                self.assertIs(result["screen_capture"], False)
                check.assert_called_once_with()
                request.assert_not_called()
                denied = await client.call_tool("macos_permissions_request", {"kind": "all"})
                self.assertTrue(denied.is_error)
                request.assert_not_called()
                requested = data(await client.call_tool("macos_permissions_request", {"kind": "screen_capture"}))
                request.assert_called_once_with("screen_capture")
                self.assertTrue(requested["request_sent"])
                self.assertFalse(requested["screen_capture"])

    async def test_unavailable_desktop_socket_does_not_break_projects(self):
        with patch("sugar_local_mcp.server.RuntimeDiagnostics") as diagnostics, \
                patch("sugar_local_mcp.server.OfficeEnvironment") as office:
            diagnostics.return_value.start.return_value = False
            async with Client(build_server(self.config())) as client:
                result = await client.call_tool("read_file", {"workspace": "demo", "path": "hello.txt"})
                self.assertFalse(result.is_error)
                self.assertEqual(data(result)["content"], "project stays usable")
                self.assertFalse((await client.call_tool("process_list", {})).is_error)
            diagnostics.return_value.close.assert_called_once()
            office.return_value.close.assert_called_once()

    async def test_read_only_ipc_allows_status_but_rejects_permission_requests_and_office_probes(self):
        with patch("sugar_local_mcp.server.RuntimeDiagnostics") as diagnostics, \
                patch("sugar_local_mcp.server.macos_permissions.request") as request, \
                patch("sugar_local_mcp.server.macos_permissions.status", return_value={"supported": False}) as check, \
                patch("sugar_local_mcp.server.OfficeEnvironment") as office:
            server = build_server(self.config(execute=False))
            handlers = diagnostics.call_args.args[1]
            self.assertEqual(set(handlers), {
                "permissions_status", "permissions_request", "office_status", "office_verify"})
            self.assertFalse(handlers["permissions_status"]()["supported"])
            check.assert_called_once_with()
            for operation, payload in [("permissions_request", {"kind": "accessibility"}),
                                       ("office_status", {}), ("office_verify", {})]:
                result = handlers[operation](**payload)
                self.assertFalse(result["ok"])
                self.assertEqual(result["error"]["code"], "execution_disabled")
            request.assert_not_called()
            office.return_value.status.assert_not_called()
            office.return_value.verify.assert_not_called()
            async with Client(server) as client:
                self.assertEqual({tool.name for tool in (await client.list_tools()).tools},
                                 {"workspace_list", "list_directory", "read_file", "search_text"})

    async def test_office_tools_route_format_specific_python_and_explicit_optional_probe(self):
        status = {"ok": True, "capabilities": {
            "xlsx": {"python": "/synthetic/python-xlsx"},
            "docx": {"python": "/synthetic/python-docx"}}}
        verified = {"ok": True, "verification": {"xlsx": {"roundtrip": True}}, "scope": "this-machine"}
        with patch("sugar_local_mcp.server.RuntimeDiagnostics"), \
                patch("sugar_local_mcp.server.OfficeEnvironment") as office:
            office.return_value.status.return_value = status
            office.return_value.verify.return_value = verified
            async with Client(build_server(self.config())) as client:
                result = data(await client.call_tool("office_status", {"refresh": True}))
                self.assertEqual(result["capabilities"], status["capabilities"])
                office.return_value.status.assert_called_once_with(refresh=True)
                self.assertEqual(data(await client.call_tool("office_verify", {})), verified)
                office.return_value.verify.assert_called_once_with(include_libreoffice=False)
                await client.call_tool("office_verify", {"include_libreoffice": True})
                office.return_value.verify.assert_called_with(include_libreoffice=True)
            office.return_value.close.assert_called_once()
        audit = (self.root / "state/audit.jsonl").read_text(encoding="utf-8")
        self.assertIn('"operation": "office_verify"', audit)
        self.assertNotIn("/synthetic/python", audit)

    async def test_gui_permission_and_office_checks_use_the_same_backend_functions(self):
        with patch("sugar_local_mcp.server.RuntimeDiagnostics") as diagnostics, \
                patch("sugar_local_mcp.server.macos_permissions.status", return_value={"pid": os.getpid()}) as check, \
                patch("sugar_local_mcp.server.macos_permissions.request", return_value={"request_sent": True}) as request, \
                patch("sugar_local_mcp.server.OfficeEnvironment") as office:
            office.return_value.status.return_value = {"ok": True}
            office.return_value.verify.return_value = {"ok": True}
            server = build_server(self.config())
            handlers = diagnostics.call_args.args[1]
            self.assertEqual(handlers["permissions_status"]()["pid"], os.getpid())
            self.assertTrue(handlers["permissions_request"](kind="accessibility")["request_sent"])
            self.assertTrue(handlers["office_status"](refresh=True)["ok"])
            self.assertTrue(handlers["office_verify"](include_libreoffice=False)["ok"])
            check.assert_called_once_with()
            request.assert_called_once_with(kind="accessibility")
            office.return_value.status.assert_called_once_with(refresh=True)
            office.return_value.verify.assert_called_once_with(include_libreoffice=False)
            async with Client(server):
                pass
            office.return_value.close.assert_called_once()
            diagnostics.return_value.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
