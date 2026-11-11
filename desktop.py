#!/usr/bin/env python3
"""Native-dialog setup and lifecycle for Sugar Local MCP Desktop 0.5.0."""
from __future__ import annotations

import argparse
import copy
import fcntl
import json
import os
from pathlib import Path
import plistlib
import re
import shlex
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid

import permission_guide as permissions
import environment_guide as environment
from security_store import CredentialError, Keychain
from workspace_config import (
    WorkspaceConfigError,
    canonical_path,
    load_document,
    save_document,
    suggest_id,
    validate_document,
)

VERSION = "0.5.0"
LABEL = "com.sugar.local-mcp.desktop.runtime"
APP_TITLE = "Sugar Local MCP"
TUNNEL_RE = re.compile(r"tunnel_(?:[a-z0-9]{4}_)?[a-z0-9]{32}\Z")
SUPPORT = Path.home() / "Library/Application Support/Sugar Local MCP Desktop"
RESOURCES = Path(__file__).resolve().parent


class DesktopError(Exception):
    pass


class Cancelled(Exception):
    pass


def run(argv, timeout=10, **kwargs):
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, **kwargs)


def as_literal(value):
    return json.dumps(str(value), ensure_ascii=False)


def dialog_script(script):
    result = run(["/usr/bin/osascript", "-e", script], timeout=600)
    if result.returncode:
        if "-128" in result.stderr or "cancel" in result.stderr.lower():
            raise Cancelled()
        raise DesktopError("macOS 未能完成窗口操作，请重新打开应用。")
    return result.stdout.rstrip("\r\n")


def message(text, buttons=("完成",), default=None):
    options = ", ".join(as_literal(x) for x in buttons)
    script = f'return button returned of (display dialog {as_literal(text)} with title {as_literal(APP_TITLE)} buttons {{{options}}} default button {as_literal(default or buttons[-1])})'
    return dialog_script(script)


def ask(text, default="", *, hidden=False):
    privacy = " with hidden answer" if hidden else ""
    script = f'return text returned of (display dialog {as_literal(text)} with title {as_literal(APP_TITLE + " · 配置")} default answer {as_literal(default)}{privacy} buttons {{"取消", "继续"}} default button "继续" cancel button "取消")'
    return dialog_script(script).strip()


def choose_list(options, prompt, *, cancel_label="返回"):
    """Return a single selection's index, never parse a displayed file path."""
    if not options:
        return None
    values = ", ".join(as_literal(value) for value in options)
    script = f'''set choicesList to {{{values}}}
set selectedItems to choose from list choicesList with title {as_literal(APP_TITLE)} with prompt {as_literal(prompt)} OK button name "选择" cancel button name {as_literal(cancel_label)}
if selectedItems is false then return "cancelled"
set selectedItem to item 1 of selectedItems
repeat with itemIndex from 1 to count of choicesList
    if item itemIndex of choicesList is selectedItem then return itemIndex as text
end repeat
return "cancelled"'''
    selected = dialog_script(script)
    if selected == "cancelled":
        return None
    try:
        index = int(selected) - 1
    except ValueError:
        raise DesktopError("未能识别所选项目，请重新选择。") from None
    if not 0 <= index < len(options):
        raise DesktopError("所选项目已失效，请重新打开列表。")
    return index


def choose_project(existing=None):
    default = ""
    if existing and Path(existing).is_dir():
        default = f" default location (POSIX file {as_literal(existing)})"
    script = 'return POSIX path of (choose folder with prompt "选择允许 ChatGPT 读取、修改和运行的本地项目目录"' + default + ')'
    return canonical_path(dialog_script(script))


def atomic_private(path: Path, value: bytes):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".sugar-save-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def settings_path():
    return SUPPORT / "settings.json"


def load_settings():
    if not settings_path().is_file():
        return None
    cfg = json.loads(settings_path().read_text(encoding="utf-8"))
    validate_tunnel_id(cfg.get("tunnel_id", ""))
    return cfg


def runtime():
    data = json.loads((SUPPORT / "runtime-info.json").read_text(encoding="utf-8"))
    architecture = data.get("arch") or data.get("machine")
    if architecture not in {"arm64", "x86_64"}:
        raise DesktopError("运行环境架构无法识别，请重新运行安装准备。")
    data["arch"] = architecture
    for key in ["python", "tunnel_client"]:
        if not Path(data[key]).is_file() or not os.access(data[key], os.X_OK):
            raise DesktopError("运行环境不完整，请重新打开安装应用。")
    return data


