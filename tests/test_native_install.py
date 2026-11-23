"""Run the real macOS installer against tiny signed disposable app fixtures.

No app is launched, no real Applications folder is touched, and no service,
Keychain entry, user preference or OS permission is modified.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "install-app.sh"


@unittest.skipUnless(platform.system() == "Darwin", "Native installer requires macOS")
class NativeInstallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler_temp = tempfile.TemporaryDirectory(prefix="codex-install-compiler-")
        cls.addClassCleanup(cls.compiler_temp.cleanup)
        cls.binary = Path(cls.compiler_temp.name) / "CodexLocalMCP"
        result = subprocess.run(
            ["/usr/bin/xcrun", "clang", "-x", "c", "-", "-o", str(cls.binary)],
            input="int main(void) { return 0; }\n", text=True,
            capture_output=True, timeout=30,
        )
        if result.returncode:
            raise unittest.SkipTest("Apple Command Line Tools are needed for signed fixtures")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="codex-install-check-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.destination = self.root / "安装目录 with spaces"
        self.destination.mkdir()
        self.target = self.destination / "Codex Local MCP.app"
        self.source = self.make_app("来源应用", "0.6.0")

    def make_app(self, name, version, identifier="com.sugar.local-mcp.desktop"):
        app = self.root / (name + ".app")
        executable = app / "Contents/MacOS/CodexLocalMCP"
        resources = app / "Contents/Resources"
        executable.parent.mkdir(parents=True)
        resources.mkdir()
        shutil.copy2(self.binary, executable)
        (resources / "fixture.txt").write_text("test fixture " + version, encoding="utf-8")
        digest = hashlib.sha256(("fixture-" + version).encode()).hexdigest()
        plist = {
            "CFBundleIdentifier": identifier,
            "CFBundleExecutable": "CodexLocalMCP",
            "CFBundleName": "Codex Local MCP",
            "CFBundlePackageType": "APPL",
            "CFBundleVersion": version,
            "CFBundleShortVersionString": version,
            "CodexBuildIdentifier": digest,
        }
        (app / "Contents/Info.plist").write_bytes(plistlib.dumps(plist))
        subprocess.run(
            ["/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", str(app)],
            check=True, capture_output=True, timeout=15,
        )
        return app

    def install(self, source=None, destination=None, success=True):
        result = subprocess.run(
            ["/bin/bash", str(INSTALLER), str(source or self.source),
             "--destination", str(destination or self.destination)],
            capture_output=True, text=True, timeout=20,
        )
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, str(self.target) + "\n")
            self.assertFalse((self.destination / ".Codex-Local-MCP-install.lock").exists())
        else:
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
        return result

    def assert_valid(self):
        result = subprocess.run(
            ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(self.target)],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_fresh_install_with_chinese_and_space_paths(self):
        self.install()
        self.assert_valid()
        self.assertEqual((self.target / "Contents/Resources/fixture.txt").read_text(),
                         "test fixture 0.6.0")
        self.assertTrue(os.access(self.target / "Contents/MacOS/CodexLocalMCP", os.X_OK))
        self.assertTrue(self.source.is_dir())

    def test_repeat_install_reuses_intact_copy(self):
        self.install()
        before = self.target.stat()
        self.install()
        after = self.target.stat()
        self.assertEqual(before.st_ino, after.st_ino)
        self.assertEqual(before.st_mtime_ns, after.st_mtime_ns)
        self.assertEqual(list(self.destination.glob("*.previous.*.app")), [])

    def test_same_version_repairs_damaged_install_and_preserves_backup(self):
        self.install()
        (self.target / "Contents/Resources/fixture.txt").write_text("damaged")
        self.install()
        self.assert_valid()
        backups = list(self.destination.glob("*.previous.*.app"))
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "Contents/Resources/fixture.txt").read_text(), "damaged")

    def test_upgrade_retains_previous_application(self):
        old = self.make_app("old", "0.5.0")
        self.install(old)
        self.install()
        self.assert_valid()
        backups = list(self.destination.glob("*.previous.*.app"))
        self.assertEqual(len(backups), 1)
        old_info = plistlib.loads((backups[0] / "Contents/Info.plist").read_bytes())
        self.assertEqual(old_info["CFBundleVersion"], "0.5.0")

    def test_downgrade_is_rejected_without_altering_current_app(self):
        self.install()
        before = self.target.stat().st_ino
        old = self.make_app("old", "0.5.0")
        self.install(old, success=False)
        self.assertEqual(before, self.target.stat().st_ino)
        self.assert_valid()
        self.assertEqual(list(self.destination.glob("*.previous.*.app")), [])

    def test_unrelated_target_is_preserved(self):
        unrelated = self.make_app("unrelated", "0.6.0", "example.other.application")
        shutil.move(str(unrelated), self.target)
        before = (self.target / "Contents/Info.plist").read_bytes()
        self.install(success=False)
        self.assertEqual((self.target / "Contents/Info.plist").read_bytes(), before)

    def test_tampered_source_is_rejected(self):
        (self.source / "Contents/Resources/fixture.txt").write_text("changed after signing")
        self.install(success=False)
        self.assertFalse(self.target.exists())

    def test_source_symlink_is_rejected(self):
        alias = self.root / "alias.app"
        alias.symlink_to(self.source, target_is_directory=True)
        self.install(alias, success=False)
        self.assertFalse(self.target.exists())

    def test_destination_symlink_is_rejected(self):
        alias = self.root / "alias-directory"
        alias.symlink_to(self.destination, target_is_directory=True)
        self.install(destination=alias, success=False)
        self.assertFalse(self.target.exists())

    def test_live_install_lock_is_not_removed(self):
        lock = self.destination / ".Codex-Local-MCP-install.lock"
        lock.mkdir()
        (lock / "pid").write_text(str(os.getpid()) + "\n")
        self.install(success=False)
        self.assertTrue(lock.is_dir())
        self.assertEqual((lock / "pid").read_text(), str(os.getpid()) + "\n")
        self.assertFalse(self.target.exists())

    def test_stale_owned_install_lock_is_recovered(self):
        lock = self.destination / ".Codex-Local-MCP-install.lock"
        lock.mkdir()
        (lock / "pid").write_text("999999999\n")
        self.install()
        self.assert_valid()

    def test_lock_symlink_cannot_delete_another_directory_pid(self):
        outside = self.root / "other directory"
        outside.mkdir()
        (outside / "pid").write_text("999999999\n")
        lock = self.destination / ".Codex-Local-MCP-install.lock"
        lock.symlink_to(outside, target_is_directory=True)
        self.install(success=False)
        self.assertTrue(lock.is_symlink())
        self.assertEqual((outside / "pid").read_text(), "999999999\n")

    def test_lock_pid_symlink_is_not_followed(self):
        sentinel = self.root / "other-pid"
        sentinel.write_text("999999999\n")
        lock = self.destination / ".Codex-Local-MCP-install.lock"
        lock.mkdir()
        (lock / "pid").symlink_to(sentinel)
        self.install(success=False)
        self.assertTrue((lock / "pid").is_symlink())
        self.assertEqual(sentinel.read_text(), "999999999\n")

    def test_running_installed_copy_is_a_noop(self):
        self.install()
        before = self.target.stat().st_ino
        self.install(self.target)
        self.assertEqual(before, self.target.stat().st_ino)


if __name__ == "__main__":
    unittest.main()
