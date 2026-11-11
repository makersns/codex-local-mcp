"""Small, native-dialog views of the running backend's Office environment."""
from __future__ import annotations

import permission_guide as permissions


_FORMAT_NAMES = {"xlsx": "Excel 文件", "docx": "Word 文件", "pptx": "PPT 文件"}
_STATES = {"detected": "已检测到依赖，尚未验证", "verified": "基础读写已验证",
           "missing": "未找到所需依赖", "failed": "验证未通过", "timed_out": "验证超时",
           "not_checked": "尚未检查"}


def _result(reply):
    problem = permissions.failure_message(reply)
    if problem:
        return {"available": False, "error": problem}
    value = reply.get("result", reply)
    if not isinstance(value, dict):
        return {"available": False, "error": "后台返回了无法识别的状态，请更新并重启连接后重试。"}
    problem = permissions.failure_message(value)
    if problem:
        return {"available": False, "error": problem}
    return value


def office_status(document, *, refresh=False):
    reply = permissions.backend_request(document, "office_status", {"refresh": refresh}, timeout=30)
    value = _result(reply)
    if value.get("available") is False:
        return value
    if (value.get("scope") != "this-machine" or type(value.get("pid")) is not int
            or not isinstance(value.get("capabilities"), dict)):
        return {"available": False, "error": "未能确认本机 Office 环境。请确认已运行新版服务。"}
    return {**value, "available": True}


def verify_office(document, *, include_libreoffice=False):
    reply = permissions.backend_request(document, "office_verify", {"include_libreoffice": include_libreoffice}, timeout=60)
    value = _result(reply)
    if value.get("available") is False:
        return value
    if value.get("scope") != "this-machine" or not isinstance(value.get("verification"), dict):
        return {"available": False, "error": "后台尚未返回完整验证结果。请检查连接后重试。"}
    return {**value, "available": True}


def format_office_status(status):
    if not status.get("available"):
        return "本机 Office 环境尚未检查。\n" + str(status.get("error") or "请先启动连接，再重新检查。")
    lines = []
    for format_name, label in _FORMAT_NAMES.items():
        capability = status.get("capabilities", {}).get(format_name, {})
        if not isinstance(capability, dict):
            capability = {}
        lines.append(f"{label}：{_STATES.get(capability.get('state'), '状态未知')}")
        if capability.get("python"):
            lines.append("  使用 Python：" + str(capability["python"]))
    selected = status.get("selected_python")
    if selected:
        lines.extend(("", "优先候选 Python：", str(selected)))
        if selected != status.get("mcp_python"):
            lines.append("使用本机已有的 Python；它与连接服务的独立运行环境分开。")
    apps = [str(item.get("name", "未知应用")) for item in (status.get("apps") or []) if isinstance(item, dict)]
    lines.extend(("", "已检测到的应用：" + ("、".join(apps[:8]) if apps else "未在常用应用目录发现")))
    probe = status.get("libreoffice_probe", {})
    if not isinstance(probe, dict):
        probe = {}
    state = probe.get("state")
    if state == "version_verified":
        lines.append("LibreOffice：独立配置下的启动检查通过；转换与公式重算尚未验证。")
    elif state == "timed_out":
        lines.append("LibreOffice：独立配置下的启动检查超时，尚不能确认转换能力。")
    elif state == "failed":
        lines.append("LibreOffice：启动检查未通过，尚不能确认转换能力。")
    elif state == "missing":
        lines.append("LibreOffice：未找到可执行程序。")
    elif status.get("libreoffice", {}).get("executable"):
        lines.append("LibreOffice：已检测到程序，尚未检查启动。")
    if status.get("verification"):
        lines.append("基础验证仅处理内存样例，未读取或修改你的文档。")
    lines.extend(("", "安装应用、发现依赖和验证通过是不同状态。",
                  "当前检查不代表能够操作已打开的 Office 窗口，也不保证复杂格式、打印效果或公式重算。",
                  "如果缺少依赖，请为本机 Python 准备后再刷新；本界面不会自动安装软件。"))
    return "\n".join(lines)


