"""The deterministic coordinator for planning, task execution, review and merge."""
from __future__ import annotations

import asyncio
from hashlib import sha256
from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Awaitable, Callable, Mapping, TypeVar
from uuid import uuid4

from stms.adapters.persistence.artifact_store import LocalArtifactStore
from stms.adapters.persistence.langgraph_engine import LocalWorkflowEngine
from stms.adapters.persistence.sqlite_store import SQLiteCheckpointStore, resumable_run_exists
from stms.agents.implementer import ImplementerAgent
from stms.agents.implementer import DEFAULT_PROMPT as IMPLEMENTER_PROMPT
from stms.agents.planner import DEFAULT_PROMPT as PLANNER_PROMPT, PlannerAgent
from stms.agents.prompts import FilePromptProvider, prompt_digest
from stms.agents.reviewer import DEFAULT_PROMPT as REVIEWER_PROMPT, ReviewerAgent
from stms.application.preflight import PreflightService
from stms.application.configuration import verify_frozen_config
from stms.application.scheduler import task_waves
from stms.deterministic.test_discovery import discover_test_commands
from stms.application.workflow import RunWorkflow
from stms.domain.errors import DomainError, InfrastructureError, StructuredOutputError
from stms.domain.models import (
    AgentConfig, AgentRole, AllowedEvent, ApprovedPlan, HarnessRequest, RunMetadata,
    RunState, RuntimeConfig, PlanTask, TestCommand, StrictModel, PlannerOutput, ImplementerOutput, ReviewerOutput,
)
from stms.domain.policies import blocking_findings, escalates_to_human, retry_exhausted
from stms.domain.ports import AgentHarness, EventRenderer, EventSink, PromptProvider, SandboxRuntime
from stms.deterministic.test_runner import DeterministicTestRunner
from stms.deterministic.worktree_manager import GitWorktreeManager


@dataclass
class RunContext:
    workflow: RunWorkflow
    config: RuntimeConfig
    repository: Path
    plan: ApprovedPlan | None = None
    context_markdown: str = ""
    correction_context: str = ""
    integration: Path | None = None


