from stms.application.scheduler import task_waves
from stms.domain.errors import DomainError, InvalidTransitionError
from stms.domain.models import AcceptanceCriterion, AllowedEvent, ApprovedPlan, PlanTask, RunMetadata, RunState, TaskDependency, TestCommand, WorkflowSnapshot
from stms.domain.policies import blocking_findings, escalates_to_human, planner_gate_required, retry_exhausted
from stms.domain.states import allowed_events, transition


def task(identifier: str, dependencies: list[str] = []) -> PlanTask:
    return PlanTask(id=identifier, title=identifier, description="d", dependencies=[TaskDependency(task_id=item) for item in dependencies], acceptance_criteria=[AcceptanceCriterion(description="ok")], essential_tests=["test"])


def test_state_transition_is_checked_before_side_effects() -> None:
    snapshot = WorkflowSnapshot(metadata=RunMetadata(run_id="run", repository="/repo", branch_base="main", commit_base="abc", config_digest="x"))
    assert transition(snapshot, AllowedEvent.PLAN_READY).state is RunState.PLAN_PENDING_APPROVAL
    assert AllowedEvent.PLAN_READY in allowed_events(RunState.INTERVIEWING)
    try: transition(snapshot, AllowedEvent.FINAL_APPROVE)
    except InvalidTransitionError: pass
    else: raise AssertionError("invalid transition accepted")


def test_waves_are_stable_limited_and_cycle_rejected() -> None:
    plan = ApprovedPlan(objective="o", expected_outcome="e", test_commands=[TestCommand(argv=["x"])], tasks=[task("a"), task("b"), task("c", ["a", "b"])])
    assert [[item.id for item in wave] for wave in task_waves(plan, 2)] == [["a", "b"], ["c"]]
    cyclic = plan.model_copy(update={"tasks": [task("a", ["b"]), task("b", ["a"])]})
    try: task_waves(cyclic, 2)
    except DomainError: pass
    else: raise AssertionError("cycle accepted")


def test_review_and_retry_policy() -> None:
    from stms.domain.models import Severity
    blocking = {"round_1": [Severity.HIGH], "round_2": [Severity.LOW], "round_3": [], "round_4": []}
    escalate = {"round_2": [Severity.LOW]}
    assert blocking_findings(1, [Severity.LOW], blocking) == []
    assert blocking_findings(2, [Severity.LOW], blocking) == [Severity.LOW]
    assert escalates_to_human(2, [Severity.LOW], escalate)
    assert not escalates_to_human(4, [Severity.HIGH], escalate)
    assert planner_gate_required(10) and retry_exhausted(3, 3)
