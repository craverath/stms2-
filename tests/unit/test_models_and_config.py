from pathlib import Path

import pytest
from pydantic import ValidationError

from stms.application.configuration import load_runtime_config, verify_frozen_config
from stms.domain.errors import ConfigurationError
from stms.domain.models import ApprovedPlan, PlanTask, AcceptanceCriterion, RuntimeConfig, TaskDependency, TestCommand


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
    config = Path("stms.example.yml").read_text(); (tmp_path / "stms.yml").write_text(config)
    loaded = load_runtime_config(tmp_path)
    assert loaded.version == 1
    verify_frozen_config(loaded, loaded.digest())
    with pytest.raises(ConfigurationError): verify_frozen_config(loaded, "changed")


def test_missing_config_supplies_example_without_creating_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError) as error: load_runtime_config(tmp_path)
    assert "version: 1" in str(error.value) and not (tmp_path / "stms.yml").exists()


def test_config_rejects_unknown_keys(tmp_path: Path) -> None:
    (tmp_path / "stms.yml").write_text(Path("stms.example.yml").read_text() + "\nunsupported: true\n")
    with pytest.raises(ConfigurationError): load_runtime_config(tmp_path)
