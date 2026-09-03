"""Scriptable offline harness used by unit, integration and conformance tests."""
from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from stms.domain.errors import InfrastructureError, SessionLostError
from stms.domain.events import NormalizedHarnessEvent
from stms.domain.models import Capability, HarnessRequest

from .base import BaseHarness, ProviderResponse


@dataclass(frozen=True)
class FakeResponse:
    output: Mapping[str, Any] = field(default_factory=dict)
    session_id: str | None = None
    usage: Mapping[str, int | float] = field(default_factory=dict)
    events: tuple[Mapping[str, Any] | NormalizedHarnessEvent, ...] = ()
    delay_seconds: float = 0


class FakeHarness(BaseHarness):
    """A deterministic harness script, including failures, loss, timeout and usage."""

    def __init__(self, script: Iterable[FakeResponse | Exception], *, capabilities: list[Capability] | None = None) -> None:
        self._script = deque(script)
        self.requests: list[HarnessRequest] = []
        self.cancelled_sessions: list[str] = []
        self._counter = 0
        self._capabilities = capabilities or _default_capabilities("fake")
        super().__init__(_FakeTransport(self), name="fake harness")

    async def _next(self, request: HarnessRequest) -> ProviderResponse:
        self.requests.append(request)
        if not self._script:
            raise InfrastructureError("Fake harness script is exhausted.", "Provide a scripted response for this harness call.")
        response = self._script.popleft()
        if isinstance(response, Exception):
            raise response
        if response.delay_seconds:
            await asyncio.sleep(response.delay_seconds)
        self._counter += 1
        return ProviderResponse(
            session_id=response.session_id or request.session_id or f"fake-{self._counter}",
            output=response.output,
            usage=response.usage,
            events=response.events,
        )

    def preflight(self, *, model: str, effort: str) -> dict[str, bool]:
        return {"authenticated": True, "model": True, "effort": True}



class _FakeTransport:
    def __init__(self, harness: FakeHarness) -> None:
        self._harness = harness

    async def start(self, request: HarnessRequest) -> ProviderResponse:
        return await self._harness._next(request)

    async def resume(self, request: HarnessRequest) -> ProviderResponse:
        return await self._harness._next(request)

    async def cancel(self, session_id: str) -> None:
        self._harness.cancelled_sessions.append(session_id)

    async def stream(self, session_id: str) -> AsyncIterator[Mapping[str, Any] | NormalizedHarnessEvent]:
        if False:
            yield {"event_type": "unreachable"}

    def capabilities(self) -> list[Capability]:
        return list(self._harness._capabilities)


def _default_capabilities(name: str) -> list[Capability]:
    return [
        Capability(name=name, supported=True), Capability(name="sessions", supported=True),
        Capability(name="streaming", supported=True), Capability(name="cancellation", supported=True),
        Capability(name="structured_output", supported=True), Capability(name="cwd", supported=True),
        Capability(name="model_effort", supported=True), Capability(name="tool_policy", supported=True),
    ]
