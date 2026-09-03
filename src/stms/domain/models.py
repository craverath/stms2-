"""Versioned, strict data contracts shared by deterministic components."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
import json
from pathlib import PurePosixPath
import re
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

SCHEMA_VERSION = 1

RunId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")]
TaskId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")]
OperationId = Annotated[str, Field(min_length=1, max_length=255)]
SessionId = Annotated[str, Field(min_length=1, max_length=255)]
AttemptId = Annotated[str, Field(min_length=1, max_length=255)]
ReviewRound = Annotated[int, Field(ge=1, le=4)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)
    schema_version: int = Field(default=SCHEMA_VERSION, ge=1)


class RunState(StrEnum):
    INTERVIEWING = "INTERVIEWING"; PLAN_PENDING_APPROVAL = "PLAN_PENDING_APPROVAL"
    IMPLEMENTING = "IMPLEMENTING"; TESTING = "TESTING"; REVIEWING = "REVIEWING"
    FINAL_APPROVAL = "FINAL_APPROVAL"; MERGING = "MERGING"; COMPLETED = "COMPLETED"
    PAUSED = "PAUSED"; REPLANNING = "REPLANNING"; FAILED = "FAILED"


class RunPhase(StrEnum):
    PLANNING = "PLANNING"; IMPLEMENTATION = "IMPLEMENTATION"; TESTING = "TESTING"
    REVIEWING = "REVIEWING"; INTEGRATION = "INTEGRATION"


class RunSubphase(StrEnum):
    NONE = "NONE"; HUMAN_ESCALATION = "HUMAN_ESCALATION"; TEST_FIX = "TEST_FIX"
    CONFLICT_RESOLUTION = "CONFLICT_RESOLUTION"; FINAL_GATE = "FINAL_GATE"


class AllowedEvent(StrEnum):
    PLAN_READY = "PLAN_READY"; FEEDBACK = "FEEDBACK"; APPROVE_PLAN = "APPROVE_PLAN"
    TASKS_READY = "TASKS_READY"; TESTS_PASSED = "TESTS_PASSED"; TESTS_FAILED = "TESTS_FAILED"
    REVIEW_ACCEPTED = "REVIEW_ACCEPTED"; REVIEW_BLOCKING = "REVIEW_BLOCKING"
    FINAL_APPROVE = "FINAL_APPROVE"; ADJUST = "ADJUST"; REPLAN = "REPLAN"
    MERGE_SUCCEEDED = "MERGE_SUCCEEDED"; BASE_CHANGED = "BASE_CHANGED"; PAUSE = "PAUSE"; ABORT = "ABORT"


class PauseReason(StrEnum):
    HUMAN_ESCALATION = "HUMAN_ESCALATION"; USER_REQUESTED = "USER_REQUESTED"
    BASE_CHANGED = "BASE_CHANGED"; RETRIES_EXHAUSTED = "RETRIES_EXHAUSTED"
    INFRASTRUCTURE = "INFRASTRUCTURE"; STRUCTURED_OUTPUT = "STRUCTURED_OUTPUT"


class ExecutionMode(StrEnum): SEQUENTIAL = "sequential"; PARALLEL = "parallel"
class Severity(StrEnum): HIGH = "high"; MEDIUM = "medium"; LOW = "low"
class AgentRole(StrEnum): PLANNER = "planner"; IMPLEMENTER = "implementer"; REVIEWER = "reviewer"; TEST_RUNNER = "test_runner"
class OperationStatus(StrEnum): PENDING = "pending"; STARTED = "started"; CONFIRMED = "confirmed"; FAILED = "failed"


_SAFE_LITERAL_ENVIRONMENT_NAMES = frozenset({"CI", "NO_COLOR", "LANG", "LC_ALL", "TZ", "PYTHONUNBUFFERED", "PYTHONDONTWRITEBYTECODE"})


class EnvironmentBinding(StrictModel):
    """Persist a safe literal or an environment-variable reference, never a secret."""
    reference: str | None = None
    literal: str | None = None

    @field_validator("reference")
    @classmethod
    def reference_name_is_safe(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
            raise ValueError("environment references must be variable names")
        return value

    @model_validator(mode="after")
    def exactly_one_source(self) -> "EnvironmentBinding":
        if (self.reference is None) == (self.literal is None):
            raise ValueError("environment binding needs exactly one of reference or literal")
        return self


class TestCommand(StrictModel):
    __test__ = False
    argv: list[str] = Field(min_length=1)
    cwd: str = "."
    timeout_seconds: int = Field(default=900, ge=1, le=86_400)
    shell: bool = False
    environment: dict[str, EnvironmentBinding] = Field(default_factory=dict)

    @field_validator("argv")
    @classmethod
    def argv_is_safe(cls, value: list[str]) -> list[str]:
        if any(not item or "\x00" in item for item in value):
            raise ValueError("argv entries must be non-empty and contain no NUL")
        return value

    @field_validator("cwd")
    @classmethod
    def relative_cwd(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("cwd must be a relative path inside the repository")
        return value

    @field_validator("environment", mode="before")
    @classmethod
    def safe_environment_bindings(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        bindings: dict[str, object] = {}
        for name, binding in value.items():
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                raise ValueError("environment names must be variable names")
            if isinstance(binding, str):
                if name not in _SAFE_LITERAL_ENVIRONMENT_NAMES:
                    raise ValueError(f"environment value for {name} must be a reference; literal values are only allowed for safe variables")
                bindings[name] = {"literal": binding}
            else:
                bindings[name] = binding
        return bindings

    def resolved_environment(self, source: dict[str, str]) -> dict[str, str]:
        result: dict[str, str] = {}
        for name, binding in self.environment.items():
            if binding.literal is not None:
                result[name] = binding.literal
            elif binding.reference is not None and binding.reference in source:
                result[name] = source[binding.reference]
        return result


class AcceptanceCriterion(StrictModel):
    description: str = Field(min_length=1)
    observable: bool = True


class TaskDependency(StrictModel):
    task_id: TaskId


class PlanTask(StrictModel):
    id: TaskId
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    dependencies: list[TaskDependency] = Field(default_factory=list)
    execution_mode: ExecutionMode = ExecutionMode.PARALLEL
    acceptance_criteria: list[AcceptanceCriterion] = Field(min_length=1)
    essential_tests: list[str] = Field(min_length=1)
    affected_paths: list[str] = Field(default_factory=list)
    focused_test_commands: list[int] = Field(default_factory=list)


class ApprovedUntrackedFile(StrictModel):
    source: str = Field(min_length=1)
    destination: str = Field(min_length=1)
    max_bytes: int = Field(default=1_048_576, ge=1, le=100_000_000)

    @field_validator("source", "destination")
    @classmethod
    def safe_relative_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("path must stay inside the repository")
        return value


class ApprovedPlan(StrictModel):
    objective: str = Field(min_length=1)
    expected_outcome: str = Field(min_length=1)
    scope: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    human_decisions: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    test_commands: list[TestCommand] = Field(min_length=1)
    tasks: list[PlanTask] = Field(min_length=1)
    untracked_files: list[ApprovedUntrackedFile] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_references(self) -> "ApprovedPlan":
        ids = [task.id for task in self.tasks]
        if len(ids) != len(set(ids)):
            raise ValueError("task IDs must be unique")
        known = set(ids)
        for task in self.tasks:
            dependencies = [dep.task_id for dep in task.dependencies]
            if task.id in dependencies or not set(dependencies) <= known:
                raise ValueError(f"task {task.id} has an invalid dependency")
            if len(dependencies) != len(set(dependencies)):
                raise ValueError(f"task {task.id} repeats a dependency")
            if any(i >= len(self.test_commands) for i in task.focused_test_commands):
                raise ValueError(f"task {task.id} references an unknown test command")
        return self


class AgentConfig(StrictModel):
    harness: str = Field(min_length=1)
    model: str = Field(min_length=1)
    effort: str = Field(min_length=1)
    prompt: str | None = None
    timeout_seconds: int = Field(default=1800, ge=1, le=86_400)
    max_turns: int = Field(default=20, ge=1, le=1000)


class WorkflowConfig(StrictModel):
    max_parallel_tasks: int = Field(default=2, ge=1, le=64)
    infrastructure_retries: int = Field(default=2, ge=0, le=20)
    implementation_retries: int = Field(default=3, ge=0, le=20)
    structured_output_retries: int = Field(default=2, ge=0, le=20)


class TestsConfig(StrictModel):
    timeout_seconds: int = Field(default=900, ge=1, le=86_400)
    commands: list[TestCommand] = Field(default_factory=list)


class ReviewConfig(StrictModel):
    severities: dict[Severity, str]
    blocking: dict[str, list[Severity]]
    escalate: dict[str, list[Severity]]

    @model_validator(mode="after")
    def validate_review_policy(self) -> "ReviewConfig":
        expected_severities = set(Severity)
        if set(self.severities) != expected_severities:
            raise ValueError("review.severities must define high, medium, and low")
        if any(not description.strip() for description in self.severities.values()):
            raise ValueError("review severity descriptions must be non-empty")
        valid_rounds = {f"round_{number}" for number in range(1, 5)}
        if set(self.blocking) != valid_rounds:
            raise ValueError("review.blocking must define exactly round_1 through round_4")
        if not set(self.escalate) <= valid_rounds:
            raise ValueError("review.escalate contains an unknown review round")
        return self


class SecurityConfig(StrictModel):
    sandbox: str = Field(default="srt", min_length=1)
    allow_native_fallback: bool = False
    planner_web: bool = True
    test_network: bool = False


class RuntimeConfig(StrictModel):
    version: Literal[1]
    agents: dict[AgentRole, AgentConfig]
    workflow: WorkflowConfig = Field(default_factory=WorkflowConfig)
    tests: TestsConfig = Field(default_factory=TestsConfig)
    review: ReviewConfig
    security: SecurityConfig = Field(default_factory=SecurityConfig)

    def digest(self) -> str:
        data = self.model_dump(mode="json", exclude_none=True)
        raw = json.dumps(data, sort_keys=True, separators=(",", ":"))
        return sha256(raw.encode()).hexdigest()


class PlannerOutput(StrictModel):
    status: Literal["needs_input", "plan_ready"]
    questions: list[str] = Field(default_factory=list, max_length=3)
    plan: ApprovedPlan | None = None
    context_notes: list[str] = Field(default_factory=list)
    web_sources: list["WebSource"] = Field(default_factory=list)

    @model_validator(mode="after")
    def output_matches_status(self) -> "PlannerOutput":
        if self.status == "plan_ready" and self.plan is None: raise ValueError("plan_ready requires a plan")
        if self.status == "needs_input" and not self.questions: raise ValueError("needs_input requires questions")
        return self


class WebSource(StrictModel):
    url: str = Field(min_length=1)
    conclusion: str = Field(min_length=1)


class ImplementerOutput(StrictModel):
    modified_files: list[str]
    tests_created: list[str]
    suggested_commands: list[TestCommand] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    requires_human_gate: bool = False

    @field_validator("modified_files", "tests_created")
    @classmethod
    def report_paths_are_relative(cls, value: list[str]) -> list[str]:
        for path_value in value:
            path = PurePosixPath(path_value)
            if not path_value or path.is_absolute() or ".." in path.parts:
                raise ValueError("reported paths must be relative paths inside the worktree")
        return value


class ReviewFinding(StrictModel):
    id: str = Field(min_length=1)
    severity: Severity
    evidence: str = Field(min_length=1)
    location: str | None = None
    suggested_fix: str = Field(min_length=1)


class ReviewerOutput(StrictModel):
    findings: list[ReviewFinding] = Field(default_factory=list)

    @model_validator(mode="after")
    def finding_ids_are_unique(self) -> "ReviewerOutput":
        identifiers = [finding.id for finding in self.findings]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("review finding IDs must be unique")
        return self


class HarnessRequest(StrictModel):
    role: AgentRole
    cwd: str
    model: str
    effort: str
    timeout_seconds: int
    max_turns: int
    prompt: str
    tools: dict[str, Any] = Field(default_factory=dict)
    session_id: SessionId | None = None


class HarnessResult(StrictModel):
    session_id: SessionId
    output: dict[str, Any]
    usage: dict[str, int | float] = Field(default_factory=dict)


class ProcessResult(StrictModel):
    argv: list[str]
    cwd: str
    started_at: datetime
    duration_seconds: float = Field(ge=0)
    exit_code: int | None = None
    signal: int | None = None
    timed_out: bool = False
    cancelled: bool = False
    stdout: str = ""
    stderr: str = ""
    truncated: bool = False

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.cancelled and self.signal is None


class TestAttempt(StrictModel):
    id: AttemptId = Field(default_factory=lambda: str(uuid4()))
    command: TestCommand
    result: ProcessResult
    log_path: str | None = None


class ExternalOperation(StrictModel):
    id: OperationId
    kind: str
    status: OperationStatus = OperationStatus.PENDING
    result_reference: str | None = None


class RunMetadata(StrictModel):
    run_id: RunId
    repository: str
    branch_base: str
    commit_base: str
    config_digest: str
    workflow_version: str = "1"
    prompt_digest: str = ""
    adapter_versions: dict[str, str] = Field(default_factory=dict)


class WorkflowSnapshot(StrictModel):
    metadata: RunMetadata
    state: RunState = RunState.INTERVIEWING
    phase: RunPhase = RunPhase.PLANNING
    subphase: RunSubphase = RunSubphase.NONE
    task_id: TaskId | None = None
    review_round: ReviewRound | None = None
    attempt: int = Field(default=0, ge=0)
    implementation_attempts: dict[str, int] = Field(default_factory=dict)
    correction_stage: str | None = None
    allowed_events: list[AllowedEvent] = Field(default_factory=list)
    last_transition: str | None = None
    resume_state: RunState | None = None
    pause_reason: str | None = None
    completed_task_ids: list[TaskId] = Field(default_factory=list)
    completed_waves: int = Field(default=0, ge=0)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Capability(StrictModel):
    name: str
    version: str | None = None
    supported: bool


class SecretReference(StrictModel):
    name: str
    reference: str
    value: SecretStr | None = Field(default=None, exclude=True, repr=False)
