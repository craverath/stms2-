"""Reusable async contract checks for every AgentHarness implementation."""
from __future__ import annotations

from collections.abc import Callable

from stms.adapters.harnesses.base import ProviderResponse
from stms.domain.models import AgentRole, HarnessRequest

from tests.fixtures.harness import ScriptedTransport


def request(*, session_id: str | None = None) -> HarnessRequest:
    return HarnessRequest(
        role=AgentRole.PLANNER, cwd="/tmp/worktree", model="model", effort="high",
        timeout_seconds=5, max_turns=4, prompt="return JSON", tools={"read_only": True}, session_id=session_id,
    )


async def assert_harness_contract(factory: Callable[[ScriptedTransport], object]) -> None:
    transport = ScriptedTransport([
        ProviderResponse("session-1", {"status": "needs_input", "questions": ["What?" ]}, {"input_tokens": 3}, ({"type": "session_started"},)),
        ProviderResponse("session-1", {"status": "needs_input", "questions": ["Why?" ]}),
    ])
    harness = factory(transport)
    first = await harness.start(request())  # type: ignore[attr-defined]
    assert first.session_id == "session-1" and first.usage == {"input_tokens": 3}
    events = [event async for event in harness.stream(first.session_id)]  # type: ignore[attr-defined]
    assert [event.event_type for event in events] == ["session_started"]
    second = await harness.send(request(session_id=first.session_id))  # type: ignore[attr-defined]
    assert second.session_id == first.session_id
    await harness.cancel(first.session_id)  # type: ignore[attr-defined]
    assert transport.cancelled == ["session-1"]
    assert all(capability.supported for capability in harness.capabilities())  # type: ignore[attr-defined]
