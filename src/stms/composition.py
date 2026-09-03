"""Concrete production composition. Imports of provider SDKs remain lazy."""
from __future__ import annotations

from pathlib import Path

from stms.adapters.harnesses.claude import ClaudeHarness
from stms.adapters.harnesses.codex import CodexHarness
from stms.adapters.harnesses.pi import PiHarness
from stms.adapters.sandbox.srt import SrtSandboxRuntime
from stms.application.orchestrator import Orchestrator


def compose(repository: Path) -> Orchestrator:
    """Build the offline-safe application; providers are contacted only after preflight."""
    sandbox = SrtSandboxRuntime()
    return Orchestrator(
        repository,
        harnesses={"codex": CodexHarness(), "claude": ClaudeHarness(), "pi": PiHarness(sandbox=sandbox)},
        sandbox=sandbox,
    )
