"""Discover and verify local Office file-processing tools without opening documents.

Detection is not a rendering or native-application automation guarantee. Optional
verification uses synthetic in-memory documents. It never installs dependencies,
opens user documents, or attaches to an existing Office application session.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any


LIBRARIES = {
    "openpyxl": "openpyxl",
    "python-docx": "docx",
    "python-pptx": "pptx",
    "pandas": "pandas",
    "PyMuPDF": "fitz",
}
FORMAT_LIBRARIES = {"xlsx": "openpyxl", "docx": "python-docx", "pptx": "python-pptx"}
_MARKER = "SUGAR_OFFICE_JSON="
_MAX_OUTPUT = 65536
_MAX_CANDIDATES = 6
_CACHE_SECONDS = 60
_DISCOVERY_TIMEOUT = 3.0
_DISCOVERY_BUDGET = 12.0
_VERIFY_TIMEOUT = 12.0
_LIBREOFFICE_TIMEOUT = 8.0
_CLOSE_TIMEOUT = 2.0


class OfficeEnvironmentClosed(ValueError):
    """The owning MCP service has stopped accepting Office probes."""

_DETECTION_SCRIPT = r'''
import importlib.util, importlib.metadata, json, platform, sys
libraries = {}
for distribution, module in {"openpyxl":"openpyxl", "python-docx":"docx", "python-pptx":"pptx", "pandas":"pandas", "PyMuPDF":"fitz"}.items():
    try:
        found = importlib.util.find_spec(module) is not None
        try:
            version = importlib.metadata.version(distribution) if found else None
        except importlib.metadata.PackageNotFoundError:
            version = None
        libraries[distribution] = {"available": found, "version": version}
    except Exception as error:
        libraries[distribution] = {"available": False, "version": None, "error_type": type(error).__name__}
print("SUGAR_OFFICE_JSON=" + json.dumps({"executable":sys.executable,"python_version":platform.python_version(),"libraries":libraries}))
'''

_VERIFICATION_SCRIPT = r'''
import io, json, sys
formats = json.loads(sys.argv[1])
result = {}
for format_name in formats:
    try:
        if format_name == "xlsx":
            from openpyxl import Workbook, load_workbook
            item = Workbook()
            item.active.title = "本地验证"
            item.active["A1"] = "原始内容"
            item.active["B1"] = 12
            item.active["C1"] = "=B1*2"
            item.active["B1"].number_format = "0.00"
            buffer = io.BytesIO(); item.save(buffer); buffer.seek(0)
            loaded = load_workbook(buffer)
            loaded.active["A1"] = "修改后中文内容"
            loaded.active["B1"] = 20
            updated = io.BytesIO(); loaded.save(updated); updated.seek(0)
            final = load_workbook(updated)
            assert final.active["A1"].value == "修改后中文内容"
            assert final.active["B1"].value == 20
            assert final.active["C1"].value == "=B1*2"
            assert final.active["B1"].number_format == "0.00"
            checks = ["create", "save_reopen", "edit_existing", "chinese_text", "cell_values", "formula_text", "number_format"]
        elif format_name == "docx":
            from docx import Document
            item = Document()
            item.add_paragraph("原始中文段落")
            item.add_table(rows=1, cols=2).cell(0,0).text = "原始表格"
            buffer = io.BytesIO(); item.save(buffer); buffer.seek(0)
            loaded = Document(buffer)
            loaded.paragraphs[0].text = "修改后的中文段落"
            loaded.tables[0].cell(0,0).text = "修改后的表格"
            updated = io.BytesIO(); loaded.save(updated); updated.seek(0)
            final = Document(updated)
            assert final.paragraphs[0].text == "修改后的中文段落"
            assert final.tables[0].cell(0,0).text == "修改后的表格"
            checks = ["create", "save_reopen", "edit_existing", "chinese_paragraphs", "tables"]
        elif format_name == "pptx":
            from pptx import Presentation
            item = Presentation()
            slide = item.slides.add_slide(item.slide_layouts[0])
            slide.shapes.title.text = "原始中文标题"
            buffer = io.BytesIO(); item.save(buffer); buffer.seek(0)
            loaded = Presentation(buffer)
            loaded.slides[0].shapes.title.text = "修改后的中文标题"
            loaded.slides[0].placeholders[1].text = "修改后的中文内容"
            updated = io.BytesIO(); loaded.save(updated); updated.seek(0)
            final = Presentation(updated)
            assert final.slides[0].shapes.title.text == "修改后的中文标题"
            assert final.slides[0].placeholders[1].text == "修改后的中文内容"
            checks = ["create", "save_reopen", "edit_existing", "slides", "chinese_text"]
        else:
            raise ValueError("unsupported format")
        result[format_name] = {"state":"verified", "checks":checks}
    except Exception as error:
        result[format_name] = {"state":"failed", "error_type":type(error).__name__}
print("SUGAR_OFFICE_JSON=" + json.dumps(result))
'''


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _child_environment() -> dict[str, str]:
    # Do not pass tunnel credentials, Python injection paths, or application keys
    # to a different Python installation merely to inspect its installed tools.
    allowed = {"PATH", "HOME", "USER", "LOGNAME", "TMPDIR", "TEMP", "TMP", "LANG", "LC_ALL", "LC_CTYPE",
               "SYSTEMROOT", "WINDIR"}
    return {key: value for key, value in os.environ.items() if key in allowed}


def _stop_owned_group(process: subprocess.Popen, *, deadline: float | None = None) -> None:
    """Clean only a process/session created by this probe, including children."""
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except PermissionError:
            return
    elif process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=0.25 if deadline is None else max(0.0, min(0.25, deadline - time.monotonic())))
    except subprocess.TimeoutExpired:
        pass
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            return
    elif process.poll() is None:
        process.kill()
    try:
        process.wait(timeout=1 if deadline is None else max(0.0, min(1.0, deadline - time.monotonic())))
    except subprocess.TimeoutExpired:
        pass


class _OwnedProbe:
    """Serialize cleanup so shutdown and the worker never kill a group twice."""

    def __init__(self, process: subprocess.Popen):
        self.process = process
        self._stop_lock = threading.Lock()
        self._stopped = False

    def stop(self, *, deadline: float | None = None) -> None:
        if deadline is None:
            acquired = self._stop_lock.acquire()
        else:
            acquired = self._stop_lock.acquire(timeout=max(0.0, deadline - time.monotonic()))
        if not acquired:
            return  # Another caller already owns the bounded cleanup.
        try:
            if not self._stopped:
                _stop_owned_group(self.process, deadline=deadline)
                self._stopped = True
        finally:
            self._stop_lock.release()


def _run_probe(argv: list[str], *, timeout: float, cwd: Path | str = "/",
               owner: OfficeEnvironment | None = None) -> dict[str, Any]:
    # File-backed capture prevents verbose startup hooks from exhausting server
    # memory. Captured text is bounded and raw stderr is never returned to MCP.
    if owner is not None:
        owner._ensure_open()
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        if owner is not None:
            owner._ensure_open()
        try:
            process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=output, stderr=errors,
                                       cwd=cwd, env=_child_environment(),
                                       start_new_session=os.name == "posix")
        except (OSError, ValueError) as error:
            return {"state": "failed", "error_type": type(error).__name__}
        probe = _OwnedProbe(process)
        state = "completed"
        try:
            if owner is not None:
                # Registration checks shutdown again. A Popen already in flight
                # when close() starts is cleaned by this worker before it exits.
                owner._register_probe(probe)
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            state = "timed_out"
        finally:
            probe.stop()
            if owner is not None:
                owner._unregister_probe(probe)
        if owner is not None:
            owner._ensure_open()
        output.seek(0, os.SEEK_END)
        size = output.tell()
        output.seek(0)
        text = output.read(_MAX_OUTPUT).decode("utf-8", errors="replace")
        if size > _MAX_OUTPUT:
            state = "output_limited"
        return {"state": state, "exit_code": process.returncode, "stdout": text}


def _decode_probe(result: dict) -> dict | None:
    if result.get("state") != "completed" or result.get("exit_code") != 0:
        return None
    lines = [line[len(_MARKER):] for line in result.get("stdout", "").splitlines() if line.startswith(_MARKER)]
    if len(lines) != 1:
        return None
    try:
        value = json.loads(lines[0])
    except (ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


class OfficeEnvironment:
    """A bounded, cached detector owned by one MCP server process."""

    def __init__(self, state_dir: Path):
        self.state_dir = Path(state_dir)
        self._lock = threading.RLock()
        # This lock is never held while spawning, waiting for, or stopping a
        # subprocess. close() must not wait for status()/verify()'s long lock.
        self._probe_lock = threading.Lock()
        self._closed = threading.Event()
        self._probes: set[_OwnedProbe] = set()
        self._cached: dict | None = None
        self._cached_at = 0.0
        self._verification: dict | None = None

    def _ensure_open(self) -> None:
        if self._closed.is_set():
            raise OfficeEnvironmentClosed("Office 环境检测已关闭；请重新启动服务后再检测。")

    def _register_probe(self, probe: _OwnedProbe) -> None:
        with self._probe_lock:
            self._ensure_open()
            self._probes.add(probe)

    def _unregister_probe(self, probe: _OwnedProbe) -> None:
        with self._probe_lock:
            self._probes.discard(probe)

    def close(self) -> None:
        """Cancel only this instance's probes, without acquiring the cache lock.

        Registered process groups receive TERM then KILL within a shared bounded
        cleanup budget. In-flight spawns that register after the close flag are
        rejected and cleaned in _run_probe's finally block. No new public probe
        (including a cached status request) is accepted after shutdown.
        """
        self._closed.set()
        with self._probe_lock:
            probes = tuple(self._probes)
        deadline = time.monotonic() + _CLOSE_TIMEOUT
        for probe in probes:
            probe.stop(deadline=deadline)

    def _python_candidates(self) -> list[dict]:
        proposed = [(sys.executable, "mcp_runtime"), (shutil.which("python3"), "development_path"),
                    ("/opt/homebrew/bin/python3", "homebrew"), ("/usr/local/bin/python3", "local"),
                    ("/usr/bin/python3", "system"), (shutil.which("python"), "development_path")]
        result, seen = [], set()
        for value, source in proposed:
            if not value:
                continue
            path = os.path.abspath(os.path.expanduser(value))
            # Do not resolve symlinks here: a venv and its base interpreter may
            # share a binary while loading different site-packages.
            if path in seen or not os.path.isfile(path) or not os.access(path, os.X_OK):
                continue
            seen.add(path)
            result.append({"executable": path, "source": source})
        return result[:_MAX_CANDIDATES]

    def _applications(self) -> list[dict]:
        if sys.platform != "darwin":
            return []
        names = {"Microsoft Excel.app": "Microsoft Excel", "Microsoft Word.app": "Microsoft Word",
                 "Microsoft PowerPoint.app": "Microsoft PowerPoint", "LibreOffice.app": "LibreOffice",
                 "wpsoffice.app": "WPS Office", "WPS Office.app": "WPS Office",
                 "Pages.app": "Pages", "Numbers.app": "Numbers", "Keynote.app": "Keynote"}
        found = []
        for directory in [Path("/Applications"), Path.home() / "Applications"]:
            for name, label in names.items():
                path = directory / name
                if path.is_dir():
                    found.append({"name": label, "path": str(path), "state": "detected",
                                  "native_automation_verified": False})
        return found

    def _libreoffice_executable(self, apps: list[dict]) -> str | None:
        candidates = [str(Path(item["path"]) / "Contents/MacOS/soffice") for item in apps if item["name"] == "LibreOffice"]
        candidates.extend(filter(None, [shutil.which("soffice"), shutil.which("libreoffice")]))
        return next((path for path in candidates if os.path.isfile(path) and os.access(path, os.X_OK)), None)

    @staticmethod
    def _signature(status: dict) -> str:
        return json.dumps(status.get("python_candidates", []), sort_keys=True)

    def status(self, refresh: bool = False) -> dict:
        if type(refresh) is not bool:
            raise ValueError("refresh must be a boolean")
        self._ensure_open()
        with self._lock:
            self._ensure_open()
            if not refresh and self._cached is not None and time.monotonic() - self._cached_at < _CACHE_SECONDS:
                result = copy.deepcopy(self._cached)
                result["cached"] = True
                return result
            deadline = time.monotonic() + _DISCOVERY_BUDGET
            candidates = []
            for candidate in self._python_candidates():
                self._ensure_open()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    candidates.append({**candidate, "state": "not_checked", "reason": "discovery_time_budget", "libraries": {}})
                    continue
                raw = _run_probe([candidate["executable"], "-B", "-c", _DETECTION_SCRIPT],
                                 timeout=min(_DISCOVERY_TIMEOUT, remaining), owner=self)
                self._ensure_open()
                decoded = _decode_probe(raw)
                libraries = decoded.get("libraries") if decoded else None
                if (not isinstance(libraries, dict) or any(
                        not isinstance(libraries.get(name), dict) or type(libraries[name].get("available")) is not bool
                        for name in LIBRARIES)):
                    candidates.append({**candidate, "state": raw["state"] if raw["state"] != "completed" else "failed",
                                       "reason": "python_probe_not_verified", "libraries": {}})
                    continue
                # Return a fixed schema; a startup hook cannot add arbitrary
                # output or extra fields to a successful capability report.
                normalized = {name: {"available": libraries[name]["available"],
                                     "version": str(libraries[name]["version"])[:80] if libraries[name].get("version") is not None else None}
                              for name in LIBRARIES}
                candidates.append({**candidate, "state": "detected", "libraries": normalized,
                                   "python_version": str(decoded.get("python_version", "unknown"))[:40]})
            detected = [row for row in candidates if row["state"] == "detected"]
            selected = max(detected, key=lambda row: sum(row["libraries"][name]["available"] for name in FORMAT_LIBRARIES.values()), default=None)
            capabilities = {}
            for format_name, package in FORMAT_LIBRARIES.items():
                compatible = next((row for row in sorted(detected, key=lambda row: row is not selected)
                                   if row["libraries"][package]["available"]), None)
                capabilities[format_name] = {"state": "detected" if compatible else "missing",
                                             "library": package,
                                             "python": compatible["executable"] if compatible else None}
            apps = self._applications()
            libreoffice = self._libreoffice_executable(apps)
            result = {"ok": True, "scope": "this-machine", "pid": os.getpid(), "checked_at": _now(), "cached": False,
                      "mcp_python": sys.executable, "selected_python": selected["executable"] if selected else None,
                      "python_candidates": candidates, "apps": apps, "capabilities": capabilities,
                      "libreoffice": {"executable": libreoffice, "state": "detected" if libreoffice else "missing"},
                      "limits": {"native_office_control": False, "rendering_verified": False,
                                 "formula_calculation_verified": False, "complex_format_preservation_verified": False},
                      "note": "依赖检测不代表原生 Office 控制、公式重算或复杂版式已验证。"}
            if self._verification and self._verification["signature"] == self._signature(result):
                self._apply_verification(result, self._verification["result"])
            self._ensure_open()
            self._cached = copy.deepcopy(result)
            self._cached_at = time.monotonic()
            return result

    @staticmethod
    def _apply_verification(status: dict, verification: dict) -> None:
        status["verification"] = copy.deepcopy(verification)
        for name, value in verification["formats"].items():
            status["capabilities"][name]["state"] = value["state"]

    def verify(self, include_libreoffice: bool = False) -> dict:
        if type(include_libreoffice) is not bool:
            raise ValueError("include_libreoffice must be a boolean")
        self._ensure_open()
        with self._lock:
            self._ensure_open()
            status = self.status(refresh=True)
            groups: dict[str, list[str]] = {}
            verification = {"checked_at": _now(), "scope": "synthetic-documents-in-memory", "formats": {},
                            "user_documents_read": False, "user_documents_changed": False,
                            "rendering_verified": False, "formula_calculation_verified": False}
            for format_name, capability in status["capabilities"].items():
                executable = capability.get("python")
                if executable:
                    groups.setdefault(executable, []).append(format_name)
                else:
                    verification["formats"][format_name] = {"state": "missing", "reason": "library_not_detected"}
            for executable, formats in groups.items():
                self._ensure_open()
                raw = _run_probe([executable, "-B", "-c", _VERIFICATION_SCRIPT, json.dumps(formats)],
                                 timeout=_VERIFY_TIMEOUT, owner=self)
                self._ensure_open()
                decoded = _decode_probe(raw)
                for format_name in formats:
                    value = decoded.get(format_name) if decoded else None
                    if not isinstance(value, dict) or value.get("state") not in {"verified", "failed"}:
                        state = "timed_out" if raw["state"] == "timed_out" else "failed"
                        value = {"state": state, "reason": "document_probe_not_verified"}
                    else:
                        value = {key: value[key] for key in ("state", "checks", "error_type") if key in value}
                    verification["formats"][format_name] = {**value, "python": executable}
            self._verification = {"signature": self._signature(status), "result": copy.deepcopy(verification)}
            self._apply_verification(status, verification)
            status["libreoffice_probe"] = self._verify_libreoffice(status["libreoffice"]["executable"]) if include_libreoffice else {
                "state": "not_requested", "conversion_verified": False, "formula_calculation_verified": False}
            self._ensure_open()
            status["cached"] = False
            self._cached = copy.deepcopy(status)
            self._cached_at = time.monotonic()
            return status

    def _verify_libreoffice(self, executable: str | None) -> dict:
        self._ensure_open()
        limits = {"conversion_verified": False, "formula_calculation_verified": False, "native_control_verified": False}
        if not executable:
            return {"state": "missing", **limits}
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.TemporaryDirectory(prefix="office-profile-", dir=self.state_dir) as directory:
            profile = (Path(directory) / "profile").resolve()
            argv = [executable, "-env:UserInstallation=" + profile.as_uri(), "--headless", "--norestore",
                    "--nodefault", "--nologo", "--version"]
            result = _run_probe(argv, timeout=_LIBREOFFICE_TIMEOUT, cwd=directory, owner=self)
        self._ensure_open()
        if result["state"] != "completed" or result.get("exit_code") != 0:
            return {"state": "timed_out" if result["state"] == "timed_out" else "failed",
                    "executable": executable, "isolated_profile": True, **limits}
        lines = [line.strip() for line in result.get("stdout", "").splitlines() if line.strip().startswith("LibreOffice")]
        return {"state": "version_verified" if lines else "failed", "executable": executable,
                "version": lines[0][:200] if lines else None, "isolated_profile": True, **limits}
