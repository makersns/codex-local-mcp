#!/usr/bin/env python3
"""Bounded, one-request native UI bridge for Codex Local MCP.

The caller is the local signed application, not a network endpoint. Secrets are
accepted only on stdin and never returned. Existing support/Keychain identities
remain compatible. An alternate support directory cannot control the everyday
connection.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import sys
import tempfile
import time
import uuid

import desktop
from security_store import CredentialError, Keychain
from workspace_config import (
    WorkspaceConfigError, canonical_path, load_document, suggest_id,
    validate_document,
)

API_VERSION = 1
DEFAULT_SUPPORT = Path.home() / "Library/Application Support/Sugar Local MCP Desktop"
MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_LOG_BYTES = 64 * 1024
LOG_FILES = {"bootstrap": "bootstrap.log", "runtime": "tunnel.stdout.log",
             "errors": "tunnel.stderr.log"}
ACTIONS = frozenset({
    "snapshot", "save_connection", "save_workspaces", "setup", "start", "stop",
    "restart", "suggest_workspace", "diagnostics", "request_permission", "logs",
})
_FINGERPRINT = re.compile(r"[a-f0-9]{64}\Z")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")


class APIError(Exception):
    def __init__(self, code, message, *, field_errors=None, retryable=False,
                 saved=False, data=None):
        super().__init__(message)
        self.code = code
        self.message = _redact(str(message))[:1200]
        self.field_errors = field_errors or {}
        self.retryable = retryable
        self.saved = saved
        self.data = data

    def response(self):
        return {"api_version": API_VERSION, "ok": False, "data": self.data,
                "error": {"code": self.code, "message": self.message,
                          "field_errors": self.field_errors,
                          "retryable": self.retryable, "saved": self.saved}}


def success(data):
    return {"api_version": API_VERSION, "ok": True, "data": data, "error": None}


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2)
            + "\n").encode("utf-8")


def _private_read(path: Path, *, maximum=MAX_REQUEST_BYTES):
    """Read regular files without following symlinks or opening special files."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        return None
    except OSError:
        raise APIError("config_unavailable", "无法读取应用配置，请检查文件权限后重试。",
                       retryable=True) from None
    try:
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
                raise APIError("unsafe_file", "应用配置必须是当前用户拥有的普通文件。")
            raw = stream.read(maximum + 1)
    except OSError:
        raise APIError("config_unavailable", "无法读取应用配置，请检查文件权限后重试。",
                       retryable=True) from None
    if len(raw) > maximum:
        raise APIError("config_too_large", "配置文件过大，请检查后重新打开应用。")
    return raw


def _revision(settings_raw, workspace_raw):
    digest = hashlib.sha256()
    for label, value in ((b"settings", settings_raw), (b"workspaces", workspace_raw)):
        digest.update(label + b"\0")
        if value is None:
            digest.update(b"missing\0")
        else:
            digest.update(b"present\0")
            digest.update(len(value).to_bytes(8, "big"))
            digest.update(value)
    return digest.hexdigest()


def _valid_fields(payload, allowed):
    if set(payload) - set(allowed):
        raise APIError("invalid_request", "此操作包含不支持的选项，请重新打开应用后重试。")


def _boolean(payload, name, default=False):
    result = payload.get(name, default)
    if type(result) is not bool:
        raise APIError("validation", "请选择有效的操作选项。",
                       field_errors={name: "请选择开启或关闭。"})
    return result


def _reject_constant(_):
    raise ValueError("Non-finite JSON value")


def _safe_parse(raw):
    try:
        return json.loads(raw.decode("utf-8-sig"), parse_constant=_reject_constant)
    except (UnicodeError, ValueError, RecursionError):
        raise APIError("invalid_json", "收到的请求不是有效的配置数据，请重试。") from None


def _redact(text):
    """Redact common credential encodings and dangerous terminal controls."""
    text = re.sub(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|$))", "", text)
    text = "".join(c for c in text if c in "\n\r\t" or ord(c) >= 32 and ord(c) != 127)
    text = re.sub(r"(?i)\bsk-[A-Za-z0-9_-]{6,}", "[密钥已隐藏]", text)
    text = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [已隐藏]", text)
    names = r"(?:authorization|api[_ -]?key|runtime[_ -]?key|control_plane_api_key|access_token|refresh_token|token|password|passwd|secret|cookie|credential)"
    text = re.sub(
        r"(?i)([\"']?" + names + r"[\"']?\s*[:=]\s*)(?!\[已隐藏\])(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;&}\]\r\n]+)",
        lambda m: m.group(1) + "[已隐藏]", text)
    # Header credentials may contain spaces and multiple cookie values. Hide
    # the complete header value, including anything following the first token.
    text = re.sub(r"(?im)((?:proxy-)?authorization\s*:\s*)[^\r\n]+",
                  lambda m: m.group(1) + "[已隐藏]", text)
    text = re.sub(r"(?im)((?:set-)?cookie\s*:\s*)[^\r\n]+",
                  lambda m: m.group(1) + "[已隐藏]", text)
    text = re.sub(r"(?i)(https?://)[^/\s:@]+:[^/\s@]+@", r"\1[凭据已隐藏]@", text)
    text = re.sub(r"(?i)([?&](?:key|access_token|refresh_token|token|secret|password)=)[^&\s\"']+",
                  r"\1[已隐藏]", text)
    return text


