"""Activity aggregation through real SDK calls, private IPC and stdio metadata."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from mcp import Client, ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.mcpserver import Context

from sugar_local_mcp.config import Config, Workspace
from sugar_local_mcp.diagnostics import request_local
from sugar_local_mcp.server import build_server


def data(result):
    return result.structured_content or json.loads(result.content[0].text)


class ServerActivityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="codex-act-", dir="/tmp")
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        (self.project / "hello.txt").write_text("synthetic content", encoding="utf-8")
        self.state = self.root / "state"

    async def asyncTearDown(self):
        self.temp.cleanup()

    def config(self):
        return Config({"demo": Workspace("demo", self.project, True, True)}, self.state)

    async def test_sdk_injects_context_without_changing_discovery_or_input_schema(self):
        with patch("sugar_local_mcp.server.RuntimeDiagnostics") as diagnostics:
            server = build_server(self.config())
            handler = diagnostics.call_args.args[1]["activity_snapshot"]
            async with Client(server) as client:
                tools = (await client.list_tools()).tools
                self.assertEqual(len(tools), 15)
                for tool in tools:
                    self.assertNotIn("context", json.dumps(tool.input_schema))
                    self.assertNotIn("openai/session", json.dumps(tool.input_schema))
                self.assertNotIn("activity_snapshot", {tool.name for tool in tools})
                for identifier in ("fixture-alpha", "fixture-alpha", "fixture-beta"):
                    result = await client.call_tool("workspace_list", {}, meta={"openai/session": identifier})
                    self.assertFalse(result.is_error)
                before = handler()
                audit_before = (self.state / "audit.jsonl").read_bytes()
                for _ in range(4):
                    after = handler()
                    self.assertEqual(after["recent_sessions"], 2)
                    self.assertEqual(after["active_calls"], 0)
                    self.assertEqual(after["last_activity_at"], before["last_activity_at"])
                self.assertEqual(audit_before, (self.state / "audit.jsonl").read_bytes())
                self.assertEqual(after["unknown_recent_calls"], 0)
                self.assertTrue(after["metadata_seen"])
                self.assertTrue(after["coverage_complete"])
        self.assertNotIn("fixture-alpha", (self.state / "audit.jsonl").read_text())
        self.assertNotIn("fixture-beta", json.dumps(after))

    async def test_parallel_sdk_calls_deduplicate_sessions_and_cleanup(self):
        entered = threading.Event()
        release = threading.Event()
        lock = threading.Lock()
        count = 0
        def blocked():
            nonlocal count
            with lock:
                count += 1
                if count == 4:
                    entered.set()
            release.wait(4)
            return {"workspaces": []}
        with patch("sugar_local_mcp.server.RuntimeDiagnostics") as diagnostics, \
                patch("sugar_local_mcp.server.FileService.workspaces", side_effect=blocked):
            server = build_server(self.config())
            handler = diagnostics.call_args.args[1]["activity_snapshot"]
            async with Client(server) as client:
                calls = [asyncio.create_task(client.call_tool("workspace_list", {}, meta=meta))
                         for meta in [{"openai/session": "fiction-a"}, {"openai/session": "fiction-a"},
                                      {"openai/session": "fiction-b"}, {}]]
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                    state = handler()
                    self.assertEqual(state["active_calls"], 4)
                    self.assertEqual(state["active_sessions"], 2)
                    self.assertEqual(state["unknown_active_calls"], 1)
                    self.assertFalse(state["coverage_complete"])
                finally:
                    release.set()
                    await asyncio.gather(*calls)
                state = handler()
                self.assertEqual(state["active_calls"], 0)
                self.assertEqual(state["active_sessions"], 0)
                self.assertEqual(state["unknown_active_calls"], 0)
                self.assertEqual(state["recent_sessions"], 2)

    async def test_exception_cancel_and_invalid_job_calls_finish_tracking(self):
        with patch("sugar_local_mcp.server.RuntimeDiagnostics") as diagnostics:
            server = build_server(self.config())
            handler = diagnostics.call_args.args[1]["activity_snapshot"]
            async with Client(server) as client:
                failed = await client.call_tool("read_file", {"workspace": "demo", "path": "missing.txt"},
                                                meta={"openai/session": "fiction-error"})
                self.assertTrue(failed.is_error)
                bad_job = await client.call_tool("read_process", {"job_id": "job_absent"},
                                                meta={"openai/session": "fiction-invalid-job"})
                self.assertTrue(bad_job.is_error)
                self.assertEqual(handler()["recent_sessions"], 2)
                self.assertEqual(handler()["active_calls"], 0)
                # Execute the SDK-registered function: cancellation must pass
                # through the actual centralized invoke's finally boundary.
                registered = server._tool_manager.get_tool("workspace_list").fn
                ctx = Context(request_context=SimpleNamespace(meta={"openai/session": "fiction-cancel"}))
                with patch("sugar_local_mcp.server.anyio.to_thread.run_sync",
                           side_effect=asyncio.CancelledError):
                    with self.assertRaises(asyncio.CancelledError):
                        await registered(context=ctx)
                self.assertEqual(handler()["active_calls"], 0)
                self.assertEqual(handler()["active_sessions"], 0)
                self.assertEqual(handler()["recent_sessions"], 3)

    async def test_invalid_workspace_parameter_never_enters_activity_or_audit(self):
        with patch("sugar_local_mcp.server.RuntimeDiagnostics") as diagnostics:
            server = build_server(self.config())
            handler = diagnostics.call_args.args[1]["activity_snapshot"]
            async with Client(server) as client:
                result = await client.call_tool("read_file", {
                    "workspace": "/private/fictional-parameter", "path": "hello.txt",
                }, meta={"openai/session": "fiction-workspace"})
                self.assertTrue(result.is_error)
                snapshot = handler()
                self.assertIsNone(snapshot["last_workspace"])
                self.assertIsNone(snapshot["sessions"][0]["last_workspace"])
                self.assertNotIn("/private/fictional-parameter", json.dumps(snapshot))
        self.assertNotIn("/private/fictional-parameter",
                         (self.state / "audit.jsonl").read_text(encoding="utf-8"))

    async def test_audit_failure_cannot_leave_activity_active(self):
        with patch("sugar_local_mcp.server.RuntimeDiagnostics") as diagnostics, \
                patch("sugar_local_mcp.server.AuditLog") as audit:
            server = build_server(self.config())
            handler = diagnostics.call_args.args[1]["activity_snapshot"]
            async with Client(server):
                registered = server._tool_manager.get_tool("workspace_list").fn
                ctx = Context(request_context=SimpleNamespace(meta={"openai/session": "fiction-audit"}))
                audit.return_value.record.side_effect = OSError("synthetic audit unavailable")
                with self.assertRaises(OSError):
                    await registered(context=ctx)
                self.assertEqual(handler()["active_calls"], 0)
                self.assertEqual(handler()["active_sessions"], 0)
                self.assertEqual(handler()["recent_sessions"], 1)

    @unittest.skipUnless(os.name == "posix", "Private diagnostic IPC requires POSIX")
    async def test_real_stdio_meta_multi_session_ipc_and_task_lifetimes(self):
        config = self.root / "config.json"
        config.write_text(json.dumps({
            "state_dir": str(self.state),
            "workspaces": [{"id": "demo", "root": str(self.project),
                            "allow_write": True, "allow_exec": True}],
        }), encoding="utf-8")
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-B", "-m", "sugar_local_mcp", "serve", "--config", str(config)],
            env={"PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
                 "PYTHONDONTWRITEBYTECODE": "1"},
        )
        async def snapshot():
            reply = await asyncio.to_thread(request_local, self.state, "activity_snapshot", timeout=2)
            self.assertTrue(reply["ok"], reply)
            return reply
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = (await session.list_tools()).tools
                self.assertEqual(len(tools), 15)
                self.assertTrue(all("context" not in json.dumps(tool.input_schema) for tool in tools))
                fresh = await snapshot()
                self.assertEqual(fresh["recent_sessions"], 0)
                self.assertFalse(fresh["metadata_seen"])
                for identifier in ("wire-fiction-a", "wire-fiction-a", "wire-fiction-b"):
                    result = await session.call_tool("read_file", {"workspace": "demo", "path": "hello.txt"},
                                                     meta={"openai/session": identifier})
                    self.assertFalse(result.is_error)
                await session.call_tool("workspace_list", {})
                await session.call_tool("workspace_list", {}, meta={"openai/session": 42})
                state = await snapshot()
                self.assertEqual(state["recent_sessions"], 2)
                self.assertEqual(state["unknown_recent_calls"], 2)
                self.assertFalse(state["coverage_complete"])
                self.assertEqual(state["active_calls"], 0)
                self.assertEqual(state["running_tasks"], 0)
                audit_before = (self.state / "audit.jsonl").read_bytes()
                again = await snapshot()
                self.assertEqual(state["last_activity_at"], again["last_activity_at"])
                self.assertEqual(audit_before, (self.state / "audit.jsonl").read_bytes())
                started = await session.call_tool("run_command", {
                    "workspace": "demo", "argv": [sys.executable, "-B", "-c",
                        "import sys;sys.stdin.buffer.read()"], "timeout_seconds": 30,
                }, meta={"openai/session": "wire-fiction-c"})
                self.assertFalse(started.is_error)
                job = data(started)["job_id"]
                try:
                    state = await snapshot()
                    self.assertEqual(state["recent_sessions"], 3)
                    self.assertEqual(state["active_calls"], 0)
                    self.assertEqual(state["active_sessions"], 0)
                    self.assertEqual(state["running_tasks"], 1)
                    self.assertNotIn("wire-fiction", json.dumps(state))
                    self.assertNotIn("argv", json.dumps(state))
                    self.assertNotIn(str(self.project), json.dumps(state))
                finally:
                    stopped = await session.call_tool("stop_process", {"job_id": job},
                                                       meta={"openai/session": "wire-fiction-c"})
                    self.assertFalse(stopped.is_error)
                state = await snapshot()
                self.assertEqual(state["running_tasks"], 0)
                self.assertEqual(state["active_calls"], 0)
                self.assertEqual(state["recent_sessions"], 3)
        audit = (self.state / "audit.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("wire-fiction", audit)
        self.assertNotIn("openai/session", audit)
        self.assertNotIn("activity_snapshot", audit)


if __name__ == "__main__":
    unittest.main()
