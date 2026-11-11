import errno
import json
import os
from pathlib import Path
import socket
import stat
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from sugar_local_mcp import diagnostics as diag


class _MemoryConnection:
    """One bounded wire request, without requiring an AF_UNIX-capable host."""
    def __init__(self, operation):
        self.incoming = diag._encode({"operation": operation}, diag.MAX_REQUEST_BYTES)
        self.outgoing = bytearray()
        self.closed = False

    def settimeout(self, timeout):
        pass

    def recv(self, limit):
        if self.closed:
            raise OSError("closed")
        chunk, self.incoming = self.incoming[:limit], self.incoming[limit:]
        return chunk

    def sendall(self, data):
        if self.closed:
            raise OSError("closed")
        self.outgoing.extend(data)

    def shutdown(self, how):
        self.closed = True

    def close(self):
        self.closed = True

    def result(self):
        return json.loads(self.outgoing)


def _memory_worker(service, operation):
    connection = _MemoryConnection(operation)
    worker = threading.Thread(target=service._handle, args=(connection,), daemon=True)
    with service._connections_lock:
        if not service._connection_slots.acquire(blocking=False):
            raise AssertionError("Test exhausted its connection allowance")
        service._connections.add(connection)
        service._workers.add(worker)
        worker.start()
    return connection, worker