def validate_tunnel_id(value):
    if not isinstance(value, str) or not TUNNEL_RE.fullmatch(value):
        raise DesktopError("Tunnel ID 格式不正确。请从 OpenAI 隧道页面复制完整的 tunnel_… ID。")
    return value


def launch_target():
    return f"gui/{os.getuid()}/{LABEL}"


def managed_pid():
    result = run(["/bin/launchctl", "print", launch_target()])
    if result.returncode:
        return None
    match = re.search(r"(?m)^\s*pid = (\d+)\s*$", result.stdout)
    return int(match.group(1)) if match else None


def health(port=8080):
    result = {"port_in_use": False, "health": None, "ready": None, "listener_pids": []}
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.4):
            result["port_in_use"] = True
    except OSError:
        return result
    listener = run(["/usr/sbin/lsof", "-t", f"-iTCP@127.0.0.1:{port}", "-sTCP:LISTEN"])
    result["listener_pids"] = sorted({int(x) for x in listener.stdout.split() if x.isdigit()})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for key, endpoint in [("health", "healthz"), ("ready", "readyz")]:
        try:
            with opener.open(f"http://127.0.0.1:{port}/{endpoint}", timeout=1.5) as response:
                result[key] = response.status
        except urllib.error.HTTPError as error:
            result[key] = error.code
        except (OSError, urllib.error.URLError):
            pass
    return result


def current_status():
    cfg = load_settings()
    probe = health((cfg or {}).get("health_port", 8080))
    pid = managed_pid()
    probe["managed_pid"] = pid
    probe["owned_listener"] = pid is not None and pid in probe["listener_pids"]
    probe["configured"] = cfg is not None
    if cfg:
        probe.update({"computer_name": cfg["computer_name"], "tunnel_id": cfg["tunnel_id"]})
    document, _ = load_workspace_document(cfg)
    probe["workspaces"] = [
        {"id": row["id"], "root": row["root"],
         "allow_write": row.get("allow_write", False),
         "allow_exec": row.get("allow_exec", False)}
        for row in document["workspaces"]
    ]
    probe["workspace_count"] = len(probe["workspaces"])
    return probe


