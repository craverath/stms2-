from pathlib import Path
import subprocess

import pytest

from stms.adapters.harnesses.fake import FakeHarness, FakeResponse
from stms.application.orchestrator import Orchestrator
from stms.application.preflight import PreflightService
from stms.domain.errors import InfrastructureError
from stms.domain.models import Capability, RunState
from stms.domain.models import AcceptanceCriterion, ImplementerOutput, PlanTask


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=True).stdout.strip()


def _repository(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"; repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com"); _git(repo, "config", "user.name", "Test")
    (repo / "base.txt").write_text("base\n")
    _git(repo, "add", "base.txt"); _git(repo, "commit", "-m", "base")
    (repo / "stms.yml").write_text("""version: 1
agents:
  planner: { harness: fake, model: model, effort: high }
  implementer: { harness: fake, model: model, effort: high }
  reviewer: { harness: fake, model: model, effort: high }
review:
  severities: { high: high, medium: medium, low: low }
  blocking: { round_1: [high, medium, low], round_2: [high, medium], round_3: [high], round_4: [] }
  escalate: { round_4: [high] }
""")
    return repo


class _Sandbox:
    def __init__(self) -> None:
        self.roles: list[str] = []

    def capabilities(self) -> list[Capability]:
        return [Capability(name="srt", supported=True), Capability(name="filesystem_policy", supported=True)]

    def prepare(self, role: str, repository: Path, worktree: Path | None = None, **_options: object) -> Path:
        self.roles.append(role)
        return repository / "policy.json"


def _plan() -> dict[str, object]:
    return {
        "status": "plan_ready",
        "plan": {
            "objective": "add feature", "expected_outcome": "feature works",
            "test_commands": [{"argv": ["true"]}],
            "tasks": [{"id": "one", "title": "one", "description": "one", "acceptance_criteria": [{"description": "works"}], "essential_tests": ["test"]}],
        },
    }


def test_preflight_allows_untracked_but_rejects_tracked_changes(tmp_path: Path) -> None:
    repo = _repository(tmp_path); harness = FakeHarness([])
    (repo / "untracked.txt").write_text("okay")
    assert PreflightService(repo, {"fake": harness}, _Sandbox()).validate().branch_base == "main"
    (repo / "base.txt").write_text("dirty")
    with pytest.raises(InfrastructureError, match="tracked changes"):
        PreflightService(repo, {"fake": harness}, _Sandbox()).validate()


@pytest.mark.parametrize(("probe", "message"), [
    ({"authenticated": False, "model": True, "effort": True}, "not authenticated"),
    ({"authenticated": True, "model": False, "effort": True}, "cannot use model"),
    ({"authenticated": True, "model": True, "effort": False}, "cannot use effort"),
])
def test_preflight_requires_every_provider_probe_capability(tmp_path: Path, probe: dict[str, bool], message: str) -> None:
    repo = _repository(tmp_path); harness = FakeHarness([])
    harness.preflight = lambda **_kwargs: probe  # type: ignore[method-assign]
    with pytest.raises(InfrastructureError, match=message):
        PreflightService(repo, {"fake": harness}, _Sandbox()).validate()


@pytest.mark.asyncio
async def test_plan_requires_explicit_approval_and_persists_artifacts(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    sandbox = _Sandbox()
    orchestrator = Orchestrator(repo, harnesses={"fake": FakeHarness([FakeResponse(_plan())])}, sandbox=sandbox)
    context = orchestrator.start("add a feature", run_id="run")
    assert context.workflow.snapshot.state == RunState.INTERVIEWING
    await orchestrator.plan_turn(context, "add a feature")
    assert context.workflow.snapshot.state == RunState.PLAN_PENDING_APPROVAL
    assert (repo / ".stms" / "estado" / "run" / "plan.md").exists()
    assert sandbox.roles == ["planner"]
    orchestrator.approve_plan(context)
    assert context.workflow.snapshot.state == RunState.IMPLEMENTING


@pytest.mark.asyncio
async def test_safe_pause_is_restored_by_resume_without_replaying_work(tmp_path: Path) -> None:
    repo = _repository(tmp_path); sandbox = _Sandbox()
    context = Orchestrator(repo, harnesses={"fake": FakeHarness([])}, sandbox=sandbox).start("work", run_id="resume-run")
    context.workflow.pause("keyboard_interrupt")
    restored = Orchestrator(repo, harnesses={"fake": FakeHarness([])}, sandbox=sandbox).resume("resume-run")
    assert restored.workflow.snapshot.state == RunState.INTERVIEWING


def test_preexisting_untouched_test_cannot_satisfy_essential_test_requirement(tmp_path: Path) -> None:
    tests = tmp_path / "tests"; tests.mkdir(); existing = tests / "test_feature.py"; existing.write_text("assert True\n")
    task = PlanTask(id="feature", title="Feature", description="Feature", acceptance_criteria=[AcceptanceCriterion(description="works")], essential_tests=["tests/test_feature.py"])
    before = Orchestrator._worktree_fingerprints(tmp_path)
    report = ImplementerOutput(modified_files=[], tests_created=["tests/test_feature.py"])
    assert not Orchestrator._valid_implementation_report(task, report, tmp_path, before)
