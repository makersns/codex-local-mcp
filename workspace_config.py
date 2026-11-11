"""Read and save the desktop application's complete workspace configuration.

The workspace document is the source of truth.  In particular, updating the
desktop settings must not reconstruct a multi-project document from the legacy
``settings.json.project_root`` field.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Iterable


class WorkspaceConfigError(Exception):
    """A local configuration problem suitable for a native dialog."""


_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_FINGERPRINT_RE = re.compile(r"[a-f0-9]{64}\Z")
_MAX_DOCUMENT_BYTES = 2 * 1024 * 1024


def _path(support: Path) -> Path:
    return Path(support).expanduser().absolute() / "workspaces.json"


def _read_bytes(path: Path) -> bytes | None:
    """Read only a regular configuration file; a missing file is distinct."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return None
    except OSError:
        raise WorkspaceConfigError("无法读取工作区配置，请检查文件权限；原配置已保留。") from None
    try:
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise WorkspaceConfigError("工作区配置必须是普通 JSON 文件；原配置已保留。")
            raw = stream.read(_MAX_DOCUMENT_BYTES + 1)
        if len(raw) > _MAX_DOCUMENT_BYTES:
            raise WorkspaceConfigError("工作区配置文件过大，请检查文件内容；原配置已保留。")
        return raw
    except OSError:
        raise WorkspaceConfigError("无法读取工作区配置，请检查文件权限；原配置已保留。") from None


def _fingerprint(raw: bytes | None) -> str | None:
    return hashlib.sha256(raw).hexdigest() if raw is not None else None


def _check_shape(document: object) -> dict:
    if not isinstance(document, dict) or not isinstance(document.get("workspaces"), list):
        raise WorkspaceConfigError("工作区配置需要包含 workspaces 列表；请修复现有 JSON，原配置已保留。")
    for workspace in document["workspaces"]:
        if not isinstance(workspace, dict):
            raise WorkspaceConfigError("工作区列表中的每项都必须是对象；原配置已保留。")
        if any(not isinstance(workspace.get(field), str) or not workspace[field]
               for field in ("id", "root")):
            raise WorkspaceConfigError("每个工作区都需要名称 id 和目录 root；原配置已保留。")
    return document


def load_document(support: Path, legacy_project: str | None = None) -> tuple[dict, str | None]:
    """Load the complete existing document without resolving project paths.

    A missing directory, duplicate ID, or overfull list can therefore be fixed
    in the graphical editor.  Invalid JSON/container structure is never
    replaced by a synthesized single-project document.
    """
    source = _path(support)
    raw = _read_bytes(source)
    if raw is not None:
        try:
            document = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeError, json.JSONDecodeError, RecursionError):
            raise WorkspaceConfigError("工作区配置不是有效的 UTF-8 JSON；请修复现有文件，原配置已保留。") from None
        return _check_shape(document), _fingerprint(raw)

    workspaces = []
    if legacy_project is not None:
        if not isinstance(legacy_project, str) or not legacy_project:
            raise WorkspaceConfigError("旧版项目目录记录无效，请检查应用设置。")
        workspaces.append({"id": "project", "root": legacy_project,
                           "allow_write": True, "allow_exec": True})
    return {"workspaces": workspaces, "state_dir": str(source.parent / "state")}, None


def canonical_path(path: str | Path) -> str:
    """Return an existing directory's absolute path without reading its files."""
    try:
        if not isinstance(path, (str, Path)) or not str(path) or "\x00" in str(path):
            raise ValueError()
        resolved = Path(path).expanduser().resolve(strict=True)
        if not resolved.is_dir():
            raise ValueError()
    except (OSError, ValueError, RuntimeError):
        raise WorkspaceConfigError("请选择本机真实存在、可以访问的项目文件夹。") from None
    return str(resolved)


