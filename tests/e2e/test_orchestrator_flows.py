"""Offline E2E flows: all provider and sandbox effects are deterministic fakes."""
from __future__ import annotations

import asyncio
from pathlib import Path
import subprocess

import pytest

from stms.adapters.harnesses.base import ProviderResponse
from stms.adapters.harnesses.fake import FakeHarness, FakeResponse
from stms.adapters.sandbox.srt import FakeSandboxRuntime
from stms.application.orchestrator import Orchestrator
from stms.domain.errors import InfrastructureError
from stms.domain.models import AgentRole, HarnessRequest, RunState


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=True).stdout.strip()


def _repository(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"; repo.mkdir(); _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com"); _git(repo, "config", "user.name", "Test")
    (repo / "base.txt").write_text("base\n"); _git(repo, "add", "base.txt"); _git(repo, "commit", "-m", "base")
    (repo / "stms.yml").write_text("""version: 1
agents:
  planner: { harness: fake, model: fake, effort: high }
  implementer: { harness: fake, model: fake, effort: high }
  reviewer: { harness: fake, model: fake, effort: high }
workflow: { max_parallel_tasks: 2, implementation_retries: 3 }
review:
  severities: { high: high, medium: medium, low: low }
  blocking: { round_1: [high, medium, low], round_2: [high, medium], round_3: [high], round_4: [] }
  escalate: { round_4: [high] }
""")
    return repo


def _plan(command: list[str]) -> dict[str, object]:
    return {"status": "plan_ready", "plan": {"objective": "feature", "expected_outcome": "works", "scope": ["feature"], "out_of_scope": ["network"], "human_decisions": ["approve"], "assumptions": ["local"], "risks": ["none"], "test_commands": [{"argv": command}], "tasks": [
        {"id": "one", "title": "one", "description": "one", "acceptance_criteria": [{"description": "one works"}], "essential_tests": ["tests/test_one.py"]},
        {"id": "two", "title": "two", "description": "two", "acceptance_criteria": [{"description": "two works"}], "essential_tests": ["tests/test_two.py"]},
    ]}}


class _WritingHarness(FakeHarness):
    def __init__(self, plan: dict[str, object], *, require_fix: bool = False) -> None:
        super().__init__([]); self.plan = plan; self.require_fix = require_fix; self.implement_starts: list[float] = []

    async def _next(self, request: HarnessRequest) -> ProviderResponse:
        self.requests.append(request)
        if request.role is AgentRole.PLANNER:
            return ProviderResponse("planner", self.plan)
        if request.role is AgentRole.REVIEWER:
            return ProviderResponse("reviewer", {"findings": []})
        self.implement_starts.append(asyncio.get_running_loop().time())
        cwd = Path(request.cwd)
        if cwd.name == "integration" and self.require_fix:
            (cwd / "fix").write_text("fixed\n")
        else:
            (cwd / f"{cwd.name}.txt").write_text("implemented\n")
            tests = cwd / "tests"; tests.mkdir(exist_ok=True)
            task_id = "one" if cwd.name.endswith("one") else "two"
            (tests / f"test_{task_id}.py").write_text("assert True\n")
        await asyncio.sleep(0.02)
        return ProviderResponse(f"implement-{len(self.implement_starts)}", {"modified_files": [f"{cwd.name}.txt"], "tests_created": [f"tests/test_{'one' if cwd.name.endswith('one') else 'two'}.py"]})


