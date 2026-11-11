#!/usr/bin/env python3
"""Compile, but never run, the app's native AppleScript dialog templates.

Run manually on a Mac:
    python3 -B tests/native_dialog_compile.py

For a collection-only check on another OS:
    python3 -B tests/native_dialog_compile.py --collect-only

No Keychain, saved application configuration, running service, permission
request, or real folder picker is used. Compiler output is temporary; a JSON
report is written to stdout. This filename is intentionally outside unittest's
default test_*.py discovery pattern.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch


def load_desktop(source_dir):
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(source_dir))
    spec = importlib.util.spec_from_file_location("sugar_desktop_native_compile", source_dir / "desktop.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load desktop.py from the requested source directory")
    desktop = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(desktop)
    return desktop


def collect_scripts(source_dir, temporary_dir):
    desktop = load_desktop(source_dir)
    collected = [("app-entrypoint", (source_dir / "App.applescript").read_text(encoding="utf-8"))]
    sample = '中文与 spaces, "double quotes", backslash \\ 路径\n第二行\t制表符'
    folder = temporary_dir / '项目 with spaces, "quoted" and backslash\\folder'
    folder.mkdir()
    active = {"name": "", "response": ""}

    def capture(script):
        if not isinstance(script, str) or not script:
            raise AssertionError("An empty or invalid AppleScript was generated")
        collected.append((active["name"], script))
        return active["response"]

    def collect(name, callback, response):
        before = len(collected)
        active.update(name=name, response=response)
        callback()
        if len(collected) != before + 1:
            raise AssertionError(f"{name}: expected exactly one generated AppleScript")

    forbidden = AssertionError("Collection must not run commands, read configuration, or use Keychain")
    with patch.object(desktop, "dialog_script", side_effect=capture), \
         patch.object(desktop, "run", side_effect=forbidden), \
         patch.object(desktop, "Keychain", side_effect=forbidden), \
         patch.object(desktop, "load_settings", side_effect=forbidden), \
         patch.object(desktop, "load_workspace_document", side_effect=forbidden), \
         patch.object(desktop, "runtime", side_effect=forbidden):
        collect("message-one-button", lambda: desktop.message(sample), "完成")
        collect("message-three-buttons", lambda: desktop.message(
            sample, ("返回", '编辑 "项目"', "移除\\目录"), default="返回"), "返回")
        collect("confirmation-two-buttons", lambda: desktop.message(
            "确认重启？\n" + sample, ("稍后", "重启并应用"), default="稍后"), "稍后")
        collect("ask-visible", lambda: desktop.ask(sample, default=sample), "example-id")
        collect("ask-hidden", lambda: desktop.ask(sample, default="compile-test-only", hidden=True), "compile-test-only")
        collect("choose-list", lambda: desktop.choose_list(
            ("＋ 添加项目目录", "1. project — " + str(folder), sample, "保存并连接"),
            sample, cancel_label='返回 "不保存"'), "1")
        collect("choose-folder", lambda: desktop.choose_project(), str(folder))
        collect("choose-folder-with-default", lambda: desktop.choose_project(str(folder)), str(folder))
        with patch.object(desktop, "load_settings", return_value={"computer_name": sample}):
            collect("chrome-integration-guide", desktop.chrome_integration_guide, "完成")
    return collected


def compile_scripts(scripts, temporary_dir):
    checks = []
    for name, script in scripts:
        source = temporary_dir / (name + ".applescript")
        compiled = temporary_dir / (name + ".scpt")
        source.write_text(script, encoding="utf-8")
        try:
            result = subprocess.run(
                ["/usr/bin/osacompile", "-l", "AppleScript", "-o", str(compiled), str(source)],
                capture_output=True, text=True, timeout=30,
            )
            passed = result.returncode == 0 and compiled.is_file()
            check = {"name": name, "passed": passed, "returncode": result.returncode}
            if not passed:
                check["diagnostic"] = (result.stderr or result.stdout or "No compiled file was produced")[:3000]
        except (OSError, subprocess.TimeoutExpired) as error:
            check = {"name": name, "passed": False, "diagnostic": type(error).__name__}
        checks.append(check)
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source-dir", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--collect-only", action="store_true",
                        help="Validate script collection without invoking the macOS compiler")
    args = parser.parse_args()
    if not args.collect_only and (sys.platform != "darwin" or not Path("/usr/bin/osacompile").is_file()):
        print(json.dumps({"ok": False, "compiled": False,
                          "error": "Compilation requires macOS and /usr/bin/osacompile; use --collect-only elsewhere."}))
        return 2
    try:
        with tempfile.TemporaryDirectory(prefix="sugar-native-compile-") as directory:
            temporary = Path(directory)
            scripts = collect_scripts(args.source_dir.expanduser().resolve(), temporary)
            if args.collect_only:
                report = {"ok": True, "compiled": False, "mode": "collection_only",
                          "script_count": len(scripts), "scripts": [name for name, _ in scripts]}
            else:
                checks = compile_scripts(scripts, temporary)
                report = {"ok": all(check["passed"] for check in checks), "compiled": True,
                          "script_count": len(scripts), "checks": checks}
    except Exception as error:
        report = {"ok": False, "compiled": False, "error": str(error)}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
