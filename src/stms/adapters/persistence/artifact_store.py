"""Atomic local artifacts, serialized JSONL events, and safe untracked copies."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
from typing import Any

from stms.domain.errors import SecurityError
from stms.domain.events import NormalizedEvent
from stms.domain.models import ApprovedUntrackedFile

_SENSITIVE = re.compile(r"(?i)((?:api[_-]?key|secret|password|token|authorization)\s*(?:=|:|\s)\s*)[^\s,]+")
_ENV_NAME = re.compile(r"(?i)(?:api[_-]?key|secret|password|token|authorization)")


def redact(value: str) -> str:
    return _SENSITIVE.sub(r"\1[REDACTED]", value)


def redact_mapping(value: dict[str, Any]) -> dict[str, Any]:
    return redact_data(value)


def redact_data(value: Any) -> Any:
    """Redact nested JSON structures before serialization, not only rendered text."""
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if _ENV_NAME.search(str(key)):
                # Preserve a reference name only when it is explicitly modeled;
                # arbitrary values under secret-like keys never reach disk.
                if isinstance(item, dict) and set(item) <= {"reference", "schema_version"}:
                    redacted[key] = redact_data(item)
                else:
                    redacted[key] = "[REDACTED]"
            else:
                redacted[key] = redact_data(item)
        return redacted
    if isinstance(value, list):
        return [redact_data(item) for item in value]
    if isinstance(value, tuple):
        return [redact_data(item) for item in value]
    if isinstance(value, str):
        return redact(value)
    return value


class LocalArtifactStore:
    _event_lock = threading.Lock()

    def __init__(self, repository: Path, run_id: str) -> None:
        self.repository = repository.resolve()
        self.root = self.repository / ".stms" / "estado" / run_id
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "tests").mkdir(exist_ok=True); (self.root / "reviews").mkdir(exist_ok=True)

    def _path(self, relative_path: str) -> Path:
        path = (self.root / relative_path).resolve()
        if self.root != path and self.root not in path.parents:
            raise SecurityError("Artifact path escapes the run directory.", "Use a relative path inside the run artifact directory.")
        return path

    def write_text(self, relative_path: str, content: str) -> Path:
        destination = self._path(relative_path); destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=destination.parent, delete=False) as temporary:
            temporary.write(redact(content)); temporary.flush(); os.fsync(temporary.fileno()); temp_name = temporary.name
        os.replace(temp_name, destination)
        return destination

    def write_json(self, relative_path: str, value: Any) -> Path:
        payload = json.dumps(redact_data(value), default=str, sort_keys=True, ensure_ascii=False, indent=2)
        return self.write_text(relative_path, payload + "\n")

    def append_event(self, event: NormalizedEvent) -> Path:
        path = self._path("events.jsonl")
        payload = event.model_dump(mode="json", exclude_none=True)
        payload = redact_mapping(payload)
        with self._event_lock, path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(payload, sort_keys=True, ensure_ascii=False) + "\n"); file.flush(); os.fsync(file.fileno())
        return path

    def write_test_log(self, attempt_id: str, content: str, max_bytes: int = 1_000_000) -> tuple[Path, bool]:
        encoded = content.encode("utf-8", errors="replace"); truncated = len(encoded) > max_bytes
        if truncated: content = encoded[:max_bytes].decode("utf-8", errors="replace") + "\n[TRUNCATED]"
        return self.write_text(f"tests/{attempt_id}.log", content), truncated

    def write_review(self, round_id: str, value: Any) -> Path:
        return self.write_json(f"reviews/{round_id}.json", value)

    def copy_approved_untracked(self, approved: ApprovedUntrackedFile, destination_root: Path) -> Path:
        source = (self.repository / approved.source).resolve()
        target_root = destination_root.resolve(); target = (target_root / approved.destination).resolve()
        if self.repository not in source.parents or source.is_symlink() or not source.is_file():
            raise SecurityError("Approved untracked source is invalid or escapes the repository.", "Approve a regular file located inside the repository.")
        if target_root != target and target_root not in target.parents:
            raise SecurityError("Approved destination escapes the worktree.", "Use a relative destination inside the worktree.")
        if source.stat().st_size > approved.max_bytes:
            raise SecurityError("Approved untracked file exceeds its size limit.", "Increase the approved limit at the plan gate or exclude the file.")
        text = source.read_text(encoding="utf-8", errors="ignore")
        if _SENSITIVE.search(text) or _ENV_NAME.search(source.name):
            raise SecurityError("Approved untracked file appears to contain a secret.", "Do not copy credentials or secret-bearing files into a worktree.")
        target.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(source, target)
        return target
