"""Semantic planner role; approval and persistence remain application concerns."""
from __future__ import annotations

from stms.domain.models import AgentRole, HarnessRequest, PlannerOutput
from stms.domain.ports import AgentHarness, PromptProvider

from ._structured import request_structured
from .prompts import resolve_prompt

DEFAULT_PROMPT = """You are the STMS planner. Inspect only the supplied repository context.
Ask at most three related questions when material decisions remain. When ready, return
only the PlannerOutput JSON schema. Propose an observable, bounded plan; never approve
the plan yourself. Include only useful context notes and web URLs actually consulted."""


class PlannerAgent:
    def __init__(self, harness: AgentHarness, prompt_provider: PromptProvider | None = None, *, structured_output_retries: int = 2) -> None:
        self._harness = harness
        self._prompt = resolve_prompt(AgentRole.PLANNER.value, DEFAULT_PROMPT, prompt_provider)
        self._structured_output_retries = structured_output_retries

    async def respond(self, request: HarnessRequest) -> PlannerOutput:
        enriched = request.model_copy(update={"prompt": f"{self._prompt}\n\nRequest:\n{request.prompt}"})
        return await request_structured(self._harness, enriched, role=AgentRole.PLANNER, output_type=PlannerOutput, structured_output_retries=self._structured_output_retries)