class DesktopAPI:
    def __init__(self, support=None):
        requested = Path(support or DEFAULT_SUPPORT).expanduser().absolute()
        if requested.is_symlink():
            raise APIError("unsafe_support", "应用配置目录不能是符号链接。")
        self.support = requested.resolve()
        self.isolated = self.support != DEFAULT_SUPPORT.expanduser().resolve()

    @contextmanager
    def _context(self):
        previous = desktop.SUPPORT
        desktop.SUPPORT = self.support
        try:
            yield
        finally:
            desktop.SUPPORT = previous

    @contextmanager
    def _lock(self):
        self.support.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.support.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
            raise APIError("unsafe_support", "应用配置目录必须属于当前用户。")
        path = self.support / "control.lock"
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags, 0o600)
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
                os.close(descriptor)
                raise APIError("unsafe_lock", "配置锁文件异常，请重新打开应用。")
            os.fchmod(descriptor, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(descriptor)
                raise APIError("busy", "另一项设置操作正在进行，请稍后重试。",
                               retryable=True) from None
        except APIError:
            raise
        except OSError:
            raise APIError("config_unavailable", "无法保存设置，请检查配置目录权限。",
                           retryable=True) from None
        try:
            yield
        finally:
            os.close(descriptor)

    def _state(self):
        settings_raw = _private_read(self.support / "settings.json")
        workspace_raw = _private_read(self.support / "workspaces.json")
        settings = _safe_parse(settings_raw) if settings_raw is not None else None
        if settings is not None:
            if not isinstance(settings, dict):
                raise APIError("invalid_config", "连接设置格式不正确，请从备份恢复后重试。")
            try:
                desktop.validate_tunnel_id(settings.get("tunnel_id", ""))
            except desktop.DesktopError:
                raise APIError("invalid_config", "已保存的连接 ID 不完整，请检查连接设置。") from None
            if not isinstance(settings.get("computer_name"), str):
                raise APIError("invalid_config", "已保存的电脑名称无效，请检查连接设置。")
        try:
            document, _ = load_document(
                self.support, legacy_project=(settings or {}).get("project_root"))
        except WorkspaceConfigError as error:
            raise APIError("invalid_config", str(error)) from None
        # Both readers must describe the same observed workspace file.
        if _private_read(self.support / "workspaces.json") != workspace_raw:
            raise APIError("conflict", "设置刚被另一项操作更新，请重新加载后重试。",
                           retryable=True)
        return settings, document, _revision(settings_raw, workspace_raw), settings_raw, workspace_raw

    def _check_revision(self, expected):
        if not isinstance(expected, str) or not _FINGERPRINT.fullmatch(expected):
            raise APIError("validation", "配置版本信息已失效，请重新加载后保存。",
                           field_errors={"expected_revision": "请重新加载最新设置。"})
        actual = _revision(_private_read(self.support / "settings.json"),
                           _private_read(self.support / "workspaces.json"))
        if actual != expected:
            raise APIError("conflict", "设置已被其他操作修改。请重新加载最新内容，再保存你的更改。",
                           retryable=True)

    def _credential_status(self, settings):
        account = (settings or {}).get("keychain_account")
        if not isinstance(account, str) or not account:
            return "missing"
        if self.isolated:
            return "unavailable"
        try:
            return "stored" if Keychain(interactive=False, account=account).exists() else "missing"
        except (CredentialError, OSError):
            return "unavailable"

    def _runtime(self):
        try:
            info = desktop.runtime()
            return {"available": True, "message": "", "python": info["python"]}
        except (desktop.DesktopError, OSError, ValueError, KeyError, TypeError):
            return {"available": False, "message": "运行环境尚未准备好，请在应用中重新准备。",
                    "python": None}

    def _status(self, settings, document, runtime):
        port = (settings or {}).get("health_port", 8080)
        if type(port) is not int or not 1 <= port <= 65535:
            port = 8080
        base = {"managed": False, "upgrade_pending": False, "pid": None, "port": port}
        if self.isolated:
            return {**base, "state": "stopped" if settings else "needs_setup",
                    "title": "隔离测试模式",
                    "message": "此配置目录不会读取密钥或控制日常连接。"}
        try:
            probe = desktop.current_status()
        except (desktop.DesktopError, WorkspaceConfigError, OSError, ValueError, KeyError):
            return {**base, "state": "error", "title": "暂时无法读取连接状态",
                    "message": "请稍后刷新，或在运行记录中检查启动结果。"}
        base["managed"] = bool(probe.get("owned_listener") or probe.get("managed_pid"))
        base["pid"] = probe.get("managed_pid")
        if probe.get("port_in_use") and not probe.get("owned_listener"):
            return {**base, "state": "port_conflict", "title": "连接端口正被占用",
                    "message": "另一个程序正在使用此端口，请先结束原连接后再试。"}
        if probe.get("owned_listener") and probe.get("ready") == 200:
            if runtime["available"]:
                try:
                    expected = desktop.render_profile(settings, desktop.runtime())
                    actual = _private_read(self.support / "tunnel-profile.yaml")
                    base["upgrade_pending"] = actual != expected
                except (desktop.DesktopError, APIError, OSError, ValueError, KeyError):
                    pass
            return {**base, "state": "ready", "title": "本机已就绪",
                    "message": ("新版本已准备好，重启连接后生效。"
                                if base["upgrade_pending"] else
                                "在 ChatGPT 中选择此连接并调用工具，确认可以使用。")}
        if not settings or not document.get("workspaces"):
            return {**base, "state": "needs_setup", "title": "完成设置即可连接",
                    "message": "填写连接信息，再选择一个允许访问的文件夹。"}
        if probe.get("managed_pid") is not None:
            return {**base, "state": "connecting", "title": "连接正在准备中",
                    "message": "请稍候；如果长时间没有就绪，可查看运行记录。"}
        return {**base, "state": "stopped", "title": "当前未连接",
                "message": "设置已保留，点击“开始连接”即可继续使用。"}

    @staticmethod
    def _activity_unavailable(reason, message):
        return {
            "available": False, "reason": reason, "message": message,
            "window_seconds": 600, "observed_at": None, "statistics_started_at": None,
            "instance_id": None, "active_sessions": None, "recent_sessions": None,
            "active_calls": None, "unknown_active_calls": None,
            "unknown_recent_calls": None, "running_tasks": None,
            "metadata_seen": None, "coverage_complete": None, "capacity_limited": None,
            "last_activity_at": None, "last_operation": None, "last_workspace": None,
            "sessions": [], "sessions_truncated": False,
        }

    def _activity(self, document, status):
        unavailable = self._activity_unavailable
        if self.isolated:
            return unavailable("isolated", "隔离模式未连接日常后台，无法读取会话活动。")
        if not status.get("managed") or status.get("state") not in {"ready", "connecting"}:
            return unavailable("not_running", "连接就绪后，这里会显示会话活动。")
        try:
            reply = desktop.permissions.backend_request(document, "activity_snapshot", timeout=1.0)
        except Exception:
            return unavailable("unavailable", "后台暂时无法提供会话统计，请稍后刷新或更新并重新连接。")
        if not isinstance(reply, dict):
            return unavailable("invalid_response", "会话统计暂时无法读取，请稍后刷新。")
        if reply.get("ok") is not True:
            error = reply.get("error")
            code = error.get("code") if isinstance(error, dict) else None
            if code == "unknown_operation":
                return unavailable("unsupported", "当前后台尚不支持会话统计，更新并重新连接后即可查看。")
            return unavailable("unavailable", "暂时无法更新会话统计，请稍后刷新；未把旧数据作为实时结果。")
        # Only allow the frozen aggregate contract through this bridge. Never
        # relay arbitrary diagnostic strings, fields or session identifiers.
        operations = {"workspace_list", "list_directory", "read_file", "search_text",
                      "write_file", "edit_file", "run_command", "process_list",
                      "read_process", "send_process_input", "stop_process",
                      "office_status", "office_verify", "macos_permissions_status",
                      "macos_permissions_request"}
        counts = ("active_sessions", "recent_sessions", "active_calls",
                  "unknown_active_calls", "unknown_recent_calls", "running_tasks")
        flags = ("metadata_seen", "coverage_complete", "capacity_limited", "sessions_truncated")

        def integer(value):
            return type(value) is int and 0 <= value <= 2 ** 53 - 1

        def timestamp(value, nullable=False):
            if value is None:
                return nullable
            if not isinstance(value, str) or len(value) > 64:
                return False
            try:
                from datetime import datetime
                return datetime.fromisoformat(value.replace("Z", "+00:00")).utcoffset() is not None
            except (ValueError, OverflowError):
                return False

        def workspace(value):
            return value is None or isinstance(value, str) and bool(_ID.fullmatch(value))

        def operation(value, nullable=False):
            return value is None and nullable or isinstance(value, str) and value in operations

        valid = (reply.get("available") is True and type(reply.get("window_seconds")) is int
                 and reply["window_seconds"] == 600
                 and isinstance(reply.get("instance_id"), str)
                 and bool(re.fullmatch(r"[a-f0-9]{32}", reply["instance_id"]))
                 and all(integer(reply.get(name)) for name in counts)
                 and all(type(reply.get(name)) is bool for name in flags)
                 and timestamp(reply.get("observed_at"))
                 and timestamp(reply.get("statistics_started_at"))
                 and timestamp(reply.get("last_activity_at"), nullable=True)
                 and operation(reply.get("last_operation"), nullable=True)
                 and workspace(reply.get("last_workspace"))
                 and isinstance(reply.get("sessions"), list) and len(reply["sessions"]) <= 32)
        if valid:
            valid = (reply["active_sessions"] <= reply["recent_sessions"] <= 256
                     and reply["active_sessions"] + reply["unknown_active_calls"] <= reply["active_calls"]
                     and (reply["metadata_seen"] or reply["recent_sessions"] == 0)
                     and reply["coverage_complete"] == (not (reply["unknown_active_calls"] or reply["unknown_recent_calls"]
                             or reply["capacity_limited"])))
        rows = []
        if valid:
            seen = set()
            for row in reply["sessions"]:
                if (not isinstance(row, dict) or not integer(row.get("number"))
                        or row["number"] == 0 or row["number"] in seen
                        or not integer(row.get("active_calls"))
                        or row["active_calls"] > reply["active_calls"]
                        or not timestamp(row.get("last_activity_at"))
                        or not operation(row.get("last_operation"))
                        or not workspace(row.get("last_workspace"))):
                    valid = False
                    break
                seen.add(row["number"])
                rows.append({name: row.get(name) for name in
                             ("number", "active_calls", "last_activity_at",
                              "last_operation", "last_workspace")})
            valid = valid and len(rows) <= reply["recent_sessions"]
        if not valid:
            return unavailable("invalid_response", "会话统计暂时无法读取，请稍后刷新。")
        result = {name: reply.get(name) for name in
                  (*counts, *flags, "window_seconds", "observed_at", "statistics_started_at",
                   "instance_id", "last_activity_at", "last_operation", "last_workspace")}
        message = "本次连接启动后统计；会话活动不代表聊天窗口持续在线。"
        if result["capacity_limited"]:
            message = "活动较多，部分来源未纳入统计；显示的是当前可识别的会话数。"
        elif result["unknown_active_calls"] or result["unknown_recent_calls"]:
            message = "部分调用未携带会话标识；已识别会话数未包含这些来源。"
        elif result["last_activity_at"] is None:
            message = "本次连接启动后尚未收到工具调用。"
        elif result["recent_sessions"] == 0:
            message = "最近 10 分钟暂无工具调用。"
        result.update({"available": True, "reason": None, "message": message, "sessions": rows})
        return result

    def snapshot(self):
        settings, document, revision, _, _ = self._state()
        runtime = self._runtime()
        status = self._status(settings, document, runtime)
        return {
            "app": {"name": "Codex Local MCP", "version": desktop.VERSION, "author": "Sugar"},
            "connection": {
                "configured": settings is not None,
                "computer_name": (settings or {}).get("computer_name",
                    socket.gethostname().split(".")[0]),
                "tunnel_id": (settings or {}).get("tunnel_id", ""),
                "credential_status": self._credential_status(settings),
            },
            "status": status, "activity": self._activity(document, status),
            "workspace_document": document, "revision": revision, "runtime": runtime,
        }

    def _validate_connection(self, payload, previous):
        connection = payload.get("connection")
        if not isinstance(connection, dict) or set(connection) - {"computer_name", "tunnel_id"}:
            raise APIError("validation", "请填写电脑名称和连接 ID。")
        fields = {}
        name = connection.get("computer_name")
        tunnel = connection.get("tunnel_id")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120 \
                or any(ord(c) < 32 or ord(c) == 127 for c in name):
            fields["computer_name"] = "请输入 1–120 个字符的电脑名称。"
        if isinstance(tunnel, str):
            tunnel = tunnel.strip()
        try:
            desktop.validate_tunnel_id(tunnel)
        except desktop.DesktopError:
            fields["tunnel_id"] = "请复制完整的连接 ID，格式为 tunnel_…，不要填写网址。"
        key = payload.get("runtime_key", "")
        if not isinstance(key, str):
            fields["runtime_key"] = "请粘贴有效的连接密钥。"
            key = ""
        else:
            key = key.strip()
            if len(key.encode("utf-8")) > 16384 or any(ord(c) < 32 or ord(c) == 127 for c in key):
                fields["runtime_key"] = "密钥内容不完整，请重新复制为单行文本。"
        if fields:
            raise APIError("validation", "请检查标出的连接信息。", field_errors=fields)
        if self.isolated and key:
            raise APIError("isolated_mode", "隔离测试配置不会保存或读取日常连接密钥。")
        if not key:
            credential = self._credential_status(previous)
            if credential == "missing":
                raise APIError("validation", "首次连接需要填写连接密钥。",
                               field_errors={"runtime_key": "请粘贴连接密钥。"})
            if credential == "unavailable" and not self.isolated:
                raise APIError("keychain_unavailable", "暂时无法读取密钥状态。请解锁登录钥匙串后重试。",
                               field_errors={"runtime_key": "请先解锁登录钥匙串。"},
                               retryable=True)
            if self.isolated and not (previous or {}).get("keychain_account"):
                raise APIError("isolated_mode", "隔离模式需要测试夹具，不能创建真实连接密钥。")
        cfg = copy.deepcopy(previous or {})
        cfg.update({"version": desktop.VERSION, "computer_name": name.strip(),
                    "tunnel_id": tunnel, "health_port": cfg.get("health_port", 8080)})
        if type(cfg["health_port"]) is not int or not 1 <= cfg["health_port"] <= 65535:
            raise APIError("invalid_config", "已保存的连接端口无效，请检查应用配置。")
        if key:
            # A new item makes a credential change transactional without reading
            # the old secret. Existing settings/accounts remain recoverable.
            cfg["keychain_account"] = "desktop-" + uuid.uuid4().hex
        if not cfg.get("development_path"):
            cfg["development_path"] = desktop.development_path()
        return cfg, key

    def _validate_document(self, document):
        if not isinstance(document, dict) or not isinstance(document.get("workspaces"), list):
            raise APIError("validation", "请先选择允许访问的文件夹。",
                           field_errors={"workspaces": "请至少添加一个文件夹。"})
        if not document["workspaces"]:
            raise APIError("validation", "请至少保留一个允许访问的文件夹。",
                           field_errors={"workspaces": "请至少添加一个文件夹。"})
        if len(document["workspaces"]) > 32:
            raise APIError("validation", "最多可以添加 32 个文件夹。",
                           field_errors={"workspaces": "文件夹数量超过上限。"})
        fields, seen = {}, set()
        for index, row in enumerate(document["workspaces"]):
            prefix = f"workspaces.{index}."
            if not isinstance(row, dict):
                fields[prefix + "root"] = "文件夹信息无效，请重新选择。"
                continue
            identifier = row.get("id")
            if not isinstance(identifier, str) or not _ID.fullmatch(identifier):
                fields[prefix + "id"] = "使用自动生成的标识，或输入字母、数字、短横线和下划线。"
            elif identifier in seen:
                fields[prefix + "id"] = "这个标识已存在，请换一个。"
            else:
                seen.add(identifier)
            root = row.get("root")
            if not isinstance(root, str) or not root or not Path(root).expanduser().is_absolute():
                fields[prefix + "root"] = "请通过“选择文件夹”选择本机目录。"
            else:
                try:
                    canonical_path(root)
                except WorkspaceConfigError:
                    fields[prefix + "root"] = "这个文件夹不存在或无法访问，请重新选择。"
            for permission in ("allow_write", "allow_exec"):
                if permission in row and type(row[permission]) is not bool:
                    fields[prefix + permission] = "请选择开启或关闭。"
        if fields:
            raise APIError("validation", "请检查标出的文件夹设置。", field_errors=fields)
        try:
            validate_document(self.support, document)
        except WorkspaceConfigError as error:
            raise APIError("validation", str(error),
                           field_errors={"workspaces": str(error)}) from None

    def _replace_documents(self, changes, expected_revision):
        """Stage all files, verify concurrency, and restore on an I/O failure."""
        staged = {}
        originals = {}
        committed = []
        try:
            for name, raw in changes.items():
                target = self.support / name
                originals[name] = _private_read(target)
                fd, temporary = tempfile.mkstemp(prefix=".codex-save-", dir=self.support)
                staged[name] = Path(temporary)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(temporary, 0o600)
            self._check_revision(expected_revision)
            for name in changes:
                os.replace(staged[name], self.support / name)
                del staged[name]
                committed.append(name)
        except (OSError, APIError) as error:
            restored = True
            for name in reversed(committed):
                target = self.support / name
                try:
                    if _private_read(target) != changes[name]:
                        restored = False
                        continue
                    if originals[name] is None:
                        target.unlink()
                    else:
                        desktop.atomic_private(target, originals[name])
                except (OSError, APIError):
                    restored = False
            if not restored:
                raise APIError("save_incomplete", "保存过程被中断，请重新加载并检查设置。",
                               retryable=True, saved=True) from None
            if isinstance(error, APIError):
                raise
            raise APIError("save_failed", "设置未保存，原有内容已恢复。请检查磁盘空间和文件权限后重试。",
                           retryable=True) from None
        finally:
            for path in staged.values():
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass

    def _mutation_data(self, *, saved=False, restarted=False, restart_required=False, message=""):
        return {"snapshot": self.snapshot(), "saved": saved, "restarted": restarted,
                "restart_required": restart_required, "message": message}

    def _require_lifecycle(self):
        if self.isolated:
            raise APIError("isolated_mode", "隔离测试目录不会启动、停止或重启日常连接。")

    def _ownership(self):
        probe = desktop.current_status()
        if probe.get("port_in_use") and not probe.get("owned_listener"):
            raise APIError("port_conflict", "另一个程序正在使用连接端口。请结束原连接后再试。",
                           retryable=True)
        return probe

    def _apply(self, saved, restart):
        cfg, doc, _, _, _ = self._state()
        if not cfg or not doc.get("workspaces"):
            return self._mutation_data(saved=saved, message="设置已保存，请继续完成连接信息和文件夹选择。")
        if self.isolated:
            return self._mutation_data(saved=saved, restart_required=True,
                                       message="设置已保存；隔离模式不会重启日常连接。")
        if not restart:
            return self._mutation_data(saved=saved, restart_required=True,
                                       message="设置已保存，重新连接后生效。")
        try:
            probe = self._ownership()
            if probe.get("owned_listener") or probe.get("managed_pid") is not None:
                desktop.stop()
            result = desktop.start()
            status = desktop.current_status()
            if not (status.get("owned_listener") and status.get("ready") == 200):
                raise desktop.DesktopError("连接尚未就绪，请稍候重试。")
            return self._mutation_data(saved=saved, restarted=True,
                                       message="设置已保存，本机已就绪。")
        except Exception as error:
            message = error.message if isinstance(error, APIError) else desktop.error_detail(error)
            data = self._mutation_data(saved=saved, restart_required=True,
                                      message="设置已保存，但连接尚未成功。")
            raise APIError("connection_failed", "设置已保存，但连接尚未成功。" + message,
                           retryable=True, saved=saved, data=data) from None

    def _save(self, action, payload):
        fields = {"expected_revision", "restart"}
        if action in {"setup", "save_connection"}:
            fields |= {"connection", "runtime_key"}
        if action in {"setup", "save_workspaces"}:
            fields.add("document")
        _valid_fields(payload, fields)
        restart = _boolean(payload, "restart", True)
        expected = payload.get("expected_revision")
        # Reject stale drafts before reading any Keychain state or running a shell.
        self._check_revision(expected)
        with self._lock():
            self._check_revision(expected)
            previous, document, _, _, _ = self._state()
            cfg, key = previous, ""
            if action in {"setup", "save_connection"}:
                cfg, key = self._validate_connection(payload, previous)
            if action in {"setup", "save_workspaces"}:
                document = copy.deepcopy(payload.get("document"))
                self._validate_document(document)
            elif document.get("workspaces"):
                # Existing folders must still be valid before an apply is attempted.
                self._validate_document(document)
            self._check_revision(expected)
            changes = {}
            if action in {"setup", "save_workspaces"}:
                changes["workspaces.json"] = _json_bytes(document)
            if action in {"setup", "save_connection"}:
                changes["settings.json"] = _json_bytes(cfg)
            new_credential = None
            try:
                if key:
                    new_credential = Keychain(account=cfg["keychain_account"])
                    new_credential.set(key)
                key = ""
                self._replace_documents(changes, expected)
            except BaseException as error:
                if new_credential is not None and not getattr(error, "saved", False):
                    try:
                        new_credential.delete()
                    except (CredentialError, OSError):
                        pass
                raise
            try:
                return self._apply(True, restart)
            except APIError as error:
                error.saved = True
                raise
            except Exception:
                raise APIError("connection_failed", "设置已保存，但连接状态暂时无法确认。请刷新后重试。",
                               retryable=True, saved=True) from None

    def _lifecycle(self, action, payload):
        _valid_fields(payload, {"confirm"} if action != "start" else set())
        if action != "start" and payload.get("confirm") is not True:
            raise APIError("confirmation_required", "请先确认这项操作会中断正在运行的任务。")
        self._require_lifecycle()
        with self._lock():
            settings, document, _, _, _ = self._state()
            if action != "stop" and (not settings or not document.get("workspaces")):
                raise APIError("needs_setup", "请先完成连接信息和文件夹设置。")
            probe = self._ownership()
            if action == "stop":
                if probe.get("owned_listener") or probe.get("managed_pid") is not None:
                    desktop.stop()
                return self._mutation_data(message="连接已停止，设置仍然保留。")
            if action == "restart" and (probe.get("owned_listener") or probe.get("managed_pid") is not None):
                desktop.stop()
            desktop.start()
            after = desktop.current_status()
            if not (after.get("owned_listener") and after.get("ready") == 200):
                raise APIError("not_ready", "连接还没有就绪，请稍候刷新或查看运行记录。",
                               retryable=True)
            return self._mutation_data(restarted=action == "restart", message="本机已就绪。")

    def _suggest(self, payload):
        _valid_fields(payload, {"root", "existing_ids"})
        try:
            root = canonical_path(payload.get("root"))
        except WorkspaceConfigError:
            raise APIError("validation", "请选择本机真实存在的文件夹。",
                           field_errors={"root": "文件夹不存在或无法访问。"}) from None
        existing = payload.get("existing_ids")
        if existing is None:
            _, document, _, _, _ = self._state()
            existing = [row["id"] for row in document["workspaces"]]
        if not isinstance(existing, list) or len(existing) > 32 \
                or any(not isinstance(x, str) or not _ID.fullmatch(x) for x in existing):
            raise APIError("validation", "文件夹列表已变化，请重新打开后再添加。")
        return {"workspace": {"id": suggest_id(root, existing), "root": root,
                              "allow_write": True, "allow_exec": False}}

    def _diagnostics(self, payload):
        _valid_fields(payload, {"kind", "verify", "include_libreoffice"})
        kind = payload.get("kind")
        verify = _boolean(payload, "verify")
        include = _boolean(payload, "include_libreoffice")
        if kind not in {"permissions", "office"}:
            raise APIError("validation", "请选择要检查的环境。")
        if self.isolated:
            return {"kind": kind, "summary": "隔离模式未连接日常后台，未执行环境检查。",
                    "checks": [], "available": False}
        _, document, _, _, _ = self._state()
        if kind == "permissions":
            result = desktop.permissions.permission_status(document)
            names = {"screen_capture": "屏幕录制", "accessibility": "辅助功能", "post_event": "键鼠事件"}
            checks = []
            for key, title in names.items():
                value = result.get(key)
                status = ("unavailable" if not result.get("available") else
                          "available" if value is True else "denied" if value is False else "unknown")
                detail = {"unavailable": "后台暂时不可达，请启动连接后重试。",
                          "available": "已授权", "denied": "尚未授权",
                          "unknown": "尚未确认"}[status]
                checks.append({"id": key, "title": title, "status": status, "detail": detail})
            return {"kind": kind, "available": result.get("available", False),
                    "summary": _redact(str(result.get("error") or
                        "普通文件和终端操作不需要这些权限；仅在有需要时申请。"))[:1200],
                    "checks": checks}
        result = (desktop.environment.verify_office(document, include_libreoffice=include)
                  if verify else desktop.environment.office_status(document, refresh=True))
        checks = []
        states = {"verified": ("available", "基础读写验证通过"),
                  "detected": ("available", "已发现所需组件，尚未验证读写"),
                  "missing": ("missing", "未找到所需组件"),
                  "failed": ("error", "基础读写验证未通过"),
                  "timed_out": ("error", "检查超时，可以重试"),
                  "not_checked": ("unknown", "尚未检查")}
        for key, title in (("xlsx", "Excel 文件"), ("docx", "Word 文件"), ("pptx", "PPT 文件")):
            capability = result.get("capabilities", {}).get(key, {})
            status, detail = states.get(capability.get("state"), ("unknown", "尚未确认"))
            if not result.get("available"):
                status, detail = "unavailable", "请启动连接后重新检查。"
            checks.append({"id": key, "title": title, "status": status, "detail": detail})
        return {"kind": kind, "available": result.get("available", False),
                "summary": _redact(str(result.get("error") or
                    ("已检查示例文档的基础读写。" if verify else
                     "已检查本机文档组件，可以继续验证基础读写。")))[:1200],
                "checks": checks}

    def _request_permission(self, payload):
        _valid_fields(payload, {"kind", "confirm"})
        kind = payload.get("kind")
        if kind not in desktop.permissions.PERMISSION_KINDS:
            raise APIError("validation", "不支持的系统权限类型。")
        if payload.get("confirm") is not True:
            raise APIError("confirmation_required", "请先确认要申请的系统权限。")
        if self.isolated:
            raise APIError("isolated_mode", "隔离测试模式不会申请系统权限。")
        _, document, _, _, _ = self._state()
        reply = desktop.permissions.request_permission(document, kind)
        problem = desktop.permissions.failure_message(reply)
        if problem:
            raise APIError("permission_unavailable", problem, retryable=True)
        result = self._diagnostics({"kind": "permissions"})
        result["message"] = "已发出权限申请，请在系统提示中完成授权，再重新检查。"
        return result

    def _logs(self, payload):
        _valid_fields(payload, {"source", "lines"})
        source = payload.get("source", "errors")
        if source not in LOG_FILES:
            raise APIError("validation", "请选择应用提供的运行记录。")
        lines = payload.get("lines", 150)
        if type(lines) is not int or not 1 <= lines <= 500:
            raise APIError("validation", "运行记录每次可查看 1–500 行。")
        directory = self.support / "logs"
        try:
            info = directory.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
                raise APIError("unsafe_file", "运行记录目录异常，无法读取。")
        except FileNotFoundError:
            return {"source": source, "text": "", "truncated": False, "available": False}
        target = directory / LOG_FILES[source]
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        try:
            descriptor = os.open(target, flags)
        except FileNotFoundError:
            return {"source": source, "text": "", "truncated": False, "available": False}
        except OSError:
            raise APIError("logs_unavailable", "无法读取运行记录，请稍后重试。",
                           retryable=True) from None
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
                raise APIError("unsafe_file", "运行记录必须是当前用户拥有的普通文件。")
            offset = max(0, info.st_size - MAX_LOG_BYTES)
            stream.seek(offset)
            raw = stream.read(MAX_LOG_BYTES)
        text = raw.decode("utf-8", errors="replace")
        if offset:
            # A partial first line might omit its credential label; discard it.
            text = text.split("\n", 1)[1] if "\n" in text else ""
        selected = text.splitlines()
        truncated = offset > 0 or len(selected) > lines
        return {"source": source, "text": _redact("\n".join(selected[-lines:])),
                "truncated": truncated, "available": True}

    def handle(self, request):
        try:
            if not isinstance(request, dict) or set(request) - {"api_version", "action", "payload"}:
                raise APIError("invalid_request", "请求格式无效，请重新打开应用后重试。")
            version = request.get("api_version", API_VERSION)
            if type(version) is not int or version != API_VERSION:
                raise APIError("unsupported_version", "界面与后台版本不一致，请重新安装同一版本。")
            action = request.get("action")
            if not isinstance(action, str) or action not in ACTIONS:
                raise APIError("unknown_action", "此操作暂不可用，请更新应用后重试。")
            payload = request.get("payload", {})
            if not isinstance(payload, dict):
                raise APIError("invalid_request", "操作参数无效，请重试。")
            with self._context():
                if action == "snapshot":
                    _valid_fields(payload, set())
                    data = self.snapshot()
                elif action in {"save_connection", "save_workspaces", "setup"}:
                    data = self._save(action, payload)
                elif action in {"start", "stop", "restart"}:
                    data = self._lifecycle(action, payload)
                elif action == "suggest_workspace":
                    data = self._suggest(payload)
                elif action == "diagnostics":
                    data = self._diagnostics(payload)
                elif action == "request_permission":
                    data = self._request_permission(payload)
                else:
                    data = self._logs(payload)
                return success(data)
        except APIError as error:
            if error.saved and error.data is None:
                try:
                    with self._context():
                        error.data = self._mutation_data(saved=True, restart_required=True,
                                                         message=error.message)
                except Exception:
                    pass
            return error.response()
        except (desktop.DesktopError, CredentialError, WorkspaceConfigError,
                desktop.permissions.PermissionGuideError) as error:
            return APIError("operation_failed", desktop.error_detail(error), retryable=True).response()
        except Exception:
            return APIError("operation_failed", "操作暂时未完成。请重试，或查看运行记录了解原因。",
                            retryable=True).response()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Codex Local MCP native JSON bridge")
    parser.add_argument("--support", type=Path, default=DEFAULT_SUPPORT)
    args = parser.parse_args(argv)
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            raise APIError("request_too_large", "设置内容过大，请减少内容后重试。")
        request = _safe_parse(raw)
        response = DesktopAPI(args.support).handle(request)
    except APIError as error:
        response = error.response()
    except Exception:
        response = APIError("operation_failed", "无法读取应用设置，请重试。",
                            retryable=True).response()
    try:
        encoded = json.dumps(response, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(encoded) > MAX_RESPONSE_BYTES:
            raise ValueError()
    except (TypeError, ValueError, UnicodeError, RecursionError):
        encoded = json.dumps(APIError("response_too_large", "返回内容过大，请减少目录配置后重试。").response(),
                             ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(encoded + b"\n")
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
