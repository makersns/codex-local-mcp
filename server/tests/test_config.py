from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import sugar_local_mcp.config as config_module
from sugar_local_mcp.config import load_config


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.config_path = self.root / "config.json"

    def tearDown(self):
        self.temporary.cleanup()

    def load(self, **changes):
        data = {"workspaces": [{"id": "demo", "root": str(self.project)}], "state_dir": str(self.root / "state")}
        data.update(changes)
        self.config_path.write_text(json.dumps(data), encoding="utf-8")
        return load_config(self.config_path)

    def test_defaults_are_read_only(self):
        config = self.load()
        self.assertFalse(config.any_write)
        self.assertFalse(config.any_exec)
        self.assertEqual(config.get_workspace("demo").root, self.project.resolve())

    def test_per_workspace_permissions(self):
        config = self.load(workspaces=[{"id": "demo", "root": str(self.project), "allow_write": True, "allow_exec": True}])
        self.assertTrue(config.any_write)
        self.assertTrue(config.any_exec)

    def test_unknown_settings_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "Unknown"):
            self.load(allow_exec=True)

    def test_relative_root_rejected(self):
        with self.assertRaisesRegex(ValueError, "absolute"):
            self.load(workspaces=[{"id": "demo", "root": "project"}])

    def test_string_boolean_rejected(self):
        with self.assertRaisesRegex(ValueError, "true or false"):
            self.load(workspaces=[{"id": "demo", "root": str(self.project), "allow_exec": "false"}])

    def test_duplicate_ids_rejected(self):
        workspace = {"id": "demo", "root": str(self.project)}
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.load(workspaces=[workspace, workspace])

    def test_backups_cannot_be_exported(self):
        with self.assertRaisesRegex(ValueError, "outside"):
            self.load(state_dir=str(self.project / "state"))

    def test_configuration_cannot_be_inside_an_exported_root(self):
        data = {"workspaces": [{"id": "demo", "root": str(self.project), "allow_write": True}],
                "state_dir": str(self.root / "state")}
        inside = self.project / "config.json"
        inside.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Configuration file must be outside"):
            load_config(inside)
        link = self.root / "linked-config.json"
        try:
            link.symlink_to(inside)
        except (OSError, NotImplementedError):
            return
        with self.assertRaisesRegex(ValueError, "Configuration file must be outside"):
            load_config(link)

    def test_writable_workspace_cannot_contain_service_code(self):
        code_directory = Path(config_module.__file__).resolve().parent
        with self.assertRaisesRegex(ValueError, "service code or Python environment"):
            self.load(workspaces=[{"id": "demo", "root": str(code_directory), "allow_write": True}])
        self.assertFalse(self.load(workspaces=[{"id": "demo", "root": str(code_directory)}]).any_write)

    def test_invalid_limits_rejected(self):
        for value in [True, -1, 100_000_000]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.load(max_file_bytes=value)


if __name__ == "__main__":
    unittest.main()
