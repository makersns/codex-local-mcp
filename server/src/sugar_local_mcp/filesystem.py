"""Bounded UTF-8 project file tools.

These restrictions cover calls to this module only. They do not constrain a
process launched by an execution tool. On POSIX, file opens and atomic replaces
use directory descriptors and O_NOFOLLOW to resist symlink substitution.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Iterator

from .config import Config


_BLOCKED_DIRS = {
    ".git", ".ssh", ".aws", ".azure", ".gcp", ".gnupg", ".kube", ".config",
    "node_modules", ".venv", "venv", "__pycache__", ".next", ".cache",
}
_BLOCKED_NAMES = {".npmrc", ".pypirc", ".netrc", "credentials.json", "credentials", "secrets.json"}
_KEY_NAMES = ("id_rsa", "id_dsa", "id_ecdsa", "id_ed25519")
_KEY_SUFFIXES = (".pem", ".key", ".p12", ".pfx")
_DESCRIPTOR_IO = os.name == "posix" and hasattr(os, "O_NOFOLLOW") and os.open in os.supports_dir_fd


def _blocked(name: str) -> bool:
    name = name.casefold()
    return (
        name in _BLOCKED_DIRS
        or name in _BLOCKED_NAMES
        or name == ".env"
        or name.startswith(".env.")
        or any(name == prefix or name.startswith(prefix + ".") for prefix in _KEY_NAMES)
        or name.endswith(_KEY_SUFFIXES)
    )


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _integer(value: int, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer between {minimum} and {maximum}")
    return value


class FileService:
    def __init__(self, config: Config):
        self.config = config
        self._write_lock = threading.RLock()

    def workspaces(self) -> dict:
        return {
            "workspaces": [
                {"id": item.id, "root": str(item.root), "allow_write": item.allow_write,
                 "allow_exec": item.allow_exec}
                for item in self.config.workspaces.values()
            ],
            "file_tools": {"max_file_bytes": self.config.max_file_bytes,
                           "max_scan_entries": self.config.max_scan_entries,
                           "max_output_chars": self.config.max_output_chars,
                           "symlinks": "rejected", "encoding": "utf-8"},
            "execution_boundary": "allow_exec runs with server OS permissions; file-tool restrictions are not a process sandbox",
        }

    def _parts(self, path: str) -> tuple[str, ...]:
        if not isinstance(path, str) or not path or len(path) > 4096 or "\x00" in path:
            raise ValueError("path must be a nonempty relative path of at most 4096 characters")
        if path.startswith("/") or "\\" in path or PureWindowsPath(path).drive:
            raise PermissionError("Only forward-slash relative paths within a workspace are accepted")
        parts = tuple(part for part in path.split("/") if part and part != ".")
        if any(part == ".." for part in parts):
            raise PermissionError("Parent traversal is not allowed")
        if any(_blocked(part) for part in parts):
            raise PermissionError("Path is excluded by the project-file policy")
        return parts

    def resolve_path(self, workspace: str, path: str = ".", allow_missing: bool = False) -> Path:
        root = self.config.get_workspace(workspace).root
        parts = self._parts(path)
        if root.is_symlink() or not root.is_dir():
            raise PermissionError("Workspace root is no longer an ordinary directory")
        candidate = root
        for index, part in enumerate(parts):
            candidate = candidate / part
            try:
                entry = candidate.lstat()
            except FileNotFoundError:
                if allow_missing and index == len(parts) - 1:
                    return candidate
                raise
            if (stat.S_ISLNK(entry.st_mode)
                    or getattr(entry, "st_file_attributes", 0)
                    & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
                raise PermissionError("Symbolic links and Windows reparse points are not accepted by file tools")
            if index < len(parts) - 1 and not stat.S_ISDIR(entry.st_mode):
                raise ValueError("A parent path component is not a directory")
        return candidate

    @contextmanager
    def _directory(self, workspace: str, path: str) -> Iterator[int | Path]:
        """Keep the exact traversed directory open during enumeration on POSIX."""
        candidate = self.resolve_path(workspace, path)
        if not candidate.is_dir():
            raise ValueError("path must name a directory")
        descriptor: int | None = None
        try:
            if _DESCRIPTOR_IO:
                flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                descriptor = os.open(self.config.get_workspace(workspace).root, flags)
                for part in self._parts(path):
                    child = os.open(part, flags, dir_fd=descriptor)
                    os.close(descriptor)
                    descriptor = child
            yield descriptor if descriptor is not None else candidate
        finally:
            if descriptor is not None:
                os.close(descriptor)

    @contextmanager
    def _parent(self, workspace: str, path: str, *, allow_missing: bool = False) -> Iterator[tuple[int | None, str, Path]]:
        candidate = self.resolve_path(workspace, path, allow_missing=allow_missing)
        parts = self._parts(path)
        if not parts:
            raise ValueError("A file path is required")
        descriptor: int | None = None
        try:
            if _DESCRIPTOR_IO:
                flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                descriptor = os.open(self.config.get_workspace(workspace).root, flags)
                for part in parts[:-1]:
                    child = os.open(part, flags, dir_fd=descriptor)
                    os.close(descriptor)
                    descriptor = child
            yield descriptor, parts[-1], candidate
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _read_at(self, directory_fd: int | None, name: str, path: Path) -> tuple[bytes, int]:
        if directory_fd is not None:
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0), dir_fd=directory_fd)
        else:
            if path.is_symlink():
                raise PermissionError("Symbolic links are not accepted by file tools")
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        with os.fdopen(descriptor, "rb") as file:
            info = os.fstat(file.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("Only regular files are supported")
            if info.st_size > self.config.max_file_bytes:
                raise ValueError(f"File exceeds max_file_bytes ({self.config.max_file_bytes})")
            data = file.read(self.config.max_file_bytes + 1)
            if len(data) > self.config.max_file_bytes:
                raise ValueError(f"File exceeds max_file_bytes ({self.config.max_file_bytes})")
            return data, stat.S_IMODE(info.st_mode)

    def _read_bytes(self, workspace: str, path: str) -> tuple[bytes, int]:
        with self._parent(workspace, path) as (descriptor, name, candidate):
            return self._read_at(descriptor, name, candidate)

    @staticmethod
    def _decode(data: bytes) -> str:
        if b"\x00" in data:
            raise ValueError("Binary files are not supported")
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("Only UTF-8 text files are supported") from None

    def list_directory(self, workspace: str, path: str = ".", offset: int = 0, limit: int = 200) -> dict:
        _integer(offset, "offset", 0, self.config.max_scan_entries)
        _integer(limit, "limit", 1, 500)
        entries = []
        scanned = 0
        scan_truncated = False
        with self._directory(workspace, path) as directory:
            with os.scandir(directory) as iterator:
                for entry in iterator:
                    scanned += 1
                    if scanned > self.config.max_scan_entries:
                        scan_truncated = True
                        break
                    if _blocked(entry.name) or entry.is_symlink():
                        continue
                    if not entry.is_dir(follow_symlinks=False) and not entry.is_file(follow_symlinks=False):
                        continue
                    relative = entry.name if path == "." else path.rstrip("/") + "/" + entry.name
                    entries.append({"name": entry.name, "path": relative,
                                    "type": "directory" if entry.is_dir(follow_symlinks=False) else "file"})
        entries.sort(key=lambda entry: (entry["type"] != "directory", entry["name"]))
        page = []
        chars = 0
        for entry in entries[offset: offset + limit]:
            entry_chars = len(json.dumps(entry, ensure_ascii=False))
            if page and chars + entry_chars > self.config.max_output_chars:
                break
            chars += entry_chars
            page.append(entry)
        next_offset = offset + len(page)
        return {"path": path, "entries": page, "next_offset": next_offset if next_offset < len(entries) else None,
                "scan_truncated": scan_truncated,
                "note": "Excluded names, symlinks, and nonregular files are omitted; listing is not a filesystem snapshot"}

    def read_file(self, workspace: str, path: str, start_line: int = 1, max_lines: int = 200) -> dict:
        _integer(start_line, "start_line", 1, 10_000_000)
        _integer(max_lines, "max_lines", 1, 1000)
        data, _ = self._read_bytes(workspace, path)
        content = self._decode(data)
        lines = content.splitlines(keepends=True)
        selected: list[str] = []
        size = 0
        line_truncated = False
        for line in lines[start_line - 1: start_line - 1 + max_lines]:
            remaining = self.config.max_output_chars - size
            if len(line) > remaining:
                if not selected:
                    selected.append(line[:remaining])
                    line_truncated = True
                break
            selected.append(line)
            size += len(line)
        end_line = start_line + len(selected) - 1
        has_more = end_line < len(lines)
        return {"path": path, "content": "".join(selected), "sha256": _sha(data),
                "size_bytes": len(data), "total_lines": len(lines), "start_line": start_line,
                "end_line": end_line, "next_line": end_line + 1 if has_more and not line_truncated else None,
                "line_truncated": line_truncated,
                "note": "A single line exceeding the output cap is truncated; use a command to inspect it when execution is permitted" if line_truncated else ""}

    def _walk(self, workspace: str, path: str) -> Iterator[str | None]:
        start = self.resolve_path(workspace, path)
        if start.is_file():
            yield path
            return
        if not start.is_dir():
            raise ValueError("path must be a regular file or directory")
        stack = [path]
        scanned = 0
        while stack:
            relative_dir = stack.pop()
            with self._directory(workspace, relative_dir) as directory:
                with os.scandir(directory) as iterator:
                    entries = []
                    for entry in iterator:
                        scanned += 1
                        if scanned > self.config.max_scan_entries:
                            yield None
                            return
                        if _blocked(entry.name) or entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            entries.append((entry.name, True))
                        elif entry.is_file(follow_symlinks=False):
                            entries.append((entry.name, False))
            for name, is_directory in sorted(entries):
                relative = "/".join((*self._parts(relative_dir), name))
                if is_directory:
                    stack.append(relative)
                else:
                    yield relative

    def search_text(self, workspace: str, query: str, path: str = ".", offset: int = 0, limit: int = 100) -> dict:
        if not isinstance(query, str) or not query or len(query) > 4096 or "\n" in query or "\r" in query:
            raise ValueError("query must be a nonempty single-line literal of at most 4096 characters")
        _integer(offset, "offset", 0, 1_000_000)
        _integer(limit, "limit", 1, 500)
        matches = []
        seen = 0
        chars = 0
        files_scanned = 0
        bytes_scanned = 0
        scan_truncated = False
        has_more = False
        # A directory may have many near-limit files. Bound cumulative I/O as
        # well as entries and response size, so search cannot read gigabytes.
        byte_budget = min(self.config.max_file_bytes * 64, 64 * 1_048_576)
        for relative in self._walk(workspace, path):
            if relative is None:
                scan_truncated = True
                break
            if bytes_scanned >= byte_budget:
                scan_truncated = True
                break
            try:
                data, _ = self._read_bytes(workspace, relative)
                bytes_scanned += len(data)
                content = self._decode(data)
            except (ValueError, OSError):
                continue
            files_scanned += 1
            for line_number, line in enumerate(content.splitlines(), 1):
                position = line.find(query)
                if position < 0:
                    continue
                if seen < offset:
                    seen += 1
                    continue
                excerpt_start = max(0, position - 120)
                entry = {"path": relative, "line": line_number,
                         "text": line[excerpt_start:excerpt_start + 1000],
                         "excerpt_column": excerpt_start + 1,
                         "text_truncated": excerpt_start > 0 or len(line) > excerpt_start + 1000}
                entry_chars = len(json.dumps(entry, ensure_ascii=False))
                if len(matches) >= limit or (matches and chars + entry_chars > self.config.max_output_chars):
                    has_more = True
                    break
                matches.append(entry)
                chars += entry_chars
                seen += 1
            if has_more:
                break
        return {"query": query, "matches": matches,
                "next_offset": offset + len(matches) if has_more else None,
                "scan_truncated": scan_truncated, "files_scanned": files_scanned,
                "bytes_scanned": bytes_scanned,
                "note": "Literal, case-sensitive UTF-8 search; excluded, binary, unreadable and oversized files are skipped; pages may change with files"}

    def _check_write(self, workspace: str, expected_sha256: str) -> None:
        if not self.config.get_workspace(workspace).allow_write:
            raise PermissionError("Writing is disabled for this workspace")
        if expected_sha256 != "missing" and not re.fullmatch(r"[0-9a-f]{64}", expected_sha256 or ""):
            raise ValueError("expected_sha256 must be a SHA-256 from read_file, or 'missing' for a new file")

    def _backup(self, workspace: str, path: str, data: bytes) -> str:
        # Backups stay outside exported roots; callers receive only an opaque id.
        identifier = uuid.uuid4().hex
        folder = self.config.state_dir / "backups" / workspace
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        for parent in [self.config.state_dir, self.config.state_dir / "backups", folder]:
            if parent.is_symlink():
                raise PermissionError("State directories must not be symlinks")
        for name, payload in [(identifier + ".bin", data), (identifier + ".json", json.dumps({
            "workspace": workspace, "path": path, "sha256": _sha(data),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }, ensure_ascii=False).encode("utf-8"))]:
            descriptor = os.open(folder / name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "wb") as file:
                file.write(payload)
                file.flush()
                os.fsync(file.fileno())
        return identifier

    def _write(self, workspace: str, path: str, data: bytes, expected_sha256: str) -> dict:
        if len(data) > self.config.max_file_bytes:
            raise ValueError(f"Content exceeds max_file_bytes ({self.config.max_file_bytes})")
        if b"\x00" in data:
            raise ValueError("Binary content is not supported")
        with self._parent(workspace, path, allow_missing=True) as (directory_fd, name, candidate):
            try:
                previous, mode = self._read_at(directory_fd, name, candidate)
                self._decode(previous)
            except FileNotFoundError:
                previous, mode = None, 0o600
            actual = _sha(previous) if previous is not None else "missing"
            if actual != expected_sha256:
                raise ValueError(f"Conflict: file changed; expected {expected_sha256}, actual {actual}. Read it again before editing.")
            if previous == data:
                return {"path": path, "sha256": actual, "size_bytes": len(data), "changed": False, "backup_id": None}
            backup_id = self._backup(workspace, path, previous) if previous is not None else None
            temporary = f".sugar-mcp-{uuid.uuid4().hex}.tmp"
            temp_path = candidate.parent / temporary
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
            if directory_fd is not None:
                descriptor = os.open(temporary, flags | os.O_NOFOLLOW, mode, dir_fd=directory_fd)
            else:
                descriptor = os.open(temp_path, flags | getattr(os, "O_BINARY", 0), mode)
            try:
                with os.fdopen(descriptor, "wb") as file:
                    file.write(data)
                    file.flush()
                    os.fsync(file.fileno())
                    if hasattr(os, "fchmod"):
                        # Retain ordinary permission bits, never propagate setid.
                        os.fchmod(file.fileno(), mode & 0o777)
                try:
                    latest, _ = self._read_at(directory_fd, name, candidate)
                    latest_hash = _sha(latest)
                except FileNotFoundError:
                    latest_hash = "missing"
                if latest_hash != actual:
                    raise ValueError("Conflict: file changed while preparing the write; read it again")
                if previous is None:
                    # Link creates without clobbering an intervening external
                    # create. Atomic overwrite below has the usual POSIX race
                    # against unrelated editors after the final hash check.
                    if directory_fd is not None:
                        os.link(temporary, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd, follow_symlinks=False)
                    else:
                        os.link(temp_path, candidate, follow_symlinks=False)
                elif directory_fd is not None:
                    os.replace(temporary, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
                else:
                    self.resolve_path(workspace, path)
                    os.replace(temp_path, candidate)
            finally:
                try:
                    if directory_fd is not None:
                        os.unlink(temporary, dir_fd=directory_fd)
                    else:
                        temp_path.unlink()
                except FileNotFoundError:
                    pass
            return {"path": path, "sha256": _sha(data), "size_bytes": len(data), "changed": True, "backup_id": backup_id}

    def write_file(self, workspace: str, path: str, content: str, expected_sha256: str) -> dict:
        self._check_write(workspace, expected_sha256)
        if not isinstance(content, str):
            raise ValueError("content must be UTF-8 text")
        with self._write_lock:
            return self._write(workspace, path, content.encode("utf-8"), expected_sha256)

    def edit_file(self, workspace: str, path: str, old_text: str, new_text: str, expected_sha256: str) -> dict:
        self._check_write(workspace, expected_sha256)
        if not isinstance(old_text, str) or not old_text or not isinstance(new_text, str):
            raise ValueError("old_text must be nonempty text; new_text must be text")
        if expected_sha256 == "missing":
            raise ValueError("edit_file requires an existing file SHA-256")
        with self._write_lock:
            previous, _ = self._read_bytes(workspace, path)
            if _sha(previous) != expected_sha256:
                raise ValueError("Conflict: file changed; read it again before editing")
            content = self._decode(previous)
            if content.count(old_text) != 1:
                raise ValueError("old_text must occur exactly once; include enough surrounding context")
            return self._write(workspace, path, content.replace(old_text, new_text, 1).encode("utf-8"), expected_sha256)
