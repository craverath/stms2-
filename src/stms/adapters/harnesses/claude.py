"""Claude Code adapter with a lazy, mockable Agent SDK boundary.

Uses the real, documented ``claude_agent_sdk`` API (``ClaudeAgentOptions`` and
``ClaudeSDKClient``) rather than shelling out to undocumented/volatile CLI
flags. The SDK is an optional dependency (extra ``claude-agent-sdk``) imported
only inside :meth:`ClaudeAgentSdkTransport._official_client`, so importing this
module — and selecting a different harness — never requires it to be
installed.
"""
from __future__ import annotations

import importlib.util
import asyncio
import subprocess
from typing import Any, Mapping

from stms.domain.errors import CompatibilityError, InfrastructureError
from stms.domain.models import Capability, HarnessRequest

from .base import BaseHarness, HarnessTransport, ProviderResponse
from .codex import _capabilities, _json_object, _usage, _validate_request
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

    def __init__(self, client_factory: Any | None = None, *, executable: str = "claude", auth_probe: Any | None = None) -> None:
        self._client_factory = client_factory
        self._executable = executable
        self._auth_probe = auth_probe
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
            result = await asyncio.wait_for(
                client.run(
                    prompt=request.prompt,
                    cwd=request.cwd,
                    model=request.model,
                    effort=request.effort,
                    tools=request.tools, sandbox=policy,
                    timeout_seconds=request.timeout_seconds,
                    max_turns=request.max_turns,
                    session_id=request.session_id if resume else None,
                ),
                timeout=request.timeout_seconds,
            )
        except asyncio.TimeoutError as error:
            raise InfrastructureError("Claude Agent SDK timed out.", "Increase the approved timeout or inspect the provider session.") from error
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
            yield event if isinstance(event, dict) else {"event_type": "message_delta"}

    def capabilities(self) -> list[Capability]:
        installed = self._client_factory is not None or importlib.util.find_spec("claude_agent_sdk") is not None
        return _capabilities("claude", installed, experimental=False)

    def preflight(self, *, model: str, effort: str) -> dict[str, bool]:
        if self._client_factory is not None:
            client = self._client_factory()
            probe = getattr(client, "preflight", None)
            if callable(probe):
                return dict(probe(model=model, effort=effort))
        installed = importlib.util.find_spec("claude_agent_sdk") is not None or self._executable_available()
        if not installed or not self._authenticated():
            return {"authenticated": False, "model": False, "effort": False}
        valid_config = bool(model) and bool(effort)
        return {"authenticated": True, "model": valid_config, "effort": valid_config}

    def _executable_available(self) -> bool:
        import shutil
        return shutil.which(self._executable) is not None

    def _authenticated(self) -> bool:
        """Use Claude Code's documented auth-status command, which accepts OAuth."""
        if self._auth_probe is not None:
            return bool(self._auth_probe())
        if not self._executable_available():
            return False
        try:
            result = subprocess.run(
                [self._executable, "auth", "status"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0

    @staticmethod
    def _official_client() -> Any:
        try:
            import claude_agent_sdk
        except ImportError as error:
            raise InfrastructureError(
                "Claude Agent SDK is not installed.",
                "Install the optional 'claude-agent-sdk' extra (pip install stms[claude]) before selecting Claude Code.",
            ) from error
        return _ClaudeSdkClient(claude_agent_sdk)


class _ClaudeSdkClient:
    """Narrow compatibility shim over ``ClaudeSDKClient``; SDK details stay here.

    ``ClaudeSDKClient`` (not the one-shot ``query`` function) is used because it
    is the SDK's own documented boundary for interruption (``interrupt``) and
    for multi-turn/streaming access after the initial turn completes, both of
    which this adapter's ``cancel``/``stream`` contract requires.
    """

    def __init__(self, sdk: Any) -> None:
        self._sdk = sdk
        self._active: dict[str, Any] = {}

    async def run(self, **kwargs: Any) -> Any:
        options_type = getattr(self._sdk, "ClaudeAgentOptions", None)
        client_type = getattr(self._sdk, "ClaudeSDKClient", None)
        if options_type is None or client_type is None:
            raise CompatibilityError("Claude Agent SDK API is incompatible.", "Install a supported Claude Agent SDK version.")
        tools = kwargs["tools"]
        tool_policy = _claude_tool_policy(kwargs["sandbox"])
        option_kwargs: dict[str, Any] = dict(
            cwd=kwargs["cwd"], model=kwargs["model"], effort=kwargs["effort"],
            max_turns=kwargs["max_turns"], permission_mode=tool_policy["permission_mode"],
            allowed_tools=tool_policy["allowed_tools"],
            disallowed_tools=tool_policy["disallowed_tools"],
            resume=kwargs["session_id"],
            sandbox=_sandbox_settings(kwargs["sandbox"]),
        )
        output_format = tools.get("output_format")
        if output_format is not None:
            option_kwargs["output_format"] = output_format
        options = options_type(**option_kwargs)
        client = client_type(options=options)
        result_type = getattr(self._sdk, "ResultMessage", None)
        await client.connect()
        try:
            await client.query(kwargs["prompt"])
            final = None
            async for message in client.receive_response():
                if result_type is not None and isinstance(message, result_type):
                    final = message
        except Exception:
            await client.disconnect()
            raise
        if final is None:
            await client.disconnect()
            raise InfrastructureError("Claude Agent SDK returned no final result message.", "Retry the request or inspect the provider session.")
        session_id = getattr(final, "session_id", None) or kwargs["session_id"]
        if not session_id:
            await client.disconnect()
            raise InfrastructureError("Claude Agent SDK did not return a session ID.", "Upgrade the SDK and retry the preflight.")
        self._active[session_id] = client
        return _ClaudeRunResult(final, session_id)

    async def cancel(self, session_id: str) -> None:
        client = self._active.pop(session_id, None)
        if client is None:
            return
        try:
            await client.interrupt()
        finally:
            await client.disconnect()

    async def stream(self, session_id: str):
        client = self._active.get(session_id)
        if client is None:
            return
        async for message in client.receive_messages():
            yield _sdk_message_to_event(message)


class _ClaudeRunResult:
    def __init__(self, message: Any, session_id: str) -> None:
        self.session_id = session_id
        self.output = getattr(message, "structured_output", None) or getattr(message, "result", None) or {}
        self.usage = getattr(message, "usage", None) or {}


def _claude_tool_policy(policy: Mapping[str, Any]) -> dict[str, Any]:
    """Map the validated role sandbox to the smallest Claude tool surface."""
    mode = policy.get("sandbox")
    read_tools = ["Read", "Glob", "Grep"]
    if mode == "workspace_write":
        return {
            "permission_mode": "acceptEdits",
            "allowed_tools": [*read_tools, "Write", "Edit"],
            "disallowed_tools": ["Bash"],
        }
    if mode == "read_only":
        return {
            "permission_mode": "plan",
            "allowed_tools": read_tools,
            "disallowed_tools": ["Bash", "Write", "Edit"],
        }
    raise InfrastructureError(
        "Claude received an unknown sandbox mode.",
        "Generate a validated read-only or workspace-write role policy before invoking the SDK.",
    )


def _sandbox_settings(policy: Mapping[str, Any]) -> dict[str, Any]:
    """Translate the prepared STMS policy into the SDK's ``SandboxSettings``.

    Per the SDK's own documentation, filesystem read/write restriction is
    expressed through tool allow/deny rules (``allowed_tools``/``disallowed_tools``
    above), not through ``sandbox``; this only governs OS-level bash sandboxing
    and its network allowlist, so it is additive defense-in-depth, not the sole
    enforcement mechanism.
    """
    allowed_domains = list(policy.get("network_domains", [])) if policy.get("network_allowed") else []
    return {"enabled": True, "network": {"allowedDomains": allowed_domains}}


def _sdk_message_to_event(message: Any) -> dict[str, Any]:
    kind = type(message).__name__
    if kind == "ResultMessage":
        return {"event_type": "run_completed", "usage": dict(getattr(message, "usage", None) or {})}
    if kind in {"AssistantMessage", "UserMessage"}:
        text = "".join(getattr(block, "text", "") for block in getattr(message, "content", []) if hasattr(block, "text"))
        return {"event_type": "message_delta", "message": text or None}
    return {"event_type": "message_delta"}
