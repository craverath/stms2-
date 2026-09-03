from pathlib import Path

import pytest
from pydantic import ValidationError

from stms.application.configuration import configuration_example, load_runtime_config, verify_frozen_config
from stms.domain.errors import ConfigurationError
from stms.domain.models import ApprovedPlan, PlanTask, AcceptanceCriterion, ReviewConfig, RuntimeConfig, TaskDependency, TestCommand
from stms.deterministic.test_discovery import discover_test_commands


def plan() -> ApprovedPlan:
    return ApprovedPlan(objective="o", expected_outcome="e", test_commands=[TestCommand(argv=["pytest"])], tasks=[PlanTask(id="one", title="one", description="d", acceptance_criteria=[AcceptanceCriterion(description="works")], essential_tests=["test"], focused_test_commands=[0])])


def test_plan_round_trip_and_bad_references_are_rejected() -> None:
    assert ApprovedPlan.model_validate_json(plan().model_dump_json()) == plan()
    with pytest.raises(ValidationError):
        plan().model_copy(update={"tasks": [PlanTask(id="one", title="x", description="x", dependencies=[TaskDependency(task_id="missing")], acceptance_criteria=[AcceptanceCriterion(description="x")], essential_tests=["x"])]}).__class__.model_validate({**plan().model_dump(), "tasks": [{**plan().tasks[0].model_dump(), "dependencies": [{"task_id": "missing"}]}]})


def test_command_rejects_path_escape() -> None:
    with pytest.raises(ValidationError): TestCommand(argv=["pytest"], cwd="../outside")


def test_command_environment_uses_references_not_secret_literals() -> None:
    command = TestCommand(argv=["pytest"], environment={"API_TOKEN": {"reference": "STMS_TEST_TOKEN"}, "CI": "1"})
    assert "secret" not in command.model_dump_json().lower()
    assert command.resolved_environment({"STMS_TEST_TOKEN": "not-persisted"})["API_TOKEN"] == "not-persisted"
    with pytest.raises(ValidationError): TestCommand(argv=["pytest"], environment={"API_TOKEN": "not-safe"})


def test_config_loads_and_digest_detects_change(tmp_path: Path) -> None:
    (tmp_path / "stms.yml").write_text(configuration_example())
    loaded = load_runtime_config(tmp_path)
    assert loaded.version == 1
    verify_frozen_config(loaded, loaded.digest())
    with pytest.raises(ConfigurationError): verify_frozen_config(loaded, "changed")


def test_missing_config_supplies_example_without_creating_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError) as error: load_runtime_config(tmp_path)
    assert "version: 1" in str(error.value) and not (tmp_path / "stms.yml").exists()


def test_config_rejects_unknown_keys(tmp_path: Path) -> None:
    (tmp_path / "stms.yml").write_text(configuration_example() + "\nunsupported: true\n")
    with pytest.raises(ConfigurationError): load_runtime_config(tmp_path)


def test_example_config_is_packaged_and_readable_via_importlib_resources() -> None:
    assert "version: 1" in configuration_example()


def test_review_config_requires_complete_rounds_and_non_empty_descriptions() -> None:
    valid = {
        "severities": {"high": "high", "medium": "medium", "low": "low"},
        "blocking": {"round_1": ["high"], "round_2": [], "round_3": [], "round_4": []},
        "escalate": {"round_4": ["high"]},
    }
    assert ReviewConfig.model_validate(valid).blocking["round_1"]
    with pytest.raises(ValidationError):
        ReviewConfig.model_validate({**valid, "blocking": {"round_1": ["high"]}})
    with pytest.raises(ValidationError):
        ReviewConfig.model_validate({**valid, "severities": {"high": "", "medium": "medium", "low": "low"}})


def test_tests_timeout_applies_only_when_command_has_no_override(tmp_path: Path) -> None:
    config = configuration_example().replace("timeout_seconds: 900\n  commands: []", "timeout_seconds: 37\n  commands:\n    - argv: [pytest]\n    - argv: [pytest, slow]\n      timeout_seconds: 99")
    (tmp_path / "stms.yml").write_text(config)
    commands = load_runtime_config(tmp_path).tests.commands
    assert [command.timeout_seconds for command in commands] == [37, 99]


def test_discovered_and_proposed_commands_inherit_configured_timeout(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\nversion='1'\n")
    assert discover_test_commands(tmp_path, [], default_timeout_seconds=41)[0].timeout_seconds == 41
    (tmp_path / "pyproject.toml").unlink()
    proposed = [TestCommand(argv=["custom"]), TestCommand(argv=["slow"], timeout_seconds=88)]
    selected = discover_test_commands(tmp_path, [], proposed, default_timeout_seconds=41)
    assert [command.timeout_seconds for command in selected] == [41, 88]
