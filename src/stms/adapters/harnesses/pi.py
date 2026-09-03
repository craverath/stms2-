"""Experimental Pi harness over a local bidirectional JSONL RPC subprocess."""
from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Mapping
import importlib.util
import json
from pathlib import Path
import shutil
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from stms.domain.errors import InfrastructureError, StructuredOutputError
from stms.domain.events import NormalizedHarnessEvent
from stms.domain.models import Capability, HarnessRequest
from stms.deterministic.test_runner import CommandSandbox

from .base import BaseHarness, ProviderResponse
from .codex import _validate_request

MAX_PI_REPAIRS = 2


class PiHarness(BaseHarness):
    """Experimental adapter; it never claims native Pi sandbox or schema support."""

    def __init__(
        self,
        executable: str = "pi",
        args: tuple[str, ...] = ("--mode", "rpc"),
        *,
        process_factory: Callable[..., Any] | None = None,
        output_model: type[BaseModel] | None = None,
        sandbox: CommandSandbox | None = None,
    ) -> None:
        super().__init__(
            PiJsonlTransport(executable, args, process_factory=process_factory, output_model=output_model, sandbox=sandbox),
            name="Pi",
        )

    def preflight(self, *, model: str, effort: str) -> dict[str, bool]:
        probe = getattr(self._transport, "preflight", None)
        if not callable(probe):
            return {"authenticated": False, "model": False, "effort": False}
        return probe(model=model, effort=effort)


