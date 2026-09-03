from pathlib import Path

import pytest

from stms.adapters.harnesses.fake import FakeHarness, FakeResponse
from stms.agents.implementer import ImplementerAgent
from stms.agents.planner import PlannerAgent
from stms.agents.prompts import FilePromptProvider, prompt_digest
from stms.agents.reviewer import ReviewerAgent
from stms.domain.errors import SessionLostError, StructuredOutputError
from stms.domain.models import AcceptanceCriterion, AgentRole, ApprovedPlan, HarnessRequest, PlanTask, TestCommand


def request(role: AgentRole = AgentRole.PLANNER, session_id: str | None = None) -> HarnessRequest:
    return HarnessRequest(role=role, cwd="/tmp/worktree", model="model", effort="high", timeout_seconds=2, max_turns=5, prompt="do work", session_id=session_id)


@pytest.mark.asyncio
async def test_planner_repairs_invalid_output_exactly_twice_then_pauses() -> None:
    harness = FakeHarness([FakeResponse({}), FakeResponse({}), FakeResponse({})])
    with pytest.raises(StructuredOutputError) as error:
        await PlannerAgent(harness).respond(request())
    assert error.value.attempts == 2
    assert len(harness.requests) == 3


@pytest.mark.asyncio
async def test_structured_output_repair_count_is_configurable() -> None:
    harness = FakeHarness([FakeResponse({}), FakeResponse({})])
    with pytest.raises(StructuredOutputError) as error:
        await PlannerAgent(harness, structured_output_retries=1).respond(request())
    assert error.value.attempts == 1
    assert len(harness.requests) == 2


@pytest.mark.asyncio
async def test_harness_rehydrates_after_lost_provider_session_and_normalizes_events() -> None:
    harness = FakeHarness([
        FakeResponse({"status": "needs_input", "questions": ["one"]}, session_id="old", events=({"type": "session_started"},)),
        SessionLostError("lost", "start another session"),
        FakeResponse({"status": "needs_input", "questions": ["two"]}, session_id="new", usage={"output_tokens": 2}),
    ])
    first = await harness.start(request())
    events = [event async for event in harness.stream(first.session_id)]
    resumed = await harness.resume(request(session_id=first.session_id))
    await harness.cancel(resumed.session_id)
    assert [event.event_type for event in events] == ["session_started"]
    assert resumed.session_id == "new" and resumed.usage == {"output_tokens": 2}
    assert len(harness.requests) == 3 and harness.cancelled_sessions == ["new"]


@pytest.mark.asyncio
async def test_implementer_and_reviewer_return_their_typed_reports() -> None:
    task = PlanTask(id="task", title="Task", description="Change one thing", acceptance_criteria=[AcceptanceCriterion(description="works")], essential_tests=["tests/test_task.py"])
    plan = ApprovedPlan(objective="o", expected_outcome="e", test_commands=[TestCommand(argv=["pytest"])], tasks=[task])
    implementer = ImplementerAgent(FakeHarness([FakeResponse({"modified_files": ["src/item.py"], "tests_created": ["tests/test_task.py"], "requires_human_gate": True})]))
    reviewer = ReviewerAgent(FakeHarness([FakeResponse({"findings": [{"id": "review-1", "severity": "high", "evidence": "observable break", "location": "src/item.py:1", "suggested_fix": "fix it"}]})]))
    implementation = await implementer.implement(request(AgentRole.IMPLEMENTER), task, plan, "context")
    review = await reviewer.review(request(AgentRole.REVIEWER))
    assert implementation.requires_human_gate and review.findings[0].id == "review-1"


def test_file_prompt_override_stays_inside_repository(tmp_path: Path) -> None:
    prompt = tmp_path / ".stms" / "planner.md"; prompt.parent.mkdir(); prompt.write_text("custom")
    assert FilePromptProvider(tmp_path, {"planner": ".stms/planner.md"}).prompt_for("planner") == "custom"
    with pytest.raises(Exception):
        FilePromptProvider(tmp_path, {"planner": "../outside.md"}).prompt_for("planner")


def test_effective_prompt_digest_covers_defaults_and_file_contents(tmp_path: Path) -> None:
    custom = tmp_path / "reviewer.md"; custom.write_text("custom reviewer")
    provider = FilePromptProvider(
        tmp_path,
        {"planner": None, "implementer": None, "reviewer": "reviewer.md"},
        {"planner": "p", "implementer": "i", "reviewer": "r"},
    )
    first = prompt_digest(provider)
    custom.write_text("changed reviewer")
    assert prompt_digest(provider) != first


@pytest.mark.asyncio
async def test_reviewer_receives_configured_severity_descriptions() -> None:
    harness = FakeHarness([FakeResponse({"findings": []})])
    await ReviewerAgent(harness, severity_descriptions={"high": "custom high"}).review(request(AgentRole.REVIEWER))
    assert "custom high" in harness.requests[0].prompt
