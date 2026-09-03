"""Safe, strict loading and freezing of project configuration."""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from stms.domain.errors import ConfigurationError
from stms.domain.models import RuntimeConfig

EXAMPLE_PATH = Path(__file__).parents[3] / "stms.example.yml"


def configuration_example() -> str:
    return EXAMPLE_PATH.read_text(encoding="utf-8")


def load_runtime_config(repository: Path) -> RuntimeConfig:
    config_path = repository / "stms.yml"
    if not config_path.is_file():
        raise ConfigurationError("Missing required stms.yml.", f"Create it manually using this example:\n{configuration_example()}")
    try:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise ConfigurationError("Invalid YAML in stms.yml.", "Fix the YAML syntax and run again.") from error
    if not isinstance(payload, dict):
        raise ConfigurationError("stms.yml must contain a mapping.", "Use version: 1 and the documented top-level sections.")
    try:
        return RuntimeConfig.model_validate(payload)
    except ValidationError as error:
        locations = ", ".join(".".join(str(part) for part in item["loc"]) for item in error.errors())
        raise ConfigurationError(f"Invalid stms.yml fields: {locations}.", "Correct the indicated field values; unsupported keys are rejected.") from error


def verify_frozen_config(config: RuntimeConfig, approved_digest: str) -> None:
    if config.digest() != approved_digest:
        raise ConfigurationError("Configuration changed after plan approval.", "Return to planning and approve the new configuration explicitly.")
