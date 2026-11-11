"""Public macOS permission APIs, evaluated in this exact server process.

Status calls never request access.  Only ``request`` asks macOS to present its
normal authorization flow; a prompt is not a grant and no TCC records are read
or changed directly.  This module does not capture screen images or observe
input events.
"""
from __future__ import annotations

import ctypes
from functools import lru_cache
import os
from pathlib import Path
import sys
from typing import Literal


PermissionKind = Literal["screen_capture", "accessibility", "post_event"]
_APPLICATION_SERVICES = "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
_CORE_GRAPHICS = "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
_CORE_FOUNDATION = "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
_CHECKS = {
    "accessibility": (_APPLICATION_SERVICES, "AXIsProcessTrusted", ctypes.c_ubyte),
    "screen_capture": (_CORE_GRAPHICS, "CGPreflightScreenCaptureAccess", ctypes.c_bool),
    "post_event": (_CORE_GRAPHICS, "CGPreflightPostEventAccess", ctypes.c_bool),
}


@lru_cache(maxsize=3)
def _framework(path: str):
    return ctypes.CDLL(path)


def _call_boolean(framework: str, function_name: str, result_type=ctypes.c_bool) -> bool:
    function = getattr(_framework(framework), function_name)
    function.argtypes = []
    function.restype = result_type
    return bool(function())


def _request_accessibility() -> bool:
    application = _framework(_APPLICATION_SERVICES)
    foundation = _framework(_CORE_FOUNDATION)
    key = ctypes.c_void_p.in_dll(application, "kAXTrustedCheckOptionPrompt")
    true_value = ctypes.c_void_p.in_dll(foundation, "kCFBooleanTrue")
    if not key.value or not true_value.value:
        raise RuntimeError("Permission prompt constants are unavailable")
    create = foundation.CFDictionaryCreate
    create.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
                       ctypes.POINTER(ctypes.c_void_p), ctypes.c_long,
                       ctypes.c_void_p, ctypes.c_void_p]
    create.restype = ctypes.c_void_p
    release = foundation.CFRelease
    release.argtypes = [ctypes.c_void_p]
    release.restype = None
    keys = (ctypes.c_void_p * 1)(key.value)
    values = (ctypes.c_void_p * 1)(true_value.value)
    # These global CF constants live for the entire process; NULL callbacks do
    # not retain/release them, which is sufficient for this synchronous call.
    options = create(None, keys, values, 1, None, None)
    if not options:
        raise RuntimeError("Could not create permission prompt options")
    try:
        ask = application.AXIsProcessTrustedWithOptions
        ask.argtypes = [ctypes.c_void_p]
        ask.restype = ctypes.c_ubyte
        return bool(ask(options))
    finally:
        release(options)


def _api_error(api: str, error: Exception) -> dict:
    # Never return arbitrary native exception text, environment values, or a
    # traceback. Missing OS symbols are unknown permission states, not denial.
    return {"api": api, "code": "api_unavailable" if isinstance(error, (AttributeError, OSError))
            else "api_failed", "message": "此系统 API 暂不可用，权限状态未知。"}


def status() -> dict:
    supported = sys.platform == "darwin"
    result = {
        "ok": True,
        "supported": supported,
        "scope": "current_process",
        "pid": os.getpid(),
        "executable": sys.executable,
        "real_executable": str(Path(sys.executable).resolve()) if sys.executable else "",
        "accessibility": None,
        "screen_capture": None,
        "post_event": None,
        "api_errors": [],
        "post_event_scope": "posting_events_only; not_input_monitoring_or_listening",
        "note": "这些状态属于当前 MCP 服务进程；系统可能将授权归属到其负责启动的应用。",
    }
    if not supported:
        result["note"] = "此平台不支持 macOS 权限 API，权限状态未知。"
        return result
    for field, (framework, function, result_type) in _CHECKS.items():
        try:
            result[field] = _call_boolean(framework, function, result_type)
        except Exception as error:
            result["api_errors"].append(_api_error(function, error))
    return result


def request(kind: PermissionKind) -> dict:
    """Ask for one permission only after an explicit local/user tool action.

    Apple documents the accessibility prompt as asynchronous. The returned
    ``request_result`` is kept separate from the subsequent preflight status.
    The caller should refresh after the user finishes in System Settings.
    """
    if not isinstance(kind, str) or kind not in _CHECKS:
        raise ValueError("Unsupported permission kind")
    before = status()
    fields = {"request_kind": kind, "request_sent": False, "request_result": None}
    if not before["supported"]:
        return {**before, **fields, "ok": False,
                "error": {"code": "unsupported_platform", "message": "只有 macOS 支持此权限申请。"}}
    if before[kind] is True:
        return {**before, **fields, "already_granted": True}
    api = {"accessibility": "AXIsProcessTrustedWithOptions",
           "screen_capture": "CGRequestScreenCaptureAccess",
           "post_event": "CGRequestPostEventAccess"}[kind]
    try:
        fields["request_result"] = (_request_accessibility() if kind == "accessibility"
                                    else _call_boolean(_CORE_GRAPHICS, api))
        fields["request_sent"] = True
    except Exception as error:
        after = status()
        after["api_errors"].append(_api_error(api, error))
        return {**after, **fields, "ok": False,
                "error": {"code": "permission_request_failed", "message": "系统未能发起权限申请，请在系统设置中检查。"}}
    return {**status(), **fields, "refresh_required": True,
            "request_note": "已向系统申请；请完成系统确认后刷新。发送申请不表示已获授权。"}