class DiagnosticsWithoutSocketsTests(unittest.TestCase):
    def test_permission_lane_is_independent_of_four_busy_execution_slots(self):
        entered, release = threading.Event(), threading.Event()
        count_lock = threading.Lock()
        count = 0
        def slow():
            nonlocal count
            with count_lock:
                count += 1
                if count == 4:
                    entered.set()
            release.wait(3)
            return {"finished": True}
        service = diag.RuntimeDiagnostics(Path("/unused"), {
            "slow": slow, "ordinary": lambda: {"ran": True},
            "permissions_status": lambda: {"checked": True},
            "permissions_request": lambda: {"request_sent": True},
        })
        workers = [_memory_worker(service, "slow") for _ in range(4)]
        try:
            self.assertTrue(entered.wait(1))
            for operation, field in [("permissions_status", "checked"),
                                     ("permissions_request", "request_sent")]:
                connection, worker = _memory_worker(service, operation)
                worker.join(0.5)
                self.assertFalse(worker.is_alive(), operation)
                self.assertTrue(connection.result()[field])
            connection, worker = _memory_worker(service, "ordinary")
            worker.join(0.5)
            self.assertFalse(worker.is_alive())
            self.assertEqual(connection.result()["error"]["code"], "busy")
        finally:
            release.set()
            for _, worker in workers:
                worker.join(1)
            service.close()
        self.assertTrue(all(connection.result()["finished"] for connection, _ in workers))
        self.assertFalse(service._workers)
        self.assertFalse(service._connections)

    def test_close_prevents_new_handler_admission_after_request_parse(self):
        parsed, continue_worker = threading.Event(), threading.Event()
        calls = []
        service = diag.RuntimeDiagnostics(Path("/unused"), {"late": lambda: calls.append(True) or {}})
        original_receive = diag._receive
        def hold_parsed_request(connection, limit, deadline):
            value = original_receive(connection, limit, deadline)
            parsed.set()
            continue_worker.wait(2)
            return value
        closing = threading.Thread(target=service.close)
        with patch.object(diag, "_receive", side_effect=hold_parsed_request):
            connection, worker = _memory_worker(service, "late")
            try:
                self.assertTrue(parsed.wait(1))
                closing.start()
                self.assertTrue(service._stop.wait(1))
            finally:
                continue_worker.set()
                worker.join(1)
                if closing.ident is not None:
                    closing.join(1)
                service.close()
        self.assertEqual(calls, [])
        self.assertFalse(worker.is_alive())
        self.assertTrue(connection.closed)
        self.assertFalse(service._workers)

    @unittest.skipUnless(diag.fcntl is not None, "Private ownership lock requires POSIX")
    def test_bounded_shutdown_retains_lock_until_final_handler_finishes(self):
        entered, release = threading.Event(), threading.Event()
        def slow():
            entered.set()
            release.wait(3)
            return {"finished": True}
        with tempfile.TemporaryDirectory(prefix="sugar-ipc-", dir="/tmp") as root:
            service = diag.RuntimeDiagnostics(Path(root), {"slow": slow})
            service._acquire_lock(Path(root))
            connection, worker = _memory_worker(service, "slow")
            contender = diag.RuntimeDiagnostics(Path(root), {})
            try:
                self.assertTrue(entered.wait(1))
                began = time.monotonic()
                with patch.object(diag, "_CLOSE_TIMEOUT", 0.03):
                    service.close()
                self.assertLess(time.monotonic() - began, 0.5)
                self.assertTrue(connection.closed)
                self.assertEqual(service.shutdown_error["code"], "handlers_still_running")
                with self.assertRaises(diag._ProtocolError) as failure:
                    contender._acquire_lock(Path(root))
                self.assertEqual(failure.exception.code, "already_running")
            finally:
                release.set()
                worker.join(1)
                service.close()
            self.assertFalse(worker.is_alive())
            self.assertIsNone(service._lock_fd)
            self.assertIsNone(service.shutdown_error)
            try:
                contender._acquire_lock(Path(root))
                self.assertIsNotNone(contender._lock_fd)
            finally:
                contender.close()

    def test_unsafe_state_directory_is_not_changed(self):
        with tempfile.TemporaryDirectory(prefix="sugar-ipc-", dir="/tmp") as root:
            state = Path(root) / "state"
            state.mkdir(mode=0o755)
            state.chmod(0o755)
            service = diag.RuntimeDiagnostics(state, {})
            self.assertFalse(service.start())
            self.assertEqual(service.startup_error["code"], "unsafe_state_directory")
            self.assertEqual(stat.S_IMODE(state.stat().st_mode), 0o755)
            service.close()

    def test_startup_os_error_is_contained_without_exception_text(self):
        with tempfile.TemporaryDirectory(prefix="sugar-ipc-", dir="/tmp") as root:
            with patch.object(diag.socket, "socket", side_effect=PermissionError("secret-native-diagnostic")):
                with diag.RuntimeDiagnostics(Path(root), {}) as service:
                    self.assertFalse(service.running)
                    self.assertEqual(service.startup_error["code"], "startup_failed")
                    self.assertNotIn("secret-native", str(service.startup_error))
            self.assertFalse((Path(root)/diag.SOCKET_NAME).exists())

    def test_regular_file_at_socket_path_is_never_removed(self):
        with tempfile.TemporaryDirectory(prefix="sugar-ipc-", dir="/tmp") as root:
            target = Path(root) / diag.SOCKET_NAME
            target.write_bytes(b"unrelated-file")
            service = diag.RuntimeDiagnostics(Path(root), {})
            self.assertFalse(service.start())
            self.assertEqual(service.startup_error["code"], "unsafe_socket")
            service.close()
            self.assertEqual(target.read_bytes(), b"unrelated-file")

    def test_client_rejects_oversized_or_ambiguous_requests_before_connecting(self):
        with patch.object(diag.socket, "socket") as connect:
            result = diag.request_local(Path("/unused"), "permissions_status", {"body":"x"*diag.MAX_REQUEST_BYTES})
            self.assertEqual(result["error"]["code"], "message_too_large")
            result = diag.request_local(Path("/unused"), "permissions_status", {"operation":"permissions_request"})
            self.assertEqual(result["error"]["code"], "invalid_request")
            result = diag.request_local(Path("/unused"), "permissions_status", timeout=float("inf"))
            self.assertEqual(result["error"]["code"], "invalid_timeout")
            connect.assert_not_called()

    def test_expected_errors_keep_setup_guidance_but_hide_credentials(self):
        hint = "未找到 Google Chrome。请先安装到 /Applications。"
        self.assertEqual(diag._expected_error_message(ValueError(hint)), hint)
        for detail in ["api_key=not-for-output", "token=not-for-output", "sk-fake-private-value"]:
            result = diag._expected_error_message(ValueError(detail))
            self.assertNotIn("not-for-output", result)
            self.assertNotIn("private-value", result)
        self.assertNotIn("private-query", diag._expected_error_message(ValueError("导航失败 https://example.test/?private-query=value")))
        self.assertLessEqual(len(diag._expected_error_message(ValueError("x"*2000))),600)


