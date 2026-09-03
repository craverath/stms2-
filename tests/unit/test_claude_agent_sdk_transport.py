"""Exercises the real ``ClaudeAgentOptions``/``ClaudeSDKClient`` shape offline.

A fake ``claude_agent_sdk`` module (matching the installed package's real
public API surface: dataclass options, ``ClaudeSDKClient.connect/query/
receive_response/interrupt/disconnect``, and ``ResultMessage``) is injected via
``sys.modules`` so the adapter's translation logic is proven correct without
importing the real optional dependency or contacting a provider.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import sys
from typing import Any

import pytest

from stms.adapters.harnesses.claude import ClaudeAgentSdkTransport
from stms.adapters.sandbox.srt import FakeSandboxRuntime
from stms.domain.models import AgentRole, HarnessRequest
from stms.domain.errors import InfrastructureError


@dataclass
class _FakeOptions:
    cwd: str | None = None
    model: str | None = None
    effort: str | None = None
    max_turns: int | None = None
    permission_mode: str | None = None
    allowed_tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)
    resume: str | None = None
    sandbox: dict[str, Any] | None = None
    output_format: dict[str, Any] | None = None


@dataclass
class _FakeResultMessage:
    session_id: str
    result: str | None = None
    structured_output: Any = None
    usage: dict[str, Any] | None = None


class _FakeSdkClient:
    instances: list["_FakeSdkClient"] = []

    def __init__(self, options: _FakeOptions | None = None) -> None:
        self.options = options
        self.connected = False
        self.interrupted = False
        self.queried_prompt: str | None = None
        _FakeSdkClient.instances.append(self)

    async def connect(self) -> None:
        self.connected = True

    async def query(self, prompt: str, session_id: str = "default") -> None:
        self.queried_prompt = prompt

    async def receive_response(self):
        yield _FakeResultMessage(session_id="claude-session-1", result='{"status":"needs_input","questions":["q"]}', usage={"input_tokens": 3})

    async def receive_messages(self):
        yield _FakeResultMessage(session_id="claude-session-1", usage={"input_tokens": 3})

    async def interrupt(self) -> None:
        self.interrupted = True

    async def disconnect(self) -> None:
        self.connected = False


@pytest.fixture
def fake_sdk_module(monkeypatch):
    _FakeSdkClient.instances.clear()
    module = type(sys)("claude_agent_sdk")
    module.ClaudeAgentOptions = _FakeOptions
    module.ClaudeSDKClient = _FakeSdkClient
    module.ResultMessage = _FakeResultMessage
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", module)
    return module


def _request(tmp_path, policy, role: AgentRole = AgentRole.PLANNER) -> HarnessRequest:
    return HarnessRequest(
        role=role, cwd=str(tmp_path), model="claude-model", effort="high",
        timeout_seconds=5, max_turns=3, prompt="return JSON", tools={"sandbox_policy": str(policy)},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("role", "expected_mode", "expected_allowed", "expected_disallowed"),
    [
        (AgentRole.REVIEWER, "plan", ["Read", "Glob", "Grep"], ["Bash", "Write", "Edit"]),
        (AgentRole.IMPLEMENTER, "acceptEdits", ["Read", "Glob", "Grep", "Write", "Edit"], ["Bash"]),
    ],
)
async def test_official_client_derives_tools_from_role_sandbox(
    tmp_path, fake_sdk_module, role, expected_mode, expected_allowed, expected_disallowed,
) -> None:
    sandbox = FakeSandboxRuntime(tmp_path / "policies")
    policy = sandbox.prepare(role.value, tmp_path, tmp_path)
    transport = ClaudeAgentSdkTransport(executable="claude")

    response = await transport.start(_request(tmp_path, policy, role))

    assert response.session_id == "claude-session-1"
    assert response.output == {"status": "needs_input", "questions": ["q"]}
    assert response.usage == {"input_tokens": 3}

    used = _FakeSdkClient.instances[0]
    assert used.connected is True
    assert used.queried_prompt == "return JSON"
    options = used.options
    assert options.cwd == str(tmp_path) and options.model == "claude-model" and options.effort == "high"
    assert options.max_turns == 3
    assert options.permission_mode == expected_mode
    assert options.allowed_tools == expected_allowed
    assert options.disallowed_tools == expected_disallowed
    assert options.sandbox == {"enabled": True, "network": {"allowedDomains": []}}


@pytest.mark.asyncio
async def test_cancel_interrupts_and_disconnects_the_real_client(tmp_path, fake_sdk_module) -> None:
    sandbox = FakeSandboxRuntime(tmp_path / "policies")
    policy = sandbox.prepare("planner", tmp_path, tmp_path)
    transport = ClaudeAgentSdkTransport(executable="claude")
    response = await transport.start(_request(tmp_path, policy))

    await transport.cancel(response.session_id)

    used = _FakeSdkClient.instances[0]
    assert used.interrupted is True
    assert used.connected is False


@pytest.mark.asyncio
async def test_harness_request_timeout_bounds_sdk_call(tmp_path) -> None:
    class SlowClient:
        async def run(self, **_kwargs):
            import asyncio
            await asyncio.sleep(1)

    sandbox = FakeSandboxRuntime(tmp_path / "policies")
    policy = sandbox.prepare("planner", tmp_path, tmp_path)
    request = HarnessRequest(
        role=AgentRole.PLANNER, cwd=str(tmp_path), model="m", effort="high",
        timeout_seconds=1, max_turns=1, prompt="p", tools={"sandbox_policy": str(policy)},
    )
    transport = ClaudeAgentSdkTransport(client_factory=SlowClient)
    with pytest.raises(InfrastructureError, match="timed out"):
        await transport.start(request)
