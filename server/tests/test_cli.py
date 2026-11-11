"""CLI regressions for dependency loading and macOS architecture selection."""

from __future__ import annotations

import contextlib
import io
import json
import os
import platform
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path, PureWindowsPath
from unittest.mock import patch

from sugar_local_mcp import cli


class CLITests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.project = self.directory / "project"
        self.project.mkdir()
        self.config_path = self.directory / "workspaces.json"
        self.state_dir = self.directory / "state"
        self.config_path.write_text(json.dumps({
            "workspaces": [{"id": "demo", "root": str(self.project)}],
            "state_dir": str(self.state_dir),
        }), encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def run_cli(self, command: str, *, failure_type: str | None = None):
        arguments = [command, "--config", str(self.config_path)]
        if failure_type is None:
            argv = [sys.executable, "-m", "sugar_local_mcp", *arguments]
        else:
            # Fail at the native dependency's actual import boundary in a fresh
            # interpreter, so an already-imported test SDK cannot hide the bug.
            bootstrap = f"""
import importlib.abc
import sys
class BrokenNativeDependency(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'pydantic_core':
            raise {failure_type}("native dependency: incompatible architecture (have 'arm64', need 'x86_64')")
sys.meta_path.insert(0, BrokenNativeDependency())
from sugar_local_mcp.cli import main
sys.exit(main(sys.argv[1:]))
"""
            argv = [sys.executable, "-c", bootstrap, *arguments]
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(argv, cwd=self.directory, env=environment,
                              capture_output=True, text=True, timeout=15)

    def test_doctor_imports_real_runtime_and_keeps_config_diagnostics(self):
        result = self.run_cli("doctor")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report["runtime_import_ok"])
        self.assertIsNone(report["runtime_import_error"])
        self.assertEqual(report["python_executable"], sys.executable)
        self.assertEqual(report["process_architecture"], platform.machine())
        self.assertEqual(report["config"], str(self.config_path.resolve()))
        self.assertEqual(report["state_dir"], str(self.state_dir.resolve()))
        self.assertEqual(report["workspaces"][0]["id"], "demo")
        self.assertFalse(report["account_access_verified"])
        self.assertFalse(self.state_dir.exists())

    def test_doctor_does_not_build_or_start_server(self):
        with patch("sugar_local_mcp.server.build_server") as build_server:
            with contextlib.redirect_stdout(io.StringIO()):
                result = cli.main(["doctor", "--config", str(self.config_path)])
        self.assertEqual(result, 0)
        build_server.assert_not_called()

    def test_doctor_native_import_failure_returns_nonzero_with_json(self):
        result = self.run_cli("doctor", failure_type="ImportError")
        self.assertEqual(result.returncode, 2, result.stderr)
        report = json.loads(result.stdout)
        self.assertFalse(report["runtime_import_ok"])
        self.assertIn("incompatible architecture", report["runtime_import_error"])
        self.assertIn("architecture", report["runtime_import_hint"])
        # Installed metadata must not be mistaken for a working runtime.
        self.assertIsNotNone(report["sdk_version"])
        self.assertEqual(report["workspaces"][0]["id"], "demo")
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse(self.state_dir.exists())

    def test_serve_native_import_failure_keeps_stdio_clean(self):
        result = self.run_cli("serve", failure_type="ImportError")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("MCP runtime import failed", result.stderr)
        self.assertIn("incompatible architecture", result.stderr)
        self.assertIn("process architecture", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_unexpected_runtime_bug_is_not_masked_as_dependency_failure(self):
        for command in ("doctor", "serve"):
            with self.subTest(command=command):
                result = self.run_cli(command, failure_type="RuntimeError")
                self.assertEqual(result.returncode, 1)
                self.assertIn("Traceback", result.stderr)
                self.assertIn("RuntimeError", result.stderr)
                self.assertNotIn('"runtime_import_ok": false', result.stdout)

    def test_darwin_command_pins_supported_architecture_and_preserves_paths(self):
        executable = "/Users/Sugar's projects/MCP/.venv/bin/python"
        config_path = Path("/Users/Sugar's projects/config/workspaces.json")
        for architecture in ("arm64", "x86_64"):
            with self.subTest(architecture=architecture):
                with patch.object(cli.platform, "system", return_value="Darwin"), \
                     patch.object(cli.platform, "machine", return_value=architecture), \
                     patch.object(cli.sys, "executable", executable), \
                     patch.object(cli.os, "name", "posix"):
                    command = cli._mcp_command(config_path)
                self.assertEqual(shlex.split(command), [
                    "/usr/bin/arch", f"-{architecture}", executable, "-m", "sugar_local_mcp",
                    "serve", "--config", str(config_path),
                ])

    def test_darwin_unknown_architecture_is_not_guessed(self):
        with patch.object(cli.platform, "system", return_value="Darwin"), \
             patch.object(cli.platform, "machine", return_value="future-arch"), \
             patch.object(cli.os, "name", "posix"):
            command = cli._mcp_command(self.config_path)
        self.assertEqual(shlex.split(command)[0], sys.executable)
        self.assertNotIn("/usr/bin/arch", command)

    def test_linux_command_has_no_macos_arch_wrapper(self):
        with patch.object(cli.platform, "system", return_value="Linux"), \
             patch.object(cli.platform, "machine", return_value="arm64"), \
             patch.object(cli.os, "name", "posix"):
            command = cli._mcp_command(self.config_path)
        self.assertEqual(shlex.split(command), [
            sys.executable, "-m", "sugar_local_mcp", "serve", "--config", str(self.config_path),
        ])

    def test_windows_command_keeps_windows_quoting(self):
        executable = r"C:\Program Files\Sugar\python.exe"
        config_path = PureWindowsPath(r"C:\Users\Sugar\Workspace Config\workspaces.json")
        with patch.object(cli.platform, "system", return_value="Windows"), \
             patch.object(cli.platform, "machine", return_value="AMD64"), \
             patch.object(cli.sys, "executable", executable), \
             patch.object(cli.os, "name", "nt"):
            command = cli._mcp_command(config_path)
        self.assertEqual(command,
                         r'"C:\Program Files\Sugar\python.exe" -m sugar_local_mcp serve --config '
                         r'"C:\Users\Sugar\Workspace Config\workspaces.json"')


if __name__ == "__main__":
    unittest.main()
