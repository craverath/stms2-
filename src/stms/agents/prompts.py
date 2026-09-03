"""Prompt resolution kept independent from CLI and provider adapters."""
from __future__ import annotations

from hashlib import sha256
import json
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

    def __init__(self, repository: Path, paths: Mapping[str, str | None], defaults: Mapping[str, str] | None = None) -> None:
        self._repository = repository.resolve()
        self._paths = dict(paths)
        self._defaults = dict(defaults or {})

    def prompt_for(self, role: str) -> str:
        try:
            configured = self._paths.get(role)
            if configured is None:
                return self._defaults[role]
        except KeyError as error:
            raise ConfigurationError("Missing effective prompt.", f"Configure or provide a default prompt for role '{role}'.") from error
        configured_path = Path(configured)
        if configured_path.is_absolute():
            raise SecurityError("Prompt override must be relative.", "Use a relative prompt path inside the repository.")
        candidate = (self._repository / configured_path).resolve()
        if candidate != self._repository and self._repository not in candidate.parents:
            raise SecurityError("Prompt override escapes the repository.", "Use a relative prompt path inside the repository.")
        if not candidate.is_file():
            raise ConfigurationError("Prompt override file is missing.", f"Create '{configured}' or remove the override.")
        return candidate.read_text(encoding="utf-8")


def prompt_digest(provider: PromptProvider, roles: tuple[str, ...] = ("planner", "implementer", "reviewer")) -> str:
    """Hash a canonical representation of the three effective prompt contents."""
    payload = {role: provider.prompt_for(role) for role in roles}
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()


def resolve_prompt(role: str, default: str, provider: PromptProvider | None) -> str:
    """Use a role-specific override when composition supplied one."""
    return provider.prompt_for(role) if provider is not None else default
