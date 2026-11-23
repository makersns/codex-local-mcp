#!/usr/bin/env python3
"""Exercise the real native controls in a Codex Local MCP --preview window.

No real connection, Keychain entry, or persisted settings are modified. Requires
the host's existing Accessibility and event-posting permission; never requests it.
"""
from __future__ import annotations
import argparse
import ctypes as C
import hashlib
import json
from pathlib import Path
import time

from native_ui_probe import PreviewProbe


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    probe = PreviewProbe(args.pid)
    checks = []
    def nodes():
        return probe.tree()
    def find(identifier=None, title=None, role=None):
        return probe.locate(nodes(), identifier=identifier, title=title, role=role)
    def present(identifier):
        return any(n["identifier"] == identifier for n in nodes())
    def press(identifier=None, title=None, role=None):
        probe.press(find(identifier, title, role))
    def wait_for(predicate, timeout=6):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                result = predicate()
                if result:
                    return result
            except RuntimeError:
                pass
            time.sleep(0.12)
        raise AssertionError("Expected native UI state did not appear.")
    def set_field(identifier, value):
        probe.set_text(find(identifier), value)
        wait_for(lambda: find(identifier).get("value") == value)
    def text_present(text):
        return any(text in str(n.get(k) or "") for n in nodes()
                   for k in ["title", "description", "value"])
    def success(name):
        checks.append(name)
        print(json.dumps({"passed": name}, ensure_ascii=False), flush=True)
    report = {"scope": "native Accessibility actions against isolated preview", "ok": False}
    try:
        probe.ax.AXUIElementSetAttributeValue(
            probe.app, probe.string("AXFrontmost"),
            C.c_void_p.in_dll(probe.cf, "kCFBooleanTrue").value)
        assert all(present("nav-" + page) for page in
                   ["overview", "folders", "connection", "diagnostics", "logs"])
        success("five-navigation-controls")
        press("nav-connection")
        wait_for(lambda: find("computerName"))
        original_name = find("computerName").get("value")
        original_tunnel = find("tunnelID").get("value")
        set_field("computerName", "原生交互验收")
        wait_for(lambda: present("cancelConnection") and find("saveConnection")["enabled"])
        success("typing-updates-draft-and-save-state")

        press("nav-folders")
        wait_for(lambda: find(title="继续编辑", role="AXButton"))
        press(title="继续编辑", role="AXButton")
        assert wait_for(lambda: find("computerName")).get("value") == "原生交互验收"
        success("navigation-cancel-preserves-draft")
        press("nav-folders")
        wait_for(lambda: find(title="放弃修改", role="AXButton"))
        press(title="放弃修改", role="AXButton")
        wait_for(lambda: present("folder-0"))
        press("nav-connection")
        assert wait_for(lambda: find("computerName")).get("value") == original_name
        success("navigation-discard-restores-saved-value")

        set_field("computerName", "已保存的测试电脑")
        set_field("tunnelID", "invalid-id")
        wait_for(lambda: find("saveConnection")["enabled"])
        press("saveConnection")
        wait_for(lambda: text_present("请复制完整的连接 ID"))
        assert find("computerName").get("value") == "已保存的测试电脑"
        success("validation-error-keeps-draft")
        set_field("tunnelID", original_tunnel)
        press("saveConnection")
        wait_for(lambda: not present("cancelConnection"))
        assert find("computerName").get("value") == "已保存的测试电脑"
        success("connection-save-updates-preview-state")

        press("nav-folders")
        wait_for(lambda: present("folder-0"))
        press("folder-0")
        wait_for(lambda: present("folderAllowWrite"))
        original_write = find("folderAllowWrite").get("value")
        press("folderAllowWrite")
        wait_for(lambda: find("saveFolder")["enabled"])
        press("cancelFolder")
        wait_for(lambda: not present("saveFolder"))
        press("folder-0")
        assert wait_for(lambda: find("folderAllowWrite")).get("value") == original_write
        success("folder-cancel-preserves-permissions")

        press("folderAllowWrite")
        wait_for(lambda: find("saveFolder")["enabled"])
        press("saveFolder")
        wait_for(lambda: not present("saveFolder"))
        press("folder-0")
        assert wait_for(lambda: find("folderAllowWrite")).get("value") != original_write
        assert bool(find("folderAllowExec").get("value"))
        assert text_present("可运行任务")
        success("folder-save-keeps-execution-semantics")

        press("folderAdvanced", role="AXDisclosureTriangle")
        # SwiftUI propagates the DisclosureGroup identifier to its descendants.
        field = wait_for(lambda: find("folderAdvanced", role="AXTextField"))
        assert field.get("value") == "project"
        success("advanced-id-is-available-on-demand")
        press("cancelFolder")

        press("folder-1")
        wait_for(lambda: present("removeFolder"))
        press("removeFolder")
        wait_for(lambda: find(title="保留", role="AXButton"))
        press(title="保留", role="AXButton")
        wait_for(lambda: present("folder-1"))
        success("remove-cancel-keeps-folder")
        press("removeFolder")
        wait_for(lambda: find(title="移除并重启", role="AXButton"))
        press(title="移除并重启", role="AXButton")
        wait_for(lambda: not present("folder-1") and not present("removeFolder"))
        press("folder-0")
        wait_for(lambda: present("removeFolder"))
        assert not find("removeFolder")["enabled"]
        assert text_present("至少保留")
        success("last-folder-cannot-be-removed")

        press("cancelFolder")
        press("nav-diagnostics")
        wait_for(lambda: present("checkOffice"))
        press("checkOffice")
        wait_for(lambda: text_present("基础读写已验证"))
        press("checkPermissions")
        wait_for(lambda: text_present("屏幕录制"))
        success("manual-diagnostics-populate-results")

        press("nav-logs")
        wait_for(lambda: present("refreshLogs"))
        wait_for(lambda: text_present("示例记录"))
        press("refreshLogs")
        wait_for(lambda: text_present("示例记录"))
        success("logs-open-and-refresh")
        press("openHelp")
        wait_for(lambda: present("closeHelp"))
        assert text_present("Sugar")
        press("closeHelp")
        wait_for(lambda: not present("closeHelp"))
        success("help-opens-and-closes")

        press("nav-overview")
        wait_for(lambda: present("stopConnection"))
        press("stopConnection")
        wait_for(lambda: find(title="取消", role="AXButton"))
        press(title="取消", role="AXButton")
        wait_for(lambda: present("stopConnection"))
        success("stop-confirmation-cancel-keeps-ready-state")
        press("stopConnection")
        wait_for(lambda: find(title="停止连接", role="AXButton"))
        press(title="停止连接", role="AXButton")
        wait_for(lambda: present("startConnection"))
        press("startConnection")
        wait_for(lambda: present("stopConnection"))
        success("stop-and-start-preview-lifecycle")

        press("nav-connection")
        wait_for(lambda: present("computerName"))
        set_field("computerName", "关闭前未保存")
        wait_for(lambda: present("cancelConnection"))
        window = next(n for n in nodes() if n["role"] == "AXWindow")
        close = probe.value(window["_element"], "AXCloseButton")
        assert close
        probe.press({"_element": close})
        wait_for(lambda: find(title="继续编辑", role="AXButton"))
        press(title="继续编辑", role="AXButton")
        assert wait_for(lambda: find("computerName")).get("value") == "关闭前未保存"
        press("cancelConnection")
        wait_for(lambda: not present("cancelConnection"))
        success("window-close-protects-unsaved-work")
        report["ok"] = True
    except Exception as error:
        report["error"] = str(error)
        report["visible_controls"] = [
            {"identifier": n["identifier"], "role": n["role"], "enabled": n["enabled"]}
            for n in nodes() if n["identifier"]]
    finally:
        report["checks"] = checks
        root = Path(__file__).resolve().parent.parent
        report["swift_sha256"] = {
            str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((root / "macos").glob("*.swift"))}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        probe.close()
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
