"""Office dialogs use the existing backend and do not change user documents."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import desktop
import environment_guide as guide
import permission_guide as permissions


def status(**changes):
    value = {"ok": True, "scope": "this-machine", "pid": 1234,
             "mcp_python": "/runtime/python", "selected_python": "/local/python",
             "capabilities": {name: {"state": "detected"} for name in ("xlsx", "docx", "pptx")},
             "apps": [{"name": "WPS Office"}], "libreoffice": {"executable": "/tools/soffice"}}
    value.update(changes)
    return value


class EnvironmentGuideTests(unittest.TestCase):
    def setUp(self):
        self.document = {"state_dir": "/tmp/sugar-environment-test", "workspaces": []}

    def test_status_uses_same_backend_and_refresh_flag(self):
        with patch.object(permissions, "backend_request", return_value=status()) as request:
            result = guide.office_status(self.document, refresh=True)
        request.assert_called_once_with(self.document, "office_status", {"refresh": True}, timeout=30)
        self.assertTrue(result["available"])
        text = guide.format_office_status(result)
        self.assertIn("已检测到依赖，尚未验证", text)
        self.assertNotIn("基础读写已验证", text)
        self.assertIn("与连接服务的独立运行环境分开", text)

    def test_unreachable_backend_is_unknown_not_missing_dependencies(self):
        with patch.object(permissions, "backend_request", return_value={"ok": False, "error": {"message": "请先启动连接"}}):
            result = guide.office_status(self.document)
        self.assertFalse(result["available"])
        text = guide.format_office_status(result)
        self.assertIn("请先启动连接", text)
        self.assertNotIn("未找到所需依赖", text)

    def test_unverified_or_wrong_scope_response_is_not_ready(self):
        for payload in (status(scope="cloud"), status(pid=True), {"ok": True}):
            with self.subTest(payload=payload), patch.object(permissions, "backend_request", return_value=payload):
                self.assertFalse(guide.office_status(self.document)["available"])

    def test_nested_backend_error_cannot_be_mistaken_for_a_success(self):
        value = {"ok": True, "result": {"ok": False, "error": {"message": "后台暂时忙，请稍后重试"}}}
        with patch.object(permissions, "backend_request", return_value=value):
            result = guide.office_status(self.document)
        self.assertFalse(result["available"])
        self.assertIn("后台暂时忙", guide.format_office_status(result))

    def test_verify_is_explicit_and_formats_libreoffice_timeout_honestly(self):
        value = status(verification={"scope": "synthetic-documents-in-memory"},
                       libreoffice_probe={"state": "timed_out"})
        value["capabilities"]["xlsx"]["state"] = "verified"
        with patch.object(permissions, "backend_request", return_value=value) as request:
            result = guide.verify_office(self.document, include_libreoffice=True)
        request.assert_called_once_with(self.document, "office_verify", {"include_libreoffice": True}, timeout=60)
        text = guide.format_office_status(result)
        self.assertIn("基础读写已验证", text)
        self.assertIn("启动检查超时", text)
        self.assertIn("未读取或修改你的文档", text)

class EnvironmentFlowTests(unittest.TestCase):
    def setUp(self):
        self.document = {"state_dir": "/tmp/sugar-environment-test", "workspaces": []}
        self.addCleanup(patch.stopall)
        patch.object(desktop, "load_workspace_document", return_value=(self.document, "hash")).start()

    def test_opening_office_panel_does_not_run_deep_verification(self):
        with patch.object(guide, "office_status", return_value={**status(), "available": True}) as read, \
             patch.object(guide, "verify_office") as verify, \
             patch.object(desktop, "choose_list", return_value=None):
            desktop.office_environment()
        read.assert_called_once_with(self.document)
        verify.assert_not_called()

    def test_deep_verification_only_calls_explicitly_selected_mode(self):
        with patch.object(guide, "office_status", return_value={**status(), "available": True}), \
             patch.object(guide, "verify_office", return_value={**status(), "available": True}) as verify, \
             patch.object(desktop, "choose_list", side_effect=[1, 2, 3]):
            desktop.office_environment()
        self.assertEqual(verify.call_args_list[0].kwargs, {"include_libreoffice": False})
        self.assertEqual(verify.call_args_list[1].kwargs, {"include_libreoffice": True})

class ProjectPermissionFlowTests(unittest.TestCase):
    def setUp(self):
        self.document = {"workspaces": [
            {"id": "frontend", "root": "/tmp/frontend", "allow_write": False, "allow_exec": False},
            {"id": "backend", "root": "/tmp/backend", "allow_write": True, "allow_exec": True}],
            "max_file_bytes": 12345}

    def test_permission_edit_changes_only_selected_flag_in_selected_project(self):
        draft = copy.deepcopy(self.document)
        with patch.object(desktop, "message", side_effect=["编辑", "开启"]), \
             patch.object(desktop, "choose_list", return_value=2):
            desktop.edit_project_in_draft(draft, 0)
        expected = copy.deepcopy(self.document)
        expected["workspaces"][0]["allow_write"] = True
        self.assertEqual(draft, expected)

    def test_permission_dialog_return_keeps_draft_unchanged(self):
        draft = copy.deepcopy(self.document)
        with patch.object(desktop, "message", return_value="返回"):
            desktop.set_project_permission(draft["workspaces"][0], "allow_exec")
        self.assertEqual(draft, self.document)

    def test_cancel_after_permission_edit_never_saves_or_restarts(self):
        with patch.object(desktop, "load_settings", return_value={}), \
             patch.object(desktop, "load_workspace_document", return_value=(self.document, "hash")), \
             patch.object(desktop, "choose_list", side_effect=[1, 3, None]), \
             patch.object(desktop, "message", side_effect=["编辑", "开启"]), \
             patch.object(desktop, "save_document") as save, patch.object(desktop, "stop") as stop:
            self.assertFalse(desktop.manage_projects())
        self.assertFalse(self.document["workspaces"][0]["allow_exec"])
        save.assert_not_called()
        stop.assert_not_called()


if __name__ == "__main__":
    unittest.main()
