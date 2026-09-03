from pathlib import Path
import subprocess

import pytest

from stms.adapters.harnesses.fake import FakeHarness, FakeResponse
from stms.application.orchestrator import Orchestrator
from stms.application.preflight import PreflightService
from stms.domain.errors import CompatibilityError, InfrastructureError, StructuredOutputError
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


def test_prompt_digest_is_persisted_and_checked_on_resume(tmp_path: Path) -> None:
    repo = _repository(tmp_path); sandbox = _Sandbox()
    prompt = repo / "planner.md"; prompt.write_text("first")
    config = (repo / "stms.yml").read_text().replace(
        "planner: { harness: fake, model: model, effort: high }",
        "planner: { harness: fake, model: model, effort: high, prompt: planner.md }",
    )
    (repo / "stms.yml").write_text(config)
    orchestrator = Orchestrator(repo, harnesses={"fake": FakeHarness([])}, sandbox=sandbox)
    context = orchestrator.start("work", run_id="prompt-run")
    assert context.workflow.snapshot.metadata.prompt_digest != "builtin"
    context.workflow.pause("keyboard_interrupt")
    prompt.write_text("second")
    with pytest.raises(CompatibilityError, match="prompt digest"):
        Orchestrator(repo, harnesses={"fake": FakeHarness([])}, sandbox=sandbox).resume("prompt-run")


def test_events_are_persisted_once_and_sent_to_renderer(tmp_path: Path) -> None:
    class Renderer:
        def __init__(self) -> None:
            self.events = []

        def render(self, event) -> None:
            self.events.append(event)

    repo = _repository(tmp_path); renderer = Renderer()
    context = Orchestrator(repo, harnesses={"fake": FakeHarness([])}, sandbox=_Sandbox(), event_renderer=renderer).start("work", run_id="events")
    lines = (context.workflow.artifacts.root / "events.jsonl").read_text().splitlines()
    assert len(lines) == 1 and len(renderer.events) == 1


def test_implementation_retry_counters_are_persisted_per_stage(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    context = Orchestrator(repo, harnesses={"fake": FakeHarness([])}, sandbox=_Sandbox()).start("work", run_id="retries")
    orchestrator = Orchestrator(repo, harnesses={"fake": FakeHarness([])}, sandbox=_Sandbox())
    assert orchestrator._reserve_implementation_retry(context, "focused:one")
    assert orchestrator._reserve_implementation_retry(context, "full")
    restored = context.workflow.engine.load("retries")
    assert restored.implementation_attempts == {"focused:one": 1, "full": 1}


@pytest.mark.asyncio
async def test_structured_output_exhaustion_does_not_become_infrastructure_retry(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    harness = FakeHarness([FakeResponse({}), FakeResponse({}), FakeResponse({})])
    delays: list[float] = []

    async def sleep(delay: float) -> None:
        delays.append(delay)

    orchestrator = Orchestrator(repo, harnesses={"fake": harness}, sandbox=_Sandbox(), sleep=sleep)
    context = orchestrator.start("work", run_id="structured")
    with pytest.raises(StructuredOutputError):
        await orchestrator.plan_turn(context, "work")
    assert len(harness.requests) == 3
    assert delays == []
    assert context.workflow.snapshot.state == RunState.PAUSED
    assert context.workflow.snapshot.pause_reason == "structured_output_retries_exhausted"


def test_preexisting_untouched_test_cannot_satisfy_essential_test_requirement(tmp_path: Path) -> None:
    tests = tmp_path / "tests"; tests.mkdir(); existing = tests / "test_feature.py"; existing.write_text("assert True\n")
    task = PlanTask(id="feature", title="Feature", description="Feature", acceptance_criteria=[AcceptanceCriterion(description="works")], essential_tests=["tests/test_feature.py"])
    before = Orchestrator._worktree_fingerprints(tmp_path)
    report = ImplementerOutput(modified_files=[], tests_created=["tests/test_feature.py"])
    assert not Orchestrator._valid_implementation_report(task, report, tmp_path, before)
