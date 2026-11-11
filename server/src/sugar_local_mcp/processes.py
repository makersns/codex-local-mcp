"""Bounded background processes for a single-user local development server.

``cwd`` and ``shell=False`` are execution conveniences, not an OS sandbox.
Commands have the privileges of the account running this module.  On POSIX,
ordinary descendants are terminated with their process group; code that
deliberately detaches requires a container/VM boundary instead.
"""

from __future__ import annotations

import codecs
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
import os
from pathlib import Path
import queue
import re
import secrets
import signal
import subprocess
import threading
import time
from typing import Any, Sequence


class ProcessError(ValueError):
    """A request cannot be performed by the process manager."""


_BASE_ENV_NAMES = frozenset(
    {
        "PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "WINDIR", "COMSPEC",
        "PATHEXT", "TMP", "TEMP", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE", "TZ",
    }
)
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MAX_OUTPUT_EVENTS = 1024
_ARGV_PREVIEW_BYTES = 2048


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _integer(value: Any, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProcessError(f"{label} must be an integer")
    if not minimum <= value <= maximum:
        raise ProcessError(f"{label} must be between {minimum} and {maximum}")
    return value


def _boundary_forward(data: bytes, index: int) -> int:
    while index < len(data) and data[index] & 0xC0 == 0x80:
        index += 1
    return index


def _boundary_backward(data: bytes, index: int) -> int:
    while 0 < index < len(data) and data[index] & 0xC0 == 0x80:
        index -= 1
    return index


def _argv_preview(argv: list[str]) -> tuple[list[str], bool]:
    remaining = _ARGV_PREVIEW_BYTES
    preview: list[str] = []
    for arg in argv:
        data = arg.encode("utf-8")
        if len(data) <= remaining:
            preview.append(arg)
            remaining -= len(data)
        else:
            if remaining >= 3:
                preview.append(data[:remaining - 3].decode("utf-8", errors="ignore") + "…")
            return preview, True
    return preview, False


@dataclass
class _Chunk:
    offset: int
    stream: str
    data: bytes


@dataclass
class _Job:
    job_id: str
    process: subprocess.Popen[bytes]
    argv: list[str]
    cwd: str
    timeout_seconds: float
    created_at: str = field(default_factory=_utc_now)
    state: str = "running"
    stop_reason: str | None = None
    finished_at: str | None = None
    chunks: deque[_Chunk] = field(default_factory=deque)
    output_end: int = 0
    retained_bytes: int = 0
    closed_readers: int = 0
    stdin_closed: bool = False
    stdin_close_requested: bool = False
    stdin_error: str | None = None
    cleanup_error: str | None = None
    input_queue: queue.Queue[tuple[bytes, bool] | None] = field(
        default_factory=lambda: queue.Queue(maxsize=8)
    )
    termination_lock: threading.Lock = field(default_factory=threading.Lock)
    readers: list[threading.Thread] = field(default_factory=list)
    input_thread: threading.Thread | None = None
    watcher: threading.Thread | None = None


class ProcessManager:
    """Manage background jobs and bounded, cursor-based output.

    Public methods are synchronous and thread-safe. ``start`` returns after
    spawning, not after completion. ``write_stdin`` queues bounded input; it
    does not wait for the child to consume that input. Output cursors count
    normalized UTF-8 bytes across interleaved stdout/stderr events. Invalid
    output bytes are replaced before assigning cursor offsets.
    """

    def __init__(
        self,
        max_running: int = 4,
        max_jobs: int = 100,
        max_output_bytes: int = 1024 * 1024,
        env_allow_names: Sequence[str] = (),
        terminate_grace_seconds: float = 1.0,
    ) -> None:
        self.max_running = _integer(max_running, "max_running", 1, 64)
        self.max_jobs = _integer(max_jobs, "max_jobs", self.max_running, 10000)
        self.max_output_bytes = _integer(
            max_output_bytes, "max_output_bytes", 4, 64 * 1024 * 1024
        )
        if (
            isinstance(terminate_grace_seconds, bool)
            or not isinstance(terminate_grace_seconds, (int, float))
            or not 0.01 <= terminate_grace_seconds <= 10
        ):
            raise ProcessError("terminate_grace_seconds must be between 0.01 and 10")
        self.terminate_grace_seconds = float(terminate_grace_seconds)
        if isinstance(env_allow_names, (str, bytes)):
            raise ProcessError("env_allow_names must be a sequence of variable names")
        if any(not isinstance(name, str) or not _ENV_NAME.fullmatch(name) for name in env_allow_names):
            raise ProcessError("env_allow_names contains an invalid environment variable name")
        allowed = _BASE_ENV_NAMES | frozenset(name.upper() for name in env_allow_names)
        # Freeze the environment at server startup. Tool callers cannot add keys.
        self._environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
        self._environment.setdefault("PATH", os.defpath)
        self._environment["PYTHONUNBUFFERED"] = "1"
        self._environment["PYTHONIOENCODING"] = "utf-8"
        self._lock = threading.RLock()
        self._jobs: dict[str, _Job] = {}
        self._closed = False

    def __enter__(self) -> ProcessManager:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def _get(self, job_id: str) -> _Job:
        if not isinstance(job_id, str) or job_id not in self._jobs:
            raise ProcessError("Unknown or expired job_id")
        return self._jobs[job_id]

    def _snapshot(self, job: _Job) -> dict[str, Any]:
        base = job.chunks[0].offset if job.chunks else job.output_end
        argv, argv_truncated = _argv_preview(job.argv)
        return {
            "job_id": job.job_id,
            "pid": job.process.pid,
            "argv": argv,
            "argv_truncated": argv_truncated,
            "argv_preview_limit_bytes": _ARGV_PREVIEW_BYTES,
            "cwd": job.cwd,
            "state": job.state,
            "process_running": job.process.poll() is None,
            "exit_code": job.process.returncode,
            "created_at": job.created_at,
            "finished_at": job.finished_at,
            "timeout_seconds": job.timeout_seconds,
            "base_cursor": base,
            "end_cursor": job.output_end,
            "retained_bytes": job.retained_bytes,
            "output_complete": job.finished_at is not None and job.closed_readers == 2,
            "stdin_closed": job.stdin_closed,
            "stdin_close_requested": job.stdin_close_requested,
            "stdin_error": job.stdin_error,
            "cleanup_error": job.cleanup_error,
        }

    def start(
        self,
        argv: Sequence[str],
        cwd: Path | str,
        timeout_seconds: float = 300,
    ) -> dict[str, Any]:
        """Start an argv command without a shell and return its job immediately."""
        if isinstance(argv, (str, bytes)) or not isinstance(argv, (list, tuple)):
            raise ProcessError("argv must be an array of strings, not a shell command")
        if not 1 <= len(argv) <= 128:
            raise ProcessError("argv must contain between 1 and 128 strings")
        if any(not isinstance(arg, str) or "\0" in arg for arg in argv):
            raise ProcessError("argv entries must be strings without NUL characters")
        if not argv[0] or sum(len(arg.encode("utf-8")) for arg in argv) > 131072:
            raise ProcessError("Executable is empty or argv exceeds 128 KiB")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not 0.05 <= timeout_seconds <= 86400
        ):
            raise ProcessError("timeout_seconds must be between 0.05 and 86400")
        try:
            directory = Path(cwd).expanduser().resolve(strict=True)
        except (OSError, ValueError, TypeError) as exc:
            raise ProcessError("cwd must be an existing directory") from exc
        if not directory.is_dir():
            raise ProcessError("cwd must be an existing directory")
        with self._lock:
            if self._closed:
                raise ProcessError("The process manager is closed")
            active = sum(job.finished_at is None or job.process.poll() is None for job in self._jobs.values())
            if active >= self.max_running:
                raise ProcessError(f"At most {self.max_running} jobs may run concurrently")
            # Evict only completed jobs, oldest first. IDs are never reused.
            while len(self._jobs) >= self.max_jobs:
                expired = next(
                    (key for key, job in self._jobs.items()
                     if job.finished_at is not None and job.closed_readers == 2 and job.process.poll() is not None),
                    None,
                )
                if expired is None:
                    raise ProcessError("Job retention capacity is full; stop existing jobs first")
                del self._jobs[expired]
            options: dict[str, Any] = {}
            if os.name == "posix":
                options["start_new_session"] = True
            elif os.name == "nt":
                options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            try:
                process = subprocess.Popen(
                    list(argv), cwd=directory, env=dict(self._environment), shell=False,
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    bufsize=0, **options,
                )
            except (OSError, ValueError) as exc:
                raise ProcessError(f"Could not start command: {exc}") from exc
            job = _Job(
                job_id="job_" + secrets.token_hex(16), process=process,
                argv=list(argv), cwd=str(directory), timeout_seconds=float(timeout_seconds),
            )
            self._jobs[job.job_id] = job
            for stream, pipe in (("stdout", process.stdout), ("stderr", process.stderr)):
                reader = threading.Thread(
                    target=self._reader, args=(job, stream, pipe),
                    name=f"{job.job_id}-{stream}", daemon=True,
                )
                job.readers.append(reader)
                reader.start()
            job.input_thread = threading.Thread(target=self._input_worker, args=(job,), daemon=True)
            job.input_thread.start()
            job.watcher = threading.Thread(target=self._watch, args=(job,), daemon=True)
            job.watcher.start()
            return self._snapshot(job)

    def _append(self, job: _Job, stream: str, text: str) -> None:
        if not text:
            return
        data = text.encode("utf-8")
        with self._lock:
            # Coalesce nearby writes and cap event metadata as well as bytes.
            # A command alternating one-byte stdout/stderr writes must not
            # turn a 1 MiB byte budget into millions of Python objects.
            if job.chunks and job.chunks[-1].stream == stream and len(job.chunks[-1].data) + len(data) <= 16384:
                job.chunks[-1].data += data
            else:
                job.chunks.append(_Chunk(job.output_end, stream, data))
            job.output_end += len(data)
            job.retained_bytes += len(data)
            while job.retained_bytes > self.max_output_bytes or len(job.chunks) > _MAX_OUTPUT_EVENTS:
                oldest = job.chunks[0]
                excess = job.retained_bytes - self.max_output_bytes
                if len(job.chunks) > _MAX_OUTPUT_EVENTS or len(oldest.data) <= excess:
                    job.retained_bytes -= len(oldest.data)
                    job.chunks.popleft()
                else:
                    cut = _boundary_forward(oldest.data, excess)
                    job.chunks[0] = _Chunk(oldest.offset + cut, oldest.stream, oldest.data[cut:])
                    job.retained_bytes -= cut
                    if not job.chunks[0].data:
                        job.chunks.popleft()

    def _reader(self, job: _Job, stream: str, pipe: Any) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            while True:
                data = pipe.read(4096)
                if not data:
                    break
                self._append(job, stream, decoder.decode(data))
            self._append(job, stream, decoder.decode(b"", final=True))
        except (OSError, ValueError):
            # Shutdown can close a pipe while a reader is waiting.
            pass
        finally:
            try:
                pipe.close()
            except OSError:
                pass
            with self._lock:
                job.closed_readers += 1

    def _input_worker(self, job: _Job) -> None:
        pipe = job.process.stdin
        try:
            while True:
                item = job.input_queue.get()
                if item is None or job.process.poll() is not None:
                    break
                data, close = item
                view = memoryview(data)
                while view:
                    written = pipe.write(view)
                    if not written:
                        raise BrokenPipeError("Child stdin is closed")
                    view = view[written:]
                if close:
                    break
        except (OSError, ValueError) as exc:
            with self._lock:
                job.stdin_error = str(exc)[:240]
        finally:
            try:
                pipe.close()
            except OSError:
                pass
            with self._lock:
                job.stdin_closed = True

    def _group_exists(self, job: _Job) -> bool:
        # Reap our own leader when it has exited. A zombie can otherwise keep
        # its group visible during termination, including after TERM succeeds.
        # Its exit alone does not mean the whole group is gone: descendants
        # still need the group probe below.
        leader_running = job.process.poll() is None
        if os.name != "posix":
            return leader_running
        try:
            os.killpg(job.process.pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    def _terminate_group(self, job: _Job) -> None:
        with job.termination_lock:
            if not self._group_exists(job):
                return
            try:
                if os.name == "posix":
                    os.killpg(job.process.pid, signal.SIGTERM)
                else:
                    job.process.terminate()
            except ProcessLookupError:
                return
            except OSError as exc:
                with self._lock:
                    job.cleanup_error = f"Could not terminate process: {exc}"
            # A child may ignore TERM or outlive its parent. Always check the
            # group after the grace period, even if the leader already exited.
            threading.Event().wait(self.terminate_grace_seconds)
            if not self._group_exists(job):
                return
            try:
                if os.name == "posix":
                    os.killpg(job.process.pid, signal.SIGKILL)
                elif job.process.poll() is None:
                    job.process.kill()
            except ProcessLookupError:
                pass
            except OSError as exc:
                with self._lock:
                    job.cleanup_error = f"Could not kill process: {exc}"

    def _watch(self, job: _Job) -> None:
        try:
            job.process.wait(timeout=job.timeout_seconds)
        except subprocess.TimeoutExpired:
            with self._lock:
                if job.stop_reason is None:
                    job.stop_reason = "timed_out"
                job.state = "stopping"
            self._terminate_group(job)
            try:
                job.process.wait(timeout=self.terminate_grace_seconds + 3)
            except subprocess.TimeoutExpired:
                with self._lock:
                    job.cleanup_error = "Process did not exit after termination"
        # Do not leave ordinary background descendants running after the main
        # command exits. A genuinely persistent dev server must remain the job.
        self._terminate_group(job)
        try:
            job.input_queue.put_nowait(None)
        except queue.Full:
            pass  # Worker checks process exit before consuming the next item.
        for reader in job.readers:
            reader.join(timeout=1)
        with self._lock:
            if job.closed_readers != 2 and job.cleanup_error is None:
                job.cleanup_error = "Output pipe remained open; a detached descendant may exist"
            job.state = "cleanup_failed" if job.process.poll() is None else (job.stop_reason or "exited")
            job.finished_at = _utc_now()

    def read(self, job_id: str, cursor: int = 0, max_bytes: int = 16384) -> dict[str, Any]:
        """Read a bounded output page. Feed next_cursor into the following call."""
        cursor = _integer(cursor, "cursor", 0, 2**63 - 1)
        max_bytes = _integer(max_bytes, "max_bytes", 4, 262144)
        with self._lock:
            job = self._get(job_id)
            if cursor > job.output_end:
                raise ProcessError("cursor is beyond the output end")
            base = job.chunks[0].offset if job.chunks else job.output_end
            start = max(cursor, base)
            position = start
            remaining = max_bytes
            events: list[dict[str, Any]] = []
            adjusted = False
            for chunk in job.chunks:
                if chunk.offset + len(chunk.data) <= position:
                    continue
                local = max(0, position - chunk.offset)
                safe_local = _boundary_forward(chunk.data, local)
                if safe_local != local:
                    adjusted = True
                    position = chunk.offset + safe_local
                    if not events:
                        start = position
                end = _boundary_backward(chunk.data, min(len(chunk.data), safe_local + remaining))
                if end <= safe_local:
                    break
                data = chunk.data[safe_local:end]
                event_end = chunk.offset + end
                events.append({
                    "stream": chunk.stream, "text": data.decode("utf-8"),
                    "start_cursor": chunk.offset + safe_local, "end_cursor": event_end,
                })
                position = event_end
                remaining -= len(data)
                if remaining == 0 or end < len(chunk.data):
                    break
            return {
                **self._snapshot(job),
                "cursor_unit": "normalized_utf8_bytes",
                "requested_cursor": cursor,
                "start_cursor": start,
                "next_cursor": position,
                "cursor_adjusted": adjusted,
                "truncated": cursor < base,
                "has_more": position < job.output_end,
                "events": events,
            }

    def list(self) -> dict[str, Any]:
        """List retained running and completed job metadata without log contents."""
        with self._lock:
            jobs = [self._snapshot(job) for job in reversed(list(self._jobs.values()))]
            return {"jobs": jobs, "max_running": self.max_running, "max_jobs": self.max_jobs, "closed": self._closed}

    def write_stdin(self, job_id: str, text: str = "", close: bool = False) -> dict[str, Any]:
        """Queue UTF-8 text and optionally EOF, without blocking the MCP call."""
        if not isinstance(text, str) or not isinstance(close, bool):
            raise ProcessError("text must be a string and close must be a boolean")
        data = text.encode("utf-8")
        if len(data) > 16384:
            raise ProcessError("A stdin write may contain at most 16384 UTF-8 bytes")
        with self._lock:
            job = self._get(job_id)
            if job.process.poll() is not None or job.stdin_closed or job.stdin_close_requested or job.stop_reason:
                raise ProcessError("This job no longer accepts stdin")
            try:
                job.input_queue.put_nowait((data, close))
            except queue.Full as exc:
                raise ProcessError("stdin queue is full; wait for the process to consume input") from exc
            if close:
                job.stdin_close_requested = True
            return {**self._snapshot(job), "queued_bytes": len(data)}

    def stop(self, job_id: str) -> dict[str, Any]:
        """Request TERM followed by KILL; return immediately and poll read()."""
        with self._lock:
            job = self._get(job_id)
            if job.finished_at is not None and job.process.poll() is not None:
                return self._snapshot(job)
            if job.stop_reason is None:
                job.stop_reason = "stopped"
                job.state = "stopping"
                threading.Thread(target=self._terminate_group, args=(job,), daemon=True).start()
            return self._snapshot(job)

    def close(self) -> None:
        """Stop all jobs. Safe to call repeatedly during server shutdown."""
        with self._lock:
            self._closed = True
            jobs = list(self._jobs.values())
        for job in jobs:
            self.stop(job.job_id)
        deadline = time.monotonic() + self.terminate_grace_seconds + 5
        for job in jobs:
            if job.watcher is not None and job.watcher is not threading.current_thread():
                job.watcher.join(timeout=max(0, deadline - time.monotonic()))

