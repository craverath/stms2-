"""Codex App Server adapter; no unsupported Python SDK is assumed."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
import json
from pathlib import Path
import shutil
import subprocess
from typing import Any, Callable

from stms.domain.errors import CompatibilityError, InfrastructureError
from stms.domain.models import Capability, HarnessRequest
from .base import BaseHarness, HarnessTransport, ProviderResponse
from .sandboxing import sdk_sandbox_options


class CodexHarness(BaseHarness):
    def __init__(self, transport: HarnessTransport | None = None) -> None:
        super().__init__(transport or CodexAppServerTransport(), name="Codex")

    def preflight(self, *, model: str, effort: str) -> dict[str, bool]:
        probe = getattr(self._transport, "preflight", None)
        return probe(model=model, effort=effort) if callable(probe) else {"authenticated": False, "model": False, "effort": False}


class CodexAppServerTransport:
    """Official local App Server JSON-RPC boundary, injectable for offline tests."""
    def __init__(self, executable: str = "codex", *, process_factory: Callable[..., Any] | None = None, command_runner: Callable[..., Any] | None = None, app_server_probe: Callable[[], Mapping[str, Any]] | None = None) -> None:
        self.executable = executable; self._factory = process_factory; self._run = command_runner or subprocess.run; self._catalog_probe = app_server_probe; self._rpc: _AppServerRpc | None = None

    async def start(self, request: HarnessRequest) -> ProviderResponse:
        _validate_request(request); policy = sdk_sandbox_options(request); rpc = await self._connection(request); await rpc.initialize()
        thread = await rpc.call("thread/start", {"cwd": request.cwd, "model": request.model, "config": {"model_reasoning_effort": request.effort}, "sandbox": policy["sandbox"], "approvalPolicy": "never"})
        thread_id = _required(thread, "threadId")
        turn = await rpc.call("turn/start", {"threadId": thread_id, "input": [{"type": "text", "text": request.prompt}]})
        completed = await rpc.wait_turn(thread_id, _required(turn, "turnId"))
        return ProviderResponse(thread_id, _json_object(completed.get("text")), _usage(completed), ({"event_type": "session_started"}, {"event_type": "run_completed", "usage": _usage(completed)}))

    async def resume(self, request: HarnessRequest) -> ProviderResponse:
        if not request.session_id: return await self.start(request)
        _validate_request(request); policy = sdk_sandbox_options(request); rpc = await self._connection(request); await rpc.initialize()
        turn = await rpc.call("turn/start", {"threadId": request.session_id, "input": [{"type": "text", "text": request.prompt}], "config": {"model_reasoning_effort": request.effort}, "sandbox": policy["sandbox"], "approvalPolicy": "never"})
        completed = await rpc.wait_turn(request.session_id, _required(turn, "turnId"))
        return ProviderResponse(request.session_id, _json_object(completed.get("text")), _usage(completed), ({"event_type": "run_completed", "usage": _usage(completed)},))

    async def cancel(self, session_id: str) -> None:
        if self._rpc is None: raise CompatibilityError("Codex App Server session is unavailable.", "Restart from persisted STMS context.")
        await self._rpc.call("turn/interrupt", {"threadId": session_id})

    async def stream(self, session_id: str) -> AsyncIterator[Mapping[str, Any]]:
        if False: yield {"event_type": "unreachable"}

    def capabilities(self) -> list[Capability]:
        available = self._factory is not None or shutil.which(self.executable) is not None
        return [Capability(name=item, supported=available) for item in ("codex", "sessions", "streaming", "cancellation", "structured_output", "cwd", "model_effort", "tool_policy")]

    def preflight(self, *, model: str, effort: str) -> dict[str, bool]:
        probe = getattr(self._factory, "preflight", None)
        if callable(probe): return dict(probe(model=model, effort=effort))
        if shutil.which(self.executable) is None: return {"authenticated": False, "model": False, "effort": False}
        result = self._run([self.executable, "login", "status"], text=True, capture_output=True)
        authenticated = result.returncode == 0
        if not authenticated: return {"authenticated": False, "model": False, "effort": False}
        try:
            catalog = self._catalog_probe() if self._catalog_probe else self._read_catalog()
        except (OSError, subprocess.SubprocessError, InfrastructureError):
            return {"authenticated": True, "model": False, "effort": False}
        entry = _catalog_model(catalog, model)
        efforts = _catalog_efforts(entry) if entry else set()
        return {"authenticated": True, "model": entry is not None, "effort": effort in efforts}

    def _read_catalog(self) -> Mapping[str, Any]:
        process = subprocess.Popen([self.executable, "app-server", "--stdio"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        try:
            assert process.stdin is not None and process.stdout is not None
            process.stdin.write(json.dumps({"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "stms", "version": "0.1"}, "capabilities": {}}}) + "\n"); process.stdin.flush()
            _read_sync_response(process.stdout, 1)
            process.stdin.write(json.dumps({"id": 2, "method": "model/list", "params": {}}) + "\n"); process.stdin.flush()
            return _read_sync_response(process.stdout, 2)
        finally:
            process.terminate()
            try: process.wait(timeout=2)
            except subprocess.TimeoutExpired: process.kill()

    async def _connection(self, request: HarnessRequest) -> "_AppServerRpc":
        if self._rpc is None:
            if self._factory is None:
                process = await asyncio.create_subprocess_exec(self.executable, "app-server", "--stdio", cwd=request.cwd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            else: process = await self._factory(self.executable, "app-server", "--stdio")
            self._rpc = _AppServerRpc(process)
        return self._rpc


class _AppServerRpc:
    def __init__(self, process: Any) -> None:
        if process.stdin is None or process.stdout is None: raise InfrastructureError("Codex App Server has no JSON-RPC pipes.", "Start codex app-server with stdio enabled.")
        self.writer = process.stdin; self.reader = process.stdout; self.identifier = 0
    async def initialize(self) -> None:
        await self.call("initialize", {"clientInfo": {"name": "stms", "version": "0.1"}, "capabilities": {}})
    async def call(self, method: str, params: Mapping[str, Any]) -> dict[str, Any]:
        self.identifier += 1; identifier = self.identifier
        self.writer.write((json.dumps({"id": identifier, "method": method, "params": dict(params)}, separators=(",", ":")) + "\n").encode()); await self.writer.drain()
        while True:
            line = await self.reader.readline()
            if not line: raise InfrastructureError("Codex App Server exited before replying.", "Run codex login status and update Codex.")
            try: message = json.loads(line)
            except json.JSONDecodeError as error: raise InfrastructureError("Codex App Server emitted invalid JSON-RPC.", "Update Codex.") from error
            if message.get("id") != identifier: continue
            if "error" in message: raise CompatibilityError("Codex App Server rejected the request.", "Choose an authenticated model and effort from the Codex catalog.")
            return message.get("result", {}) if isinstance(message.get("result", {}), dict) else {}
    async def wait_turn(self, thread_id: str, turn_id: str) -> dict[str, Any]:
        while True:
            line = await self.reader.readline()
            if not line: raise InfrastructureError("Codex App Server exited during a turn.", "Resume after repairing Codex.")
            try: event = json.loads(line)
            except json.JSONDecodeError as error: raise InfrastructureError("Codex App Server emitted invalid JSON-RPC.", "Update Codex.") from error
            params = event.get("params", {})
            if event.get("method") == "turn/completed" and params.get("threadId") == thread_id and params.get("turnId") == turn_id: return dict(params)


def _required(value: Mapping[str, Any], name: str) -> str:
    result = value.get(name)
    if not isinstance(result, str) or not result: raise InfrastructureError(f"Codex App Server did not return {name}.", "Update Codex and retry.")
    return result
def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try: value = json.loads(value)
        except json.JSONDecodeError: return {"_invalid_structured_output": True}
    return value if isinstance(value, dict) else {"_invalid_structured_output": True}
def _usage(value: Any) -> dict[str, int | float]:
    usage = value.get("usage", {}) if isinstance(value, Mapping) else getattr(value, "usage", {})
    return {key: amount for key, amount in usage.items() if isinstance(amount, (int, float))} if isinstance(usage, dict) else {}
def _validate_request(request: HarnessRequest) -> None:
    if not Path(request.cwd).is_absolute(): raise CompatibilityError("Harness cwd must be absolute.", "Pass the canonical worktree path to the harness.")
def _event_mapping(value: Any) -> dict[str, Any]: return value if isinstance(value, dict) else {"event_type": "message_delta"}
def _capabilities(name: str, installed: bool, *, experimental: bool) -> list[Capability]: return [Capability(name=item, supported=installed) for item in (name, "sessions", "streaming", "cancellation", "structured_output", "cwd", "model_effort", "tool_policy")]

def _read_sync_response(stdout: Any, identifier: int) -> Mapping[str, Any]:
    while True:
        line = stdout.readline()
        if not line: raise InfrastructureError("Codex App Server exited during preflight.", "Update Codex and run codex login status.")
        value = json.loads(line)
        if value.get("id") == identifier:
            if "error" in value: raise InfrastructureError("Codex App Server rejected preflight.", "Update Codex or choose an available model.")
            result = value.get("result", {})
            return result if isinstance(result, Mapping) else {}

def _catalog_model(catalog: Mapping[str, Any], requested: str) -> Mapping[str, Any] | None:
    models = catalog.get("models", catalog.get("data", []))
    if not isinstance(models, list): return None
    for item in models:
        if not isinstance(item, Mapping): continue
        aliases = item.get("aliases", [])
        if requested in {item.get("id"), item.get("name")} or isinstance(aliases, list) and requested in aliases:
            return item
    return None

def _catalog_efforts(model: Mapping[str, Any]) -> set[str]:
    efforts = model.get("supportedReasoningEfforts", model.get("reasoningEfforts", model.get("efforts", [])))
    if not isinstance(efforts, list):
        return set()
    return {
        value
        for item in efforts
        if isinstance(value := item.get("reasoningEffort") if isinstance(item, Mapping) else item, str)
    }

class CodexSdkTransport:
    """Test-only legacy boundary retained for injected SDK doubles.

    Production composition uses :class:`CodexAppServerTransport`; this avoids the
    nonexistent Python package while keeping older offline tests mockable.
    """
    def __init__(self, client_factory: Callable[[], Any]) -> None: self.factory = client_factory
    async def start(self, request: HarnessRequest) -> ProviderResponse:
        policy = sdk_sandbox_options(request); client = self.factory()
        thread = await client.thread_start(model=request.model, cwd=request.cwd, effort=request.effort, tools=request.tools, sandbox=policy["sandbox"], network_allowed=policy["network_allowed"], network_domains=policy["network_domains"], allow_git_mutation=False)
        raw = await thread.run(request.prompt, timeout=request.timeout_seconds, max_turns=request.max_turns)
        return ProviderResponse(str(thread.id), _json_object(getattr(raw, "final_response", raw)), _usage(raw))
    async def resume(self, request: HarnessRequest) -> ProviderResponse: return await self.start(request)
    async def cancel(self, session_id: str) -> None: return None
    async def stream(self, session_id: str) -> AsyncIterator[Mapping[str, Any]]:
        if False: yield {}
    def capabilities(self) -> list[Capability]: return _capabilities("codex", True, experimental=False)
    def preflight(self, *, model: str, effort: str) -> dict[str, bool]:
        probe = getattr(self.factory(), "preflight", None)
        return dict(probe(model=model, effort=effort)) if callable(probe) else {"authenticated": False, "model": False, "effort": False}