@pytest.mark.asyncio
async def test_start_plan_parallel_dag_review_and_squash(tmp_path: Path) -> None:
    repo = _repository(tmp_path); harness = _WritingHarness(_plan(["true"]))
    orchestrator = Orchestrator(repo, harnesses={"fake": harness}, sandbox=FakeSandboxRuntime(tmp_path / "policies"))
    context = orchestrator.start("feature", run_id="complete")
    await orchestrator.plan_turn(context, "feature"); orchestrator.approve_plan(context)
    assert await orchestrator.execute_plan(context)
    assert context.workflow.snapshot.state == RunState.REVIEWING
    assert await orchestrator.review(context)
    assert context.workflow.snapshot.state == RunState.FINAL_APPROVAL
    orchestrator.final_decision(context, "approve")
    assert context.workflow.snapshot.state == RunState.COMPLETED
    assert (repo / "task-one.txt").exists() and (repo / "task-two.txt").exists()
    assert len(harness.implement_starts) == 2 and max(harness.implement_starts) - min(harness.implement_starts) < 0.02
    assert _git(repo, "rev-list", "--count", "main") == "2"


@pytest.mark.asyncio
async def test_full_suite_failure_opens_integration_correction_then_resume(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    command = ["python3", "-c", "from pathlib import Path; import sys; sys.exit(0 if Path.cwd().name != 'integration' or Path('fix').exists() else 1)"]
    harness = _WritingHarness(_plan(command), require_fix=True)
    sandbox = FakeSandboxRuntime(tmp_path / "policies")
    orchestrator = Orchestrator(repo, harnesses={"fake": harness}, sandbox=sandbox)
    context = orchestrator.start("feature", run_id="repair")
    await orchestrator.plan_turn(context, "feature"); orchestrator.approve_plan(context)
    assert not await orchestrator.execute_plan(context)
    assert context.workflow.snapshot.state == RunState.IMPLEMENTING and "full_suite_failed" in context.correction_context
    context.workflow.pause("keyboard_interrupt")
    resumed = Orchestrator(repo, harnesses={"fake": harness}, sandbox=sandbox).resume("repair")
    assert resumed.workflow.snapshot.state == RunState.IMPLEMENTING
    assert await orchestrator.execute_plan(resumed)
    assert resumed.workflow.snapshot.state == RunState.REVIEWING
    assert await orchestrator.review(resumed)
    orchestrator.final_decision(resumed, "approve")
    assert resumed.workflow.snapshot.state == RunState.COMPLETED and (repo / "fix").exists()


@pytest.mark.asyncio
async def test_review_discards_non_blocking_findings_from_correction_context(tmp_path: Path) -> None:
    class LowReviewHarness(_WritingHarness):
        async def _next(self, request: HarnessRequest) -> ProviderResponse:
            if request.role is AgentRole.REVIEWER:
                self.requests.append(request)
                return ProviderResponse("reviewer", {"findings": [{
                    "id": "low", "severity": "low", "evidence": "minor", "suggested_fix": "polish",
                }]})
            return await super()._next(request)

    repo = _repository(tmp_path)
    (repo / "stms.yml").write_text((repo / "stms.yml").read_text().replace("round_1: [high, medium, low]", "round_1: [high]"))
    harness = LowReviewHarness(_plan(["true"]))
    orchestrator = Orchestrator(repo, harnesses={"fake": harness}, sandbox=FakeSandboxRuntime(tmp_path / "policies"))
    context = orchestrator.start("feature", run_id="nonblocking")
    await orchestrator.plan_turn(context, "feature"); orchestrator.approve_plan(context)
    assert await orchestrator.execute_plan(context)
    assert await orchestrator.review(context)
    assert context.correction_context == ""
    assert (context.workflow.artifacts.root / "correction.md").read_text() == ""


@pytest.mark.asyncio
async def test_infrastructure_retry_uses_progressive_injected_backoff(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    harness = FakeHarness([
        InfrastructureError("provider unavailable", "retry"),
        FakeResponse(_plan(["true"])),
    ])
    delays = []

    async def sleep(delay: float) -> None:
        delays.append(delay)

    orchestrator = Orchestrator(
        repo, harnesses={"fake": harness}, sandbox=FakeSandboxRuntime(tmp_path / "policies"),
        sleep=sleep, infrastructure_backoff_seconds=0.25,
    )
    context = orchestrator.start("feature", run_id="infra")
    await orchestrator.plan_turn(context, "feature")
    assert delays == [0.25]
    assert context.workflow.snapshot.review_round is None
