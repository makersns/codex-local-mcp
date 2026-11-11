"""Regression: the real stdio process must clean up jobs on SIGTERM."""

import json
import os
import select
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


@unittest.skipUnless(os.name == "posix", "POSIX process-group lifecycle")
class ShutdownTests(unittest.TestCase):
    def test_sigterm_cleans_up_job(self):
        self._exercise_shutdown(block_output=False)

    def test_sigterm_under_output_backpressure(self):
        self._exercise_shutdown(block_output=True)

    def _exercise_shutdown(self, *, block_output):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            project = directory / "project"
            project.mkdir()
            (project / "large.txt").write_text("中文日志" * 20000, encoding="utf-8")
            config = directory / "config.json"
            config.write_text(json.dumps({
                "state_dir": str(directory / "state"),
                "workspaces": [{"id": "demo", "root": str(project), "allow_exec": True}],
            }), encoding="utf-8")
            environment = dict(os.environ)
            environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
            service = subprocess.Popen(
                [sys.executable, "-m", "sugar_local_mcp", "serve", "--config", str(config)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", env=environment,
            )
            job_pid = None
            try:
                def request(identifier, method, params):
                    service.stdin.write(json.dumps({"jsonrpc": "2.0", "id": identifier,
                                                    "method": method, "params": params}) + "\n")
                    service.stdin.flush()
                    deadline = time.monotonic() + 8
                    while time.monotonic() < deadline:
                        ready, _, _ = select.select([service.stdout], [], [], max(0, deadline - time.monotonic()))
                        self.assertTrue(ready, "stdio response timed out")
                        line = service.stdout.readline()
                        self.assertTrue(line, "stdio service closed before its response")
                        message = json.loads(line)
                        if message.get("id") == identifier:
                            return message["result"]
                    self.fail("Response ID was not received")

                request(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                          "clientInfo": {"name": "shutdown-test", "version": "1"}})
                service.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
                service.stdin.flush()
                result = request(2, "tools/call", {"name": "run_command", "arguments": {
                    "workspace": "demo", "argv": [sys.executable, "-c", "import time;time.sleep(120)"],
                    "timeout_seconds": 180,
                }})
                self.assertFalse(result.get("isError", False))
                payload = result.get("structuredContent") or json.loads(result["content"][0]["text"])
                job_pid = payload["pid"]
                os.kill(job_pid, 0)
                if block_output:
                    # Leave a response larger than a pipe buffer unread.
                    service.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                        "params": {"name": "read_file", "arguments": {
                            "workspace": "demo", "path": "large.txt"}}}) + "\n")
                    service.stdin.flush()
                    time.sleep(0.2)
                service.terminate()
                self.assertEqual(service.wait(timeout=10), 0)
                with self.assertRaises(ProcessLookupError):
                    os.kill(job_pid, 0)
                job_pid = None
            finally:
                if service.poll() is None:
                    service.kill()
                    service.wait(timeout=5)
                service.stdin.close()
                service.stdout.close()
                if job_pid is not None:
                    try:
                        os.killpg(job_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass


if __name__ == "__main__":
    unittest.main()
