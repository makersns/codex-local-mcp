"""User-flow checks for project management; dialogs and service control are local fakes."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import desktop
from workspace_config import WorkspaceConfigError


class DesktopProjectTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.support = self.root / "Application Support"
        self.support.mkdir()
        self.frontend, self.backend = self.root / "前端 web", self.root / "backend api"
        self.frontend.mkdir()
        self.backend.mkdir()
        self.document = {
            "workspaces": [{"id": "frontend", "root": str(self.frontend),
                            "allow_write": False, "allow_exec": False}],
            "state_dir": str(self.root / "custom state"), "max_file_bytes": 4096,
        }
        self.settings = {
            "version": "0.2.0", "computer_name": "Existing Mac",
            "tunnel_id": "tunnel_" + "a" * 32, "project_root": str(self.frontend),
            "keychain_account": "existing-account", "health_port": 8080,
            "development_path": "/usr/bin:/bin",
        }
        self.info = {"arch": "arm64", "python": sys.executable, "tunnel_client": "/tools/tunnel-client"}
        self.target = self.support / "workspaces.json"
        self.addCleanup(patch.stopall)
        patch.object(desktop, "SUPPORT", self.support).start()
        patch.object(desktop.permissions, "backend_request", side_effect=AssertionError(
            "Project setup and save must not query or request optional OS permissions")).start()

    def existing(self):
        self.target.write_text(json.dumps(self.document, ensure_ascii=False))
        (self.support / "settings.json").write_text(json.dumps(self.settings))
        return self.target.read_bytes()

    def add_backend(self, document, **kwargs):
        document["workspaces"].append({"id": "backend", "root": str(self.backend),
                                        "allow_write": True, "allow_exec": True})
        return True

    @staticmethod
    def accept(text, buttons=("完成",), default=None):
        return buttons[-1]

    def test_graphical_draft_adds_multiple_directories_without_writing(self):
        draft = {"workspaces": [], "state_dir": str(self.root / "state")}
        with patch.object(desktop, "choose_list", side_effect=[0, 0, 3]), \
             patch.object(desktop, "choose_project", side_effect=[str(self.frontend), str(self.backend)]), \
             patch.object(desktop, "ask", side_effect=["frontend", "backend"]):
            self.assertTrue(desktop.edit_workspace_document(draft))
        self.assertEqual([x["id"] for x in draft["workspaces"]], ["frontend", "backend"])
        self.assertFalse(self.target.exists())

    def test_cancel_after_adding_discards_changes_without_stopping_connection(self):
        before = self.existing()
        with patch.object(desktop, "choose_list", side_effect=[0, None]), \
             patch.object(desktop, "choose_project", return_value=str(self.backend)), \
             patch.object(desktop, "ask", return_value="backend"), \
             patch.object(desktop, "stop") as stop, patch.object(desktop, "start") as start:
            self.assertFalse(desktop.manage_projects())
        self.assertEqual(self.target.read_bytes(), before)
        stop.assert_not_called()
        start.assert_not_called()

    def test_edit_name_and_directory_preserves_existing_permissions(self):
        draft = copy.deepcopy(self.document)
        with patch.object(desktop, "choose_list", side_effect=[1, 1, 1, 0, 2]), \
             patch.object(desktop, "message", side_effect=["编辑", "编辑"]), \
             patch.object(desktop, "choose_project", return_value=str(self.backend)), \
             patch.object(desktop, "ask", return_value="renamed"):
            self.assertTrue(desktop.edit_workspace_document(draft))
        self.assertEqual(draft["workspaces"], [{"id": "renamed", "root": str(self.backend),
                                               "allow_write": False, "allow_exec": False}])
        self.assertEqual(draft["state_dir"], self.document["state_dir"])
        self.assertEqual(draft["max_file_bytes"], 4096)

    def test_remove_entry_preserves_its_directory_and_files(self):
        marker = self.frontend / "keep.txt"
        marker.write_text("keep this project")
        draft = copy.deepcopy(self.document)
        self.add_backend(draft)
        with patch.object(desktop, "choose_list", side_effect=[1, 2]), \
             patch.object(desktop, "message", side_effect=["移除", "移除"]):
            self.assertTrue(desktop.edit_workspace_document(draft))
        self.assertEqual([x["id"] for x in draft["workspaces"]], ["backend"])
        self.assertEqual(marker.read_text(), "keep this project")

    def test_duplicate_id_prompts_for_another_name(self):
        draft = copy.deepcopy(self.document)
        with patch.object(desktop, "choose_list", side_effect=[0, 3]), \
             patch.object(desktop, "choose_project", return_value=str(self.backend)), \
             patch.object(desktop, "ask", side_effect=["frontend", "backend"]), \
             patch.object(desktop, "message") as message:
            self.assertTrue(desktop.edit_workspace_document(draft))
        self.assertIn("已经存在", message.call_args.args[0])
        self.assertEqual([x["id"] for x in draft["workspaces"]], ["frontend", "backend"])

    def test_saving_empty_list_keeps_editor_open(self):
        draft = copy.deepcopy(self.document)
        with patch.object(desktop, "choose_list", side_effect=[1, 1, None]), \
             patch.object(desktop, "message", side_effect=["移除", "移除", "完成"]) as message:
            self.assertFalse(desktop.edit_workspace_document(draft))
        self.assertIn("至少", message.call_args.args[0])
        self.assertFalse(self.target.exists())

    def test_save_retains_custom_fields_and_commits_before_restart(self):
        self.existing()
        actions = []
        def stopped():
            saved = json.loads(self.target.read_text())
            self.assertEqual(len(saved["workspaces"]), 2)
            self.assertEqual(saved["max_file_bytes"], 4096)
            self.assertFalse(saved["workspaces"][0]["allow_exec"])
            actions.append("stop")
        def started():
            actions.append("start")
            return "已连接"
        with patch.object(desktop, "edit_workspace_document", side_effect=self.add_backend), \
             patch.object(desktop, "message", side_effect=self.accept), \
             patch.object(desktop, "managed_pid", return_value=123), \
             patch.object(desktop, "stop", side_effect=stopped), \
             patch.object(desktop, "start", side_effect=started):
            self.assertTrue(desktop.manage_projects())
        self.assertEqual(actions, ["stop", "start"])
        self.assertEqual(json.loads(self.target.read_text())["state_dir"], self.document["state_dir"])

    def test_outside_edit_conflict_does_not_stop_running_service(self):
        self.existing()
        external = {**self.document, "max_file_bytes": 8192}
        def edited(document, **kwargs):
            self.add_backend(document)
            self.target.write_text(json.dumps(external))
            return True
        with patch.object(desktop, "edit_workspace_document", side_effect=edited), \
             patch.object(desktop, "message", side_effect=self.accept), \
             patch.object(desktop, "managed_pid", return_value=123), \
             patch.object(desktop, "stop") as stop, patch.object(desktop, "start") as start:
            with self.assertRaises(WorkspaceConfigError):
                desktop.manage_projects()
        self.assertEqual(json.loads(self.target.read_text()), external)
        stop.assert_not_called()
        start.assert_not_called()

    def test_connection_settings_leave_workspace_bytes_and_keychain_identity_intact(self):
        self.add_backend(self.document)
        before = self.existing()
        with patch.object(desktop, "ask", side_effect=["Updated Mac", self.settings["tunnel_id"], ""]), \
             patch.object(desktop, "message", side_effect=self.accept), \
             patch.object(desktop, "runtime", return_value=self.info), \
             patch.object(desktop, "development_path", return_value="/usr/bin:/bin"), \
             patch.object(desktop, "managed_pid", return_value=None), \
             patch.object(desktop, "Keychain") as keychain, \
             patch.object(desktop, "apply_saved_changes", return_value="已连接"):
            keychain.return_value.get.return_value = "dummy-local-test-key"
            saved = desktop.configure_connection()
        self.assertEqual(self.target.read_bytes(), before)
        self.assertEqual(saved["computer_name"], "Updated Mac")
        self.assertEqual(saved["keychain_account"], "existing-account")
        keychain.assert_called_once_with(account="existing-account")
        keychain.return_value.set.assert_not_called()

    def test_status_reads_real_multiple_workspaces_instead_of_legacy_single_path(self):
        self.add_backend(self.document)
        self.existing()
        probe = {"port_in_use": False, "listener_pids": [], "health": None, "ready": None}
        with patch.object(desktop, "health", return_value=probe), \
             patch.object(desktop, "managed_pid", return_value=None):
            status = desktop.current_status()
        self.assertEqual(status["workspace_count"], 2)
        self.assertEqual([x["id"] for x in status["workspaces"]], ["frontend", "backend"])
        self.assertFalse(status["workspaces"][0]["allow_write"])

    def test_first_setup_saves_both_projects_only_after_confirmation(self):
        with patch.object(desktop, "connection_draft", return_value=(dict(self.settings), "dummy-new-key")), \
             patch.object(desktop, "runtime", return_value=self.info), \
             patch.object(desktop, "choose_project", side_effect=[str(self.frontend), str(self.backend)]), \
             patch.object(desktop, "ask", side_effect=["frontend", "backend"]), \
             patch.object(desktop, "choose_list", side_effect=[0, 3]), \
             patch.object(desktop, "message", side_effect=self.accept), \
             patch.object(desktop, "development_path", return_value="/usr/bin:/bin"), \
             patch.object(desktop, "managed_pid", return_value=None), \
             patch.object(desktop, "Keychain") as keychain:
            desktop.initial_setup()
        self.assertEqual([x["id"] for x in json.loads(self.target.read_text())["workspaces"]], ["frontend", "backend"])
        keychain.return_value.set.assert_called_once_with("dummy-new-key")
        self.assertNotIn("dummy-new-key", (self.support / "settings.json").read_text())

    def test_first_setup_cancel_does_not_save_key_or_any_configuration(self):
        with patch.object(desktop, "connection_draft", return_value=(dict(self.settings), "dummy-new-key")), \
             patch.object(desktop, "runtime", return_value=self.info), \
             patch.object(desktop, "choose_project", return_value=str(self.frontend)), \
             patch.object(desktop, "ask", return_value="frontend"), \
             patch.object(desktop, "choose_list", return_value=None), \
             patch.object(desktop, "Keychain") as keychain, \
             patch.object(desktop, "stop") as stop:
            with self.assertRaises(desktop.Cancelled):
                desktop.initial_setup()
        self.assertFalse(self.target.exists())
        self.assertFalse((self.support / "settings.json").exists())
        keychain.assert_not_called()
        stop.assert_not_called()


if __name__ == "__main__":
    unittest.main()
