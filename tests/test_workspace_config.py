import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if importlib.util.find_spec("sugar_local_mcp") is None:
    for source in [ROOT / "server/src", ROOT.parent / "sugar-local-mcp/src"]:
        if (source / "sugar_local_mcp/config.py").is_file():
            sys.path.insert(0, str(source))
            break

import workspace_config as model


class WorkspaceConfigTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.support = self.root / "Application Support"
        self.support.mkdir()
        self.frontend = self.root / "前端 with spaces"
        self.backend = self.root / "backend"
        self.frontend.mkdir()
        self.backend.mkdir()
        self.target = self.support / "workspaces.json"
        self.document = {
            "workspaces": [
                {"id": "frontend", "root": str(self.frontend), "allow_write": False, "allow_exec": False},
                {"id": "backend", "root": str(self.backend), "allow_write": True, "allow_exec": True},
            ],
            "state_dir": str(self.root / "custom-state"),
            "max_file_bytes": 2_097_152,
            "max_scan_entries": 12_000,
            "max_output_chars": 72_000,
        }

    def write_document(self, document=None):
        raw = json.dumps(document or self.document, ensure_ascii=False, separators=(",", ":")).encode()
        self.target.write_bytes(raw)
        return raw

    def assert_no_temporary_files(self):
        self.assertEqual(list(self.support.glob(".workspace-*")), [])

    def test_existing_multiple_workspaces_override_legacy_and_roundtrip_preserves_options(self):
        raw = self.write_document()
        document, fingerprint = model.load_document(self.support, "/obsolete/legacy-project")
        self.assertEqual(document, self.document)
        self.assertEqual(fingerprint, hashlib.sha256(raw).hexdigest())
        document["workspaces"][1]["id"] = "api"
        model.save_document(self.support, document, fingerprint)
        self.assertEqual(json.loads(self.target.read_text()), document)
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o600)
        self.assertFalse(document["workspaces"][0]["allow_write"])
        self.assertEqual(document["state_dir"], self.document["state_dir"])
        self.assert_no_temporary_files()

    def test_missing_document_migrates_legacy_without_writing_or_resolving(self):
        missing_project = str(self.root / "moved-legacy-folder")
        document, fingerprint = model.load_document(self.support, missing_project)
        self.assertIsNone(fingerprint)
        self.assertEqual(document["workspaces"], [{"id": "project", "root": missing_project,
                                                   "allow_write": True, "allow_exec": True}])
        self.assertEqual(document["state_dir"], str(self.support / "state"))
        self.assertFalse(self.target.exists())

    def test_new_document_is_empty_draft_until_a_project_is_added(self):
        document, fingerprint = model.load_document(self.support)
        self.assertEqual(document["workspaces"], [])
        self.assertIsNone(fingerprint)
        with self.assertRaisesRegex(model.WorkspaceConfigError, "至少"):
            model.save_document(self.support, document, fingerprint)
        self.assertFalse(self.target.exists())
        self.assert_no_temporary_files()
        document["workspaces"] = self.document["workspaces"]
        model.save_document(self.support, document, fingerprint)
        self.assertEqual(json.loads(self.target.read_text()), document)

    def test_invalid_json_or_structure_never_falls_back_to_legacy(self):
        for raw in [b'{"workspaces":', b'[]', b'{}', b'{"workspaces":[42]}',
                    b'{"workspaces":[{"id":"x"}]}', b'\xff']:
            with self.subTest(raw=raw):
                self.target.write_bytes(raw)
                with self.assertRaises(model.WorkspaceConfigError):
                    model.load_document(self.support, str(self.frontend))
                self.assertEqual(self.target.read_bytes(), raw)
        self.assert_no_temporary_files()

    def test_load_allows_missing_paths_and_duplicate_ids_for_graphical_repair(self):
        document = copy.deepcopy(self.document)
        document["workspaces"][0]["root"] = str(self.root / "gone")
        document["workspaces"][1]["id"] = "frontend"
        self.write_document(document)
        loaded, fingerprint = model.load_document(self.support)
        self.assertEqual(loaded, document)
        loaded["workspaces"][0]["root"] = str(self.frontend)
        loaded["workspaces"][1]["id"] = "backend"
        model.save_document(self.support, loaded, fingerprint)
        self.assertEqual(json.loads(self.target.read_text()), loaded)

    def test_unknown_fields_survive_loading_and_are_not_silently_deleted(self):
        document = copy.deepcopy(self.document)
        document["future_setting"] = {"private_value": "do-not-echo-this-value"}
        raw = self.write_document(document)
        loaded, fingerprint = model.load_document(self.support)
        self.assertEqual(loaded["future_setting"], document["future_setting"])
        with self.assertRaises(model.WorkspaceConfigError) as raised:
            model.save_document(self.support, loaded, fingerprint)
        self.assertNotIn("do-not-echo-this-value", str(raised.exception))
        self.assertEqual(self.target.read_bytes(), raw)
        self.assert_no_temporary_files()

    def test_core_validation_rejects_invalid_project_collections_without_writing(self):
        variants = []
        duplicate = copy.deepcopy(self.document)
        duplicate["workspaces"][1]["id"] = "frontend"
        variants.append(duplicate)
        oversized = copy.deepcopy(self.document)
        oversized["workspaces"] = [{**self.document["workspaces"][0], "id": "project"+str(i)} for i in range(33)]
        variants.append(oversized)
        for field, value in [("id", "非法名称"), ("allow_write", "false"),
                             ("root", "relative/project"), ("root", str(self.root / "missing")),
                             ("root", str(self.support)), ("root", str(self.root.anchor))]:
            document = copy.deepcopy(self.document)
            document["workspaces"][0][field] = value
            variants.append(document)
        unsafe_state = copy.deepcopy(self.document)
        unsafe_state["state_dir"] = str(self.frontend / "state")
        variants.append(unsafe_state)
        for number, document in enumerate(variants):
            with self.subTest(case=number):
                with self.assertRaises(model.WorkspaceConfigError):
                    model.save_document(self.support, document, None)
                self.assertFalse(self.target.exists())
                self.assert_no_temporary_files()

    def test_external_edit_is_detected_before_replacing_file(self):
        self.write_document()
        loaded, fingerprint = model.load_document(self.support)
        externally_edited = copy.deepcopy(self.document)
        externally_edited["workspaces"][0]["id"] = "external"
        external_bytes = self.write_document(externally_edited)
        loaded["workspaces"][1]["id"] = "gui"
        with self.assertRaisesRegex(model.WorkspaceConfigError, "其他操作"):
            model.save_document(self.support, loaded, fingerprint)
        self.assertEqual(self.target.read_bytes(), external_bytes)
        self.assert_no_temporary_files()

    def test_edit_during_validation_is_also_detected(self):
        self.write_document()
        loaded, fingerprint = model.load_document(self.support)
        original_validator = model.validate_document
        external_bytes = b'{"workspaces": []}\n'
        def validate_then_external_edit(support, document):
            original_validator(support, document)
            self.target.write_bytes(external_bytes)
        with patch.object(model, "validate_document", side_effect=validate_then_external_edit):
            with self.assertRaisesRegex(model.WorkspaceConfigError, "其他操作"):
                model.save_document(self.support, loaded, fingerprint)
        self.assertEqual(self.target.read_bytes(), external_bytes)
        self.assert_no_temporary_files()

    def test_external_creation_or_deletion_is_a_conflict(self):
        self.write_document()
        loaded, fingerprint = model.load_document(self.support)
        with self.assertRaises(model.WorkspaceConfigError):
            model.save_document(self.support, loaded, None)
        self.target.unlink()
        with self.assertRaises(model.WorkspaceConfigError):
            model.save_document(self.support, loaded, fingerprint)
        self.assertFalse(self.target.exists())
        self.assert_no_temporary_files()

    def test_failed_atomic_replace_preserves_original_and_cleans_tempfile(self):
        original = self.write_document()
        loaded, fingerprint = model.load_document(self.support)
        loaded["workspaces"][0]["id"] = "changed"
        with patch.object(model.os, "replace", side_effect=PermissionError("simulated")):
            with self.assertRaises(model.WorkspaceConfigError):
                model.save_document(self.support, loaded, fingerprint)
        self.assertEqual(self.target.read_bytes(), original)
        self.assert_no_temporary_files()

    def test_symlink_configuration_is_not_followed_or_replaced(self):
        other = self.root / "other.json"
        content = json.dumps(self.document).encode()
        other.write_bytes(content)
        self.target.symlink_to(other)
        with self.assertRaises(model.WorkspaceConfigError):
            model.load_document(self.support)
        with self.assertRaises(model.WorkspaceConfigError):
            model.save_document(self.support, self.document, None)
        self.assertTrue(self.target.is_symlink())
        self.assertEqual(other.read_bytes(), content)
        self.assert_no_temporary_files()

    def test_canonical_path_resolves_existing_directories_and_rejects_nonfolders(self):
        self.assertEqual(model.canonical_path(self.frontend), str(self.frontend.resolve()))
        link = self.root / "project-link"
        link.symlink_to(self.frontend, target_is_directory=True)
        self.assertEqual(model.canonical_path(link), str(self.frontend.resolve()))
        ordinary_file = self.root / "file.txt"
        ordinary_file.write_text("example")
        for value in [ordinary_file, self.root / "missing", "", "bad\x00path"]:
            with self.subTest(value=str(value)):
                with self.assertRaises(model.WorkspaceConfigError):
                    model.canonical_path(value)

    def test_suggested_ids_handle_unicode_punctuation_collisions_and_length(self):
        self.assertEqual(model.suggest_id("/tmp/前端", []), "project")
        self.assertEqual(model.suggest_id("/tmp/My App___", []), "my-app")
        self.assertEqual(model.suggest_id("/tmp/web", ["web", "web-2"]), "web-3")
        name = "a" * 100
        first = model.suggest_id("/tmp/"+name, [])
        second = model.suggest_id("/tmp/"+name, [first])
        self.assertEqual(len(first), 64)
        self.assertEqual(len(second), 64)
        self.assertTrue(second.endswith("-2"))
        self.assertRegex(second, r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


if __name__ == "__main__":
    unittest.main()
