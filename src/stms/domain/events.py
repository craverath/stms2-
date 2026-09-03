"""Normalized events are observability data, never transition inputs."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from pydantic import Field

from .models import RunPhase, RunState, StrictModel, TaskId


class NormalizedEvent(StrictModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str
    event_type: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    phase: RunPhase | None = None
    state: RunState | None = None
    task_id: TaskId | None = None
    attempt: int | None = None
    review_round: int | None = None
    duration_seconds: float | None = None
    result: str | None = None
    harness: str | None = None
    model: str | None = None
    adapter_version: str | None = None
    usage: dict[str, int | float] = Field(default_factory=dict)
    artifact_reference: str | None = None


class NormalizedHarnessEvent(StrictModel):
    event_type: str
    session_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    message: str | None = None
    usage: dict[str, int | float] = Field(default_factory=dict)
