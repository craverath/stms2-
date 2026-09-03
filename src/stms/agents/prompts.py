"""Prompt resolution kept independent from CLI and provider adapters."""
from __future__ import annotations

from pathlib import Path
from typing import Mapping

from stms.domain.errors import ConfigurationError, SecurityError
from stms.domain.ports import PromptProvider


class MappingPromptProvider:
    """Small in-memory provider useful for composition and deterministic tests."""

    def __init__(self, prompts: Mapping[str, str]) -> None:
        self._prompts = dict(prompts)

    def prompt_for(self, role: str) -> str:
        try:
            return self._prompts[role]
        except KeyError as error:
            raise ConfigurationError("Missing prompt override.", f"Configure a prompt for role '{role}'.") from error


class FilePromptProvider:
    """Read approved prompt overrides only from inside the target repository."""

    def __init__(self, repository: Path, paths: Mapping[str, str]) -> None:
        self._repository = repository.resolve()
        self._paths = dict(paths)

    def prompt_for(self, role: str) -> str:
        try:
            configured = self._paths[role]
        except KeyError as error:
            raise ConfigurationError("Missing prompt override.", f"Configure a prompt for role '{role}'.") from error
        candidate = (self._repository / configured).resolve()
        if candidate != self._repository and self._repository not in candidate.parents:
            raise SecurityError("Prompt override escapes the repository.", "Use a relative prompt path inside the repository.")
        if not candidate.is_file():
            raise ConfigurationError("Prompt override file is missing.", f"Create '{configured}' or remove the override.")
        return candidate.read_text(encoding="utf-8")


def resolve_prompt(role: str, default: str, provider: PromptProvider | None) -> str:
    """Use a role-specific override when composition supplied one."""
    return provider.prompt_for(role) if provider is not None else default
