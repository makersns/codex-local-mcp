"""Exercise the native bridge's observable contract with isolated configuration.

Every launchctl, Keychain and diagnostic operation is replaced before a request
can reach it. File validation uses the real server schema on temporary folders.
"""
from __future__ import annotations

import copy
import ctypes
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "server/src")]
import desktop
import desktop_api as api
import security_store


class NativeAPITests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.support = self.root / "Application Support"
        self.support.mkdir()
        self.project = self.root / "我的项目 with spaces"
        self.project.mkdir()
        self.other = self.root / "backend"
        self.other.mkdir()
        self.document = {
            "workspaces": [
                {"id": "frontend", "root": str(self.project), "allow_write": True, "allow_exec": False},
                {"id": "backend", "root": str(self.other), "allow_write": False, "allow_exec": True},
            ],
            "state_dir": str(self.root / "custom-state"),
            "max_file_bytes": 2097152,
            "max_scan_entries": 12000,
            "max_output_chars": 72000,
        }
        self.config = {"computer_name": "我的 Mac", "tunnel_id": "tunnel_" + "a" * 32,
                       "keychain_account": "fixture-existing", "health_port": 8080,
                       "development_path": "/fixture/bin:/usr/bin", "version": "0.5.0",
                       "legacy_note": "preserve-me"}
        self.write_fixture()
        self.controller = api.DesktopAPI(self.support)
        # Production semantics are exercised only after replacing all capabilities.
        self.controller.isolated = False
        self.keychain = self.mock(api, "Keychain")
        self.keychain.return_value.exists.return_value = True
        self.keychain.return_value.get.side_effect = AssertionError("Metadata operations cannot read a secret")
        self.mock(desktop, "Keychain", side_effect=AssertionError("Unexpected legacy credential access"))
        self.runtime = self.mock(desktop, "runtime", return_value={
            "python": "/fixture/python", "tunnel_client": "/fixture/tunnel-client", "arch": "arm64"})
        self.probe = self.mock(desktop, "current_status", return_value={
            "port_in_use": False, "owned_listener": False, "managed_pid": None, "ready": None})
        self.start = self.mock(desktop, "start", side_effect=AssertionError("Unexpected service start"))
        self.stop = self.mock(desktop, "stop", side_effect=AssertionError("Unexpected service stop"))
        self.mock(desktop, "run", side_effect=AssertionError("Unexpected shell execution"))
        self.mock(desktop, "render_profile", return_value=b"fixture-profile")
        self.mock(desktop, "development_path", return_value="/fixture/bin:/usr/bin")
        self.mock(desktop.permissions, "backend_request", side_effect=AssertionError("Unexpected diagnostics"))
        self.mock(desktop, "initial_setup", side_effect=AssertionError("No legacy setup dialog"))

    def mock(self, target, name, **kwargs):
        patcher = patch.object(target, name, **kwargs)
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def write_fixture(self):
        (self.support / "settings.json").write_bytes(api._json_bytes(self.config))
        (self.support / "workspaces.json").write_bytes(api._json_bytes(self.document))

    def request(self, action, payload=None):
        return self.controller.handle({"api_version": 1, "action": action, "payload": payload or {}})

    def snapshot(self):
        result = self.request("snapshot")
        self.assertTrue(result["ok"], result)
        return result["data"]

    def revision(self):
        return api._revision((self.support / "settings.json").read_bytes()
                             if (self.support / "settings.json").exists() else None,
                             (self.support / "workspaces.json").read_bytes()
                             if (self.support / "workspaces.json").exists() else None)

    def connection_payload(self, **overrides):
        result = {"expected_revision": self.revision(),
                  "connection": {"computer_name": "更新后的 Mac", "tunnel_id": self.config["tunnel_id"]},
                  "restart": False}
        result.update(overrides)
        return result

    def assert_error(self, result, code):
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["error"]["code"], code, result)
        return result["error"]

    def assert_no_staging(self):
        self.assertEqual(list(self.support.glob(".codex-save-*")), [])
        self.assertEqual(list(self.support.glob(".workspace-validate-*")), [])

    def test_snapshot_preserves_options_without_credentials_or_side_effects(self):
        before = {p.name: p.read_bytes() for p in self.support.iterdir()}
        snapshot = self.snapshot()
        self.assertEqual(snapshot["workspace_document"], self.document)
        self.assertEqual(snapshot["app"], {"name": "Codex Local MCP", "version": "0.6.0", "author": "Sugar"})
        self.assertEqual(snapshot["connection"]["credential_status"], "stored")
        self.assertNotIn("keychain_account", snapshot["connection"])
        self.assertNotIn("development_path", json.dumps(snapshot))
        self.keychain.assert_called_once_with(interactive=False, account="fixture-existing")
        self.keychain.return_value.get.assert_not_called()
        self.start.assert_not_called()
        self.stop.assert_not_called()
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.support.iterdir()})

    def test_empty_snapshot_never_creates_files_or_contacts_real_services(self):
        empty = self.root / "not-yet-created"
        result = api.DesktopAPI(empty).handle({"action": "snapshot"})
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["data"]["connection"]["configured"])
        self.assertEqual(result["data"]["workspace_document"]["workspaces"], [])
        self.assertFalse(empty.exists())
        self.keychain.assert_not_called()
        self.probe.assert_not_called()

    def test_bad_protocol_requests_are_rejected_without_side_effects(self):
        variants = [
            ([], "invalid_request"), ({"action": "snapshot", "extra": 1}, "invalid_request"),
            ({"action": "snapshot", "api_version": True}, "unsupported_version"),
            ({"action": "snapshot", "api_version": 2}, "unsupported_version"),
            ({"action": ["snapshot"]}, "unknown_action"), ({"action": "shell"}, "unknown_action"),
            ({"action": "snapshot", "payload": []}, "invalid_request"),
            ({"action": "snapshot", "payload": {"restart": True}}, "invalid_request"),
        ]
        for request, expected in variants:
            with self.subTest(request=request):
                self.assert_error(self.controller.handle(request), expected)
        self.keychain.assert_not_called()
        self.probe.assert_not_called()
        self.assertFalse((self.support / "control.lock").exists())

    def test_revision_detects_settings_and_workspace_external_changes(self):
        for filename in ("settings.json", "workspaces.json"):
            with self.subTest(filename=filename):
                self.write_fixture()
                payload = self.connection_payload(runtime_key="fixture-never-saved")
                target = self.support / filename
                target.write_bytes(target.read_bytes() + b" ")
                outside = target.read_bytes()
                self.assert_error(self.request("save_connection", payload), "conflict")
                self.assertEqual(target.read_bytes(), outside)
        self.keychain.assert_not_called()
        self.start.assert_not_called()
        self.assert_no_staging()

    def test_edit_during_validation_is_detected_before_writing(self):
        expected = self.revision()
        original = api.validate_document
        external = {**self.config, "computer_name": "External writer"}
        def validate_and_change(support, document):
            original(support, document)
            (support / "settings.json").write_bytes(api._json_bytes(external))
        with patch.object(api, "validate_document", side_effect=validate_and_change):
            result = self.request("save_workspaces", {"expected_revision": expected,
                                 "document": self.document, "restart": False})
        self.assert_error(result, "conflict")
        self.assertEqual(json.loads((self.support / "settings.json").read_text()), external)
        self.assertEqual(json.loads((self.support / "workspaces.json").read_text()), self.document)
        self.keychain.assert_not_called()
        self.assert_no_staging()

    def test_existing_control_lock_prevents_a_second_mutation(self):
        payload = self.connection_payload()
        with (self.support / "control.lock").open("a") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assert_error(self.request("save_connection", payload), "busy")
        self.keychain.assert_not_called()
        self.start.assert_not_called()
        self.assert_no_staging()

    def test_save_connection_retains_blank_key_and_complete_workspace_bytes(self):
        workspace_bytes = (self.support / "workspaces.json").read_bytes()
        result = self.request("save_connection", self.connection_payload(runtime_key=""))
        self.assertTrue(result["ok"], result)
        saved = json.loads((self.support / "settings.json").read_text())
        self.assertEqual(saved["keychain_account"], self.config["keychain_account"])
        self.assertEqual(saved["legacy_note"], "preserve-me")
        self.assertEqual(saved["computer_name"], "更新后的 Mac")
        self.assertEqual((self.support / "workspaces.json").read_bytes(), workspace_bytes)
        self.keychain.return_value.set.assert_not_called()
        self.assertTrue(result["data"]["saved"])
        self.assertTrue(result["data"]["restart_required"])
        self.assertEqual((self.support / "settings.json").stat().st_mode & 0o777, 0o600)
        self.start.assert_not_called()
        self.assert_no_staging()

    def test_workspace_add_edit_remove_preserves_limits_and_existing_permissions(self):
        for operation in ("add", "edit", "remove"):
            with self.subTest(operation=operation):
                self.write_fixture()
                document = copy.deepcopy(self.document)
                if operation == "add":
                    document["workspaces"].append({"id": "reference", "root": str(self.other),
                                                   "allow_write": False, "allow_exec": False})
                elif operation == "edit":
                    document["workspaces"][0]["id"] = "web"
                else:
                    document["workspaces"].pop(1)
                result = self.request("save_workspaces", {"expected_revision": self.revision(),
                                     "document": document, "restart": False})
                self.assertTrue(result["ok"], result)
                self.assertEqual(json.loads((self.support / "workspaces.json").read_text()), document)
                self.assertEqual(json.loads((self.support / "settings.json").read_text()), self.config)
                self.assertFalse(document["workspaces"][0]["allow_exec"])
                self.assertEqual((self.support / "workspaces.json").stat().st_mode & 0o777, 0o600)
        self.start.assert_not_called()
        self.assert_no_staging()

    def test_invalid_workspace_drafts_do_not_write_or_restart(self):
        variants = []
        for name, value in (("id", "中文"), ("allow_exec", "false"), ("root", "relative"),
                            ("root", str(self.root / "missing")), ("root", str(self.support))):
            document = copy.deepcopy(self.document)
            document["workspaces"][0][name] = value
            variants.append(document)
        variants += [
            {**self.document, "workspaces": []},
            {**self.document, "workspaces": [self.document["workspaces"][0]] * 2},
            {**self.document, "workspaces": [{**self.document["workspaces"][0], "id": "p" + str(i)}
                                            for i in range(33)]},
            {**self.document, "state_dir": str(self.project / "unsafe-state")},
            {**self.document, "max_file_bytes": -1},
            {**self.document, "future_option": "must-not-echo"},
        ]
        before = (self.support / "workspaces.json").read_bytes()
        for document in variants:
            with self.subTest(document=document):
                result = self.request("save_workspaces", {"expected_revision": self.revision(),
                                     "document": document, "restart": True})
                self.assert_error(result, "validation")
                self.assertEqual((self.support / "workspaces.json").read_bytes(), before)
                self.assertNotIn("must-not-echo", json.dumps(result))
                self.assert_no_staging()
        self.start.assert_not_called()
        self.stop.assert_not_called()

    def test_suggestion_handles_unicode_and_spaces_without_saving(self):
        before = (self.support / "workspaces.json").read_bytes()
        result = self.request("suggest_workspace", {"root": str(self.project),
                                                 "existing_ids": ["with-spaces"]})
        self.assertTrue(result["ok"], result)
        row = result["data"]["workspace"]
        self.assertEqual(row["root"], str(self.project))
        self.assertTrue(row["allow_write"])
        self.assertFalse(row["allow_exec"])
        self.assertRegex(row["id"], r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
        self.assertEqual((self.support / "workspaces.json").read_bytes(), before)
        self.assertFalse((self.support / "control.lock").exists())

    def test_first_setup_validates_all_fields_before_creating_credentials(self):
        (self.support / "settings.json").unlink()
        (self.support / "workspaces.json").unlink()
        result = self.request("setup", self.connection_payload(runtime_key="fixture-secret",
                             document={**self.document, "workspaces": []}))
        self.assert_error(result, "validation")
        self.keychain.assert_not_called()
        self.assertFalse((self.support / "settings.json").exists())
        self.assertFalse((self.support / "workspaces.json").exists())

    def test_first_setup_saves_both_files_and_no_secret_is_returned(self):
        (self.support / "settings.json").unlink()
        (self.support / "workspaces.json").unlink()
        secret = "fictional-bridge-credential"
        result = self.request("setup", self.connection_payload(runtime_key=secret, document=self.document))
        self.assertTrue(result["ok"], result)
        cfg = json.loads((self.support / "settings.json").read_text())
        self.assertTrue(cfg["keychain_account"].startswith("desktop-"))
        self.keychain.return_value.set.assert_called_once_with(secret)
        self.assertEqual(json.loads((self.support / "workspaces.json").read_text()), self.document)
        self.assertNotIn(secret, json.dumps(result))
        self.assertNotIn(secret, (self.support / "settings.json").read_text())
        self.assert_no_staging()

    def test_keychain_failure_leaves_both_documents_unchanged(self):
        before = {name: (self.support / name).read_bytes()
                  for name in ("settings.json", "workspaces.json")}
        self.keychain.return_value.set.side_effect = security_store.CredentialError("钥匙串不可用")
        result = self.request("setup", self.connection_payload(runtime_key="fixture-new-secret",
                             document=self.document))
        self.assert_error(result, "operation_failed")
        for name, raw in before.items():
            self.assertEqual((self.support / name).read_bytes(), raw)
        self.start.assert_not_called()

    def test_second_file_failure_restores_first_and_removes_only_new_credential(self):
        before = {name: (self.support / name).read_bytes()
                  for name in ("settings.json", "workspaces.json")}
        original_replace = os.replace
        calls = 0
        def fail_second(source, target):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated full disk")
            return original_replace(source, target)
        with patch.object(api.os, "replace", side_effect=fail_second):
            result = self.request("setup", self.connection_payload(runtime_key="fixture-new-secret",
                                 document={**self.document, "max_scan_entries": 13000}))
        self.assert_error(result, "save_failed")
        self.assertFalse(result["error"]["saved"])
        for name, raw in before.items():
            self.assertEqual((self.support / name).read_bytes(), raw)
        self.keychain.return_value.delete.assert_called_once()
        created_accounts = [c.kwargs["account"] for c in self.keychain.call_args_list
                            if c.kwargs.get("interactive") is not False]
        self.assertEqual(len(created_accounts), 1)
        self.assertNotEqual(created_accounts[0], self.config["keychain_account"])
        self.start.assert_not_called()
        self.assert_no_staging()

    def test_first_setup_failure_does_not_leave_half_configuration(self):
        (self.support / "settings.json").unlink()
        (self.support / "workspaces.json").unlink()
        original_replace = os.replace
        def fail_settings(source, target):
            if Path(target).name == "settings.json":
                raise OSError("simulated full disk")
            return original_replace(source, target)
        with patch.object(api.os, "replace", side_effect=fail_settings):
            result = self.request("setup", self.connection_payload(runtime_key="fixture-new-secret",
                                 document=self.document))
        self.assert_error(result, "save_failed")
        self.assertFalse((self.support / "settings.json").exists())
        self.assertFalse((self.support / "workspaces.json").exists())
        self.keychain.return_value.delete.assert_called_once()
        self.assert_no_staging()

    def test_saved_configuration_survives_failed_connection_with_explicit_result(self):
        self.start.side_effect = desktop.DesktopError("网络暂不可达")
        result = self.request("save_connection", self.connection_payload(restart=True))
        error = self.assert_error(result, "connection_failed")
        self.assertTrue(error["saved"])
        self.assertTrue(error["retryable"])
        self.assertEqual(result["data"]["snapshot"]["connection"]["computer_name"], "更新后的 Mac")
        self.assertTrue(result["data"]["restart_required"])
        self.assertEqual(json.loads((self.support / "settings.json").read_text())["computer_name"], "更新后的 Mac")
        self.start.assert_called_once()

    def test_saved_configuration_remains_when_another_program_owns_port(self):
        self.probe.return_value = {"port_in_use": True, "owned_listener": False, "managed_pid": 4321}
        result = self.request("save_connection", self.connection_payload(restart=True))
        self.assert_error(result, "connection_failed")
        self.assertTrue(result["error"]["saved"])
        self.assertEqual(result["data"]["snapshot"]["status"]["state"], "port_conflict")
        self.stop.assert_not_called()
        self.start.assert_not_called()

    def test_stop_restart_require_confirmation_and_refuse_unmanaged_listeners(self):
        self.probe.return_value = {"port_in_use": True, "owned_listener": False, "managed_pid": 4321}
        for action in ("stop", "restart"):
            self.assert_error(self.request(action), "confirmation_required")
            self.assert_error(self.request(action, {"confirm": True}), "port_conflict")
        self.assert_error(self.request("start"), "port_conflict")
        self.stop.assert_not_called()
        self.start.assert_not_called()

    def test_restart_orders_stop_before_start_and_only_reports_verified_readiness(self):
        (self.support / "tunnel-profile.yaml").write_bytes(b"fixture-profile")
        events = []
        self.probe.return_value = {"port_in_use": True, "owned_listener": True,
                                  "managed_pid": 4321, "ready": 200}
        self.stop.side_effect = lambda: events.append("stop")
        self.start.side_effect = lambda: events.append("start")
        result = self.request("restart", {"confirm": True})
        self.assertTrue(result["ok"], result)
        self.assertEqual(events, ["stop", "start"])
        self.assertEqual(result["data"]["snapshot"]["status"]["state"], "ready")
        self.assertFalse(result["data"]["saved"])
        self.assertIn("ChatGPT", result["data"]["snapshot"]["status"]["message"])

    def test_start_without_configuration_never_opens_legacy_setup_dialog(self):
        (self.support / "settings.json").unlink()
        self.assert_error(self.request("start"), "needs_setup")
        self.start.assert_not_called()

    def test_failure_to_stop_prevents_following_start(self):
        self.probe.return_value = {"port_in_use": True, "owned_listener": True, "managed_pid": 4321}
        self.stop.side_effect = desktop.DesktopError("连接仍在退出")
        self.assert_error(self.request("restart", {"confirm": True}), "operation_failed")
        self.start.assert_not_called()

    def test_healthy_foreign_listener_does_not_count_as_ready(self):
        self.probe.return_value = {"port_in_use": True, "owned_listener": False,
                                  "managed_pid": None, "ready": 200}
        self.assertEqual(self.snapshot()["status"]["state"], "port_conflict")

    def test_isolated_support_never_touches_production_capabilities(self):
        self.controller.isolated = True
        for action in ("start", "stop", "restart"):
            payload = {"confirm": True} if action != "start" else {}
            self.assert_error(self.request(action, payload), "isolated_mode")
        self.assert_error(self.request("request_permission", {"kind": "screen_capture",
                          "confirm": True}), "isolated_mode")
        result = self.request("save_connection", self.connection_payload(restart=True))
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["data"]["restart_required"])
        self.assertEqual(result["data"]["snapshot"]["connection"]["credential_status"], "unavailable")
        for kind in ("office", "permissions"):
            response = self.request("diagnostics", {"kind": kind})
            self.assertTrue(response["ok"], response)
            self.assertFalse(response["data"]["available"])
        self.keychain.assert_not_called()
        self.probe.assert_not_called()
        self.start.assert_not_called()
        self.stop.assert_not_called()

    def test_permission_request_is_explicit_and_rechecks_actual_status(self):
        with patch.object(desktop.permissions, "request_permission", return_value={"ok": True}) as request, \
             patch.object(desktop.permissions, "permission_status", return_value={
                 "available": True, "screen_capture": False, "accessibility": None, "post_event": True}):
            self.assert_error(self.request("request_permission", {"kind": "screen_capture"}),
                              "confirmation_required")
            request.assert_not_called()
            result = self.request("request_permission", {"kind": "screen_capture", "confirm": True})
        self.assertTrue(result["ok"], result)
        states = {row["id"]: row["status"] for row in result["data"]["checks"]}
        self.assertEqual(states, {"screen_capture": "denied", "accessibility": "unknown",
                                  "post_event": "available"})
        request.assert_called_once_with(self.document, "screen_capture")

    def test_office_diagnostics_distinguish_detection_and_verification(self):
        statuses = {"available": True, "capabilities": {
            "xlsx": {"state": "detected"}, "docx": {"state": "verified"}, "pptx": {"state": "missing"}}}
        with patch.object(desktop.environment, "office_status", return_value=statuses) as check, \
             patch.object(desktop.environment, "verify_office", return_value=statuses) as verify:
            detected = self.request("diagnostics", {"kind": "office"})
            checked = self.request("diagnostics", {"kind": "office", "verify": True})
        self.assertTrue(detected["ok"], detected)
        self.assertTrue(checked["ok"], checked)
        self.assertIn("尚未验证", detected["data"]["checks"][0]["detail"])
        check.assert_called_once_with(self.document, refresh=True)
        verify.assert_called_once_with(self.document, include_libreoffice=False)

    def test_logs_are_bounded_and_redact_headers_keys_urls_and_control_codes(self):
        logs = self.support / "logs"
        logs.mkdir()
        text = (
            "safe line\n"
            "Authorization: Basic dXNlcjpwYXNzd29yZA==\n"
            "Cookie: sid=firstsecret; auth=secondsecret\n"
            'runtime_key="fictional-raw-key"\n'
            "token=fictional-token\n"
            "https://username:fictional-password@example.org/?access_token=fictional-access\n"
            "\x1b[31mBearer fictional-bearer\x1b[0m\n"
            "sk-fictional0123456789\n"
        )
        (logs / "tunnel.stderr.log").write_text(text)
        result = self.request("logs", {"source": "errors", "lines": 150})
        self.assertTrue(result["ok"], result)
        value = result["data"]["text"]
        self.assertIn("safe line", value)
        for secret in ("dXNlcjpwYXNzd29yZA==", "firstsecret", "secondsecret", "fictional-raw-key",
                       "fictional-token", "fictional-password", "fictional-access", "fictional-bearer",
                       "sk-fictional0123456789", "\x1b"):
            self.assertNotIn(secret, value)
        (logs / "tunnel.stderr.log").write_text("unsafe-partial" * 10000 + "\nlast\n")
        result = self.request("logs", {"source": "errors", "lines": 1})
        self.assertEqual(result["data"]["text"], "last")
        self.assertTrue(result["data"]["truncated"])

    def test_logs_refuse_arbitrary_paths_and_symlinks(self):
        self.assert_error(self.request("logs", {"source": "../settings.json"}), "validation")
        self.assert_error(self.request("logs", {"lines": True}), "validation")
        logs = self.support / "logs"
        logs.mkdir()
        (logs / "tunnel.stderr.log").symlink_to(self.support / "settings.json")
        result = self.request("logs")
        self.assertFalse(result["ok"])
        self.assertNotIn(self.config["keychain_account"], json.dumps(result))

    def test_error_messages_do_not_expose_raw_unhandled_exceptions(self):
        with patch.object(self.controller, "_state", side_effect=RuntimeError("do-not-display-this-secret")):
            result = self.request("snapshot")
        self.assert_error(result, "operation_failed")
        self.assertNotIn("do-not-display-this-secret", json.dumps(result))

    def test_symlink_configuration_is_rejected_without_following_it(self):
        target = self.support / "settings.json"
        original = target.read_bytes()
        target.unlink()
        elsewhere = self.root / "sensitive.json"
        elsewhere.write_bytes(original)
        target.symlink_to(elsewhere)
        self.assertFalse(self.request("snapshot")["ok"])
        self.assertEqual(elsewhere.read_bytes(), original)
        self.keychain.assert_not_called()

    def test_json_cli_returns_one_safe_response_and_checks_request_size(self):
        support = self.root / "isolated-cli"
        for raw, expected in (
            (b'{"action":"snapshot"}', None),
            (b'{"action":"snapshot","payload":NaN}', "invalid_json"),
            (b"\xff", "invalid_json"),
            (b" " * (api.MAX_REQUEST_BYTES + 1), "request_too_large"),
        ):
            with self.subTest(expected=expected):
                result = subprocess.run([sys.executable, "-B", str(ROOT / "desktop_api.py"),
                                         "--support", str(support)], input=raw,
                                        capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0)
                response = json.loads(result.stdout)
                self.assertEqual(result.stderr, b"")
                if expected:
                    self.assert_error(response, expected)
                else:
                    self.assertTrue(response["ok"], response)
                self.assertFalse(support.exists())


