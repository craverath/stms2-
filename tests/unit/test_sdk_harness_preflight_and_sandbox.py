from pathlib import Path

import pytest

from stms.adapters.harnesses.claude import ClaudeAgentSdkTransport, ClaudeHarness
from stms.adapters.harnesses.codex import CodexHarness, CodexSdkTransport
from stms.adapters.sandbox.srt import FakeSandboxRuntime
from stms.domain.errors import InfrastructureError
from stms.domain.models import AgentRole, HarnessRequest


def _request(tmp_path: Path, policy: Path | None) -> HarnessRequest:
    return HarnessRequest(
        role=AgentRole.PLANNER, cwd=str(tmp_path), model="model", effort="high",
        timeout_seconds=5, max_turns=3, prompt="return JSON",
        tools={} if policy is None else {"sandbox_policy": str(policy)},
    )


class _Thread:
    id = "codex-session"
    async def run(self, *_args, **_kwargs):
        return type("Result", (), {"final_response": '{"status":"needs_input","questions":["q"]}', "usage": {}})()


class _CodexClient:
    def __init__(self) -> None: self.kwargs = {}
    def preflight(self, **_kwargs): return {"authenticated": True, "model": True, "effort": True}
    async def thread_start(self, **kwargs): self.kwargs = kwargs; return _Thread()


class _ClaudeClient:
    def __init__(self) -> None: self.kwargs = {}
    def preflight(self, **_kwargs): return {"authenticated": True, "model": True, "effort": True}
    async def run(self, **kwargs):
        self.kwargs = kwargs
        return type("Result", (), {"session_id": "claude-session", "output": '{"status":"needs_input","questions":["q"]}', "usage": {}})()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["codex", "claude"])
async def test_sdk_adapters_require_and_apply_a_role_sandbox_policy(tmp_path: Path, kind: str) -> None:
    sandbox = FakeSandboxRuntime(tmp_path / "policies")
    policy = sandbox.prepare("planner", tmp_path, tmp_path)
    client = _CodexClient() if kind == "codex" else _ClaudeClient()
    harness = CodexHarness(CodexSdkTransport(lambda: client)) if kind == "codex" else ClaudeHarness(ClaudeAgentSdkTransport(lambda: client))
    result = await harness.start(_request(tmp_path, policy))
    assert result.session_id
    assert client.kwargs["sandbox"] == ("read_only" if kind == "codex" else {"sandbox": "read_only", "network_allowed": False, "network_domains": [], "allow_git_mutation": False})
    assert client.kwargs.get("allow_git_mutation", False) is False
    with pytest.raises(InfrastructureError, match="no prepared sandbox policy"):
        await harness.start(_request(tmp_path, None))


def test_sdk_preflight_uses_the_injected_real_boundary_and_fails_closed_without_one() -> None:
    client = _CodexClient()
    assert CodexHarness(CodexSdkTransport(lambda: client)).preflight(model="model", effort="high") == {"authenticated": True, "model": True, "effort": True}
    assert set(CodexHarness().preflight(model="model", effort="high")) == {"authenticated", "model", "effort"}
