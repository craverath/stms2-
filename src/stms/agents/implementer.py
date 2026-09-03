"""Semantic implementer role; it cannot execute tests or mutate workflow policy."""
from __future__ import annotations

import json

from stms.domain.models import AgentRole, ApprovedPlan, HarnessRequest, ImplementerOutput, PlanTask
from stms.domain.ports import AgentHarness, PromptProvider

from ._structured import request_structured
from .prompts import resolve_prompt

DEFAULT_PROMPT = """You are the STMS implementer. Work only on the assigned task in the
approved frozen plan. Create the task's essential tests before implementation, then
implement only the requested behavior. Do not run tests, alter Git state, change the
plan, or broaden scope. Report a material discovery with requires_human_gate=true.
Return only the ImplementerOutput JSON schema."""


class ImplementerAgent:
    def __init__(self, harness: AgentHarness, prompt_provider: PromptProvider | None = None, *, structured_output_retries: int = 2) -> None:
        self._harness = harness
        self._prompt = resolve_prompt(AgentRole.IMPLEMENTER.value, DEFAULT_PROMPT, prompt_provider)
        self._structured_output_retries = structured_output_retries

    async def implement(self, request: HarnessRequest, task: PlanTask, plan: ApprovedPlan, context: str = "") -> ImplementerOutput:
        if task.id not in {candidate.id for candidate in plan.tasks}:
            raise ValueError("Assigned task is not part of the approved plan.")
        frozen = json.dumps(plan.model_dump(mode="json"), sort_keys=True)
        enriched = request.model_copy(update={"prompt": (
            f"{self._prompt}\n\nAssigned task:\n{task.model_dump_json()}\n\n"
            f"Approved frozen plan:\n{frozen}\n\nRelevant context:\n{context}\n\nRequest:\n{request.prompt}"
        )})
        return await request_structured(self._harness, enriched, role=AgentRole.IMPLEMENTER, output_type=ImplementerOutput, structured_output_retries=self._structured_output_retries)