class KeychainMetadataTests(unittest.TestCase):
    def test_exists_uses_null_password_pointers_and_releases_item(self):
        keychain = object.__new__(security_store.Keychain)
        keychain.service = b"fixture-service"
        keychain.account = b"fixture-account"
        keychain.security = Mock()
        keychain.core = Mock()
        def found(*args):
            self.assertIsNone(args[5])
            self.assertIsNone(args[6])
            ctypes.cast(args[7], ctypes.POINTER(ctypes.c_void_p))[0] = 123
            return 0
        keychain.security.SecKeychainFindGenericPassword.side_effect = found
        self.assertTrue(keychain.exists())
        keychain.core.CFRelease.assert_called_once()
        keychain.security.SecKeychainItemFreeContent.assert_not_called()

    def test_missing_and_unavailable_credentials_are_distinct(self):
        keychain = object.__new__(security_store.Keychain)
        keychain.service, keychain.account = b"fixture", b"fixture"
        keychain.security, keychain.core = Mock(), Mock()
        keychain.security.SecKeychainFindGenericPassword.return_value = -25300
        self.assertFalse(keychain.exists())
        keychain.security.SecKeychainFindGenericPassword.return_value = -25308
        with self.assertRaises(security_store.CredentialError):
            keychain.exists()


if __name__ == "__main__":
    unittest.main()
