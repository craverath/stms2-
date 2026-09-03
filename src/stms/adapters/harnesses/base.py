"""Reusable provider boundary for local coding-agent harnesses.

Vendor SDK objects never leave this module family.  Concrete adapters receive a
small injectable transport, which keeps conformance tests deterministic and avoids
loading optional SDKs during normal imports.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from stms.domain.errors import InfrastructureError, SessionLostError
from stms.domain.events import NormalizedHarnessEvent
from stms.domain.models import Capability, HarnessRequest, HarnessResult


@dataclass(frozen=True)
class ProviderResponse:
    session_id: str
    output: Mapping[str, Any]
    usage: Mapping[str, int | float] | None = None
    events: tuple[Mapping[str, Any] | NormalizedHarnessEvent, ...] = ()


class HarnessTransport(Protocol):
    async def start(self, request: HarnessRequest) -> ProviderResponse: ...
    async def resume(self, request: HarnessRequest) -> ProviderResponse: ...
    async def cancel(self, session_id: str) -> None: ...
    async def stream(self, session_id: str) -> AsyncIterator[Mapping[str, Any] | NormalizedHarnessEvent]: ...
    def capabilities(self) -> list[Capability]: ...


class BaseHarness:
    """Apply timeout, session-loss recovery, event normalization and cancellation."""

    def __init__(self, transport: HarnessTransport, *, name: str) -> None:
        self._transport = transport
        self._name = name
        self._events: dict[str, list[NormalizedHarnessEvent]] = defaultdict(list)

    async def start(self, request: HarnessRequest) -> HarnessResult:
        response = await self._bounded(self._transport.start(request), request.timeout_seconds)
        return self._record(response)

    async def resume(self, request: HarnessRequest) -> HarnessResult:
        if not request.session_id:
            return await self.start(request)
        try:
            response = await self._bounded(self._transport.resume(request), request.timeout_seconds)
        except SessionLostError:
            # The caller's prompt is hydrated from persisted plan/context, so a fresh
            # provider session is safe and does not rely on opaque provider history.
            response = await self._bounded(
                self._transport.start(request.model_copy(update={"session_id": None})), request.timeout_seconds
            )
        return self._record(response)

    async def send(self, request: HarnessRequest) -> HarnessResult:
        """Send a subsequent turn; a missing session deliberately starts fresh."""
        return await self.resume(request)

    async def stream(self, session_id: str) -> AsyncIterator[NormalizedHarnessEvent]:
        queued = self._events.pop(session_id, [])
        for event in queued:
            yield event
            if event.event_type in {"run_completed", "run_failed"}:
                return
        try:
            async for event in self._transport.stream(session_id):
                normalized = self._normalize_event(event, session_id)
                yield normalized
                if normalized.event_type in {"run_completed", "run_failed"}:
                    return
        except SessionLostError:
            yield NormalizedHarnessEvent(event_type="run_failed", session_id=session_id, message="provider session lost")

    async def cancel(self, session_id: str) -> None:
        try:
            await self._transport.cancel(session_id)
        except Exception as error:
            raise InfrastructureError(f"{self._name} could not cancel the session.", "Retry cancellation or terminate the local harness process.") from error

    async def _bounded(self, operation: Any, timeout_seconds: int) -> ProviderResponse:
        try:
            return await asyncio.wait_for(operation, timeout=timeout_seconds)
        except asyncio.TimeoutError as error:
            raise InfrastructureError(f"{self._name} timed out.", "Increase the approved timeout or inspect the harness process.") from error
        except (InfrastructureError, SessionLostError):
            raise
        except Exception as error:
            raise InfrastructureError(f"{self._name} failed to run the request.", "Inspect the harness installation, authentication, and configuration.") from error

    def _record(self, response: ProviderResponse) -> HarnessResult:
        self._events[response.session_id].extend(self._normalize_event(item, response.session_id) for item in response.events)
        return HarnessResult(session_id=response.session_id, output=dict(response.output), usage=dict(response.usage or {}))

    @staticmethod
    def _normalize_event(event: Mapping[str, Any] | NormalizedHarnessEvent, session_id: str) -> NormalizedHarnessEvent:
        if isinstance(event, NormalizedHarnessEvent):
            return event.model_copy(update={"session_id": session_id})
        event_type = event.get("event_type") or event.get("type")
        if not isinstance(event_type, str):
            event_type = "message_delta"
        message = event.get("message") or event.get("text")
        usage = event.get("usage")
        return NormalizedHarnessEvent(
            event_type=event_type,
            session_id=session_id,
            message=message if isinstance(message, str) else None,
            usage=dict(usage) if isinstance(usage, Mapping) else {},
        )

    def capabilities(self) -> list[Capability]:
        return self._transport.capabilities()
