"""Pure state-machine validation. No external effect belongs here."""
from __future__ import annotations

from dataclasses import dataclass

from .errors import InvalidTransitionError
from .models import AllowedEvent, RunPhase, RunState, RunSubphase, WorkflowSnapshot


@dataclass(frozen=True)
class Transition:
    event: AllowedEvent
    target: RunState
    phase: RunPhase
    subphase: RunSubphase = RunSubphase.NONE


_TRANSITIONS: dict[RunState, tuple[Transition, ...]] = {
    RunState.INTERVIEWING: (Transition(AllowedEvent.PLAN_READY, RunState.PLAN_PENDING_APPROVAL, RunPhase.PLANNING), Transition(AllowedEvent.PAUSE, RunState.PAUSED, RunPhase.PLANNING), Transition(AllowedEvent.ABORT, RunState.FAILED, RunPhase.PLANNING)),
    RunState.PLAN_PENDING_APPROVAL: (Transition(AllowedEvent.FEEDBACK, RunState.INTERVIEWING, RunPhase.PLANNING), Transition(AllowedEvent.APPROVE_PLAN, RunState.IMPLEMENTING, RunPhase.IMPLEMENTATION), Transition(AllowedEvent.PAUSE, RunState.PAUSED, RunPhase.PLANNING), Transition(AllowedEvent.ABORT, RunState.FAILED, RunPhase.PLANNING)),
    RunState.IMPLEMENTING: (Transition(AllowedEvent.TASKS_READY, RunState.TESTING, RunPhase.TESTING), Transition(AllowedEvent.PAUSE, RunState.PAUSED, RunPhase.IMPLEMENTATION), Transition(AllowedEvent.ABORT, RunState.FAILED, RunPhase.IMPLEMENTATION)),
    RunState.TESTING: (Transition(AllowedEvent.TESTS_PASSED, RunState.REVIEWING, RunPhase.REVIEWING), Transition(AllowedEvent.TESTS_FAILED, RunState.IMPLEMENTING, RunPhase.IMPLEMENTATION, RunSubphase.TEST_FIX), Transition(AllowedEvent.PAUSE, RunState.PAUSED, RunPhase.TESTING), Transition(AllowedEvent.ABORT, RunState.FAILED, RunPhase.TESTING)),
    RunState.REVIEWING: (Transition(AllowedEvent.REVIEW_ACCEPTED, RunState.FINAL_APPROVAL, RunPhase.REVIEWING, RunSubphase.FINAL_GATE), Transition(AllowedEvent.REVIEW_BLOCKING, RunState.IMPLEMENTING, RunPhase.IMPLEMENTATION), Transition(AllowedEvent.PAUSE, RunState.PAUSED, RunPhase.REVIEWING, RunSubphase.HUMAN_ESCALATION), Transition(AllowedEvent.ABORT, RunState.FAILED, RunPhase.REVIEWING)),
    RunState.FINAL_APPROVAL: (Transition(AllowedEvent.FINAL_APPROVE, RunState.MERGING, RunPhase.INTEGRATION), Transition(AllowedEvent.ADJUST, RunState.IMPLEMENTING, RunPhase.IMPLEMENTATION), Transition(AllowedEvent.REPLAN, RunState.REPLANNING, RunPhase.PLANNING), Transition(AllowedEvent.PAUSE, RunState.PAUSED, RunPhase.REVIEWING, RunSubphase.FINAL_GATE), Transition(AllowedEvent.ABORT, RunState.FAILED, RunPhase.REVIEWING)),
    RunState.REPLANNING: (Transition(AllowedEvent.FEEDBACK, RunState.INTERVIEWING, RunPhase.PLANNING), Transition(AllowedEvent.PAUSE, RunState.PAUSED, RunPhase.PLANNING), Transition(AllowedEvent.ABORT, RunState.FAILED, RunPhase.PLANNING)),
    RunState.MERGING: (Transition(AllowedEvent.MERGE_SUCCEEDED, RunState.COMPLETED, RunPhase.INTEGRATION), Transition(AllowedEvent.BASE_CHANGED, RunState.PAUSED, RunPhase.INTEGRATION), Transition(AllowedEvent.PAUSE, RunState.PAUSED, RunPhase.INTEGRATION), Transition(AllowedEvent.ABORT, RunState.FAILED, RunPhase.INTEGRATION)),
    RunState.PAUSED: (Transition(AllowedEvent.PAUSE, RunState.PAUSED, RunPhase.PLANNING), Transition(AllowedEvent.ABORT, RunState.FAILED, RunPhase.PLANNING)),
    RunState.COMPLETED: (), RunState.FAILED: (),
}


def allowed_events(state: RunState) -> list[AllowedEvent]:
    return [transition.event for transition in _TRANSITIONS[state]]


def transition(snapshot: WorkflowSnapshot, event: AllowedEvent) -> WorkflowSnapshot:
    candidate = next((item for item in _TRANSITIONS[snapshot.state] if item.event == event), None)
    if candidate is None:
        raise InvalidTransitionError(f"Event {event} is not allowed from {snapshot.state}", "Choose one of the state’s allowed events before performing an external action.")
    return snapshot.model_copy(update={
        "state": candidate.target, "phase": candidate.phase, "subphase": candidate.subphase,
        "allowed_events": allowed_events(candidate.target), "last_transition": f"{snapshot.state}:{event}->{candidate.target}",
    })
