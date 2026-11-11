"""Small, explicit configuration for a single-owner local development MCP.

File permissions here apply to the file tools. Enabling ``allow_exec`` gives
programs the operating-system permissions of the server account; it is not a
filesystem sandbox.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Workspace:
    id: str
    root: Path
    allow_write: bool = False
    allow_exec: bool = False


@dataclass(frozen=True)
class Config:
    workspaces: dict[str, Workspace]
    state_dir: Path = field(default_factory=lambda: Path.home() / ".local" / "state" / "sugar-local-mcp")
    max_file_bytes: int = 1_048_576
    max_scan_entries: int = 10_000
    max_output_chars: int = 64_000

    def get_workspace(self, workspace: str) -> Workspace:
        try:
            return self.workspaces[workspace]
        except KeyError:
            raise ValueError(f"Unknown workspace: {workspace!r}") from None

    @property
    def any_write(self) -> bool:
        return any(workspace.allow_write for workspace in self.workspaces.values())

    @property
    def any_exec(self) -> bool:
        return any(workspace.allow_exec for workspace in self.workspaces.values())


def _boolean(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be true or false")
    return value


def _integer(data: dict[str, Any], name: str, default: int, lower: int, upper: int) -> int:
    value = data.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
        raise ValueError(f"{name} must be an integer between {lower} and {upper}")
    return value


def load_config(path: str | Path) -> Config:
    """Load a JSON file; relative roots and unknown settings are rejected.

    ``~`` is expanded only in root/state paths. The service does not expand
    environment variables or execute any text from its configuration.
    """
    source_path = Path(path).expanduser().absolute()
    resolved_source = source_path.resolve(strict=True)
    with resolved_source.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError("Configuration must be a JSON object")
    allowed = {"workspaces", "state_dir", "max_file_bytes", "max_scan_entries", "max_output_chars"}
    unknown = set(data) - allowed
    if unknown:
        raise ValueError(f"Unknown configuration settings: {', '.join(sorted(unknown))}")
    raw_workspaces = data.get("workspaces")
    if not isinstance(raw_workspaces, list) or not raw_workspaces:
        raise ValueError("workspaces must be a nonempty array")
    if len(raw_workspaces) > 32:
        raise ValueError("At most 32 workspaces may be configured")
    workspaces: dict[str, Workspace] = {}
    for raw in raw_workspaces:
        if not isinstance(raw, dict):
            raise ValueError("Each workspace must be an object")
        unknown = set(raw) - {"id", "root", "allow_write", "allow_exec"}
        if unknown:
            raise ValueError(f"Unknown workspace settings: {', '.join(sorted(unknown))}")
        identifier = raw.get("id")
        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", identifier):
            raise ValueError("Workspace id must contain 1-64 letters, digits, underscores or hyphens")
        if identifier in workspaces:
            raise ValueError(f"Duplicate workspace id: {identifier}")
        root_value = raw.get("root")
        if not isinstance(root_value, str) or not root_value or "\x00" in root_value:
            raise ValueError(f"Workspace {identifier} needs an absolute root directory")
        root = Path(root_value).expanduser()
        if not root.is_absolute():
            raise ValueError(f"Workspace {identifier}: root must be absolute")
        root = root.resolve(strict=True)
        if not root.is_dir():
            raise ValueError(f"Workspace {identifier}: root is not a directory")
        if root == Path(root.anchor):
            raise ValueError("Export a project directory, not an entire filesystem root")
        workspaces[identifier] = Workspace(
            id=identifier,
            root=root,
            allow_write=_boolean(raw.get("allow_write", False), "allow_write"),
            allow_exec=_boolean(raw.get("allow_exec", False), "allow_exec"),
        )
    for workspace in workspaces.values():
        for config_path in (source_path, resolved_source):
            if config_path == workspace.root or workspace.root in config_path.parents:
                raise ValueError("Configuration file must be outside all exported workspace directories")
        if workspace.allow_write:
            # A file-write-only configuration must not be able to rewrite this
            # service or its Python environment, then gain execution on restart.
            # Arbitrary execution remains a separate, explicitly trusted mode.
            protected = (Path(__file__).resolve(), Path(sys.prefix).resolve())
            if any(item == workspace.root or workspace.root in item.parents for item in protected):
                raise ValueError("Writable workspace must not contain the MCP service code or Python environment")
    state_value = data.get("state_dir", str(Path.home() / ".local" / "state" / "sugar-local-mcp"))
    if not isinstance(state_value, str) or not state_value or "\x00" in state_value:
        raise ValueError("state_dir must be an absolute path")
    state_dir = Path(state_value).expanduser()
    if not state_dir.is_absolute():
        raise ValueError("state_dir must be absolute")
    state_dir = state_dir.resolve()
    for workspace in workspaces.values():
        if state_dir == workspace.root or workspace.root in state_dir.parents:
            raise ValueError("state_dir must be outside all exported workspace directories")
    return Config(
        workspaces=workspaces,
        state_dir=state_dir,
        max_file_bytes=_integer(data, "max_file_bytes", 1_048_576, 1024, 16_777_216),
        max_scan_entries=_integer(data, "max_scan_entries", 10_000, 100, 100_000),
        max_output_chars=_integer(data, "max_output_chars", 64_000, 1024, 256_000),
    )
