"""Bounded, private Unix-socket requests executed in the MCP server process.

This channel is for small status/control JSON messages, never screenshots or
project contents. A failure to start it is recorded rather than raised, so
normal MCP tools remain usable. One newline-delimited request is handled per
connection, and handlers receive the remaining request fields as keywords.
"""
from __future__ import annotations

import errno
try:
    import fcntl
except ImportError:  # The core MCP package can still import on non-POSIX hosts.
    fcntl = None
import json
import math
import os
from pathlib import Path
import re
import socket
import stat
import sys
import threading
import time
from typing import Callable, Mapping


SOCKET_NAME = "desktop-control.sock"
MAX_REQUEST_BYTES = 16 * 1024
MAX_RESPONSE_BYTES = 128 * 1024
_OPERATION = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_CONTROL_OPERATIONS = frozenset({"permissions_status", "permissions_request"})
_CONTROL_WORKERS = 2
_PENDING_CONNECTIONS = 4
_CLOSE_TIMEOUT = 2.0


class _ProtocolError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def _error(code: str, message: str) -> dict:
    return {"ok": False, "error": {"code": code, "message": message}}


def _expected_error_message(error: ValueError) -> str:
    """Keep actionable setup/input errors without returning debug payloads.

    Registered handlers use ValueError for user-correctable validation or
    installation errors. Unexpected exception classes remain fully opaque.
    """
    lines = str(error).splitlines()
    message = lines[0] if lines else ""
    message = "".join(" " if ord(char) < 32 or ord(char) == 127 else char
                      for char in message[:2048]).strip()
    # A declared input error is not permission to expose an included key/header.
    if re.search(r"(?i)\bsk-[A-Za-z0-9_-]{6,}|(?:authorization|api[_ -]?key|token|password|passwd|secret|cookie|credential)\s*[:=]|traceback", message):
        return "诊断参数或本机环境不符合要求，请检查配置后重试。"
    message = re.sub(r"(?:https?|wss?)://[^\s]+", "[地址已隐藏]", message)
    return message[:600] or "诊断参数或本机环境不符合要求，请检查配置后重试。"


def _encode(value: dict, limit: int) -> bytes:
    try:
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise _ProtocolError("invalid_json", "本地诊断消息不是有效的 JSON 对象。") from None
    if len(raw) > limit:
        raise _ProtocolError("message_too_large", "本地诊断消息超过大小限制。")
    return raw + b"\n"


