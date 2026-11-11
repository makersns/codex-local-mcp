from __future__ import annotations

import json
import errno
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sugar_local_mcp.processes import ProcessError, ProcessManager, _Job


def output(result: dict, stream: str | None = None) -> str:
    return "".join(event["text"] for event in result["events"] if stream is None or event["stream"] == stream)


class ProcessManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.cwd = Path(self.temp.name)
        self.manager = ProcessManager(terminate_grace_seconds=0.05)

    def tearDown(self) -> None:
        self.manager.close()
        self.temp.cleanup()

    def start(self, code: str, **kwargs: object) -> dict:
        return self.manager.start([sys.executable, "-c", code], self.cwd, **kwargs)

    def wait(self, job_id: str, timeout: float = 6) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = self.manager.read(job_id)
            if result["finished_at"] is not None and result["output_complete"]:
                return result
            time.sleep(0.01)
        self.fail(f"Job did not finish: {self.manager.read(job_id)}")

    def test_normal_exit_and_stderr(self) -> None:
        job = self.start("import sys; print('hello'); print('warning', file=sys.stderr)")
        result = self.wait(job["job_id"])
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["state"], "exited")
        self.assertEqual(output(result, "stdout"), "hello\n")
        self.assertEqual(output(result, "stderr"), "warning\n")
        self.assertEqual(result["end_cursor"], len("hello\nwarning\n".encode()))
        self.assertFalse(self.manager.read(job["job_id"], result["next_cursor"])["events"])

    def test_exit_code(self) -> None:
        job = self.start("import sys; sys.exit(7)")
        self.assertEqual(self.wait(job["job_id"])["exit_code"], 7)

    def test_argv_preview_is_bounded_without_changing_execution(self) -> None:
        long_arg = "中" * 3000
        job = self.manager.start([sys.executable, "-c", "import sys; print(len(sys.argv[1]))", long_arg], self.cwd)
        self.assertTrue(job["argv_truncated"])
        self.assertLessEqual(sum(len(arg.encode()) for arg in job["argv"]), 2048)
        self.assertEqual(output(self.wait(job["job_id"])), "3000\n")

    def test_start_returns_before_process_finishes(self) -> None:
        started = time.monotonic()
        job = self.start("import time; time.sleep(20)")
        self.assertLess(time.monotonic() - started, 1)
        self.assertTrue(job["process_running"])
        self.manager.stop(job["job_id"])
        self.assertEqual(self.wait(job["job_id"])["state"], "stopped")

    def test_input_and_eof(self) -> None:
        job = self.start("import sys; print(sys.stdin.read().upper())")
        sent = self.manager.write_stdin(job["job_id"], "hello 中文", close=True)
        self.assertEqual(sent["queued_bytes"], len("hello 中文".encode()))
        with self.assertRaises(ProcessError):
            self.manager.write_stdin(job["job_id"], "more")
        self.assertEqual(output(self.wait(job["job_id"])), "HELLO 中文\n")

    def test_input_queue_never_blocks(self) -> None:
        job = self.start("import time; time.sleep(20)")
        start = time.monotonic()
        with self.assertRaises(ProcessError):
            for _ in range(50):
                self.manager.write_stdin(job["job_id"], "x" * 16384)
        self.assertLess(time.monotonic() - start, 1)
        self.manager.stop(job["job_id"])

    def test_timeout(self) -> None:
        job = self.start("import time; time.sleep(20)", timeout_seconds=0.1)
        result = self.wait(job["job_id"])
        self.assertEqual(result["state"], "timed_out")
        self.assertIsNotNone(result["exit_code"])
        self.assertIsNone(result["cleanup_error"])

    @unittest.skipUnless(os.name == "posix", "POSIX process groups")
    def test_term_reaps_exited_leader_without_sending_redundant_kill(self) -> None:
        # Model Darwin's expired leader: TERM succeeds, then the un-reaped
        # group can reject SIGKILL. Reaping our own child removes that group.
        exited, reaped = False, False
        sent = []
        def poll():
            nonlocal reaped
            if exited:
                reaped = True
                return -signal.SIGTERM
            return None
        def killpg(pid, sig):
            nonlocal exited
            if reaped:
                raise ProcessLookupError(errno.ESRCH, "No such process")
            if sig == signal.SIGTERM:
                sent.append(sig)
                exited = True
            elif sig == signal.SIGKILL:
                sent.append(sig)
                raise PermissionError(errno.EPERM, "Operation not permitted")
        process = SimpleNamespace(pid=45678, poll=poll)
        job = _Job("synthetic", process, [], str(self.cwd), 1)
        with patch("sugar_local_mcp.processes.os.killpg", side_effect=killpg):
            self.manager._terminate_group(job)
            self.manager._terminate_group(job)
        self.assertTrue(reaped)
        self.assertEqual(sent, [signal.SIGTERM])
        self.assertIsNone(job.cleanup_error)

    @unittest.skipUnless(os.name == "posix", "POSIX process groups")
    def test_exited_leader_does_not_prevent_escalating_a_surviving_group(self) -> None:
        process = SimpleNamespace(pid=45678, poll=lambda: -signal.SIGTERM)
        job = _Job("synthetic", process, [], str(self.cwd), 1)
        with patch("sugar_local_mcp.processes.os.killpg") as killpg:
            self.manager._terminate_group(job)
        self.assertIn((45678, signal.SIGTERM), [call.args for call in killpg.call_args_list])
        self.assertIn((45678, signal.SIGKILL), [call.args for call in killpg.call_args_list])
        self.assertIsNone(job.cleanup_error)

    @unittest.skipUnless(os.name == "posix", "POSIX process groups")
    def test_permission_denial_is_reported_even_after_the_leader_exits(self) -> None:
        process = SimpleNamespace(pid=45678, poll=lambda: -signal.SIGTERM)
        job = _Job("synthetic", process, [], str(self.cwd), 1)
        with patch("sugar_local_mcp.processes.os.killpg", side_effect=PermissionError(
                errno.EPERM, "Operation not permitted")) as killpg:
            self.manager._terminate_group(job)
        self.assertIn((45678, signal.SIGKILL), [call.args for call in killpg.call_args_list])
        self.assertIn("Operation not permitted", job.cleanup_error)

    def test_bounded_output_and_expired_cursor(self) -> None:
        self.manager.close()
        self.manager = ProcessManager(max_output_bytes=64, terminate_grace_seconds=0.05)
        job = self.start("import sys; sys.stdout.write('x'*1000)")
        result = self.wait(job["job_id"])
        self.assertTrue(result["truncated"])
        self.assertEqual(result["end_cursor"], 1000)
        self.assertEqual(result["base_cursor"], 936)
        self.assertEqual(result["retained_bytes"], 64)
        self.assertEqual(output(result), "x" * 64)

    def test_utf8_pagination(self) -> None:
        text = "a中文🙂z" * 20
        job = self.start(f"import sys; sys.stdout.write({text!r})")
        self.wait(job["job_id"])
        cursor = 0
        received = ""
        while True:
            result = self.manager.read(job["job_id"], cursor=cursor, max_bytes=5)
            page = output(result)
            self.assertLessEqual(len(page.encode()), 5)
            self.assertNotIn("�", page)
            received += page
            cursor = result["next_cursor"]
            if not result["has_more"]:
                break
            self.assertTrue(page)
        self.assertEqual(received, text)
        self.assertEqual(cursor, len(text.encode()))
        result = self.manager.read(job["job_id"], cursor=2, max_bytes=8)
        self.assertTrue(result["cursor_adjusted"])
        self.assertNotIn("�", output(result))

    def test_utf8_truncation_keeps_character_boundaries(self) -> None:
        self.manager.close()
        self.manager = ProcessManager(max_output_bytes=9, terminate_grace_seconds=0.05)
        job = self.start("import sys; sys.stdout.write('🙂'*20)")
        result = self.wait(job["job_id"])
        self.assertEqual(output(result), "🙂🙂")
        self.assertEqual(result["retained_bytes"], 8)
        self.assertEqual(result["base_cursor"], 72)

    def test_split_utf8_and_binary_output(self) -> None:
        job = self.start(
            "import os,time; "
            "os.write(1,b'\\xe4'); time.sleep(.05); "
            "os.write(1,b'\\xb8\\xad\\xff')"
        )
        self.assertEqual(output(self.wait(job["job_id"])), "中�")

    def test_credentials_not_inherited(self) -> None:
        self.manager.close()
        with patch.dict(os.environ, {"CONTROL_PLANE_API_KEY": "synthetic-secret", "OPENAI_API_KEY": "synthetic-secret", "AWS_SECRET_ACCESS_KEY": "synthetic-secret", "SUGAR_ALLOWED": "explicit"}):
            self.manager = ProcessManager(env_allow_names=["SUGAR_ALLOWED"], terminate_grace_seconds=0.05)
        job = self.start(
            "import os,json; print(json.dumps({k:os.environ.get(k) for k in "
            "['CONTROL_PLANE_API_KEY','OPENAI_API_KEY','AWS_SECRET_ACCESS_KEY','SUGAR_ALLOWED']}))"
        )
        values = json.loads(output(self.wait(job["job_id"])))
        self.assertIsNone(values["CONTROL_PLANE_API_KEY"])
        self.assertIsNone(values["OPENAI_API_KEY"])
        self.assertIsNone(values["AWS_SECRET_ACCESS_KEY"])
        self.assertEqual(values["SUGAR_ALLOWED"], "explicit")

    def test_spawn_failure_does_not_consume_capacity(self) -> None:
        self.manager.close()
        self.manager = ProcessManager(max_running=1, max_jobs=1, terminate_grace_seconds=0.05)
        with self.assertRaises(ProcessError):
            self.manager.start(["/nonexistent/sugar-local-command"], self.cwd)
        self.assertEqual(self.manager.list()["jobs"], [])
        job = self.start("print('ok')")
        self.assertEqual(output(self.wait(job["job_id"])), "ok\n")

    def test_capacity_and_retention(self) -> None:
        self.manager.close()
        self.manager = ProcessManager(max_running=1, max_jobs=1, terminate_grace_seconds=0.05)
        first = self.start("import time; time.sleep(20)")
        with self.assertRaises(ProcessError):
            self.start("print('blocked')")
        self.manager.stop(first["job_id"])
        self.wait(first["job_id"])
        second = self.start("print('replacement')")
        self.wait(second["job_id"])
        with self.assertRaises(ProcessError):
            self.manager.read(first["job_id"])
        self.assertEqual(len(self.manager.list()["jobs"]), 1)

    @unittest.skipUnless(os.name == "posix", "POSIX process groups")
    def test_term_ignored_is_escalated(self) -> None:
        job = self.start("import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); print('ready',flush=True); time.sleep(20)")
        deadline = time.monotonic() + 3
        while "ready" not in output(self.manager.read(job["job_id"])) and time.monotonic() < deadline:
            time.sleep(0.01)
        self.manager.stop(job["job_id"])
        self.assertEqual(self.wait(job["job_id"])["exit_code"], -signal.SIGKILL)

    @unittest.skipUnless(os.name == "posix", "POSIX process groups")
    def test_normal_exit_cleans_background_child(self) -> None:
        child_file = self.cwd / "child-finished"
        child_code = f"import time,pathlib; time.sleep(1); pathlib.Path({str(child_file)!r}).write_text('leaked')"
        code = f"import subprocess,sys; subprocess.Popen([sys.executable,'-c',{child_code!r}]); print('parent done')"
        job = self.start(code)
        result = self.wait(job["job_id"])
        self.assertEqual(result["state"], "exited")
        self.assertTrue(result["output_complete"])
        time.sleep(1.1)
        self.assertFalse(child_file.exists())

    def test_close_stops_running_jobs(self) -> None:
        job = self.start("import time; time.sleep(20)")
        self.manager.close()
        result = self.manager.read(job["job_id"])
        self.assertEqual(result["state"], "stopped")
        self.assertFalse(result["process_running"])
        with self.assertRaises(ProcessError):
            self.start("print('closed')")

    def test_validation_and_no_implicit_shell(self) -> None:
        with self.assertRaises(ProcessError):
            self.manager.start("echo hello", self.cwd)
        with self.assertRaises(ProcessError):
            self.manager.start([sys.executable], self.cwd, timeout_seconds=-1)
        with self.assertRaises(ProcessError):
            self.manager.start([sys.executable], self.cwd / "missing")
        job = self.manager.start([sys.executable, "-c", "import sys; print(sys.argv[1])", "$(echo unsafe)"], self.cwd)
        self.assertEqual(output(self.wait(job["job_id"])), "$(echo unsafe)\n")
        with self.assertRaises(ProcessError):
            self.manager.read(job["job_id"], cursor=100000)


if __name__ == "__main__":
    unittest.main()
