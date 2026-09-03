import asyncio
import json

import pytest

from stms.adapters.harnesses.pi import PiHarness
from stms.domain.errors import InfrastructureError
from stms.domain.models import AgentRole, HarnessRequest, PlannerOutput


class _Writer:
    def __init__(self, reader: asyncio.StreamReader, *, invalid_json: bool = False) -> None:
        self.reader = reader; self.invalid_json = invalid_json

    def write(self, data: bytes) -> None:
        message = json.loads(data)
        if self.invalid_json:
            self.reader.feed_data(b"not-json\n")
            return
        method = message["type"]
        if method == "prompt":
            # Feed notification and response together to cover multiple JSONL frames.
            self.reader.feed_data((json.dumps({"type": "tool_execution_start", "session_id": "pi-1"}) + "\n" + json.dumps({"id": message["id"], "type": "response", "command": "prompt", "success": True}) + "\n" + json.dumps({"type": "agent_end", "session_id": "pi-1"}) + "\n").encode())
        elif method == "get_last_assistant_text":
            self.reader.feed_data((json.dumps({"id": message["id"], "type": "response", "command": method, "success": True, "data": {"text": "{}" if message["id"] == 2 else '{\"status\":\"needs_input\",\"questions\":[\"Which target?\"]}'}}) + "\n").encode())
        elif method == "get_state":
            self.reader.feed_data((json.dumps({"id": message["id"], "type": "response", "command": method, "success": True, "data": {"sessionId": "pi-1"}}) + "\n").encode())
        elif method == "abort":
            self.reader.feed_data((json.dumps({"id": message["id"], "type": "response", "command": method, "success": True}) + "\n").encode())

    async def drain(self) -> None: pass


class _Process:
    def __init__(self, *, invalid_json: bool = False) -> None:
        self.stdout = asyncio.StreamReader(); self.stdin = _Writer(self.stdout, invalid_json=invalid_json)
        self.stderr = asyncio.StreamReader(); self.returncode: int | None = None; self.terminated = False

    def terminate(self) -> None:
        self.terminated = True; self.returncode = -15; self.stdout.feed_eof()

    def kill(self) -> None:
        self.terminated = True; self.returncode = -9; self.stdout.feed_eof()

    async def wait(self) -> int:
        return self.returncode or 0


def request() -> HarnessRequest:
    return HarnessRequest(role=AgentRole.PLANNER, cwd="/tmp/worktree", model="model", effort="high", timeout_seconds=2, max_turns=3, prompt="plan")


@pytest.mark.asyncio
async def test_pi_repairs_schema_over_jsonl_and_terminates_on_cancel() -> None:
    process = _Process()

    async def factory(*_args): return process

    harness = PiHarness(process_factory=factory, output_model=PlannerOutput)
    result = await harness.start(request())
    events = [event async for event in harness.stream(result.session_id)]
    await harness.cancel(result.session_id)
    assert result.output["status"] == "needs_input"
    assert events[0].event_type == "tool_execution_start"
    assert events[-1].event_type == "run_completed"
    assert process.terminated


@pytest.mark.asyncio
async def test_pi_rejects_invalid_jsonl() -> None:
    async def factory(*_args): return _Process(invalid_json=True)

    with pytest.raises(InfrastructureError, match="invalid JSON"):
        await PiHarness(process_factory=factory).start(request())
