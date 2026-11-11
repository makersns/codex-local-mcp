"""Ask the running MCP backend about macOS permissions through private IPC.

Checks and requests apply to the running MCP process, not the setup UI. They
are optional diagnostics and are never run as part of normal app startup.
"""
from __future__ import annotations

from pathlib import Path
import subprocess


PERMISSION_KINDS = ("screen_capture", "accessibility", "post_event")
PRIVACY_URLS = {
    "screen_capture": "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
    "accessibility": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
    "post_event": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
}


class PermissionGuideError(Exception):
    pass


def resolve_state_dir(document):
    """Match the core configuration's default, expansion, and absolute paths."""
    if not isinstance(document, dict):
        raise PermissionGuideError("无法读取项目配置中的后台状态目录。")
    value = document.get("state_dir", str(Path.home() / ".local/state/sugar-local-mcp"))
    if not isinstance(value, str) or not value or "\x00" in value:
        raise PermissionGuideError("后台状态目录配置无效，请检查项目配置。")
    try:
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ValueError()
        return path.resolve()
    except (OSError, RuntimeError, ValueError):
        raise PermissionGuideError("后台状态目录必须是有效的绝对路径或 ~/ 路径。") from None


def _request_local(state_dir, operation, payload=None, timeout=15):
    # Lazy import lets an older/unprepared runtime still show a useful guide.
    from sugar_local_mcp.diagnostics import request_local
    return request_local(state_dir, operation, payload=payload, timeout=timeout)


def backend_request(document, operation, payload=None, timeout=15):
    try:
        state_dir = resolve_state_dir(document)
        reply = _request_local(state_dir, operation, payload=payload, timeout=timeout)
    except PermissionGuideError as error:
        return {"ok": False, "error": {"code": "invalid_state_dir", "message": str(error)}}
    except Exception:
        # Do not report a transport failure as a denied OS permission, or expose
        # arbitrary exception strings from a local process/environment.
        return {"ok": False, "error": {
            "code": "backend_unavailable",
            "message": "未能连接实际 MCP 后台进程。请确认连接已启动后重试。",
        }}
    if not isinstance(reply, dict):
        return {"ok": False, "error": {"code": "invalid_response", "message": "后台诊断返回了无法识别的结果，请重新检查。"}}
    return reply


def failure_message(reply):
    if reply.get("ok") is not False:
        return None
    error = reply.get("error")
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return error["message"]
    return "后台操作没有完成，请重新检查连接。"


def _unavailable(message):
    return {"available": False, "supported": None, "pid": None,
            "executable": None, "scope": None, "api_errors": {},
            "accessibility": None, "screen_capture": None, "post_event": None,
            "error": message}


def permission_status(document):
    reply = backend_request(document, "permissions_status", timeout=3)
    problem = failure_message(reply)
    if problem:
        return _unavailable(problem)
    pid = reply.get("pid")
    executable = reply.get("executable")
    if (reply.get("scope") != "current_process"
            or type(reply.get("supported")) is not bool
            or type(pid) is not int or pid <= 0
            or not isinstance(executable, str) or not executable):
        return _unavailable("未能确认权限结果来自哪个 MCP 后台进程，请重新检查或重启连接。")
    status = {"available": True, "supported": reply["supported"], "pid": pid,
              "executable": executable, "scope": "current_process",
              "api_errors": reply.get("api_errors", {}), "error": None}
    for kind in PERMISSION_KINDS:
        value = reply.get(kind)
        status[kind] = value if type(value) is bool else None
    return status


def format_permission_status(status):
    if not status.get("available"):
        return "未能检查 MCP 后台权限。\n" + str(status.get("error") or "后台进程暂时不可达。")
    lines = [f"检查对象：MCP 后台进程 {status['pid']}", f"程序：{status['executable']}"]
    if status.get("supported") is False:
        return "\n".join(lines + ["此后台运行环境不支持 macOS 原生权限检查。"])
    names = {"screen_capture": "屏幕录制", "accessibility": "辅助功能", "post_event": "键鼠事件"}
    for kind in PERMISSION_KINDS:
        value = status.get(kind)
        label = "已授权" if value is True else "尚未授权" if value is False else "未能确定"
        lines.append(f"{names[kind]}：{label}")
    if status.get("api_errors"):
        lines.append("部分系统权限 API 未能返回完整结果，可刷新或重启连接后复检。")
    return "\n".join(lines)


def request_permission(document, kind):
    """Call only after a corresponding explicit click in the native guide."""
    if kind not in PERMISSION_KINDS:
        raise PermissionGuideError("不支持的系统权限类型。")
    return backend_request(document, "permissions_request", {"kind": kind}, timeout=30)


def open_privacy_settings(kind):
    if kind not in PRIVACY_URLS:
        raise PermissionGuideError("不支持的系统设置页面。")
    try:
        result = subprocess.run(["/usr/bin/open", PRIVACY_URLS[kind]],
                                capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        raise PermissionGuideError("无法打开系统设置。请手动进入“隐私与安全性”。") from None
    if result.returncode:
        raise PermissionGuideError("无法打开系统设置。请手动进入“隐私与安全性”。")