@unittest.skipUnless(os.name == "posix" and hasattr(socket, "AF_UNIX"), "Unix sockets require POSIX")
class DiagnosticsIPCTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Some managed Linux executors deny socket creation at the kernel
        # boundary. Skip only that explicit environment EPERM, never an error
        # from the implementation or a failed bind/listen/request.
        try:
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        except PermissionError as error:
            if error.errno == errno.EPERM:
                raise unittest.SkipTest("Executor denies AF_UNIX socket creation (EPERM); run these on macOS")
            raise
        probe.close()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="sugar-ipc-", dir="/tmp")
        self.addCleanup(self.temporary.cleanup)
        self.state = Path(self.temporary.name).resolve()
        self.path = self.state / diag.SOCKET_NAME

    def service(self, handlers, **options):
        result = diag.RuntimeDiagnostics(self.state, handlers, **options)
        self.addCleanup(result.close)
        self.assertTrue(result.start(), result.startup_error)
        return result

    def wire_request(self, raw):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(2)
            connection.connect(str(self.path))
            connection.sendall(raw)
            data = bytearray()
            while b"\n" not in data:
                chunk = connection.recv(4096)
                if not chunk:
                    break
                data.extend(chunk)
            return json.loads(bytes(data).split(b"\n",1)[0])

    def test_roundtrip_uses_server_process_and_private_permissions(self):
        main_thread = threading.get_ident()
        service = self.service({"permissions_status": lambda: {
            "pid":os.getpid(), "scope":"current_process", "thread":threading.get_ident(),
            "accessibility":False, "screen_capture":None,
        }})
        result = diag.request_local(self.state, "permissions_status")
        self.assertTrue(result["ok"])
        self.assertEqual(result["pid"], os.getpid())
        self.assertNotEqual(result["thread"], main_thread)
        self.assertIsNone(result["screen_capture"])
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertTrue(service.start())
        service.close()
        service.close()
        self.assertFalse(self.path.exists())

    def test_read_status_never_invokes_request_handler(self):
        calls = []
        self.service({"permissions_status": lambda: {"screen_capture":False},
                      "permissions_request": lambda kind: calls.append(kind) or {"request_sent":True}})
        diag.request_local(self.state, "permissions_status")
        self.assertEqual(calls, [])
        result = diag.request_local(self.state, "permissions_request", {"kind":"screen_capture"})
        self.assertTrue(result["request_sent"])
        self.assertEqual(calls, ["screen_capture"])

    def test_status_remains_available_while_another_handler_waits(self):
        entered, release = threading.Event(), threading.Event()
        def waiting():
            entered.set()
            release.wait(2)
            return {"finished":True}
        self.service({"waiting":waiting, "permissions_status":lambda:{"ready":True}})
        replies = []
        worker = threading.Thread(target=lambda: replies.append(diag.request_local(self.state,"waiting",timeout=3)))
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            result = diag.request_local(self.state,"permissions_status",timeout=0.5)
            self.assertTrue(result["ready"])
        finally:
            release.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertTrue(replies[0]["finished"])

    def test_permissions_remain_available_with_four_slow_office_handlers(self):
        all_entered, release = threading.Event(), threading.Event()
        count_lock = threading.Lock()
        entered = 0
        def slow():
            nonlocal entered
            with count_lock:
                entered += 1
                if entered == 4:
                    all_entered.set()
            release.wait(4)
            return {"finished": True}
        service = self.service({
            "office_verify": slow,
            "permissions_status": lambda: {"ready": True},
            "permissions_request": lambda: {"request_sent": True},
            "office_status": lambda: {"detected": True},
        }, max_workers=4)
        replies = []
        clients = [threading.Thread(target=lambda: replies.append(
            diag.request_local(self.state, "office_verify", timeout=5))) for _ in range(4)]
        for client in clients:
            client.start()
        try:
            self.assertTrue(all_entered.wait(1))
            began = time.monotonic()
            result = diag.request_local(self.state, "permissions_status", timeout=0.75)
            self.assertTrue(result["ready"], result)
            self.assertLess(time.monotonic() - began, 0.75)
            self.assertTrue(diag.request_local(self.state, "permissions_request", timeout=0.75)["request_sent"])
            # Ordinary execution remains bounded at four; the extra lane is
            # deliberately not available to arbitrary handler names.
            busy = diag.request_local(self.state, "office_status", timeout=0.75)
            self.assertEqual(busy["error"]["code"], "busy")
        finally:
            release.set()
            for client in clients:
                client.join(2)
        service.close()
        self.assertTrue(all(not client.is_alive() for client in clients))
        self.assertEqual(len(replies), 4)
        self.assertTrue(all(reply.get("finished") for reply in replies))
        self.assertFalse(service._workers)

    def test_close_refuses_a_parsed_request_not_yet_admitted(self):
        parsed, continue_worker = threading.Event(), threading.Event()
        calls = []
        service = self.service({"late": lambda: calls.append(True) or {"ran": True}})
        original_receive = diag._receive
        def held_receive(connection, limit, deadline):
            value = original_receive(connection, limit, deadline)
            if threading.current_thread().name == "sugar-desktop-request":
                parsed.set()
                continue_worker.wait(2)
            return value
        client = threading.Thread(target=lambda: diag.request_local(self.state, "late", timeout=3))
        closing = threading.Thread(target=service.close)
        with patch.object(diag, "_receive", side_effect=held_receive):
            client.start()
            try:
                self.assertTrue(parsed.wait(1))
                closing.start()
                self.assertTrue(service._stop.wait(1))
            finally:
                continue_worker.set()
                client.join(2)
                if closing.ident is not None:
                    closing.join(2)
        self.assertEqual(calls, [])
        self.assertFalse(service._workers)
        self.assertFalse(self.path.exists())
        self.assertIsNone(service._lock_fd)

    def test_close_is_bounded_and_holds_ownership_until_workers_finish(self):
        entered, release = threading.Event(), threading.Event()
        def slow():
            entered.set()
            release.wait(5)
            return {"finished": True}
        service = self.service({"slow": slow})
        client = threading.Thread(target=lambda: diag.request_local(self.state, "slow", timeout=4))
        client.start()
        workers = ()
        replacement = diag.RuntimeDiagnostics(self.state, {"status": lambda: {"ready": True}})
        self.addCleanup(replacement.close)
        try:
            self.assertTrue(entered.wait(1))
            workers = tuple(service._workers)
            began = time.monotonic()
            with patch.object(diag, "_CLOSE_TIMEOUT", 0.05):
                service.close()
            self.assertLess(time.monotonic() - began, 0.5)
            self.assertFalse(self.path.exists())
            self.assertEqual(service.shutdown_error["code"], "handlers_still_running")
            self.assertIsNotNone(service._lock_fd)
            self.assertFalse(replacement.start())
            self.assertEqual(replacement.startup_error["code"], "already_running")
        finally:
            release.set()
            client.join(2)
            for worker in tuple(service._workers):
                worker.join(2)
        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertIsNone(service._lock_fd)
        self.assertTrue(replacement.start(), replacement.startup_error)
        self.assertTrue(diag.request_local(self.state, "status")["ready"])

    def test_total_connections_remain_bounded_even_before_request_parsing(self):
        service = self.service({"permissions_status": lambda: {"ready": True}},
                               max_workers=1, request_timeout=2)
        maximum = 1 + diag._CONTROL_WORKERS + diag._PENDING_CONNECTIONS
        clients = []
        try:
            for _ in range(maximum):
                connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                connection.settimeout(1)
                connection.connect(str(self.path))
                connection.sendall(b'{"operation":')
                clients.append(connection)
            deadline = time.monotonic() + 0.5
            while len(service._connections) < maximum and time.monotonic() < deadline:
                time.sleep(0.005)
            self.assertEqual(len(service._connections), maximum)
            result = self.wire_request(b'{"operation":"permissions_status"}\n')
            self.assertEqual(result["error"]["code"], "busy")
            self.assertLessEqual(len(service._workers), maximum)
        finally:
            for connection in clients:
                connection.close()

    def test_second_instance_does_not_take_over_live_socket(self):
        self.service({"permissions_status":lambda:{"owner":"first"}})
        original = (self.path.stat().st_dev,self.path.stat().st_ino)
        second = diag.RuntimeDiagnostics(self.state,{})
        self.addCleanup(second.close)
        self.assertFalse(second.start())
        self.assertEqual(second.startup_error["code"],"already_running")
        second.close()
        self.assertEqual((self.path.stat().st_dev,self.path.stat().st_ino),original)
        self.assertEqual(diag.request_local(self.state,"permissions_status")["owner"],"first")

    def test_active_socket_without_our_lock_is_not_taken_over(self):
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as existing:
            existing.bind(str(self.path))
            self.path.chmod(0o600)
            existing.listen(2)
            original = self.path.stat().st_ino
            service = diag.RuntimeDiagnostics(self.state,{})
            self.addCleanup(service.close)
            self.assertFalse(service.start())
            self.assertEqual(service.startup_error["code"],"already_running")
            self.assertEqual(self.path.stat().st_ino,original)

    def test_stale_owned_socket_can_be_replaced(self):
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as stale:
            stale.bind(str(self.path))
        self.assertTrue(self.path.exists())
        self.service({"permissions_status":lambda:{"alive":True}})
        self.assertTrue(diag.request_local(self.state,"permissions_status")["alive"])

    def test_close_leaves_a_replacement_socket_untouched(self):
        original = self.service({})
        self.path.rename(self.state/"retired.sock")
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as replacement:
            replacement.bind(str(self.path))
            replacement.listen(2)
            identity = self.path.stat().st_ino
            original.close()
            self.assertTrue(self.path.exists())
            self.assertEqual(self.path.stat().st_ino,identity)

    def test_bad_requests_and_oversized_requests_do_not_invoke_handlers(self):
        calls = []
        self.service({"permissions_status":lambda: calls.append(True) or {"value":True}})
        for raw, code in [(b"[]\n","invalid_json"), (b"{bad}\n","invalid_json"),
                          (b'{"operation":"unknown"}\n',"unknown_operation"),
                          (b'{"operation":42}\n',"invalid_operation"),
                          (b'{"operation":"permissions_status","body":"'+b"x"*17000+b'"}\n',"message_too_large")]:
            with self.subTest(code=code):
                result=self.wire_request(raw)
                self.assertFalse(result["ok"])
                self.assertEqual(result["error"]["code"],code)
        self.assertEqual(calls,[])

    def test_handler_exceptions_and_large_results_are_bounded(self):
        def failure():
            raise RuntimeError("private-runtime-key-must-not-leak")
        self.service({"failure":failure, "large":lambda:{"body":"x"*diag.MAX_RESPONSE_BYTES}})
        failed=diag.request_local(self.state,"failure")
        self.assertEqual(failed["error"]["code"],"handler_failed")
        self.assertNotIn("runtime-key",str(failed))
        self.assertNotIn("Traceback",str(failed))
        large=diag.request_local(self.state,"large")
        self.assertEqual(large["error"]["code"],"invalid_response")
        self.assertLess(len(json.dumps(large)),1000)

    def test_expected_handler_errors_preserve_actionable_setup_message(self):
        def missing_chrome():
            raise ValueError("未找到 Google Chrome。请先将它安装到 /Applications。")
        def sensitive_error():
            raise ValueError("api_key=never-display-this")
        self.service({"missing_chrome":missing_chrome,"sensitive_error":sensitive_error})
        result=diag.request_local(self.state,"missing_chrome")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"],"invalid_request_or_environment")
        self.assertIn("安装到 /Applications",result["error"]["message"])
        result=diag.request_local(self.state,"sensitive_error")
        self.assertNotIn("never-display-this",str(result))

    def test_timeout_is_unknown_and_does_not_stop_service(self):
        release=threading.Event()
        self.service({"wait":lambda: release.wait(1) or {"late":True},
                      "permissions_status":lambda:{"alive":True}})
        try:
            result=diag.request_local(self.state,"wait",timeout=0.05)
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"],"timeout")
            self.assertTrue(diag.request_local(self.state,"permissions_status")["alive"])
        finally:
            release.set()

    def test_partial_request_times_out_without_hanging_worker(self):
        self.service({},request_timeout=0.05)
        result=self.wire_request(b'{"operation":')
        self.assertEqual(result["error"]["code"],"timeout")

    def test_client_rejects_malformed_and_oversized_responses(self):
        responses=[b'{"missing_ok":true}\n', b'{"ok":false,"error":"not-an-object"}\n',
                   b'[]\n', b'{"ok":true,"body":"'+b'x'*diag.MAX_RESPONSE_BYTES+b'"}\n']
        for index,response in enumerate(responses):
            with self.subTest(index=index):
                state=self.state/str(index)
                state.mkdir(mode=0o700)
                path=state/diag.SOCKET_NAME
                with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as listener:
                    listener.bind(str(path)); path.chmod(0o600); listener.listen(1)
                    listener.settimeout(2)
                    def respond():
                        try:
                            connection,_=listener.accept()
                            with connection:
                                connection.recv(16384)
                                connection.sendall(response)
                        except OSError:
                            pass
                    worker=threading.Thread(target=respond,daemon=True)
                    worker.start()
                    result=diag.request_local(state,"permissions_status",timeout=1)
                    worker.join(1)
                self.assertFalse(result["ok"])
                self.assertIn(result["error"]["code"],{"invalid_response","invalid_json","message_too_large"})


if __name__ == "__main__":
    unittest.main()
