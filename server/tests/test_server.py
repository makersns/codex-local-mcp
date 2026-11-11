"""Transport-level checks with the official SDK, using disposable projects."""

import asyncio
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp import ClientSession

from sugar_local_mcp.config import Config, Workspace
from sugar_local_mcp.server import build_server, http_security


def data(result):
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(result.content[0].text)


class ServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.project = self.directory / "project"
        self.project.mkdir()
        (self.project / "hello.txt").write_text("你好，MCP\n", encoding="utf-8")
        self.state = self.directory / "state"

    async def asyncTearDown(self):
        self.temp.cleanup()

    def config(self, write=False, execute=False):
        return Config({"demo": Workspace("demo", self.project, write, execute)}, self.state)

    async def test_read_only_tools_and_server_rejection(self):
        async with Client(build_server(self.config())) as client:
            tools = (await client.list_tools()).tools
            self.assertEqual({x.name for x in tools},
                             {"workspace_list", "list_directory", "read_file", "search_text"})
            self.assertTrue(all(x.annotations.read_only_hint is True for x in tools))
            result = await client.call_tool("read_file", {"workspace": "demo", "path": "hello.txt"})
            self.assertFalse(result.is_error)
            self.assertEqual(data(result)["content"], "你好，MCP\n")
            self.assertTrue((await client.call_tool("write_file", {
                "workspace": "demo", "path": "hello.txt", "content": "oops", "expected_sha256": "missing"
            })).is_error)
            self.assertTrue((await client.call_tool("read_file", {
                "workspace": "demo", "path": "../private.txt"
            })).is_error)
        self.assertEqual((self.project / "hello.txt").read_text(encoding="utf-8"), "你好，MCP\n")

    async def test_writes_require_hash_and_workspace_permission(self):
        config = self.config(write=True)
        config.workspaces["readonly"] = Workspace("readonly", self.project)
        async with Client(build_server(config)) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            self.assertFalse(tools["write_file"].annotations.read_only_hint)
            self.assertNotIn("run_command", tools)
            read = data(await client.call_tool("read_file", {"workspace": "demo", "path": "hello.txt"}))
            denied = await client.call_tool("write_file", {
                "workspace": "readonly", "path": "hello.txt", "content": "bad", "expected_sha256": read["sha256"]
            })
            self.assertTrue(denied.is_error)
            edited = await client.call_tool("edit_file", {
                "workspace": "demo", "path": "hello.txt", "old_text": "MCP", "new_text": "本地开发",
                "expected_sha256": read["sha256"]
            })
            self.assertFalse(edited.is_error)
            stale = await client.call_tool("write_file", {
                "workspace": "demo", "path": "hello.txt", "content": "stale", "expected_sha256": read["sha256"]
            })
            self.assertTrue(stale.is_error)
            self.assertIn("Conflict:", stale.content[0].text)
        self.assertEqual((self.project / "hello.txt").read_text(encoding="utf-8"), "你好，本地开发\n")

    async def test_managed_command_through_tools(self):
        config = self.config(execute=True)
        config.workspaces["readonly"] = Workspace("readonly", self.project)
        async with Client(build_server(config)) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            self.assertFalse(tools["run_command"].annotations.read_only_hint)
            self.assertTrue(tools["run_command"].annotations.open_world_hint)
            denied = await client.call_tool("run_command", {
                "workspace": "readonly", "argv": [sys.executable, "-c", "print('bad')"]})
            self.assertTrue(denied.is_error)
            started = await client.call_tool("run_command", {
                "workspace": "demo", "argv": [sys.executable, "-u", "-c", "print('任务完成')"]})
            self.assertFalse(started.is_error)
            job = data(started)["job_id"]
            output = []
            cursor = 0
            for _ in range(100):
                page = data(await client.call_tool("read_process", {"job_id": job, "cursor": cursor}))
                output.extend(event["text"] for event in page["events"])
                cursor = page["next_cursor"]
                if page["output_complete"] and not page["has_more"]:
                    break
                await asyncio.sleep(0.02)
            else:
                self.fail("Process did not complete in the integration-test budget")
            self.assertEqual(page["exit_code"], 0)
            self.assertIn("任务完成", "".join(output))
            listed = data(await client.call_tool("process_list", {}))
            self.assertEqual(listed["jobs"][0]["workspace"], "demo")
        # Audit records contain operation metadata, never source content or argv.
        audit = (self.state / "audit.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("任务完成", audit)
        self.assertIn('"operation": "run_command"', audit)

    async def test_real_stdio_initialization_and_read(self):
        path = self.directory / "config.json"
        path.write_text(json.dumps({"workspaces": [{"id": "demo", "root": str(self.project)}],
                                    "state_dir": str(self.state)}), encoding="utf-8")
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "sugar_local_mcp", "serve", "--config", str(path)],
            env={"PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
        )
        # Legacy initialize remains relevant to tunnel clients. Exercise real
        # pipes instead of testing only SDK in-process dispatch.
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                self.assertEqual(initialized.server_info.name, "Sugar Local MCP")
                self.assertEqual(len((await session.list_tools()).tools), 4)
                result = await session.call_tool("read_file", {"workspace": "demo", "path": "hello.txt"})
                self.assertFalse(result.is_error)
                self.assertEqual(data(result)["sha256"], hashlib.sha256("你好，MCP\n".encode()).hexdigest())

    async def test_http_host_and_origin_protection(self):
        import httpx2
        server = build_server(self.config())
        app = server.streamable_http_app(stateless_http=True, json_response=True,
                                         transport_security=http_security(8765))
        # Enter the Starlette lifespan explicitly for the MCP HTTP manager.
        async with app.router.lifespan_context(app):
            async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app),
                                           base_url="http://127.0.0.1:8765") as client:
                body = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                    "protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"}}}
                headers = {"Accept": "application/json, text/event-stream"}
                valid = await client.post("/mcp", json=body, headers=headers)
                self.assertEqual(valid.status_code, 200)
                self.assertEqual(valid.json()["result"]["serverInfo"]["name"], "Sugar Local MCP")
                invalid_host = await client.post("/mcp", json=body,
                    headers={**headers, "Host": "untrusted.example"})
                self.assertEqual(invalid_host.status_code, 421)
                invalid_origin = await client.post("/mcp", json=body,
                    headers={**headers, "Origin": "https://untrusted.example"})
                self.assertEqual(invalid_origin.status_code, 403)


if __name__ == "__main__":
    unittest.main()
