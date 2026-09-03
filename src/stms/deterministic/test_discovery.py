"""Read-only test command discovery with prescribed precedence."""
from __future__ import annotations

import re
from pathlib import Path

from stms.domain.models import TestCommand


def discover_test_commands(repository: Path, explicit: list[TestCommand], proposed: list[TestCommand] | None = None) -> list[TestCommand]:
    if explicit:
        return explicit
    documented = _documented_commands(repository)
    if documented:
        return documented
    manifest = _manifest_commands(repository)
    if manifest:
        return manifest
    return list(proposed or [])


def _documented_commands(repository: Path) -> list[TestCommand]:
    candidates = [repository / "README.md", repository / ".github/workflows/test.yml", repository / ".gitlab-ci.yml"]
    commands: list[TestCommand] = []
    pattern = re.compile(r"(?:^|`)(pytest(?:\s+[^`\n]+)?|npm\s+(?:run\s+)?test|cargo\s+test)(?:`|$)", re.MULTILINE)
    for file in candidates:
        if not file.is_file(): continue
        for match in pattern.finditer(file.read_text(encoding="utf-8", errors="ignore")):
            commands.append(TestCommand(argv=match.group(1).split()))
    return _deduplicate(commands)


def _manifest_commands(repository: Path) -> list[TestCommand]:
    if (repository / "pyproject.toml").is_file() or (repository / "pytest.ini").is_file() or (repository / "tox.ini").is_file():
        return [TestCommand(argv=["pytest"])]
    if (repository / "package.json").is_file(): return [TestCommand(argv=["npm", "test"])]
    if (repository / "Cargo.toml").is_file(): return [TestCommand(argv=["cargo", "test"])]
    return []


def _deduplicate(commands: list[TestCommand]) -> list[TestCommand]:
    seen: set[tuple[str, ...]] = set(); result = []
    for command in commands:
        key = tuple(command.argv)
        if key not in seen: seen.add(key); result.append(command)
    return result