def suggest_id(path: str | Path, existing_ids: Iterable[str]) -> str:
    """Suggest a stable ASCII workspace name; callers may offer an edit."""
    name = Path(str(path).rstrip("/")).name
    base = re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-_").lower()
    if not base:
        base = "project"
    base = base[:64].rstrip("-_") or "project"
    taken = set(existing_ids)
    candidate = base
    number = 2
    while candidate in taken:
        suffix = "-" + str(number)
        candidate = base[:64 - len(suffix)].rstrip("-_") + suffix
        number += 1
    assert _ID_RE.fullmatch(candidate)
    return candidate


def _serialize(document: dict) -> bytes:
    _check_shape(document)
    try:
        return (json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False)
                + "\n").encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise WorkspaceConfigError("工作区配置包含无法保存的值；原配置已保留。") from None


def _validation_message(error: Exception) -> str:
    """Translate known validator failures without echoing configuration values."""
    detail = str(error)
    if "nonempty array" in detail:
        return "请至少添加一个项目目录。"
    if "At most 32" in detail:
        return "最多可以添加 32 个项目目录。"
    if "Workspace id must" in detail:
        return "项目名称需为 1–64 位英文字母、数字、下划线或短横线，并以字母或数字开头。"
    if "Duplicate workspace id" in detail:
        return "项目名称不能重复，请给每个项目设置不同的名称。"
    if "must be true or false" in detail:
        return "项目读写和执行权限必须使用 true 或 false。"
    if "Configuration file must be outside" in detail:
        return "请选择项目专用目录，不能包含本应用的配置目录。"
    if "state_dir must be outside" in detail:
        return "项目目录不能包含工作区的状态或备份目录。"
    if "Writable workspace must not contain" in detail:
        return "可写项目目录不能包含 MCP 服务或其 Python 运行环境。"
    if "entire filesystem root" in detail:
        return "请选择具体项目目录，不能选择整个文件系统根目录。"
    if isinstance(error, (FileNotFoundError, NotADirectoryError)) or "root is not a directory" in detail:
        return "有项目目录不存在或不是文件夹，请更换该目录或从列表中移除。"
    if "root must be absolute" in detail or "absolute root directory" in detail:
        return "项目目录必须使用绝对路径，或以 ~/ 开头的本机目录。"
    if "Unknown" in detail:
        return "现有工作区 JSON 含有不支持的配置项，请检查文件；原有字段不会被自动删除。"
    if isinstance(error, PermissionError):
        return "无法访问某个项目目录或配置文件，请检查本机权限。"
    return "工作区配置校验未通过，请检查目录、权限和其他设置；原配置已保留。"


def validate_document(support: Path, document: dict) -> None:
    """Use the service's own validator on a private, temporary full document."""
    data = _serialize(document)
    directory = _path(support).parent
    temporary: str | None = None
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".workspace-validate-", suffix=".json", dir=directory)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
        os.chmod(temporary, 0o600)
        from sugar_local_mcp.config import load_config
        load_config(temporary)
    except (ValueError, OSError, RuntimeError) as error:
        raise WorkspaceConfigError(_validation_message(error)) from None
    except ImportError:
        raise WorkspaceConfigError("MCP 配置校验组件无法加载，请先完成应用的运行环境准备。") from None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def save_document(support: Path, document: dict, expected_fingerprint: str | None) -> None:
    """Validate, refuse observed external edits, then atomically save mode 0600.

    This is an optimistic content check. The GUI's control lock serializes its
    own editors; external writers should also avoid editing during this save.
    """
    if expected_fingerprint is not None and (
            not isinstance(expected_fingerprint, str)
            or not _FINGERPRINT_RE.fullmatch(expected_fingerprint)):
        raise WorkspaceConfigError("配置版本信息无效，请重新打开项目管理。")
    validate_document(support, document)
    data = _serialize(document)
    target = _path(support)
    temporary: str | None = None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix=".workspace-save-", suffix=".json", dir=target.parent)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        if _fingerprint(_read_bytes(target)) != expected_fingerprint:
            raise WorkspaceConfigError("工作区配置已被其他操作修改，请重新打开项目管理后再保存。")
        os.replace(temporary, target)
        temporary = None
    except OSError:
        raise WorkspaceConfigError("无法保存工作区配置，请检查本机权限；原配置已保留。") from None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
