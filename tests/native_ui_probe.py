#!/usr/bin/env python3
"""Inspect and exercise only an explicitly isolated Codex Local MCP preview.

Uses macOS Accessibility APIs without changing permissions. Refuses production
app processes. No credentials, user documents, or live connection state are read.
This is an integration QA helper, not an application feature.
"""
from __future__ import annotations

import argparse
import ctypes as C
import json
import subprocess
import sys

CF = "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
AX = "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
CG = "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
UTF8 = 0x08000100


class PreviewProbe:
    def __init__(self, pid: int):
        command = subprocess.check_output(
            ["/bin/ps", "-ww", "-p", str(pid), "-o", "command="],
            text=True, timeout=5,
        ).strip()
        if "CodexLocalMCP" not in command or "--preview" not in command.split():
            raise RuntimeError("Only an explicitly launched CodexLocalMCP --preview process is allowed.")
        self.pid = pid
        self.cf = C.CDLL(CF)
        self.ax = C.CDLL(AX)
        self.cg = C.CDLL(CG)
        self.kept = []
        self._declare(self.cf, "CFRelease", [C.c_void_p], None)
        self._declare(self.cf, "CFRetain", [C.c_void_p], C.c_void_p)
        self._declare(self.cf, "CFGetTypeID", [C.c_void_p], C.c_ulong)
        for name in ["CFStringGetTypeID", "CFArrayGetTypeID", "CFBooleanGetTypeID", "CFNumberGetTypeID"]:
            self._declare(self.cf, name, [], C.c_ulong)
        self._declare(self.cf, "CFStringCreateWithCString",
                      [C.c_void_p, C.c_char_p, C.c_uint32], C.c_void_p)
        self._declare(self.cf, "CFStringGetCString",
                      [C.c_void_p, C.c_char_p, C.c_long, C.c_uint32], C.c_bool)
        self._declare(self.cf, "CFArrayGetCount", [C.c_void_p], C.c_long)
        self._declare(self.cf, "CFArrayGetValueAtIndex", [C.c_void_p, C.c_long], C.c_void_p)
        self._declare(self.cf, "CFBooleanGetValue", [C.c_void_p], C.c_bool)
        self._declare(self.cf, "CFNumberGetValue", [C.c_void_p, C.c_int, C.c_void_p], C.c_bool)
        self._declare(self.ax, "AXIsProcessTrusted", [], C.c_bool)
        self._declare(self.ax, "AXUIElementCreateApplication", [C.c_int], C.c_void_p)
        self._declare(self.ax, "AXUIElementSetMessagingTimeout", [C.c_void_p, C.c_float], C.c_int)
        self._declare(self.ax, "AXUIElementCopyAttributeValue",
                      [C.c_void_p, C.c_void_p, C.POINTER(C.c_void_p)], C.c_int)
        self._declare(self.ax, "AXUIElementSetAttributeValue",
                      [C.c_void_p, C.c_void_p, C.c_void_p], C.c_int)
        self._declare(self.ax, "AXUIElementPerformAction",
                      [C.c_void_p, C.c_void_p], C.c_int)
        self._declare(self.cg, "CGEventCreateKeyboardEvent",
                      [C.c_void_p, C.c_uint16, C.c_bool], C.c_void_p)
        self._declare(self.cg, "CGEventSetFlags", [C.c_void_p, C.c_uint64], None)
        self._declare(self.cg, "CGEventKeyboardSetUnicodeString",
                      [C.c_void_p, C.c_ulong, C.POINTER(C.c_uint16)], None)
        self._declare(self.cg, "CGEventPostToPid", [C.c_int, C.c_void_p], None)
        if not self.ax.AXIsProcessTrusted():
            raise RuntimeError("Accessibility permission is not granted to this QA process.")
        self.app = self.keep(self.ax.AXUIElementCreateApplication(pid))
        self.ax.AXUIElementSetMessagingTimeout(self.app, 2.0)

    @staticmethod
    def _declare(lib, name, args, result):
        method = getattr(lib, name)
        method.argtypes = args
        method.restype = result

    def keep(self, value):
        if value:
            self.kept.append(value)
        return value

    def string(self, text):
        return self.keep(self.cf.CFStringCreateWithCString(None, text.encode("utf-8"), UTF8))

    def value(self, element, name):
        pointer = C.c_void_p()
        error = self.ax.AXUIElementCopyAttributeValue(element, self.string(name), C.byref(pointer))
        if error or not pointer.value:
            return None
        value = self.keep(pointer.value)
        kind = self.cf.CFGetTypeID(value)
        if kind == self.cf.CFStringGetTypeID():
            buffer = C.create_string_buffer(32768)
            return buffer.value.decode("utf-8", "replace") if self.cf.CFStringGetCString(value, buffer, len(buffer), UTF8) else "<long text>"
        if kind == self.cf.CFBooleanGetTypeID():
            return bool(self.cf.CFBooleanGetValue(value))
        if kind == self.cf.CFNumberGetTypeID():
            number = C.c_double()
            return number.value if self.cf.CFNumberGetValue(value, 13, C.byref(number)) else None
        if kind == self.cf.CFArrayGetTypeID():
            return [self.cf.CFArrayGetValueAtIndex(value, i)
                    for i in range(min(600, self.cf.CFArrayGetCount(value)))]
        return value

    def tree(self):
        result = []
        visited = set()

        def walk(element, depth, parent):
            if not element or element in visited or depth > 22 or len(result) >= 800:
                return
            visited.add(element)
            role = self.value(element, "AXRole")
            subrole = self.value(element, "AXSubrole")
            row = {
                "index": len(result), "parent": parent, "depth": depth,
                "role": role, "identifier": self.value(element, "AXIdentifier"),
                "title": self.value(element, "AXTitle"),
                "description": self.value(element, "AXDescription"),
                "enabled": self.value(element, "AXEnabled"),
            }
            if subrole == "AXSecureTextField":
                row["value"] = "<secure>"
            else:
                scalar = self.value(element, "AXValue")
                if isinstance(scalar, (str, bool, float)):
                    row["value"] = scalar
            row["_element"] = element
            result.append(row)
            # Never traverse the application menu: macOS includes unrelated recent items.
            children = self.value(element, "AXWindows" if depth == 0 else "AXChildren")
            if depth == 0 and not children:
                main = self.value(element, "AXMainWindow") or self.value(element, "AXFocusedWindow")
                children = [main] if isinstance(main, int) else []
            for child in children or []:
                walk(child, depth + 1, row["index"])

        walk(self.app, 0, None)
        return result

    def locate(self, nodes, identifier=None, title=None, role=None, index=None):
        if index is not None:
            matches = [n for n in nodes if n["index"] == index]
        else:
            matches = [n for n in nodes
                       if (not role or n["role"] == role)
                       and (not identifier or n["identifier"] == identifier)
                       and (not title or title in (n["title"], n["description"], n.get("value")))]
        if title and len(matches) > 1:
            titled = [n for n in matches if n["title"] == title]
            if titled:
                matches = titled
        if len(matches) != 1:
            raise RuntimeError(f"Expected exactly one control; found {len(matches)}.")
        return matches[0]

    def press(self, node):
        status = self.ax.AXUIElementPerformAction(node["_element"], self.string("AXPress"))
        if status:
            raise RuntimeError(f"AXPress failed: {status}")

    def set_text(self, node, text):
        if node["role"] != "AXTextField":
            raise RuntimeError("Text entry is restricted to text fields.")
        true_value = C.c_void_p.in_dll(self.cf, "kCFBooleanTrue").value
        status = self.ax.AXUIElementSetAttributeValue(
            node["_element"], self.string("AXFocused"), true_value)
        if status:
            raise RuntimeError(f"Focusing text field failed: {status}")
        # Native key events exercise the editor binding; AXValue alone can update
        # an NSTextField display without sending its text-change notification.
        self.key("command+a")
        data = text.encode("utf-16-le")
        units = (C.c_uint16 * (len(data) // 2)).from_buffer_copy(data)
        for down in [True, False]:
            event = self.keep(self.cg.CGEventCreateKeyboardEvent(None, 0, down))
            self.cg.CGEventSetFlags(event, 0)
            self.cg.CGEventKeyboardSetUnicodeString(event, len(units), units)
            self.cg.CGEventPostToPid(self.pid, event)

    def key(self, name):
        keys = {"escape": (53, 0), "enter": (36, 0), "tab": (48, 0),
                "command+a": (0, 1 << 20), "command+shift+g": (5, (1 << 20) | (1 << 17))}
        code, flags = keys[name]
        for down in [True, False]:
            event = self.keep(self.cg.CGEventCreateKeyboardEvent(None, code, down))
            self.cg.CGEventSetFlags(event, flags)
            self.cg.CGEventPostToPid(self.pid, event)

    def close(self):
        for value in reversed(self.kept):
            self.cf.CFRelease(value)
        self.kept.clear()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("action", choices=["tree", "press", "set", "key"])
    parser.add_argument("--id")
    parser.add_argument("--title")
    parser.add_argument("--role")
    parser.add_argument("--index", type=int)
    parser.add_argument("--text")
    parser.add_argument("--key", choices=["escape", "enter", "tab", "command+a", "command+shift+g"])
    args = parser.parse_args()
    probe = PreviewProbe(args.pid)
    try:
        if args.action == "key":
            if not args.key:
                parser.error("--key is required")
            probe.key(args.key)
            print(json.dumps({"ok": True, "action": "key", "key": args.key}))
            return
        nodes = probe.tree()
        if args.action == "tree":
            print(json.dumps([{k: v for k, v in n.items() if k != "_element"}
                              for n in nodes], ensure_ascii=False, indent=2))
            return
        if not args.id and not args.title and args.index is None:
            parser.error("Supply a unique --id, --title, or observed --index.")
        node = probe.locate(nodes, args.id, args.title, args.role, args.index)
        if args.action == "press":
            probe.press(node)
        elif args.action == "set":
            if args.text is None:
                parser.error("--text is required")
            probe.set_text(node, args.text)
        print(json.dumps({"ok": True, "action": args.action,
                          "identifier": node["identifier"], "title": node["title"]},
                         ensure_ascii=False))
    finally:
        probe.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        sys.exit(1)