class Orchestrator:
    """Coordinates effects while agents only provide typed semantic output."""

    def __init__(
        self,
        repository: Path,
        *,
        harnesses: Mapping[str, AgentHarness],
        sandbox: SandboxRuntime,
        worktrees: GitWorktreeManager | None = None,
        event_sink: EventSink | None = None,
        event_renderer: EventRenderer | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        infrastructure_backoff_seconds: float = 1.0,
    ) -> None:
        self.repository = repository.resolve()
        self.harnesses = harnesses
        self.sandbox = sandbox
        self.worktrees = worktrees or GitWorktreeManager(self.repository)
        self.event_sink = event_sink
        self.event_renderer = event_renderer
        self._sleep = sleep
        self._infrastructure_backoff_seconds = infrastructure_backoff_seconds

    def start(self, request_text: str, *, run_id: str | None = None) -> RunContext:
        if not request_text.strip():
            raise DomainError("A non-empty request is required.", "Provide a prompt or pass --file with a non-empty UTF-8 document.")
        # Preflight is deliberately fully read-only.  Constructing the SQLite
        # control store creates WAL files, so it occurs only after validation.
        preflight = PreflightService(self.repository, self.harnesses, self.sandbox).validate()
        prompts = self._prompt_provider(preflight.config)
        effective_prompt_digest = prompt_digest(prompts)
        if resumable_run_exists(self.repository):
            raise InfrastructureError("Repository already has a resumable STMS run.", "Use stms resume or abort the existing run before starting another one.")
        control = self._control_store()
        identifier = run_id or uuid4().hex[:12]
        control.acquire_lock(self.repository, identifier)
        try:
            artifacts = LocalArtifactStore(self.repository, identifier)
            store = SQLiteCheckpointStore(artifacts.root / "checkpoint.sqlite")
            engine = LocalWorkflowEngine(store)
            metadata = RunMetadata(
                run_id=identifier, repository=str(self.repository), branch_base=preflight.branch_base,
                commit_base=preflight.commit_base, config_digest=preflight.config.digest(),
                prompt_digest=effective_prompt_digest, adapter_versions=preflight.adapter_versions,
            )
            workflow = RunWorkflow.new(engine, artifacts, metadata, event_sink=self.event_sink, event_renderer=self.event_renderer)
            artifacts.write_text("request.md", request_text)
            return RunContext(workflow, preflight.config, self.repository)
        except Exception:
            control.release_lock(self.repository, identifier)
            raise

    async def plan_turn(self, context: RunContext, user_text: str) -> object:
        self._require_state(context, RunState.INTERVIEWING)
        agent_config = context.config.agents[AgentRole.PLANNER]
        agent = PlannerAgent(
            self._harness(agent_config.harness), self._prompt_provider(context.config),
            structured_output_retries=context.config.workflow.structured_output_retries,
        )
        policy = await self._external_retry(context, "sandbox:planner", lambda: asyncio.to_thread(self._prepare_policy, AgentRole.PLANNER, context.repository, context.config))
        artifacts = context.workflow.artifacts
        history = self._planning_history(artifacts)
        history.append({"speaker": "user", "text": user_text})
        planner_prompt = "Conversation so far:\n" + "\n".join(f"{item['speaker']}: {item['text']}" for item in history) + "\n\nCurrent user turn:\n" + user_text
        response = await self._harness_invocation(
            context, "planner", (str(len(history)),), "harness/planner-current.json", PlannerOutput,
            lambda: agent.respond(self._request(AgentRole.PLANNER, agent_config, context.repository, planner_prompt, policy)),
            {"prompt": planner_prompt, "policy": str(policy)},
        )
        history.append({"speaker": "planner", "status": response.status, "questions": response.questions})
        artifacts.write_json("planning-history.json", history)
        if response.status == "needs_input":
            previous = artifacts.root / "planning.json"
            turns = 1
            if previous.exists():
                import json
                turns = int(json.loads(previous.read_text(encoding="utf-8")).get("turns_without_plan", 0)) + 1
            artifacts.write_json("planning.json", {"status": response.status, "questions": response.questions, "turns_without_plan": turns, "human_gate_required": turns >= 10})
            return response
        assert response.plan is not None
        context.context_markdown = self._context_markdown(response.context_notes, response.web_sources)
        effective_commands = discover_test_commands(
            context.repository, context.config.tests.commands, response.plan.test_commands,
            default_timeout_seconds=context.config.tests.timeout_seconds,
        )
        if effective_commands:
            # Discovery precedence is frozen into the approved artifact.
            response = response.model_copy(update={"plan": response.plan.model_copy(update={"test_commands": effective_commands})})
            assert response.plan is not None
        context.plan = response.plan
        artifacts.write_text("plan.md", self._plan_markdown(response.plan))
        artifacts.write_text("context.md", context.context_markdown)
        artifacts.write_json("approved-plan.json", response.plan.model_dump(mode="json"))
        context.workflow.apply(AllowedEvent.PLAN_READY, result="awaiting_human_approval")
        return response

    def approve_plan(self, context: RunContext) -> None:
        self._require_state(context, RunState.PLAN_PENDING_APPROVAL)
        if context.plan is None:
            context.plan = self._load_plan(context.workflow.artifacts)
        verify_frozen_config(context.config, context.workflow.snapshot.metadata.config_digest)
        context.workflow.apply(AllowedEvent.APPROVE_PLAN, result="plan_frozen")

    def feedback(self, context: RunContext, feedback: str) -> None:
        self._require_state(context, RunState.PLAN_PENDING_APPROVAL)
        if not feedback.strip():
            raise DomainError("Plan feedback cannot be empty.", "Describe the change needed or approve/abort the plan.")
        context.workflow.artifacts.write_text("feedback.md", feedback)
        history = self._planning_history(context.workflow.artifacts)
        history.append({"speaker": "user", "text": feedback, "kind": "plan_feedback"})
        context.workflow.artifacts.write_json("planning-history.json", history)
        context.workflow.apply(AllowedEvent.FEEDBACK, result="planner_feedback")

    def abort(self, context: RunContext) -> None:
        context.workflow.abort()
        self._control_store().release_lock(self.repository, context.workflow.snapshot.metadata.run_id)

    async def execute_plan(self, context: RunContext) -> bool:
        self._require_state(context, RunState.IMPLEMENTING)
        plan = context.plan or self._load_plan(context.workflow.artifacts)
        context.plan = plan
        if context.integration is None:
            created = await self._external_retry(
                context,
                "worktree:integration",
                lambda: asyncio.to_thread(
                    context.workflow.effect,
                    "integration-worktree",
                    ("create",),
                    lambda: self.worktrees.create_integration(context.workflow.snapshot.metadata.run_id, context.workflow.snapshot.metadata.commit_base),
                ),
            )
            context.integration = created or await self._external_retry(
                context,
                "worktree:integration:reconcile",
                lambda: asyncio.to_thread(self.worktrees.create_integration, context.workflow.snapshot.metadata.run_id, context.workflow.snapshot.metadata.commit_base),
            )
            context.workflow.observe("worktree-created", result=str(context.integration))
        elif context.correction_context and not (context.workflow.snapshot.correction_stage or "").startswith("focused:"):
            return await self._correct_integration(context)
        for wave_index, wave in enumerate(task_waves(plan, context.config.workflow.max_parallel_tasks)):
            if wave_index < context.workflow.snapshot.completed_waves:
                continue
            base = self._git_head(context.integration)
            task_paths = {
                task.id: await self._external_retry(
                    context, f"worktree:task:{task.id}",
                    lambda task=task: asyncio.to_thread(self.worktrees.create_task, context.workflow.snapshot.metadata.run_id, task.id, base),
                )
                for task in wave
            }
            outputs = await self._implement_wave(context, wave, task_paths)
            if not all(outputs.values()):
                return False
            if context.workflow.snapshot.correction_stage and context.workflow.snapshot.correction_stage.startswith("focused:"):
                context.workflow.replace_snapshot(context.workflow.snapshot.model_copy(update={"correction_stage": None}), event_type="focused-correction-completed")
                context.correction_context = ""
                context.workflow.artifacts.write_text("correction.md", "")
            for task in wave:
                try:
                    commit_id = context.workflow.operation_id("task-commit", task.id)
                    commit_artifact = context.workflow.artifacts.root / f"operations/{commit_id}.json"
                    if not context.workflow.confirmed(commit_id):
                        reconciled = self.worktrees.reconciled_task_commit(task.id, self._operation_metadata(commit_artifact))
                        if reconciled is None:
                            # The reconciliation contract is durable before git commit.
                            context.workflow.artifacts.write_json(f"operations/{commit_id}.json", self.worktrees.prepare_task_commit(task.id))
                            commit = context.workflow.effect("task-commit", (task.id,), lambda task=task: self.worktrees.commit_task(task.id, f"stms task {task.id}"))
                            metadata = self._operation_metadata(commit_artifact) or {}
                            metadata.update(self.worktrees.task_commit_metadata(task.id))
                            context.workflow.artifacts.write_json(f"operations/{commit_id}.json", metadata)
                        else:
                            context.workflow.engine.checkpoint_after(context.workflow.snapshot, commit_id, reconciled)
                    elif not self.worktrees.reconciled_task_commit(task.id, self._operation_metadata(commit_artifact)):
                        context.workflow.pause("ambiguous_task_commit")
                        return False
                    integration_id = context.workflow.operation_id("task-integration", task.id)
                    integration_artifact = context.workflow.artifacts.root / f"operations/{integration_id}.json"
                    if not context.workflow.confirmed(integration_id):
                        if self.worktrees.reconciled_task_integration(task.id, self._operation_metadata(integration_artifact)):
                            context.workflow.engine.checkpoint_after(context.workflow.snapshot, integration_id, f"task:{task.id}")
                        else:
                            context.workflow.artifacts.write_json(f"operations/{integration_id}.json", self.worktrees.prepare_task_integration(task.id))
                            context.workflow.effect("task-integration", (task.id,), lambda task=task: self.worktrees.integrate_task(task.id))
                            metadata = self._operation_metadata(integration_artifact) or {}
                            metadata.update(self.worktrees.integration_metadata())
                            context.workflow.artifacts.write_json(f"operations/{integration_id}.json", metadata)
                    elif not self.worktrees.reconciled_task_integration(task.id, self._operation_metadata(integration_artifact)):
                        context.workflow.pause("ambiguous_task_integration")
                        return False
                except InfrastructureError:
                    context.correction_context = self._correction_evidence(context, "integration_conflict")
                    context.workflow.artifacts.write_text("correction.md", context.correction_context)
                    return self._return_to_implementation(context, "integration_conflict", f"conflict:{task.id}")
            snapshot = context.workflow.snapshot.model_copy(update={
                "completed_task_ids": [*context.workflow.snapshot.completed_task_ids, *(task.id for task in wave if task.id not in context.workflow.snapshot.completed_task_ids)],
                "completed_waves": wave_index + 1,
            })
            context.workflow.replace_snapshot(snapshot, event_type="wave-integrated")
        context.workflow.apply(AllowedEvent.TASKS_READY, result="tasks_integrated")
        return await self.run_full_tests(context)

    async def run_full_tests(self, context: RunContext) -> bool:
        self._require_state(context, RunState.TESTING)
        plan = context.plan or self._load_plan(context.workflow.artifacts)
        cwd = context.integration or self.repository
        policy = await self._external_retry(
            context, "sandbox:test:full",
            lambda: asyncio.to_thread(self._prepare_policy, AgentRole.TEST_RUNNER, cwd, context.config),
        )
        runner = DeterministicTestRunner(artifact_store=context.workflow.artifacts, sandbox=self.sandbox, policy_path=policy)
        results = [
            await self._test_attempt_async(context, runner, command, cwd, "full", str(context.workflow.snapshot.attempt), str(index))
            for index, command in enumerate(self._commands(context))
        ]
        if all(attempt.result.succeeded for attempt in results):
            if context.workflow.snapshot.correction_stage is not None:
                context.workflow.replace_snapshot(context.workflow.snapshot.model_copy(update={"correction_stage": None}), event_type="correction-stage-completed")
            context.workflow.apply(AllowedEvent.TESTS_PASSED, result="full_suite_passed")
            return True
        context.correction_context = self._correction_evidence(context, "full_suite_failed")
        context.workflow.artifacts.write_text("correction.md", context.correction_context)
        return self._return_to_implementation(context, "full_suite_failed", context.workflow.snapshot.correction_stage or "full")

    async def review(self, context: RunContext) -> bool:
        self._require_state(context, RunState.REVIEWING)
        current = context.workflow.snapshot.review_round or 1
        agent_config = context.config.agents[AgentRole.REVIEWER]
        integration = context.integration or self.repository
        diff = self._git_diff(integration, context.workflow.snapshot.metadata.commit_base)
        plan_text = (context.workflow.artifacts.root / "plan.md").read_text(encoding="utf-8")
        context_text = (context.workflow.artifacts.root / "context.md").read_text(encoding="utf-8")
        logs = "\n".join(path.read_text(encoding="utf-8", errors="replace")[-4000:] for path in sorted((context.workflow.artifacts.root / "tests").glob("*.log"))[-3:])
        request_text = f"Approved plan:\n{plan_text}\nContext:\n{context_text}\nTest results/logs:\n{logs}\nPrior findings:\n{context.correction_context}\nDiff:\n{diff}"
        policy = await self._external_retry(
            context, f"sandbox:reviewer:{current}",
            lambda: asyncio.to_thread(self._prepare_policy, AgentRole.REVIEWER, integration, context.config),
        )
        output = await self._harness_invocation(
            context, "reviewer", (str(current),), f"harness/reviewer-{current}.json", ReviewerOutput,
            lambda: ReviewerAgent(
                self._harness(agent_config.harness), self._prompt_provider(context.config),
                severity_descriptions=context.config.review.severities,
                structured_output_retries=context.config.workflow.structured_output_retries,
            ).review(self._request(AgentRole.REVIEWER, agent_config, integration, request_text, policy)),
            {"prompt": request_text, "diff": diff, "plan": self._plan_markdown(context.plan or self._load_plan(context.workflow.artifacts))},
        )
        context.workflow.artifacts.write_review(str(current), output.model_dump(mode="json"))
        severities = [finding.severity for finding in output.findings]
        blocking_severities = blocking_findings(current, severities, context.config.review.blocking)
        blocking = [finding for finding in output.findings if finding.severity in set(blocking_severities)]
        context.correction_context = "Blocking review findings:\n" + "\n".join(
            f"- {finding.id} ({finding.severity}): {finding.evidence}; fix: {finding.suggested_fix}" for finding in blocking
        )
        context.workflow.artifacts.write_text("correction.md", context.correction_context if blocking else "")
        snapshot = context.workflow.snapshot.model_copy(update={"review_round": current})
        context.workflow.replace_snapshot(snapshot, event_type="review-completed")
        if escalates_to_human(current, severities, context.config.review.escalate):
            context.workflow.pause("review_round_4_high")
            return False
        if blocking:
            if not self._reserve_implementation_retry(context, f"review:{current}"):
                return False
            context.workflow.replace_snapshot(
                context.workflow.snapshot.model_copy(update={"review_round": min(current + 1, 4)}),
                event_type="review-next-round",
            )
            context.workflow.apply(AllowedEvent.REVIEW_BLOCKING, result=f"review_round_{current}_blocking")
            return False
        context.correction_context = ""
        context.workflow.apply(AllowedEvent.REVIEW_ACCEPTED, result=f"review_round_{current}_accepted")
        return True

    def final_decision(self, context: RunContext, decision: str, details: str = "") -> RunState:
        self._require_state(context, RunState.FINAL_APPROVAL)
        decision = decision.lower()
        if decision == "abort":
            self.abort(context)
        elif decision == "adjust":
            context.workflow.artifacts.write_text("final-adjustment.md", details)
            context.correction_context = f"Human final adjustment:\n{details}"
            context.workflow.artifacts.write_text("correction.md", context.correction_context)
            context.workflow.apply(AllowedEvent.ADJUST, result="final_adjustment")
            context.workflow.replace_snapshot(context.workflow.snapshot.model_copy(update={"review_round": None, "correction_stage": "final_adjustment"}), event_type="review_reset")
        elif decision == "replan":
            context.workflow.apply(AllowedEvent.REPLAN, result="human_replan")
            context.workflow.apply(AllowedEvent.FEEDBACK, result="return_to_interview")
        elif decision == "approve":
            context.workflow.apply(AllowedEvent.FINAL_APPROVE, result="merge_approved")
            self._merge(context)
        else:
            raise DomainError("Unknown final decision.", "Choose approve, adjust, replan, or abort.")
        return context.workflow.snapshot.state

    def resume(self, run_id: str | None = None) -> RunContext:
        artifact_root = self.repository / ".stms" / "estado"
        candidates = sorted(artifact_root.glob("*/checkpoint.sqlite"), key=lambda item: item.stat().st_mtime, reverse=True) if artifact_root.exists() else []
        if run_id:
            candidates = [artifact_root / run_id / "checkpoint.sqlite"]
        for database in candidates:
            if not database.exists():
                continue
            store = SQLiteCheckpointStore(database)
            run = run_id or database.parent.name
            snapshot = store.latest_snapshot(run)
            if snapshot is None or snapshot.state in {RunState.COMPLETED, RunState.FAILED}:
                continue
            preflight = PreflightService(self.repository, self.harnesses, self.sandbox).validate()
            prompts = self._prompt_provider(preflight.config)
            store.verify_compatibility(snapshot, config_digest=preflight.config.digest(), workflow_version="1", prompt_digest=prompt_digest(prompts), adapter_versions=preflight.adapter_versions)
            self._control_store().acquire_lock(self.repository, snapshot.metadata.run_id)
            artifacts = LocalArtifactStore(self.repository, snapshot.metadata.run_id)
            workflow = RunWorkflow(LocalWorkflowEngine(store), artifacts, snapshot, event_sink=self.event_sink, event_renderer=self.event_renderer)
            workflow.resume()
            correction = (artifacts.root / "correction.md")
            integration = None
            if workflow.snapshot.completed_waves:
                integration = self.worktrees.create_integration(snapshot.metadata.run_id, snapshot.metadata.commit_base)
            return RunContext(workflow, preflight.config, self.repository, self._load_plan(artifacts, optional=True), (artifacts.root / "context.md").read_text(encoding="utf-8") if (artifacts.root / "context.md").exists() else "", correction.read_text(encoding="utf-8") if correction.exists() else "", integration)
        raise InfrastructureError("No resumable STMS run was found.", "Start a new run or provide the ID of an existing paused run.")

    async def _implement_wave(self, context: RunContext, tasks: list[PlanTask], task_paths: Mapping[str, Path]) -> dict[str, bool]:
        semaphore = asyncio.Semaphore(context.config.workflow.max_parallel_tasks)
        agent_config = context.config.agents[AgentRole.IMPLEMENTER]
        plan = context.plan
        assert plan is not None

        async def run(task: PlanTask) -> tuple[str, bool]:
            async with semaphore:
                path = task_paths[task.id]
                context.workflow.observe("task-started", task_id=task.id, result=str(path))
                for approved in plan.untracked_files:
                    await self._external_retry(
                        context, f"copy-untracked:{task.id}:{approved.source}",
                        lambda approved=approved: asyncio.to_thread(context.workflow.artifacts.copy_approved_untracked, approved, path),
                    )
                before_files = await asyncio.to_thread(self._worktree_fingerprints, path)
                policy = await self._external_retry(
                    context, f"sandbox:implementer:{task.id}",
                    lambda: asyncio.to_thread(self._prepare_policy, AgentRole.IMPLEMENTER, path, context.config),
                )
                prompt = f"Implement task {task.id}.\n{context.correction_context}"
                response = await self._harness_invocation(
                    context, "implementer", (str(context.workflow.snapshot.attempt), task.id), f"harness/implementer-{context.workflow.snapshot.attempt}-{task.id}.json", ImplementerOutput,
                    lambda: ImplementerAgent(
                        self._harness(agent_config.harness), self._prompt_provider(context.config),
                        structured_output_retries=context.config.workflow.structured_output_retries,
                    ).implement(self._request(AgentRole.IMPLEMENTER, agent_config, path, prompt, policy), task, plan, context.context_markdown),
                    {"prompt": prompt, "task": task.model_dump(mode="json"), "context": context.context_markdown},
                )
                if not await asyncio.to_thread(self._valid_implementation_report, task, response, path, before_files):
                    return task.id, False
                if response.requires_human_gate:
                    return task.id, False
                commands = [self._commands(context)[index] for index in task.focused_test_commands if index < len(self._commands(context))] or self._commands(context)
                test_policy = await self._external_retry(
                    context, f"sandbox:test:{task.id}",
                    lambda: asyncio.to_thread(self._prepare_policy, AgentRole.TEST_RUNNER, path, context.config),
                )
                runner = DeterministicTestRunner(artifact_store=context.workflow.artifacts, sandbox=self.sandbox, policy_path=test_policy)
                attempts = []
                for index, command in enumerate(commands):
                    attempts.append(await self._test_attempt_async(context, runner, command, path, "focused", str(context.workflow.snapshot.attempt), task.id, str(index)))
                succeeded = all(attempt.result.succeeded for attempt in attempts)
                context.workflow.observe("task-tests-completed", task_id=task.id, result="passed" if succeeded else "failed")
                return task.id, succeeded

        completed = await asyncio.gather(*(run(task) for task in tasks))
        result = dict(completed)
        if not all(result.values()):
            failed_task = next(task.id for task in tasks if not result[task.id])
            context.correction_context = self._focused_correction_evidence(context, failed_task, task_paths[failed_task])
            context.workflow.artifacts.write_text("correction.md", context.correction_context)
            self._return_to_implementation(context, "focused_test_or_human_gate_failed", f"focused:{failed_task}")
        return result

    def _merge(self, context: RunContext) -> None:
        metadata = context.workflow.snapshot.metadata
        summary = (context.plan.objective if context.plan else "approved change")[:72]
        try:
            operation_id = context.workflow.operation_id("squash-merge", metadata.branch_base, metadata.commit_base)
            if not context.workflow.confirmed(operation_id):
                pending = context.workflow.engine.store.operation(metadata.run_id, operation_id)
                if pending is not None and pending.status.value in {"pending", "started"}:
                    reconciliation = self.worktrees.reconcile_pending_squash(branch_base=metadata.branch_base, commit_base=metadata.commit_base, run_id=metadata.run_id)
                    if reconciliation == "ambiguous":
                        context.workflow.pause("ambiguous_squash_state")
                        raise InfrastructureError("Interrupted squash merge has changes not attributable to STMS.", "Resolve the original branch working tree manually; STMS will not discard possible user changes.")
                reconciled = self.worktrees.reconciled_squash(branch_base=metadata.branch_base, summary=summary, run_id=metadata.run_id)
                if reconciled is None:
                    context.workflow.effect("squash-merge", (metadata.branch_base, metadata.commit_base), lambda: self.worktrees.squash_merge(branch_base=metadata.branch_base, commit_base=metadata.commit_base, summary=summary, run_id=metadata.run_id))
                else:
                    context.workflow.engine.checkpoint_after(context.workflow.snapshot, operation_id, reconciled)
        except InfrastructureError as error:
            if "Base branch changed" in str(error):
                context.workflow.apply(AllowedEvent.BASE_CHANGED, result="base_changed")
                context.workflow.replace_snapshot(context.workflow.snapshot.model_copy(update={"pause_reason": "base_changed", "resume_state": None}), event_type="base-change-paused")
                return
            raise
        context.workflow.effect("cleanup", (metadata.run_id,), self.worktrees.cleanup_success)
        context.workflow.apply(AllowedEvent.MERGE_SUCCEEDED, result="squash_merged")
        self._control_store().release_lock(self.repository, metadata.run_id)

    def _return_to_implementation(self, context: RunContext, result: str, stage: str) -> bool:
        if not self._reserve_implementation_retry(context, stage):
            return False
        if context.workflow.snapshot.state == RunState.TESTING:
            context.workflow.apply(AllowedEvent.TESTS_FAILED, result=result)
        return False

    def _reserve_implementation_retry(self, context: RunContext, stage: str) -> bool:
        used = context.workflow.snapshot.implementation_attempts.get(stage, 0)
        if retry_exhausted(used, context.config.workflow.implementation_retries):
            context.workflow.pause(f"implementation_retries_exhausted:{stage}")
            return False
        counters = dict(context.workflow.snapshot.implementation_attempts)
        counters[stage] = used + 1
        snapshot = context.workflow.snapshot.model_copy(update={
            "attempt": context.workflow.snapshot.attempt + 1,
            "implementation_attempts": counters,
            "correction_stage": stage,
        })
        context.workflow.replace_snapshot(snapshot, event_type="implementation_retry")
        return True

    async def _correct_integration(self, context: RunContext) -> bool:
        """Use a fresh implementer session in the integration worktree for fixes."""
        assert context.integration is not None
        plan = context.plan or self._load_plan(context.workflow.artifacts)
        assert plan is not None
        config = context.config.agents[AgentRole.IMPLEMENTER]
        policy = await self._external_retry(
            context, "sandbox:corrector",
            lambda: asyncio.to_thread(self._prepare_policy, AgentRole.IMPLEMENTER, context.integration, context.config),
        )
        operation_id = context.workflow.operation_id("harness-corrector", str(context.workflow.snapshot.attempt))
        artifact = f"corrections/{operation_id}.json"
        report = await self._harness_invocation(
            context, "corrector", (str(context.workflow.snapshot.attempt),), artifact, ImplementerOutput,
            lambda: ImplementerAgent(
                self._harness(config.harness), self._prompt_provider(context.config),
                structured_output_retries=context.config.workflow.structured_output_retries,
            ).implement(self._request(AgentRole.IMPLEMENTER, config, context.integration, context.correction_context, policy), plan.tasks[0], plan, context.context_markdown),
            {"prompt": context.correction_context, "diff": self._git_diff(context.integration, context.workflow.snapshot.metadata.commit_base)},
        )
        if report.requires_human_gate:
            context.workflow.pause("correction_requires_human_gate")
            return False
        context.workflow.effect("integration-correction-commit", (str(context.workflow.snapshot.attempt),), lambda: self.worktrees.commit_integration(f"stms correction {context.workflow.snapshot.attempt}"))
        context.workflow.apply(AllowedEvent.TASKS_READY, result="integration_correction_ready")
        return await self.run_full_tests(context)

    def _correction_evidence(self, context: RunContext, reason: str) -> str:
        logs = sorted(context.workflow.artifacts.root.glob("tests/*.log"))
        log_references = "\n".join(f"- {path.relative_to(context.workflow.artifacts.root)}" for path in logs[-5:])
        integration = context.integration or self.repository
        return f"Correction required: {reason}\nTest logs:\n{log_references}\nIntegrated diff:\n{self._git_diff(integration, context.workflow.snapshot.metadata.commit_base)}"

    def _focused_correction_evidence(self, context: RunContext, task_id: str, worktree: Path) -> str:
        logs = sorted(context.workflow.artifacts.root.glob("tests/*.log"))
        log_references = "\n".join(f"- {path.relative_to(context.workflow.artifacts.root)}" for path in logs[-5:])
        diff = subprocess.run(
            ["git", "diff", context.workflow.snapshot.metadata.commit_base],
            cwd=worktree, text=True, capture_output=True, check=True,
        ).stdout
        return f"Correction required: focused tests failed for {task_id}\nTest logs:\n{log_references}\nTask diff:\n{diff}"

    @staticmethod
    def _valid_implementation_report(task: PlanTask, report: ImplementerOutput, worktree: Path, before_files: dict[str, str]) -> bool:
        """Require the approved essential tests before deterministic execution."""
        reported = [*report.modified_files, *report.tests_created]
        for relative in reported:
            candidate = (worktree / relative).resolve()
            if worktree.resolve() not in candidate.parents or not candidate.is_file():
                return False
        if task.essential_tests and not report.tests_created:
            return False
        created = set(report.tests_created)
        after_files = Orchestrator._worktree_fingerprints(worktree)
        if any(after_files.get(path) == before_files.get(path) for path in created):
            return False
        for expected in task.essential_tests:
            # Paths and globs are contracts; prose-only expectations still require
            # at least one concrete test artifact above.
            if "/" in expected or "*" in expected:
                import fnmatch
                if not any(fnmatch.fnmatch(item, expected) for item in created):
                    return False
        return True

    @staticmethod
    def _worktree_fingerprints(worktree: Path) -> dict[str, str]:
        result: dict[str, str] = {}
        for file in worktree.rglob("*"):
            if not file.is_file() or ".git" in file.parts:
                continue
            relative = str(file.relative_to(worktree))
            result[relative] = sha256(file.read_bytes()).hexdigest()
        return result

    def _request(self, role: AgentRole, config: AgentConfig, cwd: Path, prompt: str, policy: Path | None = None) -> HarnessRequest:
        tools = {"sandbox_policy": str(policy)} if policy else {}
        return HarnessRequest(role=role, cwd=str(cwd.resolve()), model=config.model, effort=config.effort, timeout_seconds=config.timeout_seconds, max_turns=config.max_turns, prompt=prompt, tools=tools)

    def _prepare_policy(self, role: AgentRole, worktree: Path, config: RuntimeConfig) -> Path:
        """Generate the least-privilege policy before every external session/run."""
        try:
            return self.sandbox.prepare(
                role.value, self.repository, worktree,
                planner_web=config.security.planner_web if role is AgentRole.PLANNER else False,
                test_network=config.security.test_network if role is AgentRole.TEST_RUNNER else False,
            )
        except TypeError as error:
            # Legacy adapters cannot prove they received the frozen network policy.
            raise InfrastructureError("Sandbox adapter cannot apply frozen network policy.", "Use a sandbox adapter that supports planner_web and test_network controls.") from error

    def _commands(self, context: RunContext) -> list[TestCommand]:
        plan = context.plan or self._load_plan(context.workflow.artifacts)
        assert plan is not None
        return plan.test_commands

    @staticmethod
    def _operation_metadata(path: Path) -> dict[str, str] | None:
        if not path.exists():
            return None
        import json
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) and all(isinstance(item, str) for item in value.values()) else None

    @staticmethod
    def _planning_history(artifacts: LocalArtifactStore) -> list[dict[str, object]]:
        path = artifacts.root / "planning-history.json"
        if not path.exists():
            return []
        import json
        content = json.loads(path.read_text(encoding="utf-8"))
        return content if isinstance(content, list) else []

    def _test_attempt(self, context: RunContext, runner: DeterministicTestRunner, command: TestCommand, cwd: Path, scope: str, *parts: str):
        """Persist a deterministic result so resume does not rerun confirmed tests."""
        operation_id = context.workflow.operation_id("test", scope, *parts)
        artifact = f"tests/{operation_id}.json"
        existing = context.workflow.artifacts.root / artifact
        if context.workflow.confirmed(operation_id) and existing.exists():
            from stms.domain.models import TestAttempt
            return TestAttempt.model_validate_json(existing.read_text(encoding="utf-8"))
        context.workflow.engine.checkpoint_before(context.workflow.snapshot, operation_id, "test")
        # The worktree/integration tree itself is the process boundary: worktrees
        # are created outside the repository (see GitWorktreeManager), so bounding
        # by the repository here would incorrectly reject every legitimate run.
        attempt = runner.run_attempt(command, cwd)
        context.workflow.artifacts.write_json(artifact, attempt.model_dump(mode="json"))
        context.workflow.engine.checkpoint_after(context.workflow.snapshot, operation_id, artifact)
        context.workflow.observe("test-completed", result="passed" if attempt.result.succeeded else "failed", duration_seconds=attempt.result.duration_seconds)
        return attempt

    async def _test_attempt_async(self, context: RunContext, runner: DeterministicTestRunner, command: TestCommand, cwd: Path, scope: str, *parts: str):
        operation_id = context.workflow.operation_id("test", scope, *parts)
        artifact = f"tests/{operation_id}.json"
        existing = context.workflow.artifacts.root / artifact
        if context.workflow.confirmed(operation_id) and existing.exists():
            from stms.domain.models import TestAttempt
            return TestAttempt.model_validate_json(existing.read_text(encoding="utf-8"))
        context.workflow.engine.checkpoint_before(context.workflow.snapshot, operation_id, "test")
        attempt = await self._external_retry(
            context, f"test-process:{scope}:{':'.join(parts)}",
            lambda: asyncio.to_thread(runner.run_attempt, command, cwd),
        )
        context.workflow.artifacts.write_json(artifact, attempt.model_dump(mode="json"))
        context.workflow.engine.checkpoint_after(context.workflow.snapshot, operation_id, artifact)
        context.workflow.observe("test-completed", result="passed" if attempt.result.succeeded else "failed", duration_seconds=attempt.result.duration_seconds)
        return attempt

    Output = TypeVar("Output", bound=StrictModel)

    async def _harness_invocation(self, context: RunContext, kind: str, parts: tuple[str, ...], artifact: str, output_type: type[Output], call: Callable[[], Awaitable[Output]], evidence: dict[str, object]) -> Output:
        """Checkpoint a provider call and replay only its persisted typed output."""
        operation_id = context.workflow.operation_id(f"harness-{kind}", *parts)
        path = context.workflow.artifacts.root / artifact
        operation = context.workflow.engine.store.operation(context.workflow.snapshot.metadata.run_id, operation_id)
        if operation is not None:
            if operation.status.value == "confirmed" and path.exists():
                import json
                return output_type.model_validate(json.loads(path.read_text(encoding="utf-8"))["output"])
            context.workflow.pause("ambiguous_harness_invocation")
            raise InfrastructureError("Harness invocation is pending or has no persisted typed output.", "Inspect the provider session/artifacts and resolve the run manually; STMS will not duplicate the request.")
        context.workflow.engine.checkpoint_before(context.workflow.snapshot, operation_id, f"harness-{kind}")
        try:
            output = await self._external_retry(context, f"harness:{kind}", call)
        except StructuredOutputError:
            context.workflow.pause("structured_output_retries_exhausted")
            raise
        context.workflow.artifacts.write_json(artifact, {"operation_id": operation_id, "session_id": None, "evidence": evidence, "output": output.model_dump(mode="json")})
        context.workflow.engine.checkpoint_after(context.workflow.snapshot, operation_id, artifact)
        return output

    async def _external_retry(self, context: RunContext, stage: str, call: Callable[[], Awaitable[Output]]) -> Output:
        retries = context.config.workflow.infrastructure_retries
        attempt = 0
        while True:
            try:
                return await call()
            except StructuredOutputError:
                raise
            except (InfrastructureError, OSError):
                if retry_exhausted(attempt, retries):
                    context.workflow.pause(f"infrastructure_retries_exhausted:{stage}")
                    raise
                attempt += 1
                context.workflow.observe("infrastructure-retry", result=stage)
                await self._sleep(self._infrastructure_backoff_seconds * attempt)

    def _harness(self, name: str) -> AgentHarness:
        return self.harnesses[name]

    def _control_store(self) -> SQLiteCheckpointStore:
        return SQLiteCheckpointStore(self.repository / ".stms" / "control.sqlite")

    def _prompt_provider(self, config: RuntimeConfig) -> PromptProvider:
        defaults = {
            AgentRole.PLANNER.value: PLANNER_PROMPT,
            AgentRole.IMPLEMENTER.value: IMPLEMENTER_PROMPT,
            AgentRole.REVIEWER.value: REVIEWER_PROMPT,
        }
        paths = {role.value: config.agents[role].prompt for role in (AgentRole.PLANNER, AgentRole.IMPLEMENTER, AgentRole.REVIEWER)}
        return FilePromptProvider(self.repository, paths, defaults)

    @staticmethod
    def _require_state(context: RunContext, state: RunState) -> None:
        if context.workflow.snapshot.state != state:
            raise DomainError(f"Operation requires {state}, found {context.workflow.snapshot.state}.", "Resume the workflow at its current human gate or allowed event.")

    @staticmethod
    def _load_plan(artifacts: LocalArtifactStore, optional: bool = False) -> ApprovedPlan | None:
        path = artifacts.root / "approved-plan.json"
        if not path.exists() and optional:
            return None
        if not path.exists():
            raise InfrastructureError("Approved plan artifact is missing.", "Resume from planning or restore the run artifacts.")
        return ApprovedPlan.model_validate_json(path.read_text(encoding="utf-8"))

    @staticmethod
    def _context_markdown(notes: list[str], sources: list[object]) -> str:
        lines = ["# Context", "", "## Notes", *(f"- {note}" for note in notes)]
        if sources:
            lines.extend(["", "## Sources", *(f"- {source.conclusion}: {source.url}" for source in sources)])
        return "\n".join(lines) + "\n"

    @staticmethod
    def _plan_markdown(plan: ApprovedPlan) -> str:
        lines = [f"# {plan.objective}", "", "## Expected outcome", plan.expected_outcome, "", "## Scope", *(f"- {item}" for item in plan.scope), "", "## Out of scope", *(f"- {item}" for item in plan.out_of_scope), "", "## Human decisions", *(f"- {item}" for item in plan.human_decisions), "", "## Assumptions", *(f"- {item}" for item in plan.assumptions), "", "## Risks and dependencies", *(f"- {item}" for item in plan.risks), "", "## Proposed test commands"]
        for command in plan.test_commands:
            lines.append(f"- `{' '.join(command.argv)}` (cwd: {command.cwd}, timeout: {command.timeout_seconds}s)")
        lines.extend(["", "## Tasks"])
        for task in plan.tasks:
            deps = ", ".join(dep.task_id for dep in task.dependencies) or "none"
            paths = ", ".join(task.affected_paths) or "not yet known"
            criteria = "; ".join(item.description for item in task.acceptance_criteria)
            lines.extend([f"### {task.id}: {task.title}", task.description, f"Execution: {task.execution_mode}", f"Dependencies: {deps}", f"Likely affected paths: {paths}", f"Acceptance criteria: {criteria}", f"Essential tests: {', '.join(task.essential_tests)}", ""])
        return "\n".join(lines)

    @staticmethod
    def _git_head(path: Path) -> str:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=path, text=True, capture_output=True, check=True).stdout.strip()

    @staticmethod
    def _git_diff(path: Path, base: str) -> str:
        return subprocess.run(["git", "diff", f"{base}..HEAD"], cwd=path, text=True, capture_output=True, check=True).stdout