def development_path():
    """Retain just PATH from the user's normal login shell, never its secrets."""
    base = os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    shell = os.environ.get("SHELL", "/bin/zsh")
    if shell not in {"/bin/zsh", "/bin/bash"}:
        shell = "/bin/zsh"
    marker = "SUGAR_MCP_PATH_" + uuid.uuid4().hex
    try:
        result = run([shell, "-ilc", f'printf "\\n{marker}%s{marker}\\n" "$PATH"'], timeout=8)
        found = re.search(re.escape(marker) + r"([^\r\n]*)" + re.escape(marker), result.stdout)
        if found:
            base = found.group(1)
    except (OSError, subprocess.TimeoutExpired):
        pass
    paths = base.split(":") + ["/opt/homebrew/bin", "/usr/local/bin", str(Path.home()/".local/bin"), "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    return ":".join(dict.fromkeys(x for x in paths if x and Path(x).is_absolute()))


def load_workspace_document(cfg=None):
    if cfg is None:
        cfg = load_settings()
    return load_document(SUPPORT, legacy_project=(cfg or {}).get("project_root"))


def permission_text(workspace):
    def state(name):
        value = workspace.get(name, False)
        return "开启" if value is True else "关闭" if value is False else "配置无效"
    return f"读取；写入{state('allow_write')}；命令执行{state('allow_exec')}"


def workspace_summary(document, *, limit=6):
    rows = document["workspaces"]
    lines = [f"项目目录：{len(rows)} 个"]
    for row in rows[:limit]:
        lines.extend((f"{row['id']} — {row['root']}", f"  {permission_text(row)}"))
    if len(rows) > limit:
        lines.append(f"另有 {len(rows) - limit} 个，可在“管理项目目录”中查看。")
    return "\n".join(lines)


def ask_workspace_id(document, default, *, exclude=None):
    taken = {row["id"] for index, row in enumerate(document["workspaces"]) if index != exclude}
    while True:
        identifier = ask("项目 ID 用来在 ChatGPT 中区分目录。\n请输入 1–64 位英文字母、数字、下划线或短横线，以字母或数字开头；每个项目必须不同。", default)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", identifier):
            message("项目 ID 格式不正确，请按要求填写。")
        elif identifier in taken:
            message("这个项目 ID 已经存在，请换一个名称。")
        else:
            return identifier
        default = identifier


def add_project_to_draft(document):
    if len(document["workspaces"]) >= 32:
        raise WorkspaceConfigError("最多可以添加 32 个项目目录。")
    project = choose_project()
    identifier = ask_workspace_id(document, suggest_id(project, [row["id"] for row in document["workspaces"]]))
    document["workspaces"].append({"id": identifier, "root": project,
                                   "allow_write": True, "allow_exec": True})


def edit_project_in_draft(document, index):
    workspace = document["workspaces"][index]
    detail = f"项目 ID：{workspace['id']}\n\n完整目录：\n{workspace['root']}\n\n权限：{permission_text(workspace)}"
    action = message(detail, ("返回", "编辑", "移除"), default="返回")
    if action == "移除":
        confirm = message(f"从 ChatGPT 可访问的列表中移除“{workspace['id']}”？\n\n{workspace['root']}\n\n磁盘上的文件和目录不会删除；列表更改需要保存后才生效。", ("返回", "移除"), default="返回")
        if confirm == "移除":
            document["workspaces"].pop(index)
    elif action == "编辑":
        selected = choose_list(("修改项目 ID", "更换项目目录", "设置文件写入权限", "设置命令执行权限"),
                               "选择要修改的内容。更改仅在保存项目列表后生效。")
        if selected == 0:
            identifier = ask_workspace_id(document, workspace["id"], exclude=index)
            workspace["id"] = identifier
        elif selected == 1:
            project = choose_project(workspace["root"])
            workspace["root"] = project
        elif selected in (2, 3):
            set_project_permission(workspace, "allow_write" if selected == 2 else "allow_exec")


def set_project_permission(workspace, permission):
    if permission not in {"allow_write", "allow_exec"}:
        raise WorkspaceConfigError("不支持的项目权限。")
    label = "文件写入" if permission == "allow_write" else "命令执行"
    current = workspace.get(permission, False) is True
    detail = f"项目：{workspace['id']}\n目录：{workspace['root']}\n\n{label}当前{'开启' if current else '关闭'}。"
    if permission == "allow_exec":
        detail += "\n命令使用当前 macOS 用户的系统权限，工作目录不会限制命令可访问的文件。"
    else:
        detail += "\n此开关控制文件写入工具；如命令执行已开启，命令本身仍可以修改文件。"
    detail += "\n\n选择后仍需保存项目列表；取消更改会丢弃这次选择。"
    selected = message(detail, ("返回", "关闭", "开启"), default="开启" if current else "关闭")
    if selected in {"关闭", "开启"}:
        workspace[permission] = selected == "开启"


def edit_workspace_document(document, *, save_label="保存并连接"):
    """Edit only this in-memory draft; returning False discards every change."""
    while True:
        rows = document["workspaces"]
        # Indices make duplicate IDs/paths repairable without ambiguous labels.
        options = ["＋ 添加项目目录"]
        options.extend(f"{index + 1}. {row['id']} — {row['root']}" for index, row in enumerate(rows))
        options.extend((save_label, "取消更改"))
        selected = choose_list(options, f"已选择 {len(rows)} 个项目。选中项目可查看完整路径、权限，并编辑或移除。\n更改仅在保存后生效。", cancel_label="取消更改")
        if selected is None or selected == len(rows) + 2:
            return False
        try:
            if selected == 0:
                add_project_to_draft(document)
            elif selected == len(rows) + 1:
                validate_document(SUPPORT, document)
                return True
            else:
                edit_project_in_draft(document, selected - 1)
        except Cancelled:
            # Cancelling one picker leaves the rest of the draft available.
            continue
        except WorkspaceConfigError as error:
            message(str(error))


def mcp_command(info):
    return shlex.join(["/usr/bin/arch", "-" + info["arch"], info["python"], "-m", "sugar_local_mcp", "serve", "--config", str(SUPPORT / "workspaces.json")])


def render_profile(cfg, info):
    validate_tunnel_id(cfg["tunnel_id"])
    return (
        "config_version: 1\ncontrol_plane:\n  base_url: \"https://api.openai.com\"\n"
        + "  tunnel_id: " + json.dumps(cfg["tunnel_id"]) + "\n"
        + '  api_key: "env:CONTROL_PLANE_API_KEY"\n'
        + f'health:\n  listen_addr: "127.0.0.1:{cfg["health_port"]}"\n'
        + "admin_ui:\n  open_browser: false\nlog:\n  level: warn\n  format: json\n"
        + "mcp:\n  commands:\n    - channel: main\n      command: " + json.dumps(mcp_command(info)) + "\n"
    ).encode()


def write_agent(cfg, info):
    logs = SUPPORT / "logs"
    logs.mkdir(mode=0o700, exist_ok=True)
    for name in ["tunnel.stdout.log", "tunnel.stderr.log"]:
        path = logs / name
        if not path.exists():
            atomic_private(path, b"")
    plist = {"Label": LABEL, "ProgramArguments": ["/usr/bin/arch", "-" + info["arch"], info["python"], "-B", str(RESOURCES / "desktop.py"), "--support", str(SUPPORT), "daemon"],
             "WorkingDirectory": str(SUPPORT), "RunAtLoad": True, "KeepAlive": False,
             "ProcessType": "Interactive", "Umask": 0o077,
             "EnvironmentVariables": {"PATH": cfg["development_path"]},
             "StandardOutPath": str(logs/"tunnel.stdout.log"),
             "StandardErrorPath": str(logs/"tunnel.stderr.log")}
    atomic_private(SUPPORT / "runtime.plist", plistlib.dumps(plist))


def stop():
    previous_pid = managed_pid()
    result = run(["/bin/launchctl", "bootout", launch_target()])
    if result.returncode and managed_pid() is not None:
        raise DesktopError("macOS 未能停止此应用管理的后台任务。")
    if previous_pid is not None:
        port = (load_settings() or {}).get("health_port", 8080)
        deadline = time.monotonic() + 10
        while previous_pid in health(port)["listener_pids"]:
            if time.monotonic() >= deadline:
                raise DesktopError("后台任务正在退出，尚未释放端口。请稍候再点击连接。")
            time.sleep(0.25)
    return "此应用管理的后台连接已停止。"


def connection_draft(previous):
    """Collect connection fields and a credential in memory, without saving."""
    name = ask("给这台 Mac 起一个名称，用来区分 ChatGPT 中的连接。", previous.get("computer_name", socket.gethostname().split(".")[0]))
    if not name:
        raise DesktopError("请填写本机名称。")
    tunnel_id = validate_tunnel_id(ask("填写这台 Mac 专用的 Tunnel ID。另一台电脑应使用不同的 Tunnel ID。", previous.get("tunnel_id", "")))
    account = previous.get("keychain_account") or "desktop-" + uuid.uuid4().hex
    keychain = Keychain(account=account)
    stored_key = keychain.get()
    hint = "已有密钥时可留空继续使用。" if stored_key else ""
    key = ask("输入具有 Tunnels Read + Use 权限的 OpenAI Runtime key。\n仅保存在这台 Mac 的登录钥匙串。" + hint, hidden=True)
    if not key and not stored_key:
        raise DesktopError("首次配置需要填写 Runtime key。")
    if "\n" in key or "\r" in key:
        raise DesktopError("密钥不能包含换行。")
    del stored_key
    cfg = dict(previous)
    cfg.update({"version": VERSION, "computer_name": name, "tunnel_id": tunnel_id,
                "keychain_account": account, "health_port": previous.get("health_port", 8080)})
    return cfg, key


def save_connection_settings(cfg, key, info):
    """Persist connection data only; never reconstruct the workspace document."""
    if key:
        Keychain(account=cfg["keychain_account"]).set(key)
    cfg["development_path"] = development_path()
    atomic_private(SUPPORT / "tunnel-profile.yaml", render_profile(cfg, info))
    write_agent(cfg, info)
    atomic_private(settings_path(), json_bytes(cfg))


def restart_notice():
    if managed_pid() is not None:
        return "保存后将重启此应用的后台连接，正在执行的调试任务可能中断。请先保存工作进度。"
    return "保存后将尝试启动后台连接。"


def error_detail(error):
    if isinstance(error, (DesktopError, CredentialError, WorkspaceConfigError, permissions.PermissionGuideError)):
        return str(error)
    return f"系统操作失败：{type(error).__name__}"


def connection_status_text(result):
    """Describe local readiness without claiming an end-to-end ChatGPT check."""
    if result == "已连接" or result.startswith("已连接（"):
        return ("本机隧道已就绪" + result[len("已连接"):]
                + "\n此状态只确认本机隧道就绪，尚未验证 ChatGPT 工具调用。")
    return result


def apply_saved_changes(saved_message):
    if not load_settings():
        return saved_message + "。请继续完成连接设置。"
    try:
        if managed_pid() is not None:
            stop()
        return saved_message + "；" + connection_status_text(start())
    except (DesktopError, CredentialError, WorkspaceConfigError, OSError, subprocess.TimeoutExpired) as error:
        raise DesktopError(saved_message + "，但后台连接未完成更新。\n" + error_detail(error)) from None


def initial_setup():
    """Prepare the first connection and all folders before one final commit."""
    previous = load_settings() or {}
    document, fingerprint = load_workspace_document(previous)
    draft = copy.deepcopy(document)
    info = runtime()
    cfg, key = connection_draft(previous)
    if not draft["workspaces"]:
        add_project_to_draft(draft)
    if not edit_workspace_document(draft, save_label="完成目录选择"):
        raise Cancelled()
    summary = f"本机：{cfg['computer_name']}\n隧道：{cfg['tunnel_id']}\n\n{workspace_summary(draft)}\n\n{restart_notice()}"
    if message(summary, ("取消", "保存并连接")) != "保存并连接":
        raise Cancelled()
    # The full document, including manually supplied limits, stays intact.
    # No credential or formal configuration is written before confirmation.
    validate_document(SUPPORT, draft)
    if key:
        Keychain(account=cfg["keychain_account"]).set(key)
    del key
    save_document(SUPPORT, draft, fingerprint)
    save_connection_settings(cfg, "", info)
    if managed_pid() is not None:
        stop()
    return cfg


def configure_connection():
    previous = load_settings()
    if not previous:
        cfg = initial_setup()
        message(connection_status_text(start()))
        return cfg
    info = runtime()
    cfg, key = connection_draft(previous)
    summary = f"本机：{cfg['computer_name']}\n隧道：{cfg['tunnel_id']}\n\n此次只保存连接设置。项目目录及其权限继续使用当前列表。\n\n{restart_notice()}"
    if message(summary, ("取消", "保存并连接")) != "保存并连接":
        raise Cancelled()
    save_connection_settings(cfg, key, info)
    del key
    message(apply_saved_changes("连接设置已保存"))
    return cfg


def manage_projects():
    cfg = load_settings()
    document, fingerprint = load_workspace_document(cfg)
    draft = copy.deepcopy(document)
    if not edit_workspace_document(draft):
        return False
    if draft == document and fingerprint is not None:
        message("项目列表没有更改。")
        return False
    notice = restart_notice() if cfg else "项目列表将保存，完成连接设置后即可使用。"
    summary = workspace_summary(draft) + "\n\n" + notice
    if message(summary, ("取消", "保存并连接")) != "保存并连接":
        return False
    # Validate and detect outside edits before stopping any running process.
    save_document(SUPPORT, draft, fingerprint)
    message(apply_saved_changes("项目列表已保存"))
    return True


def open_logs():
    logs = SUPPORT / "logs"
    run(["/usr/bin/open", str(logs if logs.is_dir() else SUPPORT)])


def system_permissions():
    """Offer optional diagnostics only after the user opens this panel."""
    options = ("重新检查后台权限", "请求屏幕录制权限", "请求辅助功能权限", "请求键鼠事件权限",
               "打开屏幕录制设置", "打开辅助功能设置", "重启连接并重新检查", "返回")
    while True:
        document, _ = load_workspace_document()
        status = permissions.permission_status(document)
        prompt = (permissions.format_permission_status(status)
                  + "\n\n这是可选检查。普通项目文件、命令和 ChatGPT Chrome 集成无需给 Sugar 授予屏幕录制或辅助功能权限。"
                  + "\n\n请按系统设置实际列出的程序条目授权，名称不一定是 Sugar。"
                  + "\n更换应用或 Python 运行环境后，macOS 可能需要重新授权。")
        selected = choose_list(options, prompt)
        if selected is None or selected == 7:
            return
        try:
            if selected == 0:
                continue
            if selected in (1, 2, 3):
                kind = {1: "screen_capture", 2: "accessibility", 3: "post_event"}[selected]
                reply = permissions.request_permission(document, kind)
                problem = permissions.failure_message(reply)
                if problem:
                    message(problem)
                else:
                    message("后台已处理权限申请。请在 macOS 弹窗或系统设置中完成授权，再返回此处检查。\n\n申请请求成功不代表已经获得权限；如系统要求退出程序，可选择“重启连接并重新检查”。")
            elif selected in (4, 5):
                permissions.open_privacy_settings("screen_capture" if selected == 4 else "accessibility")
            elif selected == 6:
                confirm = message("重启本应用的后台连接后重新检查权限？\n正在执行的调试任务可能中断，请先保存工作进度。", ("取消", "重启并检查"), default="取消")
                if confirm != "重启并检查":
                    continue
                if managed_pid() is not None:
                    stop()
                message(connection_status_text(start()) + "\n接下来检查新后台进程的权限。")
        except Cancelled:
            continue
        except (DesktopError, CredentialError, WorkspaceConfigError,
                permissions.PermissionGuideError, OSError, subprocess.TimeoutExpired) as error:
            message(error_detail(error))


def chrome_integration_guide():
    """Explain the external Chrome integration without inspecting its state."""
    cfg = load_settings() or {}
    computer_name = cfg.get("computer_name") or socket.gethostname().split(".")[0]
    message(f"Chrome 集成使用说明\n本机：{computer_name}\n\n"
            "1. 在 ChatGPT 当前会话中启用 Chrome 集成，连接这台 Mac 的 Chrome。\n"
            "2. 选择对应的 Sugar Local MCP，让 ChatGPT 在项目目录启动开发服务。\n"
            "3. 使用 Chrome 集成打开服务地址，例如 http://127.0.0.1:3000，再操作和调试网页。\n\n"
            "多台 Mac 同时使用时，请确认所选 Sugar MCP 与 Chrome 集成连接的是同一台 Mac。\n"
            "Chrome 集成的连接状态请在 ChatGPT 中确认。\n\n"
            "普通项目文件、命令和 Chrome 集成无需给 Sugar 授予屏幕录制或辅助功能权限。")


def office_environment():
    """Inspect and explicitly verify dependencies through the active backend."""
    document, _ = load_workspace_document()
    status = environment.office_status(document)
    while True:
        selected = choose_list(("刷新依赖检测", "验证基础文档读写", "验证文档并检查 LibreOffice", "返回"),
                               environment.format_office_status(status))
        if selected is None or selected == 3:
            return
        document, _ = load_workspace_document()
        if selected == 0:
            status = environment.office_status(document, refresh=True)
        elif selected in (1, 2):
            status = environment.verify_office(document, include_libreoffice=selected == 2)


def restart_connection():
    """Restart only this app's backend after an explicit local confirmation."""
    choice = message("重启本应用管理的后台连接？\n\n正在执行的项目命令或调试任务可能中断，请先保存工作进度。",
                     ("取消", "重启连接"), default="取消")
    if choice != "重启连接":
        return False
    # Inspect ownership after the user confirms; it may change while the
    # confirmation dialog is open. start() checks it again before launching.
    status = current_status()
    if status.get("port_in_use") and not status.get("owned_listener"):
        raise DesktopError("本机隧道端口正由其他进程使用，尚未重启连接。请先处理端口占用后重试。")
    if status.get("owned_listener") or status.get("managed_pid") is not None:
        stop()
    message(connection_status_text(start()))
    return True


def configure():
    while True:
        selected = choose_list(("管理项目目录", "修改连接设置", "查看日志", "返回",
                                "系统权限检查（可选）", "Chrome 集成使用说明", "Office 环境检测与验证",
                                "重启连接"), "选择要配置的内容。")
        try:
            if selected is None or selected == 3:
                return
            if selected == 0:
                manage_projects()
            elif selected == 1:
                configure_connection()
            elif selected == 2:
                open_logs()
            elif selected == 4:
                system_permissions()
            elif selected == 5:
                chrome_integration_guide()
            elif selected == 6:
                office_environment()
            elif selected == 7:
                restart_connection()
        except Cancelled:
            continue
        except (DesktopError, CredentialError, WorkspaceConfigError, permissions.PermissionGuideError, OSError, subprocess.TimeoutExpired) as error:
            message(error_detail(error))


def preflight(info):
    result = run(["/usr/bin/arch", "-"+info["arch"], info["python"], "-c", "from sugar_local_mcp.server import build_server"], timeout=20)
    if result.returncode:
        raise DesktopError("MCP 的 Python 依赖无法加载。请查看安装日志或重新运行安装准备；当前不会启动隧道。")


def start():
    cfg = load_settings()
    if not cfg:
        cfg = initial_setup()
    with open(SUPPORT / "start.lock", "a", encoding="utf-8") as lock:
        os.chmod(SUPPORT / "start.lock", 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return "另一启动操作正在进行，请稍候。"
        status = current_status()
        if status["port_in_use"]:
            if status["owned_listener"]:
                if status["ready"] == 200:
                    return "已连接"
                raise DesktopError("此后台任务正在运行，但隧道尚未就绪。可查看日志检查密钥、权限或网络。")
            raise DesktopError(f"本机端口 {cfg['health_port']} 正由其他进程使用。请先停止原来的终端隧道或占用此端口的程序，再打开本应用。")
        document, fingerprint = load_workspace_document(cfg)
        validate_document(SUPPORT, document)
        if fingerprint is None:
            # Only a missing document is migrated from the old project_root.
            save_document(SUPPORT, document, fingerprint)
        info = runtime()
        preflight(info)
        secret = Keychain(account=cfg["keychain_account"]).get()
        if not secret:
            raise DesktopError("登录钥匙串中没有此连接的密钥，请打开“配置”重新填写。")
        del secret
        # Refresh the interpreter path after an application update. Do not replace
        # an active service; the port check above already handled that case.
        atomic_private(SUPPORT / "tunnel-profile.yaml", render_profile(cfg, info))
        write_agent(cfg, info)
        target = launch_target()
        if run(["/bin/launchctl", "print", target]).returncode == 0:
            # The previous job is stopped. Reload so it uses the updated runtime.
            result = run(["/bin/launchctl", "bootout", target])
            if result.returncode:
                raise DesktopError("无法更新已停止的后台任务，请重新登录 macOS 后重试。")
        result = run(["/bin/launchctl", "bootstrap", f"gui/{os.getuid()}", str(SUPPORT / "runtime.plist")])
        if result.returncode:
            raise DesktopError("macOS 无法启动后台任务。请打开本机日志目录检查。")
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            probe = current_status()
            if probe["owned_listener"] and probe["ready"] == 200:
                return "已连接"
            time.sleep(1)
        raise DesktopError("后台任务已提交，但连接尚未就绪。请检查网络、Runtime key 的 Tunnels 权限和 Tunnel ID。")


def daemon():
    cfg, info = load_settings(), runtime()
    if not cfg:
        raise DesktopError("尚未配置连接。")
    if health(cfg["health_port"])["port_in_use"]:
        raise DesktopError("隧道端口已经被占用，本次不会重复启动。")
    key = Keychain(interactive=False, account=cfg["keychain_account"]).get()
    if not key:
        raise DesktopError("未找到 Runtime key，请从应用配置。")
    # tunnel-client gives inherited environment settings precedence over YAML.
    # A launchctl environment from another setup must not change this route.
    client_prefixes = ("CONTROL_PLANE_", "MCP_", "HARPOON_", "HEALTH_", "ADMIN_UI_", "TUNNEL_", "LOG_")
    environment = {name: value for name, value in os.environ.items()
                   if not name.startswith(client_prefixes)}
    environment["CONTROL_PLANE_API_KEY"] = key
    environment["PATH"] = cfg["development_path"]
    os.chdir(SUPPORT)
    argv = [info["tunnel_client"], "run", "--profile-file", str(SUPPORT / "tunnel-profile.yaml"),
            "--control-plane.tunnel-id", cfg["tunnel_id"]]
    os.execve(info["tunnel_client"], argv, environment)


def runtime_upgrade_guide(current_result, *, prompt=True):
    """Read upgrade state, optionally offering an explicitly confirmed restart."""
    status = current_status()
    if not (status.get("owned_listener") and status.get("ready") == 200):
        return current_result, False
    cfg = load_settings()
    try:
        expected = render_profile(cfg, runtime())
        existing = (SUPPORT / "tunnel-profile.yaml").read_bytes()
    except (DesktopError, OSError):
        # Keep the existing connection alive when the comparison is unavailable.
        return current_result + "（运行环境版本暂未检查，可稍后重新打开应用）", True
    if existing == expected:
        return current_result, False
    pending_result = current_result + "（升级待重启；当前继续使用原后台）"
    if not prompt:
        # Returning from a configuration panel only refreshes the notice. An
        # unchanged old backend must never be restarted without confirmation.
        return pending_result, True
    choice = message("新运行环境已经准备好，当前后台仍在使用先前的运行环境。\n\n重启本应用的连接后，新版本才会生效。正在执行的任务可能中断，请先保存工作进度。", ("稍后", "重启并应用"), default="稍后")
    if choice != "重启并应用":
        return pending_result, True
    # Ownership may have changed while the native confirmation was open.
    latest = current_status()
    if latest.get("port_in_use") and not latest.get("owned_listener"):
        raise DesktopError("升级尚未应用：监听端口当前由其他进程占用。该进程没有被停止，请处理端口占用后重试。")
    if latest.get("owned_listener") or managed_pid() is not None:
        stop()
    return start(), False


def gui():
    if not load_settings():
        initial_setup()
    result = start()
    result, _ = runtime_upgrade_guide(result)
    while True:
        cfg = load_settings()
        document, _ = load_workspace_document(cfg)
        summary = f"{connection_status_text(result)}\n\n本机：{cfg['computer_name']}\nTunnel ID：{cfg['tunnel_id']}\n\n{workspace_summary(document)}\n\n在 ChatGPT 中选择对应连接，通过项目 ID 指定目录。\nSugar 提供本机文件和终端，网页操作请使用 ChatGPT 的 Chrome 集成。\n可在“配置”中查看 Chrome 说明、检查 Office 环境或重启连接。"
        choice = message(summary, ("停止连接", "配置", "完成"), default="完成")
        if choice == "停止连接":
            message(stop())
            return
        if choice != "配置":
            return
        configure()
        status = current_status()
        result = "已连接" if status["owned_listener"] and status["ready"] == 200 else "当前未连接"
        result, _ = runtime_upgrade_guide(result, prompt=False)


def main():
    global SUPPORT
    parser = argparse.ArgumentParser()
    parser.add_argument("--support", type=Path)
    parser.add_argument("action", choices=["gui", "configure", "start", "stop", "status", "daemon"])
    args = parser.parse_args()
    if args.support:
        SUPPORT = args.support.expanduser().resolve()
    SUPPORT.mkdir(mode=0o700, parents=True, exist_ok=True)
    # The downloaded and installed copies may be opened concurrently. Protect
    # the entire wizard, including its error recovery, before reading settings.
    # daemon/status never take this lock, so the GUI can still start its job.
    control_lock = None
    if args.action in {"gui", "configure", "start", "stop"}:
        control_lock = open(SUPPORT / "control.lock", "a", encoding="utf-8")
        os.chmod(SUPPORT / "control.lock", 0o600)
        try:
            fcntl.flock(control_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            control_lock.close()
            detail = "另一 Sugar Local MCP 窗口正在配置或连接。请先完成或关闭该窗口。"
            if args.action in {"gui", "configure"}:
                message(detail)
            else:
                print(detail)
            return 0
    try:
        if args.action == "gui":
            gui()
        elif args.action == "configure":
            configure()
        elif args.action == "start":
            print(start())
        elif args.action == "stop":
            print(stop())
        elif args.action == "status":
            print(json.dumps(current_status(), ensure_ascii=False, indent=2))
        else:
            daemon()
        return 0
    except Cancelled:
        return 0
    except (DesktopError, CredentialError, WorkspaceConfigError, permissions.PermissionGuideError, OSError, subprocess.TimeoutExpired) as error:
        # No exception body from a credential subprocess is propagated here.
        detail = error_detail(error)
        if args.action in {"gui", "configure"}:
            choice = message(detail, ("关闭", "查看日志", "配置"), default="关闭")
            if choice == "查看日志":
                open_logs()
            elif choice == "配置":
                try:
                    configure()
                    return 0
                except Cancelled:
                    return 0
                except (DesktopError, CredentialError, WorkspaceConfigError, permissions.PermissionGuideError, OSError, subprocess.TimeoutExpired):
                    message("配置或连接尚未完成。请查看日志后重新打开应用。")
        else:
            print(detail, file=sys.stderr)
        return 2
    finally:
        if control_lock is not None:
            control_lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
