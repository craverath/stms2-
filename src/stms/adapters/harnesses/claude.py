"""Claude Code adapter with a lazy, mockable Agent SDK boundary."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
from typing import Any

from stms.domain.errors import CompatibilityError, InfrastructureError
from stms.domain.models import Capability, HarnessRequest

from .base import BaseHarness, HarnessTransport, ProviderResponse
from .codex import _capabilities, _event_mapping, _json_object, _usage, _validate_request
from .sandboxing import sdk_sandbox_options


class ClaudeHarness(BaseHarness):
    """Supported Claude Code harness; an injected transport is the test boundary."""

    def __init__(self, transport: HarnessTransport | None = None) -> None:
        super().__init__(transport or ClaudeAgentSdkTransport(), name="Claude Code")

    def preflight(self, *, model: str, effort: str) -> dict[str, bool]:
        probe = getattr(self._transport, "preflight", None)
        if not callable(probe):
            return {"authenticated": False, "model": False, "effort": False}
        return probe(model=model, effort=effort)


class ClaudeAgentSdkTransport:
    """Wrap the optional Claude Agent SDK without importing it at module load."""

    def __init__(self, client_factory: Any | None = None, *, executable: str = "claude", command_runner: Any | None = None, probe_timeout_seconds: int = 10) -> None:
        self._client_factory = client_factory
        self._executable = executable; self._command_runner = command_runner or subprocess.run; self._probe_timeout_seconds = probe_timeout_seconds
        self._sessions: dict[str, Any] = {}

    async def start(self, request: HarnessRequest) -> ProviderResponse:
        return await self._run(request, resume=False)

    async def resume(self, request: HarnessRequest) -> ProviderResponse:
        return await self._run(request, resume=True)

    async def _run(self, request: HarnessRequest, *, resume: bool) -> ProviderResponse:
        _validate_request(request)
        policy = sdk_sandbox_options(request)
        client = self._client_factory() if self._client_factory is not None else self._official_client()
        try:
            result = await client.run(
                prompt=request.prompt,
                cwd=request.cwd,
                model=request.model,
                effort=request.effort,
                tools=request.tools, sandbox=policy,
                timeout_seconds=request.timeout_seconds,
                max_turns=request.max_turns,
                session_id=request.session_id if resume else None,
            )
        except TypeError as error:
            raise CompatibilityError(
                "Installed Claude Agent SDK cannot apply the configured cwd, effort, tool policy, or limits.",
                "Install a compatible Claude Agent SDK or choose supported configuration values.",
            ) from error
        session_id = str(getattr(result, "session_id", ""))
        if not session_id:
            raise InfrastructureError("Claude Agent SDK did not return a session ID.", "Upgrade the SDK and retry the preflight.")
        self._sessions[session_id] = client
        output = _json_object(getattr(result, "output", getattr(result, "result", result)))
        return ProviderResponse(session_id=session_id, output=output, usage=_usage(result), events=({"event_type": "session_started"}, {"event_type": "run_completed", "usage": _usage(result)}))

    async def cancel(self, session_id: str) -> None:
        client = self._sessions.get(session_id)
        if client is None or not hasattr(client, "cancel"):
            raise CompatibilityError("Claude session cannot be cancelled by this SDK.", "Upgrade the Claude Agent SDK or terminate the session through its supported control plane.")
        outcome = client.cancel(session_id)
        if hasattr(outcome, "__await__"):
            await outcome

    async def stream(self, session_id: str):
        client = self._sessions.get(session_id)
        if client is None or not hasattr(client, "stream"):
            return
        async for event in client.stream(session_id):
            yield _event_mapping(event)

    def capabilities(self) -> list[Capability]:
        installed = self._client_factory is not None or importlib.util.find_spec("claude_agent_sdk") is not None
        return _capabilities("claude", installed, experimental=False)

    def preflight(self, *, model: str, effort: str) -> dict[str, bool]:
        if self._client_factory is not None:
            client = self._client_factory()
            probe = getattr(client, "preflight", None)
            if callable(probe):
                return dict(probe(model=model, effort=effort))
        if shutil.which(self._executable) is None:
            return {"authenticated": False, "model": False, "effort": False}
        try:
            auth = self._command_runner([self._executable, "auth", "status", "--json"], text=True, capture_output=True, timeout=self._probe_timeout_seconds)
        except (subprocess.TimeoutExpired, OSError):
            return {"authenticated": False, "model": False, "effort": False}
        if auth.returncode != 0 or not _authenticated_json(auth.stdout):
            return {"authenticated": False, "model": False, "effort": False}
        command = [self._executable, "--print", "--output-format", "json", "--model", model, "--effort", effort, "--max-turns", "1", "--restricted", "--safe-mode", "--no-session-persistence"]
        try:
            probe = self._command_runner(command, input="Respond with {} only.", text=True, capture_output=True, timeout=self._probe_timeout_seconds)
        except (subprocess.TimeoutExpired, OSError):
            return {"authenticated": True, "model": False, "effort": False}
        if probe.returncode != 0:
            return {"authenticated": True, "model": False, "effort": False}
        try:
            payload = json.loads(probe.stdout)
        except (TypeError, json.JSONDecodeError):
            return {"authenticated": True, "model": False, "effort": False}
        # Only the JSON envelope is inspected.  Account/content fields are never
        # returned, logged, or persisted by the probe.
        return {"authenticated": True, "model": isinstance(payload, dict), "effort": isinstance(payload, dict)}

    @staticmethod
    def _official_client() -> Any:
        try:
            import claude_agent_sdk
        except ImportError as error:
            raise InfrastructureError("Claude Agent SDK is not installed.", "Install the compatible optional Claude Agent SDK before selecting Claude Code.") from error
        return _ClaudeSdkClient(claude_agent_sdk)


class _ClaudeSdkClient:
    """Narrow compatibility shim; SDK API details stay out of the domain layer."""

    def __init__(self, sdk: Any) -> None:
        self._sdk = sdk

    async def run(self, **kwargs: Any) -> Any:
        options_type = getattr(self._sdk, "ClaudeAgentOptions", None)
        query = getattr(self._sdk, "query", None)
        if options_type is None or query is None:
            raise CompatibilityError("Claude Agent SDK API is incompatible.", "Install a supported Claude Agent SDK version.")
        tools = kwargs["tools"]
        # The Agent SDK supports these explicit controls.  Absent an allowlist, a
        # conservative denylist prevents this adapter from widening a role policy.
        options = options_type(
            cwd=kwargs["cwd"], model=kwargs["model"], effort=kwargs["effort"],
            max_turns=kwargs["max_turns"], permission_mode=tools.get("permission_mode"),
            allowed_tools=list(tools.get("allowed_tools", [])),
            disallowed_tools=list(tools.get("disallowed_tools", ["Bash", "Write", "Edit"])),
            output_format=tools.get("output_format"), resume=kwargs["session_id"],
            sandbox=kwargs["sandbox"],
        )
        final = None
        session_id = None
        async for message in query(prompt=kwargs["prompt"], options=options):
            final = message
            session_id = getattr(message, "session_id", session_id)
        if final is None:
            raise InfrastructureError("Claude Agent SDK returned no final message.", "Retry the request or inspect the provider session.")
        return _ClaudeRunResult(final, session_id)


class _ClaudeRunResult:
    def __init__(self, message: Any, session_id: str | None) -> None:
        self.session_id = session_id
        self.output = getattr(message, "result", getattr(message, "output", message))
        self.usage = getattr(message, "usage", {})


def _authenticated_json(raw: str) -> bool:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return False
    if not isinstance(value, dict):
        return False
    return value.get("authenticated") is True or value.get("loggedIn") is True or value.get("status") == "authenticated"
