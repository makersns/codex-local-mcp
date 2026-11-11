"""Bounded local operation metadata; never records code, command arguments or output."""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path


class AuditLog:
    def __init__(self, state_dir: Path, max_bytes: int = 1_048_576):
        self.directory = state_dir
        self.path = state_dir / "audit.jsonl"
        self.max_bytes = max_bytes
        self.lock = threading.Lock()

    def record(self, operation: str, *, success: bool, workspace: str | None = None,
               error_type: str | None = None) -> None:
        record = {"time": datetime.now(timezone.utc).isoformat(),
                  "operation": operation, "success": success}
        if workspace is not None:
            record["workspace"] = workspace
        if error_type is not None:
            record["error_type"] = error_type
        try:
            with self.lock:
                self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                if self.path.is_symlink():
                    raise OSError("Audit file must not be a symlink")
                if self.path.exists() and self.path.stat().st_size >= self.max_bytes:
                    for number in (2, 1):
                        old = self.directory / f"audit.jsonl.{number}"
                        new = self.directory / f"audit.jsonl.{number + 1}"
                        if old.exists():
                            os.replace(old, new)
                    os.replace(self.path, self.directory / "audit.jsonl.1")
                flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(self.path, flags, 0o600)
                with os.fdopen(descriptor, "a", encoding="utf-8") as stream:
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            # Logging failure must not turn an already-completed mutation into a
            # retriable tool failure. Report the limitation to the local operator.
            logging.getLogger(__name__).warning("Could not append local audit metadata")
