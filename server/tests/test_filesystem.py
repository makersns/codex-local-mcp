from __future__ import annotations

import hashlib
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sugar_local_mcp.config import Config, Workspace
from sugar_local_mcp.filesystem import FileService


class FileServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.root = self.base / "project"
        self.root.mkdir()
        self.state = self.base / "state"
        self.service = self.make_service()

    def tearDown(self):
        self.temporary.cleanup()

    def make_service(self, write=True, **limits):
        return FileService(Config({"demo": Workspace("demo", self.root, allow_write=write)}, self.state, **limits))

    def put(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode("utf-8"))
        return path

    def test_relative_path_escape_and_windows_paths_rejected(self):
        for path in ["../secret", "a/../secret", "/etc/passwd", "C:/Windows/file", "C:file", "\\\\host\\share", "a\\..\\secret", "x\x00y"]:
            with self.subTest(path=path), self.assertRaises((PermissionError, ValueError)):
                self.service.resolve_path("demo", path, allow_missing=True)

    def test_symlink_file_and_parent_rejected(self):
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("private", encoding="utf-8")
        try:
            (self.root / "linked").symlink_to(outside, target_is_directory=True)
            (self.root / "file.txt").symlink_to(outside / "secret.txt")
        except (OSError, NotImplementedError):
            self.skipTest("This OS user cannot create symlinks")
        for path in ["linked/secret.txt", "file.txt"]:
            with self.subTest(path=path), self.assertRaises(PermissionError):
                self.service.read_file("demo", path)
        self.assertEqual(self.service.list_directory("demo")["entries"], [])

    def test_blocked_files_absent_from_read_listing_and_search(self):
        for name in [".env", ".env.production", "secret.pem", "server.key", ".git/config", ".ssh/id_rsa", "node_modules/example.js"]:
            self.put(name, "secret-match")
            with self.subTest(name=name), self.assertRaises(PermissionError):
                self.service.read_file("demo", name)
        self.put("src/main.py", "public-match")
        self.assertEqual([entry["name"] for entry in self.service.list_directory("demo")["entries"]], ["src"])
        self.assertEqual(self.service.search_text("demo", "secret-match")["matches"], [])

    def test_read_line_pages_and_byte_hash_preserve_crlf(self):
        content = "第一行\r\nsecond\r\nthird\n"
        self.put("code.txt", content)
        result = self.service.read_file("demo", "code.txt", 2, 1)
        self.assertEqual(result["content"], "second\r\n")
        self.assertEqual(result["next_line"], 3)
        self.assertEqual(result["sha256"], hashlib.sha256(content.encode()).hexdigest())

    def test_list_paging(self):
        for name in ["c.txt", "a.txt", "b.txt"]:
            self.put(name, "x")
        result = self.service.list_directory("demo", offset=0, limit=2)
        self.assertEqual([entry["name"] for entry in result["entries"]], ["a.txt", "b.txt"])
        self.assertEqual(result["next_offset"], 2)
        result = self.service.list_directory("demo", offset=2, limit=2)
        self.assertEqual([entry["name"] for entry in result["entries"]], ["c.txt"])
        self.assertIsNone(result["next_offset"])

    def test_literal_search_and_paging(self):
        self.put("src/code.txt", "a.b first\na.b second\naXb third\n")
        first = self.service.search_text("demo", "a.b", limit=1)
        self.assertEqual(first["matches"][0]["line"], 1)
        self.assertEqual(first["next_offset"], 1)
        second = self.service.search_text("demo", "a.b", offset=1, limit=1)
        self.assertEqual(second["matches"][0]["line"], 2)
        self.assertIsNone(second["next_offset"])

    def test_read_only_write_rejected(self):
        with self.assertRaises(PermissionError):
            self.make_service(write=False).write_file("demo", "new.txt", "x", "missing")
        self.assertFalse((self.root / "new.txt").exists())

    def test_create_update_backup_and_permissions(self):
        created = self.service.write_file("demo", "new.txt", "old\r\n", "missing")
        self.assertTrue(created["changed"])
        self.assertIsNone(created["backup_id"])
        path = self.root / "new.txt"
        if os.name == "posix":
            path.chmod(0o640)
        updated = self.service.write_file("demo", "new.txt", "new\r\n", created["sha256"])
        self.assertEqual(path.read_bytes(), b"new\r\n")
        backup = self.state / "backups" / "demo" / (updated["backup_id"] + ".bin")
        self.assertEqual(backup.read_bytes(), b"old\r\n")
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)
            self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)

    def test_stale_hash_does_not_overwrite(self):
        self.put("a.txt", "first")
        initial = self.service.read_file("demo", "a.txt")
        self.put("a.txt", "changed-by-editor")
        with self.assertRaisesRegex(ValueError, "Conflict"):
            self.service.write_file("demo", "a.txt", "would-overwrite", initial["sha256"])
        self.assertEqual((self.root / "a.txt").read_text(), "changed-by-editor")

    def test_external_edit_during_backup_is_not_overwritten(self):
        self.put("a.txt", "first")
        initial = self.service.read_file("demo", "a.txt")
        original_backup = self.service._backup

        def edit_during_backup(workspace, path, data):
            backup = original_backup(workspace, path, data)
            self.put("a.txt", "external-edit")
            return backup

        with patch.object(self.service, "_backup", side_effect=edit_during_backup):
            with self.assertRaisesRegex(ValueError, "Conflict"):
                self.service.write_file("demo", "a.txt", "our-edit", initial["sha256"])
        self.assertEqual((self.root / "a.txt").read_text(), "external-edit")

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "O_NOFOLLOW"), "POSIX descriptor protection")
    def test_symlink_swap_after_validation_cannot_read_outside(self):
        self.put("src/file.txt", "allowed")
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "file.txt").write_text("secret", encoding="utf-8")
        original_resolve = self.service.resolve_path

        def swap_after_validation(workspace, path, allow_missing=False):
            result = original_resolve(workspace, path, allow_missing)
            (self.root / "src").rename(self.root / "old-src")
            (self.root / "src").symlink_to(outside, target_is_directory=True)
            return result

        with patch.object(self.service, "resolve_path", side_effect=swap_after_validation):
            with self.assertRaises(OSError):
                self.service.read_file("demo", "src/file.txt")

    def test_missing_sentinel_never_overwrites_existing(self):
        self.put("a.txt", "existing")
        with self.assertRaisesRegex(ValueError, "Conflict"):
            self.service.write_file("demo", "a.txt", "new", "missing")

    def test_edit_requires_unique_context(self):
        self.put("a.txt", "same same\n")
        digest = self.service.read_file("demo", "a.txt")["sha256"]
        with self.assertRaisesRegex(ValueError, "exactly once"):
            self.service.edit_file("demo", "a.txt", "same", "other", digest)
        result = self.service.edit_file("demo", "a.txt", "same same", "unique", digest)
        self.assertTrue(result["changed"])
        self.assertEqual((self.root / "a.txt").read_bytes(), b"unique\n")

    def test_noop_edit_does_not_create_backup(self):
        self.put("a.txt", "unchanged")
        digest = self.service.read_file("demo", "a.txt")["sha256"]
        result = self.service.write_file("demo", "a.txt", "unchanged", digest)
        self.assertFalse(result["changed"])
        self.assertFalse(self.state.exists())

    def test_binary_and_oversized_files_rejected(self):
        (self.root / "binary.bin").write_bytes(b"\x00hello")
        (self.root / "invalid.txt").write_bytes(b"\xff\xfe")
        self.put("huge.txt", "x" * 1100)
        service = self.make_service(max_file_bytes=1024)
        for name in ["binary.bin", "invalid.txt", "huge.txt"]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                service.read_file("demo", name)

    def test_output_budget_and_long_line_truncation(self):
        self.put("long.txt", "x" * 3000)
        result = self.make_service(max_output_chars=1024).read_file("demo", "long.txt")
        self.assertEqual(len(result["content"]), 1024)
        self.assertTrue(result["line_truncated"])

    def test_scan_budget_is_reported(self):
        for index in range(5):
            self.put(f"{index}.txt", "a")
        service = self.make_service(max_scan_entries=3)
        self.assertTrue(service.list_directory("demo")["scan_truncated"])
        self.assertTrue(service.search_text("demo", "absent")["scan_truncated"])

    @unittest.skipUnless(os.name == "posix", "POSIX FIFO test")
    def test_fifo_is_not_opened_as_text(self):
        os.mkfifo(self.root / "named-pipe")
        with self.assertRaisesRegex(ValueError, "regular"):
            self.service.read_file("demo", "named-pipe")
        self.assertEqual(self.service.list_directory("demo")["entries"], [])


if __name__ == "__main__":
    unittest.main()
