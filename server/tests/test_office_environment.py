"""Environment readiness checks never open a user's Office document."""
from __future__ import annotations

import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import unquote, urlparse

from sugar_local_mcp import office_environment as module


def reply(value):
    return {"state": "completed", "exit_code": 0, "stdout": module._MARKER + json.dumps(value) + "\n"}


def detected(*available, version="1.0"):
    return {"python_version": "3.12.1", "libraries": {
        name: {"available": name in available, "version": version if name in available else None}
        for name in module.LIBRARIES}}


class OfficeEnvironmentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.environment = module.OfficeEnvironment(self.root / "private state")
        self.addCleanup(self.environment.close)
        self.candidates = [{"executable": "/runtime/python", "source": "mcp_runtime"},
                           {"executable": "/local/python", "source": "development_path"}]
        self.addCleanup(patch.stopall)
        patch.object(self.environment, "_python_candidates", return_value=self.candidates).start()
        patch.object(self.environment, "_applications", return_value=[]).start()
        patch.object(self.environment, "_libreoffice_executable", return_value=None).start()

    def test_selects_external_libraries_without_claiming_the_mcp_runtime_has_them(self):
        def run(argv, **kwargs):
            return reply(detected() if argv[0] == "/runtime/python" else detected(*module.FORMAT_LIBRARIES.values()))
        with patch.object(module, "_run_probe", side_effect=run):
            result = self.environment.status()
        self.assertEqual(result["selected_python"], "/local/python")
        self.assertEqual(result["mcp_python"], sys.executable)
        self.assertFalse(result["python_candidates"][0]["libraries"]["openpyxl"]["available"])
        self.assertEqual(result["capabilities"]["xlsx"]["state"], "detected")
        self.assertFalse(result["limits"]["formula_calculation_verified"])
        self.assertNotIn("verification", result)

    def test_detection_is_cached_and_returned_objects_cannot_corrupt_it(self):
        with patch.object(module, "_run_probe", return_value=reply(detected("openpyxl"))) as run:
            result = self.environment.status()
            result["capabilities"]["xlsx"]["state"] = "invented"
            cached = self.environment.status()
            self.assertEqual(run.call_count, 2)
            self.assertTrue(cached["cached"])
            self.assertEqual(cached["capabilities"]["xlsx"]["state"], "detected")
            self.environment.status(refresh=True)
            self.assertEqual(run.call_count, 4)

    def test_timeout_or_malformed_probe_never_marks_a_python_ready(self):
        outcomes = [{"state": "timed_out", "exit_code": -15, "stdout": ""},
                    reply({"libraries": {"openpyxl": {"available": "true"}}})]
        with patch.object(module, "_run_probe", side_effect=outcomes):
            result = self.environment.status()
        self.assertIsNone(result["selected_python"])
        self.assertEqual(result["python_candidates"][0]["state"], "timed_out")
        self.assertEqual(result["python_candidates"][1]["state"], "failed")
        self.assertTrue(all(item["state"] == "missing" for item in result["capabilities"].values()))

    def test_verification_detects_broken_imports_even_after_dependencies_are_detected(self):
        def run(argv, **kwargs):
            if argv[3] == module._DETECTION_SCRIPT:
                return reply(detected(*module.FORMAT_LIBRARIES.values()))
            return reply({name: {"state": "failed", "error_type": "ImportError"}
                          for name in json.loads(argv[4])})
        with patch.object(module, "_run_probe", side_effect=run):
            result = self.environment.verify()
        self.assertTrue(result["ok"])
        self.assertTrue(all(item["state"] == "failed" for item in result["capabilities"].values()))
        self.assertFalse(result["verification"]["user_documents_changed"])
        self.assertEqual(result["libreoffice_probe"]["state"], "not_requested")

    def test_individual_formats_can_use_different_detected_interpreters(self):
        executions = []
        def run(argv, **kwargs):
            if argv[3] == module._DETECTION_SCRIPT:
                return reply(detected("openpyxl") if argv[0] == "/runtime/python" else detected("python-docx", "python-pptx"))
            executions.append((argv[0], json.loads(argv[4])))
            return reply({name: {"state": "verified", "checks": ["edit_existing"]} for name in json.loads(argv[4])})
        with patch.object(module, "_run_probe", side_effect=run):
            result = self.environment.verify()
        self.assertEqual(dict(executions), {"/runtime/python": ["xlsx"], "/local/python": ["docx", "pptx"]})
        self.assertTrue(all(item["state"] == "verified" for item in result["capabilities"].values()))
        self.assertFalse(result["limits"]["rendering_verified"])

    def test_changed_library_version_invalidates_previous_verification(self):
        current = "1.0"
        def run(argv, **kwargs):
            if argv[3] == module._DETECTION_SCRIPT:
                return reply(detected(*module.FORMAT_LIBRARIES.values(), version=current))
            return reply({name: {"state": "verified", "checks": []} for name in json.loads(argv[4])})
        with patch.object(module, "_run_probe", side_effect=run):
            self.environment.verify()
            self.assertEqual(self.environment.status()["capabilities"]["xlsx"]["state"], "verified")
            current = "2.0"
            result = self.environment.status(refresh=True)
        self.assertEqual(result["capabilities"]["xlsx"]["state"], "detected")
        self.assertNotIn("verification", result)

    def test_libreoffice_check_uses_unique_profile_and_does_not_claim_rendering(self):
        # macOS commonly exposes its temporary root through /var -> /private/var.
        # Exercise an explicit alias on every platform so lexical containment
        # cannot accidentally stand in for containment of the canonical paths.
        self.environment.state_dir.mkdir(parents=True)
        linked_state = self.root / "state-alias"
        linked_state.symlink_to(self.environment.state_dir, target_is_directory=True)
        self.environment.state_dir = linked_state
        invocations = []
        def run(argv, **kwargs):
            invocations.append(argv)
            profile_argument = next(item for item in argv if item.startswith("-env:UserInstallation="))
            profile = Path(unquote(urlparse(profile_argument.split("=", 1)[1]).path))
            self.assertTrue(profile.parent.is_dir())
            self.assertTrue(profile.is_relative_to(self.environment.state_dir.resolve()))
            self.assertEqual(profile.parent, Path(kwargs["cwd"]).resolve())
            return {"state": "completed", "exit_code": 0, "stdout": "LibreOffice 25.2.1\n"}
        with patch.object(module, "_run_probe", side_effect=run):
            result = self.environment._verify_libreoffice("/Applications/LibreOffice.app/Contents/MacOS/soffice")
        self.assertEqual(result["state"], "version_verified")
        self.assertTrue(result["isolated_profile"])
        self.assertFalse(result["conversion_verified"])
        self.assertFalse(result["formula_calculation_verified"])
        self.assertIn("--headless", invocations[0])
        self.assertIn("--norestore", invocations[0])
        self.assertEqual(list(self.environment.state_dir.iterdir()), [])

    def test_libreoffice_timeout_is_not_reported_as_missing_or_ready(self):
        with patch.object(module, "_run_probe", return_value={"state": "timed_out", "exit_code": -15, "stdout": ""}):
            result = self.environment._verify_libreoffice("/tools/soffice")
        self.assertEqual(result["state"], "timed_out")
        self.assertFalse(result["conversion_verified"])

    def test_boolean_flags_are_strict(self):
        for invalid in (1, "false", None):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    self.environment.status(refresh=invalid)
                with self.assertRaises(ValueError):
                    self.environment.verify(include_libreoffice=invalid)

    def test_closed_environment_rejects_cached_status_and_all_new_probes(self):
        with patch.object(module, "_run_probe", return_value=reply(detected("openpyxl"))):
            self.environment.status()
        self.environment.close()
        self.environment.close()  # Idempotent even when no subprocess was active.
        with patch.object(module.subprocess, "Popen") as popen:
            for operation in (self.environment.status,
                              lambda: self.environment.status(refresh=True),
                              self.environment.verify,
                              lambda: self.environment.verify(include_libreoffice=True),
                              lambda: self.environment._verify_libreoffice("/tools/soffice"),
                              lambda: module._run_probe([sys.executable], timeout=1, owner=self.environment)):
                with self.subTest(operation=operation):
                    with self.assertRaises(module.OfficeEnvironmentClosed):
                        operation()
            popen.assert_not_called()


class ProbeLifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.environment = module.OfficeEnvironment(self.root / "state")
        self.addCleanup(self.environment.close)

    def _worker(self, operation):
        errors = []

        def work():
            try:
                operation()
            except Exception as error:
                errors.append(error)

        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        return worker, errors

    def _assert_cancelled(self, worker, errors, probe):
        worker.join(timeout=3)
        self.assertFalse(worker.is_alive(), "The probe worker did not finish after close()")
        self.assertEqual(len(errors), 1, errors)
        self.assertIsInstance(errors[0], module.OfficeEnvironmentClosed)
        self.assertIsNotNone(probe.process.poll(), "The owned subprocess survived shutdown")
        self.assertEqual(self.environment._probes, set())

    def test_close_terminates_a_real_status_probe_without_waiting_for_status_lock(self):
        probes = []
        registered = threading.Event()
        ready_file = self.root / "sleeping"
        original_register = self.environment._register_probe

        def register(probe):
            original_register(probe)
            probes.append(probe)
            registered.set()

        # Ignore TERM so this test also exercises the bounded KILL escalation.
        script = ("import pathlib, signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                  f"pathlib.Path({str(ready_file)!r}).touch(); time.sleep(30)")
        candidates = [{"executable": sys.executable, "source": "mcp_runtime"}] * 2
        with patch.object(self.environment, "_python_candidates", return_value=candidates), \
             patch.object(self.environment, "_register_probe", side_effect=register), \
             patch.object(module, "_DETECTION_SCRIPT", script):
            worker, errors = self._worker(lambda: self.environment.status(refresh=True))
            try:
                self.assertTrue(registered.wait(timeout=3))
                deadline = time.monotonic() + 3
                while not ready_file.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(ready_file.exists())
                started = time.monotonic()
                self.environment.close()
                self.assertLess(time.monotonic() - started, module._CLOSE_TIMEOUT + 0.5)
                self._assert_cancelled(worker, errors, probes[0])
                self.assertEqual(len(probes), 1, "Cancellation must prevent the next interpreter probe")
            finally:
                self.environment.close()
                worker.join(timeout=3)

    def test_probe_registering_after_close_is_rejected_and_cleaned(self):
        spawned = threading.Event()
        release_registration = threading.Event()
        probes = []
        original_register = self.environment._register_probe

        def delayed_register(probe):
            probes.append(probe)
            spawned.set()
            release_registration.wait(timeout=3)
            original_register(probe)

        with patch.object(self.environment, "_register_probe", side_effect=delayed_register):
            worker, errors = self._worker(lambda: module._run_probe(
                [sys.executable, "-B", "-c", "import time; time.sleep(30)"],
                timeout=30, owner=self.environment))
            try:
                self.assertTrue(spawned.wait(timeout=3))
                started = time.monotonic()
                self.environment.close()
                self.assertLess(time.monotonic() - started, module._CLOSE_TIMEOUT + 0.5)
                release_registration.set()
                self._assert_cancelled(worker, errors, probes[0])
            finally:
                release_registration.set()
                self.environment.close()
                worker.join(timeout=3)
                for probe in probes:
                    probe.stop()

    def test_libreoffice_profile_is_removed_when_real_probe_is_cancelled(self):
        probes = []
        registered = threading.Event()
        original_register = self.environment._register_probe
        original_run = module._run_probe
        profiles = []

        def register(probe):
            original_register(probe)
            probes.append(probe)
            registered.set()

        def sleeping_office(argv, **kwargs):
            profiles.append(Path(kwargs["cwd"]))
            self.assertTrue(profiles[-1].is_dir())
            return original_run([sys.executable, "-B", "-c", "import time; time.sleep(30)"], **kwargs)

        with patch.object(self.environment, "_register_probe", side_effect=register), \
             patch.object(module, "_run_probe", side_effect=sleeping_office):
            worker, errors = self._worker(lambda: self.environment._verify_libreoffice("/test/soffice"))
            try:
                self.assertTrue(registered.wait(timeout=3))
                self.environment.close()
                self._assert_cancelled(worker, errors, probes[0])
                self.assertEqual(len(profiles), 1)
                self.assertFalse(profiles[0].exists())
                self.assertEqual(list(self.environment.state_dir.iterdir()), [])
            finally:
                self.environment.close()
                worker.join(timeout=3)

    def test_close_does_not_stop_another_environment_probe(self):
        other = module.OfficeEnvironment(self.root / "other-state")
        ready = threading.Event()
        probes = []
        original_register = other._register_probe

        def register(probe):
            original_register(probe)
            probes.append(probe)
            ready.set()

        with patch.object(other, "_register_probe", side_effect=register):
            worker, errors = self._worker(lambda: module._run_probe(
                [sys.executable, "-B", "-c", "import time; time.sleep(30)"], timeout=30, owner=other))
            try:
                self.assertTrue(ready.wait(timeout=3))
                self.environment.close()
                self.assertIsNone(probes[0].process.poll())
                self.assertFalse(other._closed.is_set())
            finally:
                other.close()
                worker.join(timeout=3)
            self.assertFalse(worker.is_alive())
            self.assertEqual(len(errors), 1, errors)
            self.assertIsInstance(errors[0], module.OfficeEnvironmentClosed)
            self.assertIsNotNone(probes[0].process.poll())


class ProbeIsolationTests(unittest.TestCase):
    def test_process_environment_does_not_inherit_tunnel_or_python_injection_secrets(self):
        with patch.dict(os.environ, {"CONTROL_PLANE_API_KEY": "test-key", "OPENAI_API_KEY": "test-key",
                                     "PYTHONPATH": "/untrusted/project", "PYTHONSTARTUP": "/untrusted/start.py"}):
            result = module._child_environment()
        self.assertNotIn("CONTROL_PLANE_API_KEY", result)
        self.assertNotIn("OPENAI_API_KEY", result)
        self.assertNotIn("PYTHONPATH", result)
        self.assertNotIn("PYTHONSTARTUP", result)

    def test_same_binary_virtual_environments_are_not_merged(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            venv = root / "venv-python"
            external = root / "external-python"
            venv.symlink_to(sys.executable)
            external.symlink_to(sys.executable)
            with patch.object(module.sys, "executable", str(venv)), \
                 patch.object(module.shutil, "which", return_value=str(external)):
                candidates = module.OfficeEnvironment(root / "state")._python_candidates()
        paths = [item["executable"] for item in candidates]
        self.assertIn(str(venv), paths)
        self.assertIn(str(external), paths)

    def test_real_probe_timeout_is_bounded_and_cleans_its_process(self):
        result = module._run_probe([sys.executable, "-B", "-c", "import time; time.sleep(30)"], timeout=0.1)
        self.assertEqual(result["state"], "timed_out")
        self.assertIsNotNone(result["exit_code"])

    def test_real_synthetic_edit_roundtrip_when_libraries_are_present(self):
        formats = [name for name, library in module.FORMAT_LIBRARIES.items()
                   if importlib.util.find_spec(module.LIBRARIES[library]) is not None]
        if not formats:
            self.skipTest("The test environment does not contain optional Office libraries")
        result = module._run_probe([sys.executable, "-B", "-c", module._VERIFICATION_SCRIPT, json.dumps(formats)], timeout=12)
        decoded = module._decode_probe(result)
        self.assertIsNotNone(decoded)
        for name in formats:
            self.assertEqual(decoded[name]["state"], "verified", decoded)
            self.assertIn("edit_existing", decoded[name]["checks"])


if __name__ == "__main__":
    unittest.main()