def _receive(connection: socket.socket, limit: int, deadline: float) -> dict:
    data = bytearray()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _ProtocolError("timeout", "本地诊断请求超时，状态尚未确认。")
        connection.settimeout(remaining)
        try:
            chunk = connection.recv(min(8192, limit + 2 - len(data)))
        except socket.timeout:
            raise _ProtocolError("timeout", "本地诊断请求超时，状态尚未确认。") from None
        if not chunk:
            raise _ProtocolError("incomplete_message", "本地诊断连接提前关闭，状态尚未确认。")
        data.extend(chunk)
        newline = data.find(b"\n")
        if newline >= 0:
            if newline > limit:
                raise _ProtocolError("message_too_large", "本地诊断消息超过大小限制。")
            if data[newline + 1:].strip():
                raise _ProtocolError("invalid_json", "每个本地诊断连接只能发送一条消息。")
            raw = bytes(data[:newline])
            break
        if len(data) > limit:
            raise _ProtocolError("message_too_large", "本地诊断消息超过大小限制。")
    try:
        value = json.loads(raw.decode("utf-8"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (UnicodeError, ValueError, RecursionError):
        raise _ProtocolError("invalid_json", "本地诊断消息不是有效的 UTF-8 JSON。") from None
    if not isinstance(value, dict):
        raise _ProtocolError("invalid_json", "本地诊断消息必须是 JSON 对象。")
    return value


def _private_directory(state_dir: Path, *, create: bool) -> Path:
    directory = Path(state_dir).expanduser().absolute()
    if create:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o777 != 0o700:
        raise _ProtocolError("unsafe_state_directory", "本地诊断需要当前用户独占、权限为 0700 的状态目录。")
    path = directory / SOCKET_NAME
    maximum = 103 if sys.platform == "darwin" else 107
    if len(os.fsencode(path)) > maximum:
        raise _ProtocolError("socket_path_too_long", "状态目录路径过长，无法创建本地诊断入口。")
    return directory


def _socket_identity(path: Path):
    info = path.lstat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.geteuid():
        raise _ProtocolError("unsafe_socket", "本地诊断路径不是当前用户拥有的套接字。")
    return info.st_dev, info.st_ino


class RuntimeDiagnostics:
    def __init__(self, state_dir: Path, handlers: Mapping[str, Callable], *,
                 request_timeout: float = 5.0, max_workers: int = 4):
        self.state_dir = Path(state_dir)
        self.handlers = dict(handlers)
        self.request_timeout = request_timeout
        self.startup_error: dict | None = None
        self.shutdown_error: dict | None = None
        self._maximum_workers = max_workers
        self._slots = threading.BoundedSemaphore(4)
        self._control_slots = threading.BoundedSemaphore(_CONTROL_WORKERS)
        self._connection_slots = threading.BoundedSemaphore(4 + _CONTROL_WORKERS + _PENDING_CONNECTIONS)
        self._stop = threading.Event()
        self._listener: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._path: Path | None = None
        self._identity: tuple[int, int] | None = None
        self._lock_fd: int | None = None
        self._connections: set[socket.socket] = set()
        self._workers: set[threading.Thread] = set()
        self._connections_lock = threading.RLock()

    @property
    def running(self) -> bool:
        return self._listener is not None and self._thread is not None and self._thread.is_alive()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.close()

    def _acquire_lock(self, directory: Path) -> None:
        path = directory / ".desktop-control.lock"
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
                raise _ProtocolError("unsafe_lock", "本地诊断锁文件不属于当前服务用户。")
            os.fchmod(descriptor, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise _ProtocolError("already_running", "已有后台服务使用此状态目录，本次不会接管。") from None
        except BaseException:
            os.close(descriptor)
            raise
        self._lock_fd = descriptor

    def _remove_stale_socket(self, path: Path) -> None:
        try:
            identity = _socket_identity(path)
        except FileNotFoundError:
            return
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.settimeout(0.25)
            try:
                probe.connect(str(path))
            except OSError as error:
                if error.errno not in {errno.ECONNREFUSED, errno.ENOENT}:
                    raise _ProtocolError("socket_busy", "现有本地诊断入口状态未明，本次不会接管。") from None
            else:
                raise _ProtocolError("already_running", "已有后台服务使用本地诊断入口，本次不会接管。")
        finally:
            probe.close()
        # An active instance holding our lock could never reach this branch.
        # Also compare inode for processes that do not use this module's lock.
        try:
            if _socket_identity(path) != identity:
                raise _ProtocolError("socket_changed", "本地诊断入口已变化，请稍后重试。")
            path.unlink()
        except FileNotFoundError:
            pass

    def start(self) -> bool:
        if self.running:
            return True
        self.startup_error = None
        try:
            with self._connections_lock:
                if self._workers:
                    raise _ProtocolError("closing", "上一次本地诊断仍在结束，请稍后重新启动。")
            if not hasattr(socket, "AF_UNIX") or os.name != "posix" or fcntl is None:
                raise _ProtocolError("unsupported_platform", "此平台不支持私有 Unix 诊断入口。")
            if (isinstance(self._maximum_workers, bool) or not isinstance(self._maximum_workers, int)
                    or not 1 <= self._maximum_workers <= 16
                    or isinstance(self.request_timeout, bool) or not isinstance(self.request_timeout, (int, float))
                    or not math.isfinite(self.request_timeout) or not 0 < self.request_timeout <= 60):
                raise _ProtocolError("invalid_configuration", "本地诊断服务参数无效。")
            if any(not isinstance(name, str) or not _OPERATION.fullmatch(name) or not callable(handler)
                   for name, handler in self.handlers.items()):
                raise _ProtocolError("invalid_configuration", "本地诊断处理器配置无效。")
            self._slots = threading.BoundedSemaphore(self._maximum_workers)
            self._control_slots = threading.BoundedSemaphore(_CONTROL_WORKERS)
            self._connection_slots = threading.BoundedSemaphore(
                self._maximum_workers + _CONTROL_WORKERS + _PENDING_CONNECTIONS)
            self.shutdown_error = None
            directory = _private_directory(self.state_dir, create=True)
            self._path = directory / SOCKET_NAME
            self._acquire_lock(directory)
            self._remove_stale_socket(self._path)
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._listener = listener
            listener.bind(str(self._path))
            self._identity = _socket_identity(self._path)
            self._path.chmod(0o600)
            listener.listen(8)
            listener.settimeout(0.25)
            self._stop.clear()
            self._thread = threading.Thread(target=self._accept, name="sugar-desktop-diagnostics", daemon=True)
            self._thread.start()
            return True
        except _ProtocolError as error:
            self.startup_error = {"code": error.code, "message": error.message}
        except Exception:
            self.startup_error = {"code": "startup_failed", "message": "本地诊断入口未能启动，项目工具仍可继续使用。"}
        self.close()
        return False

    def _send(self, connection: socket.socket, result: dict) -> None:
        try:
            raw = _encode(result, MAX_RESPONSE_BYTES)
        except _ProtocolError:
            raw = _encode(_error("invalid_response", "后台诊断结果无效或超过大小限制。"), MAX_RESPONSE_BYTES)
        try:
            connection.settimeout(1.0)
            connection.sendall(raw)
        except OSError:
            pass

    def _accept(self) -> None:
        listener = self._listener
        if listener is None:
            return
        while not self._stop.is_set():
            try:
                connection, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            # Operation names are known only after reading a bounded request.
            # Keep parsing connections bounded separately from execution slots,
            # so slow Office probes cannot occupy the permission/status lane.
            if not self._connection_slots.acquire(blocking=False):
                self._send(connection, _error("busy", "本地诊断请求正在处理中，请稍后重试。"))
                connection.close()
                continue
            with self._connections_lock:
                if self._stop.is_set():
                    connection.close()
                    self._connection_slots.release()
                    return
                self._connections.add(connection)
                worker = threading.Thread(target=self._handle, args=(connection,),
                                          name="sugar-desktop-request", daemon=True)
                self._workers.add(worker)
                try:
                    # Starting under the lifecycle lock prevents close() from
                    # observing an unstarted thread and trying to join it.
                    worker.start()
                except Exception:
                    self._workers.discard(worker)
                    self._connections.discard(connection)
                    self._connection_slots.release()
                    connection.close()

    def _handle(self, connection: socket.socket) -> None:
        execution_slots = None
        try:
            value = _receive(connection, MAX_REQUEST_BYTES, time.monotonic() + self.request_timeout)
            operation = value.pop("operation", None)
            if not isinstance(operation, str) or not _OPERATION.fullmatch(operation):
                raise _ProtocolError("invalid_operation", "本地诊断操作名称无效。")
            handler = self.handlers.get(operation)
            if handler is None:
                raise _ProtocolError("unknown_operation", "后台服务不支持此诊断操作。")
            selected_slots = self._control_slots if operation in _CONTROL_OPERATIONS else self._slots
            if not selected_slots.acquire(blocking=False):
                raise _ProtocolError("busy", "本地诊断请求正在处理中，请稍后重试。")
            execution_slots = selected_slots
            with self._connections_lock:
                if self._stop.is_set():
                    raise _ProtocolError("closing", "本地诊断正在关闭，没有执行此请求。")
                # Admission is serialized with close(). A handler admitted
                # before shutdown may still finish; close() waits for workers
                # and retains the directory lock if a bounded wait expires.
            try:
                result = handler(**value)
            except ValueError as error:
                result = _error("invalid_request_or_environment", _expected_error_message(error))
            except Exception:
                result = _error("handler_failed", "后台未能完成该诊断操作，状态尚未确认。")
            if not isinstance(result, dict) or ("ok" in result and not isinstance(result["ok"], bool)):
                result = _error("invalid_response", "后台诊断处理器没有返回有效对象。")
            else:
                result = {"ok": True, **result}
            self._send(connection, result)
        except _ProtocolError as error:
            self._send(connection, _error(error.code, error.message))
        except Exception:
            self._send(connection, _error("connection_failed", "本地诊断请求未完成，状态尚未确认。"))
        finally:
            if execution_slots is not None:
                execution_slots.release()
            connection.close()
            with self._connections_lock:
                self._connections.discard(connection)
                self._connection_slots.release()
                self._workers.discard(threading.current_thread())
                self._release_directory_lock_if_idle()

    def _release_directory_lock_if_idle(self) -> None:
        # Also called by the last worker after a bounded close() returned. This
        # keeps a replacement service from overlapping an old in-flight handler.
        with self._connections_lock:
            if not self._stop.is_set() or self._workers:
                return
            self.shutdown_error = None
            if self._lock_fd is None:
                return
            descriptor, self._lock_fd = self._lock_fd, None
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    def close(self) -> None:
        deadline = time.monotonic() + _CLOSE_TIMEOUT
        with self._connections_lock:
            self._stop.set()
            listener, self._listener = self._listener, None
            thread, self._thread = self._thread, None
            connections = tuple(self._connections)
            workers = tuple(self._workers)
        if listener is not None:
            listener.close()
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(0, deadline - time.monotonic()))
        for worker in workers:
            if worker is not threading.current_thread():
                worker.join(timeout=max(0, deadline - time.monotonic()))
        if self._path is not None and self._identity is not None:
            try:
                if _socket_identity(self._path) == self._identity:
                    self._path.unlink()
            except (FileNotFoundError, OSError, _ProtocolError):
                pass
        self._identity = None
        with self._connections_lock:
            if self._workers:
                self.shutdown_error = {
                    "code": "handlers_still_running", "worker_count": len(self._workers),
                    "message": "诊断入口已关闭；仍有处理器正在结束，目录锁将保留至清理完成。",
                }
            else:
                self.shutdown_error = None
            self._release_directory_lock_if_idle()


def request_local(state_dir: Path, operation: str, payload: dict | None = None, *, timeout: float = 5.0) -> dict:
    """Call the actual MCP process, returning bounded errors instead of traces."""
    connection: socket.socket | None = None
    try:
        if not hasattr(socket, "AF_UNIX") or os.name != "posix":
            raise _ProtocolError("unsupported_platform", "此平台不支持私有 Unix 诊断入口。")
        if not isinstance(operation, str) or not _OPERATION.fullmatch(operation):
            raise _ProtocolError("invalid_operation", "本地诊断操作名称无效。")
        if (not isinstance(timeout, (int, float)) or isinstance(timeout, bool)
                or not math.isfinite(timeout) or not 0 < timeout <= 60):
            raise _ProtocolError("invalid_timeout", "本地诊断超时参数无效。")
        payload = {} if payload is None else payload
        if not isinstance(payload, dict) or "operation" in payload:
            raise _ProtocolError("invalid_request", "本地诊断参数无效。")
        raw = _encode({"operation": operation, **payload}, MAX_REQUEST_BYTES)
        directory = _private_directory(Path(state_dir), create=False)
        path = directory / SOCKET_NAME
        _socket_identity(path)
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise _ProtocolError("unsafe_socket", "本地诊断入口权限过宽，未发送请求。")
        deadline = time.monotonic() + timeout
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(timeout)
        connection.connect(str(path))
        connection.settimeout(max(0.001, deadline - time.monotonic()))
        connection.sendall(raw)
        result = _receive(connection, MAX_RESPONSE_BYTES, deadline)
        if not isinstance(result.get("ok"), bool):
            raise _ProtocolError("invalid_response", "后台诊断响应缺少有效状态。")
        if result["ok"] is False:
            error = result.get("error")
            if not isinstance(error, dict) or not isinstance(error.get("code"), str) or not isinstance(error.get("message"), str):
                raise _ProtocolError("invalid_response", "后台诊断错误响应格式无效。")
        return result
    except _ProtocolError as error:
        return _error(error.code, error.message)
    except (socket.timeout, TimeoutError):
        return _error("timeout", "本地诊断请求超时，状态尚未确认。")
    except Exception:
        return _error("runtime_unavailable", "后台服务尚未提供本地诊断入口，请先连接后重试。")
    finally:
        if connection is not None:
            connection.close()
