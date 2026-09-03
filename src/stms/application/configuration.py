"""Safe, strict loading and freezing of project configuration."""
from __future__ import annotations

from importlib import resources
from pathlib import Path

import yaml
from pydantic import ValidationError

from stms.domain.errors import ConfigurationError
from stms.domain.models import RuntimeConfig

EXAMPLE_RESOURCE = "stms.example.yml"


def configuration_example() -> str:
    """Read the packaged example config, included in the wheel via package_data.

    A missing resource means STMS was installed incorrectly (a build regression),
    not that the user's project is missing configuration, so it fails loudly here
    with a corrective action instead of degrading to an empty or partial example.
    """
    try:
        return resources.files("stms").joinpath(EXAMPLE_RESOURCE).read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError) as error:
        raise ConfigurationError(
            f"Packaged example configuration '{EXAMPLE_RESOURCE}' is missing.",
            "Reinstall stms from a wheel that includes its packaged resources.",
        ) from error


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
        config = RuntimeConfig.model_validate(payload)
        configured_commands = [
            command if "timeout_seconds" in command.model_fields_set else command.model_copy(
                update={"timeout_seconds": config.tests.timeout_seconds}
            )
            for command in config.tests.commands
        ]
        return config.model_copy(update={
            "tests": config.tests.model_copy(update={"commands": configured_commands})
        })
    except ValidationError as error:
        locations = ", ".join(".".join(str(part) for part in item["loc"]) for item in error.errors())
        raise ConfigurationError(f"Invalid stms.yml fields: {locations}.", "Correct the indicated field values; unsupported keys are rejected.") from error


def verify_frozen_config(config: RuntimeConfig, approved_digest: str) -> None:
    if config.digest() != approved_digest:
        raise ConfigurationError("Configuration changed after plan approval.", "Return to planning and approve the new configuration explicitly.")
