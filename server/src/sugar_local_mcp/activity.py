"""Bounded, per-process activity counts; raw conversation IDs never leave a call.

The official openai/session metadata correlates calls, not open chat windows.
Only HMAC keys and local sequential numbers are retained, in memory. GUI status
requests never enter this registry. No arguments, paths, content or credentials
are accepted as activity data.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import re
import secrets
import threading
import time
from typing import Callable

WINDOW_SECONDS = 600
MAX_SESSIONS = 256
MAX_UNKNOWN_EVENTS = 4096
MAX_SESSION_ID_BYTES = 1024
MAX_VISIBLE_SESSIONS = 32
_WORKSPACE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_OPERATION = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


@dataclass
class _Session:
    number: int
    last_seen: float
    last_activity_at: str
    last_operation: str
    last_workspace: str | None
    active_calls: int = 0


@dataclass
class _Call:
    kind: str
    key: bytes | None
    finished: bool = False


class ActivityTracker:
    """Thread-safe, bounded activity for one MCP server lifetime.

    Session counts are lower bounds whenever coverage_complete is false.
    capacity_limited distinguishes registry limits from unidentified input.
    Unknown recent calls are starts in the rolling window; unknown active calls
    separately include long-running calls that began before that window.
    """
    def __init__(self, *, max_sessions: int = MAX_SESSIONS,
                 max_unknown_events: int = MAX_UNKNOWN_EVENTS,
                 monotonic: Callable[[], float] = time.monotonic,
                 wall_clock: Callable[[], float] = time.time):
        if type(max_sessions) is not int or not 1 <= max_sessions <= MAX_SESSIONS:
            raise ValueError("Invalid session capacity")
        if type(max_unknown_events) is not int or not 1 <= max_unknown_events <= MAX_UNKNOWN_EVENTS:
            raise ValueError("Invalid unidentified-call capacity")
        self._max_sessions = max_sessions
        self._max_unknown_events = max_unknown_events
        self._monotonic = monotonic
        self._wall_clock = wall_clock
        self._lock = threading.Lock()
        self._secret = secrets.token_bytes(32)
        self._instance_id = secrets.token_hex(16)
        self._started_at = self._timestamp()
        self._sessions: dict[bytes, _Session] = {}
        self._unknown_starts: deque[float] = deque()
        self._next_number = 1
        self._active_calls = 0
        self._unknown_active = 0
        self._overflow_active = 0
        self._limited_until = float("-inf")
        self._metadata_seen = False
        self._last_activity_at: str | None = None
        self._last_operation: str | None = None
        self._last_workspace: str | None = None

    def _timestamp(self) -> str:
        return datetime.fromtimestamp(self._wall_clock(), timezone.utc).isoformat()

    def _key(self, context) -> bytes | None:
        try:
            meta = context.request_context.meta
        except Exception:
            return None
        if not isinstance(meta, dict):
            return None
        value = meta.get("openai/session")
        if (not isinstance(value, str) or not value or len(value) > MAX_SESSION_ID_BYTES
                or not value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value)):
            return None
        try:
            encoded = value.encode("utf-8")
        except UnicodeError:
            return None
        if len(encoded) > MAX_SESSION_ID_BYTES:
            return None
        return hmac.new(self._secret, encoded, hashlib.sha256).digest()

    def _prune(self, now: float) -> None:
        threshold = now - WINDOW_SECONDS
        for key, session in tuple(self._sessions.items()):
            if session.active_calls == 0 and session.last_seen <= threshold:
                del self._sessions[key]
        while self._unknown_starts and self._unknown_starts[0] <= threshold:
            self._unknown_starts.popleft()

    def begin(self, context, operation: str, workspace: str | None = None) -> _Call:
        if not isinstance(operation, str) or not _OPERATION.fullmatch(operation):
            raise ValueError("Activity requires a registered operation name")
        workspace = (workspace if isinstance(workspace, str)
                     and _WORKSPACE.fullmatch(workspace) else None)
        key = self._key(context)
        with self._lock:
            now, stamp = self._monotonic(), self._timestamp()
            self._prune(now)
            self._active_calls += 1
            self._last_activity_at, self._last_operation = stamp, operation
            self._last_workspace = workspace
            if key is None:
                self._unknown_active += 1
                if len(self._unknown_starts) >= self._max_unknown_events:
                    dropped = self._unknown_starts.popleft()
                    self._limited_until = max(self._limited_until, dropped + WINDOW_SECONDS)
                self._unknown_starts.append(now)
                return _Call("unknown", None)
            self._metadata_seen = True
            entry = self._sessions.get(key)
            if entry is None:
                if len(self._sessions) >= self._max_sessions:
                    idle = [(item.last_seen, candidate)
                            for candidate, item in self._sessions.items()
                            if item.active_calls == 0]
                    if not idle:
                        self._limited_until = max(self._limited_until, now + WINDOW_SECONDS)
                        self._overflow_active += 1
                        return _Call("overflow", None)
                    _, evicted = min(idle)
                    dropped = self._sessions.pop(evicted)
                    self._limited_until = max(self._limited_until,
                                              dropped.last_seen + WINDOW_SECONDS)
                entry = _Session(self._next_number, now, stamp, operation, workspace)
                self._next_number += 1
                self._sessions[key] = entry
            entry.last_seen, entry.last_activity_at = now, stamp
            entry.last_operation, entry.last_workspace = operation, workspace
            entry.active_calls += 1
            return _Call("session", key)

    def finish(self, call: _Call) -> None:
        with self._lock:
            if call.finished:
                return
            call.finished = True
            self._active_calls -= 1
            if call.kind == "unknown":
                self._unknown_active -= 1
            elif call.kind == "overflow":
                self._overflow_active -= 1
            else:
                self._sessions[call.key].active_calls -= 1
            self._prune(self._monotonic())

    def snapshot(self, *, running_tasks: int = 0) -> dict:
        if type(running_tasks) is not int or running_tasks < 0:
            raise ValueError("Invalid running-task count")
        with self._lock:
            now = self._monotonic()
            self._prune(now)
            limited = self._overflow_active > 0 or self._limited_until > now
            complete = not (limited or self._unknown_active or self._unknown_starts)
            entries = sorted(self._sessions.values(),
                             key=lambda entry: (entry.active_calls > 0, entry.last_seen),
                             reverse=True)
            active_sessions = sum(entry.active_calls > 0 for entry in entries)
            message = "本次连接启动后统计；会话活动不代表聊天窗口持续在线。"
            if limited:
                message = "活动较多，部分来源未纳入统计；会话数为当前可识别的部分。"
            elif self._unknown_active or self._unknown_starts:
                message = "部分调用未携带会话标识；已识别会话数未包含这些来源。"
            elif not entries and self._last_activity_at is None:
                message = "本次连接启动后尚未收到工具调用。"
            elif not entries:
                message = "最近 10 分钟暂无工具调用。"
            return {
                "available": True, "reason": None, "message": message,
                "window_seconds": WINDOW_SECONDS, "observed_at": self._timestamp(),
                "statistics_started_at": self._started_at, "instance_id": self._instance_id,
                "active_sessions": active_sessions, "recent_sessions": len(entries),
                "active_calls": self._active_calls,
                "unknown_active_calls": self._unknown_active,
                "unknown_recent_calls": len(self._unknown_starts),
                "running_tasks": running_tasks, "metadata_seen": self._metadata_seen,
                "coverage_complete": complete, "capacity_limited": limited,
                "last_activity_at": self._last_activity_at,
                "last_operation": self._last_operation, "last_workspace": self._last_workspace,
                "sessions": [{"number": entry.number, "active_calls": entry.active_calls,
                              "last_activity_at": entry.last_activity_at,
                              "last_operation": entry.last_operation,
                              "last_workspace": entry.last_workspace}
                             for entry in entries[:MAX_VISIBLE_SESSIONS]],
                "sessions_truncated": len(entries) > MAX_VISIBLE_SESSIONS,
            }
