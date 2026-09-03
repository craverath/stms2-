"""Shared bounded Pydantic-output repair policy for semantic agents."""
from __future__ import annotations

from typing import TypeVar

from pydantic import ValidationError

from stms.domain.errors import StructuredOutputError
from stms.domain.models import AgentRole, HarnessRequest, StrictModel
from stms.domain.ports import AgentHarness

Output = TypeVar("Output", bound=StrictModel)
MAX_STRUCTURED_REPAIRS = 2


async def request_structured(
    harness: AgentHarness,
    request: HarnessRequest,
    *,
    role: AgentRole,
    output_type: type[Output],
) -> Output:
    """Return a typed result or fail closed after exactly two repair turns."""
    if request.role is not role:
        raise ValueError(f"Expected a {role.value} request, got {request.role.value}.")

    current = request
    for repair_attempt in range(MAX_STRUCTURED_REPAIRS + 1):
        result = await (harness.resume(current) if current.session_id else harness.start(current))
        try:
            return output_type.model_validate(result.output)
        except ValidationError:
            if repair_attempt == MAX_STRUCTURED_REPAIRS:
                raise StructuredOutputError(MAX_STRUCTURED_REPAIRS) from None
            current = current.model_copy(update={
                "session_id": result.session_id,
                "prompt": (
                    f"{request.prompt}\n\nYour previous response did not match the required JSON schema. "
                    "Return only a complete JSON object matching that schema; do not add prose."
                ),
            })
    raise AssertionError("bounded repair loop must return or raise")
