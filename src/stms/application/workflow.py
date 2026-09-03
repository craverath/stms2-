"""Application-level checkpointing around the pure state machine."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import uuid4
from hashlib import sha256
from typing import Callable, TypeVar

from stms.adapters.persistence.artifact_store import LocalArtifactStore
from stms.adapters.persistence.langgraph_engine import LocalWorkflowEngine
from stms.domain.events import NormalizedEvent
from stms.domain.models import AllowedEvent, RunMetadata, RunState, WorkflowSnapshot
from stms.domain.states import allowed_events, transition


class RunWorkflow:
    """Own state changes and their readable projections for one persisted run."""

    def __init__(self, engine: LocalWorkflowEngine, artifacts: LocalArtifactStore, snapshot: WorkflowSnapshot) -> None:
        self.engine = engine
        self.artifacts = artifacts
        self.snapshot = snapshot

    @classmethod
    def new(cls, engine: LocalWorkflowEngine, artifacts: LocalArtifactStore, metadata: RunMetadata) -> "RunWorkflow":
        snapshot = WorkflowSnapshot(metadata=metadata, allowed_events=allowed_events(RunState.INTERVIEWING))
        workflow = cls(engine, artifacts, snapshot)
        workflow._persist("run-created")
        return workflow

    def apply(self, event: AllowedEvent, *, operation: str | None = None, result: str | None = None) -> WorkflowSnapshot:
        operation_id = operation or f"transition-{uuid4().hex}"
        self.engine.checkpoint_before(self.snapshot, operation_id, "transition")
        self.snapshot = transition(self.snapshot, event)
        self.engine.checkpoint_after(self.snapshot, operation_id)
        self._persist(event.value.lower(), result=result)
        return self.snapshot

    def operation_id(self, kind: str, *parts: str) -> str:
        """Stable idempotency key: resume observes the same external effect."""
        value = "\x1f".join((self.snapshot.metadata.run_id, kind, *parts))
        return f"{kind}-{sha256(value.encode()).hexdigest()[:24]}"

    def confirmed(self, operation_id: str) -> bool:
        operation = self.engine.store.operation(self.snapshot.metadata.run_id, operation_id)
        return operation is not None and operation.status.value == "confirmed"

    T = TypeVar("T")

    def effect(self, kind: str, parts: tuple[str, ...], operation: Callable[[], T], *, reference: str | None = None) -> T | None:
        """Checkpoint an external operation exactly once within a run."""
        operation_id = self.operation_id(kind, *parts)
        if self.confirmed(operation_id):
            return None
        self.engine.checkpoint_before(self.snapshot, operation_id, kind)
        result = operation()
        self.engine.checkpoint_after(self.snapshot, operation_id, reference)
        self._persist(f"{kind}-confirmed", result=reference)
        return result

    def replace_snapshot(self, snapshot: WorkflowSnapshot, *, event_type: str = "checkpoint") -> None:
        self.snapshot = snapshot
        self.engine.checkpoint_after(snapshot, f"checkpoint-{uuid4().hex}")
        self._persist(event_type)

    def pause(self, result: str = "user_requested") -> WorkflowSnapshot:
        if self.snapshot.state in {RunState.PAUSED, RunState.COMPLETED, RunState.FAILED}:
            return self.snapshot
        prior_state = self.snapshot.state
        paused = self.apply(AllowedEvent.PAUSE, result=result)
        self.replace_snapshot(paused.model_copy(update={"resume_state": prior_state, "pause_reason": result}), event_type="run-paused")
        return self.snapshot

    def resume(self) -> WorkflowSnapshot:
        """Restore a user-interrupted state without replaying an external effect."""
        if self.snapshot.state != RunState.PAUSED:
            return self.snapshot
        if self.snapshot.pause_reason == "base_changed" or self.snapshot.resume_state is None:
            return self.snapshot
        restored = self.snapshot.model_copy(update={
            "state": self.snapshot.resume_state,
            "allowed_events": allowed_events(self.snapshot.resume_state),
            "last_transition": f"PAUSED:RESUME->{self.snapshot.resume_state}",
            "resume_state": None,
            "pause_reason": None,
        })
        self.replace_snapshot(restored, event_type="run-resumed")
        return self.snapshot

    def abort(self) -> WorkflowSnapshot:
        return self.apply(AllowedEvent.ABORT, result="user_aborted")

    def _persist(self, event_type: str, *, result: str | None = None) -> None:
        self.artifacts.write_json("state.json", self.snapshot.model_dump(mode="json"))
        self.artifacts.append_event(NormalizedEvent(
            run_id=self.snapshot.metadata.run_id,
            event_type=event_type,
            phase=self.snapshot.phase,
            state=self.snapshot.state,
            task_id=self.snapshot.task_id,
            attempt=self.snapshot.attempt,
            review_round=self.snapshot.review_round,
            result=result,
        ))
