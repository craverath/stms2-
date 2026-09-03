import asyncio
from pathlib import Path
import sys

import pytest

from stms.adapters.harnesses.pi import PiHarness
from stms.adapters.sandbox.srt import FakeSandboxRuntime
from stms.deterministic.test_runner import DeterministicTestRunner
from stms.domain.errors import InfrastructureError
from stms.domain.models import AgentRole, HarnessRequest, TestCommand


def test_test_runner_applies_wrapper_and_honours_command_cwd(tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"; nested = worktree / "nested"; nested.mkdir(parents=True)
    sandbox = FakeSandboxRuntime(tmp_path / "policies")
    policy = sandbox.prepare("test_runner", worktree, worktree)
    command = TestCommand(argv=[sys.executable, "-c", "import os; print(os.getcwd())"], cwd="nested")
    result = DeterministicTestRunner(sandbox=sandbox, policy_path=policy).run(command, worktree, repository=worktree)
    assert result.succeeded
    assert result.stdout.strip() == str(nested.resolve())
    assert result.cwd == str(nested.resolve())
    assert sandbox.wrapped == [(policy, command.argv)]


def test_sandboxed_test_runner_fails_closed_without_a_policy(tmp_path: Path) -> None:
    sandbox = FakeSandboxRuntime(tmp_path / "policies")
    runner = DeterministicTestRunner(sandbox=sandbox)
    with pytest.raises(InfrastructureError, match="no policy"):
        runner.run(TestCommand(argv=[sys.executable, "-c", "pass"]), tmp_path, repository=tmp_path)


class _Writer:
    def __init__(self, reader: asyncio.StreamReader) -> None:
        self.reader = reader

    def write(self, data: bytes) -> None:
        import json
        message = json.loads(data); method = message["type"]
        if method == "prompt":
            self.reader.feed_data((json.dumps({"id": message["id"], "type": "response", "command": method, "success": True}) + "\n" + json.dumps({"type": "agent_end", "session_id": "pi"}) + "\n").encode())
        elif method == "get_last_assistant_text":
            self.reader.feed_data((json.dumps({"id": message["id"], "type": "response", "command": method, "success": True, "data": {"text": "{}"}}) + "\n").encode())
        elif method == "get_state":
            self.reader.feed_data((json.dumps({"id": message["id"], "type": "response", "command": method, "success": True, "data": {"sessionId": "pi"}}) + "\n").encode())

    async def drain(self) -> None: pass


class _Process:
    def __init__(self) -> None:
        self.stdout = asyncio.StreamReader(); self.stdin = _Writer(self.stdout); self.stderr = asyncio.StreamReader(); self.returncode = None
    def terminate(self) -> None: self.returncode = 0; self.stdout.feed_eof()
    def kill(self) -> None: self.returncode = -9; self.stdout.feed_eof()
    async def wait(self) -> int: return self.returncode or 0


@pytest.mark.asyncio
async def test_pi_subprocess_is_started_through_sandbox_wrapper(tmp_path: Path) -> None:
    sandbox = FakeSandboxRuntime(tmp_path / "policies")
    policy = sandbox.prepare("planner", tmp_path, tmp_path)
    observed: list[str] = []

    async def factory(*argv: str) -> _Process:
        observed.extend(argv)
        return _Process()

    harness = PiHarness(process_factory=factory, sandbox=sandbox)
    await harness.start(HarnessRequest(role=AgentRole.PLANNER, cwd=str(tmp_path), model="model", effort="high", timeout_seconds=2, max_turns=2, prompt="plan", tools={"sandbox_policy": str(policy)}))
    assert observed[:3] == ["pi", "--mode", "rpc"]
    assert sandbox.wrapped and sandbox.wrapped[0] == (policy, ["pi", "--mode", "rpc"])
