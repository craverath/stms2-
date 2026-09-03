"""Semantic reviewer role; review policy and state changes remain deterministic."""
from __future__ import annotations

import json
from collections.abc import Mapping

from stms.domain.models import AgentRole, HarnessRequest, ReviewerOutput
from stms.domain.ports import AgentHarness, PromptProvider

from ._structured import request_structured
from .prompts import resolve_prompt

DEFAULT_PROMPT = """You are the STMS reviewer. Inspect the provided diff and approved
criteria read-only. Return only ReviewerOutput JSON. Each finding needs a stable ID,
severity, concrete evidence, optional location, and a suggested fix. Do not apply
changes, execute tests, or decide whether the workflow is accepted."""


class ReviewerAgent:
    def __init__(self, harness: AgentHarness, prompt_provider: PromptProvider | None = None, *, severity_descriptions: Mapping[object, str] | None = None, structured_output_retries: int = 2) -> None:
        self._harness = harness
        self._prompt = resolve_prompt(AgentRole.REVIEWER.value, DEFAULT_PROMPT, prompt_provider)
        self._severity_descriptions = {getattr(key, "value", str(key)): value for key, value in (severity_descriptions or {}).items()}
        self._structured_output_retries = structured_output_retries

    async def review(self, request: HarnessRequest) -> ReviewerOutput:
        severities = json.dumps(self._severity_descriptions, sort_keys=True)
        enriched = request.model_copy(update={"prompt": f"{self._prompt}\n\nConfigured severity definitions:\n{severities}\n\nReview request:\n{request.prompt}"})
        return await request_structured(self._harness, enriched, role=AgentRole.REVIEWER, output_type=ReviewerOutput, structured_output_retries=self._structured_output_retries)