class PiJsonlTransport:
    """JSONL framing is deliberately owned here, not by semantic agents."""

    def __init__(
        self,
        executable: str,
        args: tuple[str, ...],
        *,
        process_factory: Callable[..., Any] | None,
        output_model: type[BaseModel] | None,
        sandbox: CommandSandbox | None,
    ) -> None:
        self._executable = executable
        self._args = args
        self._factory = process_factory
        self._output_model = output_model
        self._sandbox = sandbox
        self._rpc: _JsonlRpc | None = None
        self._events: dict[str, deque[Mapping[str, Any]]] = defaultdict(deque)

    async def start(self, request: HarnessRequest) -> ProviderResponse:
        _validate_request(request)
        return await self._run_prompt(request)

    async def resume(self, request: HarnessRequest) -> ProviderResponse:
        _validate_request(request)
        # Pi sessions are process-local in this adapter.  The persisted STMS prompt
        # is complete enough to hydrate a fresh Pi process when it was lost.
        return await self._run_prompt(request)

    async def cancel(self, session_id: str) -> None:
        try:
            await self._call("abort", {})
        finally:
            # Pi has no durable cancellation guarantee: kill the child to prevent a
            # cancelled run from continuing to mutate its worktree.
            if self._rpc is not None:
                await self._rpc.close()
                self._rpc = None

    async def stream(self, session_id: str) -> AsyncIterator[Mapping[str, Any]]:
        while self._events[session_id]:
            yield self._events[session_id].popleft()
        if self._rpc is None:
            return
        async for event in self._rpc.notifications():
            event_session = str(event.get("session_id", session_id))
            self._events[event_session].append(event)
            if event_session == session_id:
                yield self._events[session_id].popleft()
            if event.get("event_type") in {"run_completed", "run_failed"}:
                return

    def capabilities(self) -> list[Capability]:
        available = self._factory is not None or shutil.which(self._executable) is not None
        return [
            Capability(name="pi", supported=available), Capability(name="experimental", supported=True),
            Capability(name="jsonl_rpc", supported=available), Capability(name="sessions", supported=available),
            Capability(name="streaming", supported=available), Capability(name="cancellation", supported=available),
            Capability(name="cwd", supported=available), Capability(name="model_effort", supported=available),
            Capability(name="tool_policy", supported=available), Capability(name="structured_output", supported=available),
            Capability(name="structured_output_native", supported=False), Capability(name="sandbox_native", supported=False),
        ]

    def preflight(self, *, model: str, effort: str) -> dict[str, bool]:
        """Pi RPC has no standard auth/model introspection; do not guess."""
        probe = getattr(self._factory, "preflight", None)
        if callable(probe):
            return dict(probe(model=model, effort=effort))
        return {"authenticated": False, "model": False, "effort": False}

    async def _run_prompt(self, request: HarnessRequest) -> ProviderResponse:
        await self._connection(request)
        output = await self._assistant_output(request.prompt)
        session_id = await self._session_id()
        validated = await self._repair_if_needed(session_id, output)
        return ProviderResponse(
            session_id=session_id, output=validated,
            events=tuple(self._events.pop(session_id, ())) + ({"event_type": "run_completed"},),
        )

    async def _assistant_output(self, prompt: str) -> dict[str, Any]:
        await self._call("prompt", {"message": prompt})
        rpc = await self._connection()
        events = await rpc.wait_for_events({"agent_end", "agent_settled"})
        for event in events:
            session_id = str(event.get("session_id", ""))
            if session_id:
                self._events[session_id].append(event)
        last = await self._call("get_last_assistant_text", {})
        text = last.get("text")
        if not isinstance(text, str):
            return {"_invalid_structured_output": True}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {"_invalid_structured_output": True}
        return dict(parsed) if isinstance(parsed, Mapping) else {"_invalid_structured_output": True}

    async def _session_id(self) -> str:
        state = await self._call("get_state", {})
        session_id = state.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            raise InfrastructureError("Pi RPC state is missing sessionId.", "Use a compatible Pi RPC server.")
        return session_id

    async def _call(self, method: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
        rpc = await self._connection()
        try:
            result, notifications = await rpc.request(method, params)
        except asyncio.CancelledError:
            await rpc.close()
            self._rpc = None
            raise
        for event in notifications:
            session_id = str(event.get("session_id", ""))
            if session_id:
                self._events[session_id].append(event)
        return result

    async def _repair_if_needed(self, session_id: str, output: dict[str, Any]) -> dict[str, Any]:
        if self._output_model is None:
            return output
        current = output
        for repair in range(MAX_PI_REPAIRS + 1):
            try:
                self._output_model.model_validate(current)
                return current
            except ValidationError:
                if repair == MAX_PI_REPAIRS:
                    raise StructuredOutputError(MAX_PI_REPAIRS) from None
                current = await self._assistant_output(
                    "Your previous response did not match the required schema. Return only a complete JSON object matching it."
                )
        raise AssertionError("bounded repair loop must return or raise")

    async def _connection(self, request: HarnessRequest | None = None) -> "_JsonlRpc":
        if self._rpc is None:
            if self._factory is None:
                if request is None:
                    raise InfrastructureError("Pi process has not been initialized.", "Start a Pi request before accessing its RPC connection.")
                argv = [self._executable, *self._args, "--model", f"{request.model}:{request.effort}"]
                argv = self._wrapped_argv(request, argv)
                process = await asyncio.create_subprocess_exec(
                    *argv, cwd=request.cwd, stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
            else:
                argv = self._wrapped_argv(request, [self._executable, *self._args]) if request is not None else [self._executable, *self._args]
                process = await self._factory(*argv)
            self._rpc = _JsonlRpc(process)
        return self._rpc

    def _wrapped_argv(self, request: HarnessRequest, argv: list[str]) -> list[str]:
        if self._sandbox is None:
            return argv
        policy = request.tools.get("sandbox_policy")
        if not isinstance(policy, str) or not policy:
            raise InfrastructureError("Pi requires a prepared sandbox policy.", "Run Pi only through the STMS sandboxed harness wrapper.")
        return self._sandbox.wrap_command(Path(policy), argv)


class _JsonlRpc:
    """Sequential JSON-RPC calls with correct partial/multiple JSONL framing."""

    def __init__(self, process: Any) -> None:
        if process.stdin is None or process.stdout is None:
            raise InfrastructureError("Pi process has no JSONL pipes.", "Start Pi with stdin and stdout connected for RPC.")
        self._process = process
        self._writer = process.stdin
        self._reader = process.stdout
        self._next_id = 0

    async def request(self, method: str, params: Mapping[str, Any]) -> tuple[Mapping[str, Any], list[Mapping[str, Any]]]:
        self._next_id += 1
        identifier = self._next_id
        self._writer.write((json.dumps({"id": identifier, "type": method, **dict(params)}, separators=(",", ":")) + "\n").encode())
        await self._writer.drain()
        notifications: list[Mapping[str, Any]] = []
        while True:
            line = await self._reader.readline()
            if not line:
                raise InfrastructureError("Pi RPC process exited before replying.", "Inspect Pi logs and restart the experimental harness.")
            try:
                message = json.loads(line)
            except json.JSONDecodeError as error:
                raise InfrastructureError("Pi RPC emitted invalid JSON.", "Use a compatible Pi RPC server that emits one JSON object per line.") from error
            if not isinstance(message, Mapping):
                raise InfrastructureError("Pi RPC emitted a non-object message.", "Use a compatible Pi RPC server.")
            if message.get("id") != identifier or message.get("type") != "response":
                notifications.append(dict(message))
                continue
            if message.get("success") is not True:
                raise InfrastructureError("Pi RPC returned an error.", "Inspect Pi's local RPC error and retry the request.")
            result = message.get("data", {})
            if not isinstance(result, Mapping):
                raise InfrastructureError("Pi RPC response has no object result.", "Use a compatible Pi RPC server.")
            return dict(result), notifications

    async def wait_for_events(self, event_types: set[str]) -> list[Mapping[str, Any]]:
        events: list[Mapping[str, Any]] = []
        while True:
            line = await self._reader.readline()
            if not line:
                raise InfrastructureError("Pi RPC process exited before completing the prompt.", "Inspect Pi logs and restart the experimental harness.")
            try:
                message = json.loads(line)
            except json.JSONDecodeError as error:
                raise InfrastructureError("Pi RPC emitted invalid JSON.", "Use a compatible Pi RPC server that emits one JSON object per line.") from error
            if not isinstance(message, Mapping):
                continue
            events.append(dict(message))
            if message.get("type") in event_types:
                return events

    async def notifications(self) -> AsyncIterator[Mapping[str, Any]]:
        while True:
            line = await self._reader.readline()
            if not line:
                return
            try:
                message = json.loads(line)
            except json.JSONDecodeError as error:
                raise InfrastructureError("Pi RPC emitted invalid JSON.", "Use a compatible Pi RPC server that emits one JSON object per line.") from error
            if isinstance(message, Mapping) and "id" not in message:
                yield dict(message)

    async def close(self) -> None:
        if getattr(self._process, "returncode", None) is not None:
            return
        self._process.terminate()
        try:
            await asyncio.wait_for(self._process.wait(), timeout=2)
        except asyncio.TimeoutError:
            self._process.kill()
            await self._process.wait()
