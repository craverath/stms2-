import pytest

from stms.adapters.harnesses.claude import ClaudeHarness
from stms.adapters.harnesses.codex import CodexHarness

from .harness_contract import assert_harness_contract


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter", [CodexHarness, ClaudeHarness])
async def test_supported_adapters_meet_common_harness_contract(adapter) -> None:
    await assert_harness_contract(adapter)
