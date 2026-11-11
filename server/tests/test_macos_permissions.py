import copy
import ctypes
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from sugar_local_mcp import macos_permissions as permissions


class MacOSPermissionsTests(unittest.TestCase):
    def state(self, **changes):
        return {"ok": True, "supported": True, "scope": "current_process", "pid": os.getpid(),
                "executable": sys.executable, "accessibility": False,
                "screen_capture": False, "post_event": False, "api_errors": [], **changes}

    def test_unsupported_platform_is_unknown_and_does_not_load_native_libraries(self):
        with patch.object(permissions.sys, "platform", "linux"), patch.object(permissions, "_framework") as native:
            result = permissions.status()
        self.assertTrue(result["ok"])
        self.assertFalse(result["supported"])
        self.assertEqual(result["scope"], "current_process")
        self.assertEqual(result["pid"], os.getpid())
        self.assertEqual(result["executable"], sys.executable)
        self.assertIsNone(result["screen_capture"])
        self.assertIsNone(result["accessibility"])
        self.assertIsNone(result["post_event"])
        native.assert_not_called()

    def test_status_only_uses_preflight_and_trust_checks(self):
        called = []
        def boolean(framework, name, result_type=ctypes.c_bool):
            called.append(name)
            return name != "CGPreflightScreenCaptureAccess"
        with patch.object(permissions.sys, "platform", "darwin"), \
             patch.object(permissions, "_call_boolean", side_effect=boolean), \
             patch.object(permissions, "_request_accessibility") as prompt:
            result = permissions.status()
        self.assertEqual(called, ["AXIsProcessTrusted", "CGPreflightScreenCaptureAccess", "CGPreflightPostEventAccess"])
        self.assertTrue(result["accessibility"])
        self.assertFalse(result["screen_capture"])
        self.assertTrue(result["post_event"])
        self.assertIn("not_input_monitoring", result["post_event_scope"])
        prompt.assert_not_called()

    def test_api_failure_is_unknown_and_does_not_echo_native_error_text(self):
        def boolean(framework, name, result_type=ctypes.c_bool):
            if name == "CGPreflightScreenCaptureAccess":
                raise OSError("sensitive-native-diagnostic-must-not-leak")
            return False
        with patch.object(permissions.sys, "platform", "darwin"), \
             patch.object(permissions, "_call_boolean", side_effect=boolean):
            result = permissions.status()
        self.assertIsNone(result["screen_capture"])
        self.assertFalse(result["accessibility"])
        self.assertEqual(result["api_errors"][0]["code"], "api_unavailable")
        self.assertNotIn("sensitive-native", str(result))

    def test_unsupported_request_fails_without_calling_a_request_api(self):
        with patch.object(permissions, "status", return_value=self.state(supported=False)), \
             patch.object(permissions, "_call_boolean") as native, \
             patch.object(permissions, "_request_accessibility") as accessibility:
            result = permissions.request("screen_capture")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "unsupported_platform")
        self.assertFalse(result["request_sent"])
        native.assert_not_called()
        accessibility.assert_not_called()

    def test_existing_grant_does_not_reprompt(self):
        with patch.object(permissions, "status", return_value=self.state(accessibility=True)), \
             patch.object(permissions, "_request_accessibility") as prompt:
            result = permissions.request("accessibility")
        self.assertTrue(result["already_granted"])
        self.assertFalse(result["request_sent"])
        prompt.assert_not_called()

    def test_request_result_is_not_treated_as_actual_accessibility_grant(self):
        with patch.object(permissions, "status", side_effect=lambda: self.state()) as state, \
             patch.object(permissions, "_request_accessibility", return_value=True) as prompt:
            result = permissions.request("accessibility")
        self.assertEqual(state.call_count, 2)
        prompt.assert_called_once_with()
        self.assertTrue(result["request_result"])
        self.assertTrue(result["request_sent"])
        self.assertFalse(result["accessibility"])
        self.assertTrue(result["refresh_required"])

    def test_screen_and_post_event_requests_call_only_the_selected_api(self):
        for kind, api in [("screen_capture", "CGRequestScreenCaptureAccess"),
                          ("post_event", "CGRequestPostEventAccess")]:
            with self.subTest(kind=kind):
                after = self.state(**{kind: True})
                with patch.object(permissions, "status", side_effect=[self.state(), after]), \
                     patch.object(permissions, "_call_boolean", return_value=True) as native, \
                     patch.object(permissions, "_request_accessibility") as accessibility:
                    result = permissions.request(kind)
                native.assert_called_once_with(permissions._CORE_GRAPHICS, api)
                accessibility.assert_not_called()
                self.assertEqual(result["request_kind"], kind)
                self.assertTrue(result[kind])

    def test_request_api_exception_does_not_leak_details_or_claim_request_sent(self):
        with patch.object(permissions, "status", side_effect=lambda: self.state()), \
             patch.object(permissions, "_call_boolean", side_effect=RuntimeError("secret=never-return")):
            result = permissions.request("screen_capture")
        self.assertFalse(result["ok"])
        self.assertFalse(result["request_sent"])
        self.assertFalse(result["screen_capture"])
        self.assertEqual(result["error"]["code"], "permission_request_failed")
        self.assertNotIn("never-return", str(result))

    def test_unknown_permission_is_rejected_before_native_checks(self):
        for value in ["input_monitoring", "everything", None, [], {}]:
            with self.subTest(value=value), patch.object(permissions, "status") as state:
                with self.assertRaises(ValueError):
                    permissions.request(value)
                state.assert_not_called()

    def test_accessibility_prompt_builds_true_option_and_releases_dictionary(self):
        application, foundation = MagicMock(), MagicMock()
        foundation.CFDictionaryCreate.return_value = 12345
        application.AXIsProcessTrustedWithOptions.return_value = 0
        constants = [ctypes.c_void_p(111), ctypes.c_void_p(222)]
        with patch.object(permissions, "_framework", side_effect=[application, foundation]), \
             patch.object(ctypes.c_void_p, "in_dll", side_effect=constants):
            result = permissions._request_accessibility()
        self.assertFalse(result)
        args = foundation.CFDictionaryCreate.call_args.args
        self.assertEqual(args[1][0], 111)
        self.assertEqual(args[2][0], 222)
        self.assertEqual(args[3], 1)
        application.AXIsProcessTrustedWithOptions.assert_called_once_with(12345)
        foundation.CFRelease.assert_called_once_with(12345)


if __name__ == "__main__":
    unittest.main()
