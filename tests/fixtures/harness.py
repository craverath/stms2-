"""Provider-boundary doubles shared by harness conformance tests."""
from __future__ import annotations

from collections import deque
from collections.abc import AsyncIterator, Iterable, Mapping
from typing import Any

from stms.adapters.harnesses.base import ProviderResponse
from stms.domain.events import NormalizedHarnessEvent
from stms.domain.models import Capability, HarnessRequest


class ScriptedTransport:
    def __init__(self, responses: Iterable[ProviderResponse | Exception]) -> None:
        self.responses = deque(responses)
        self.requests: list[HarnessRequest] = []
        self.cancelled: list[str] = []

    async def start(self, request: HarnessRequest) -> ProviderResponse:
        return self._next(request)

    async def resume(self, request: HarnessRequest) -> ProviderResponse:
        return self._next(request)

    def _next(self, request: HarnessRequest) -> ProviderResponse:
        self.requests.append(request)
        value = self.responses.popleft()
        if isinstance(value, Exception):
            raise value
        return value

    async def cancel(self, session_id: str) -> None:
        self.cancelled.append(session_id)

    async def stream(self, session_id: str) -> AsyncIterator[Mapping[str, Any] | NormalizedHarnessEvent]:
        if False:
            yield {"event_type": "unreachable"}

    def capabilities(self) -> list[Capability]:
        return [Capability(name=name, supported=True) for name in ("sessions", "streaming", "cancellation", "structured_output", "cwd", "model_effort", "tool_policy")]
