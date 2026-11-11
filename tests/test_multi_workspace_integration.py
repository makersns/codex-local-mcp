"""Exercise GUI-style configuration through a real local MCP stdio session."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workspace_config import load_document, save_document


def result_data(result):
    if result.is_error:
        raise AssertionError(result.content)
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(result.content[0].text)


class MultiWorkspaceIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_configured_projects_route_reads_writes_and_commands(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            support = root / "application support"
            support.mkdir()
            projects = {"frontend": root / "前端 web", "backend": root / "backend api"}
            for name, project in projects.items():
                project.mkdir()
                (project / "identity.txt").write_text(name, encoding="utf-8")
            document, fingerprint = load_document(support)
            document["workspaces"] = [
                {"id": name, "root": str(path), "allow_write": True, "allow_exec": True}
                for name, path in projects.items()
            ]
            save_document(support, document, fingerprint)
            parameters = StdioServerParameters(
                command=sys.executable,
                args=["-B", "-m", "sugar_local_mcp", "serve", "--config", str(support / "workspaces.json")],
                env={"PYTHONDONTWRITEBYTECODE": "1"},
            )
            async with asyncio.timeout(30):
                async with stdio_client(parameters) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        listed = result_data(await session.call_tool("workspace_list", {}))
                        self.assertEqual({x["id"] for x in listed["workspaces"]}, set(projects))
                        jobs = {}
                        for name, path in projects.items():
                            content = result_data(await session.call_tool("read_file", {
                                "workspace": name, "path": "identity.txt",
                            }))
                            self.assertEqual(content["content"], name)
                            result_data(await session.call_tool("write_file", {
                                "workspace": name, "path": "from-mcp.txt", "content": name,
                                "expected_sha256": "missing",
                            }))
                            started = result_data(await session.call_tool("run_command", {
                                "workspace": name,
                                "argv": [sys.executable, "-B", "-c", "from pathlib import Path; print(Path.cwd())"],
                            }))
                            jobs[name] = started["job_id"]
                        for name, job_id in jobs.items():
                            cursor, output = 0, []
                            for _ in range(150):
                                page = result_data(await session.call_tool("read_process", {
                                    "job_id": job_id, "cursor": cursor,
                                }))
                                output.extend(x["text"] for x in page["events"] if x["stream"] == "stdout")
                                cursor = page["next_cursor"]
                                if page["output_complete"] and not page["has_more"]:
                                    break
                                await asyncio.sleep(0.02)
                            else:
                                self.fail("Managed command did not complete")
                            self.assertEqual(page["exit_code"], 0)
                            self.assertEqual("".join(output).strip(), str(projects[name]))
            for name, path in projects.items():
                self.assertEqual((path / "from-mcp.txt").read_text(), name)


if __name__ == "__main__":
    unittest.main()
