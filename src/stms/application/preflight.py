"""Read-only validation performed before a run or a lock is created."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
from typing import Mapping

from stms.application.configuration import load_runtime_config
from stms.domain.errors import ConfigurationError, InfrastructureError
from stms.domain.models import Capability, RuntimeConfig
from stms.domain.ports import AgentHarness, SandboxRuntime


@dataclass(frozen=True)
class PreflightResult:
    repository: Path
    config: RuntimeConfig
    branch_base: str
    commit_base: str
    git_name: str
    git_email: str
    adapter_versions: dict[str, str]


class PreflightService:
    """Validate all startup prerequisites without writing to the repository."""

    def __init__(
        self,
        repository: Path,
        harnesses: Mapping[str, AgentHarness],
        sandbox: SandboxRuntime,
        *,
        control_store: object | None = None,
    ) -> None:
        self.repository = repository.resolve()
        self.harnesses = harnesses
        self.sandbox = sandbox
        self.control_store = control_store

    def validate(self) -> PreflightResult:
        root = self._repository_root()
        if root != self.repository:
            raise InfrastructureError(
                "STMS must be run from the Git repository root.",
                f"Change directory to {root} and run the command again.",
            )
        commit_base = self._git("rev-parse", "--verify", "HEAD")
        branch_base = self._git("symbolic-ref", "--quiet", "--short", "HEAD")
        if self._git("status", "--porcelain", "--untracked-files=no"):
            raise InfrastructureError(
                "Repository has tracked changes.",
                "Commit, stash, or discard tracked changes before starting STMS; untracked files are allowed.",
            )
        name, email = self._git("config", "--get", "user.name"), self._git("config", "--get", "user.email")
        if not name or not email:
            raise InfrastructureError(
                "Git user.name and user.email are required for the final squash commit.",
                "Configure Git identity with git config user.name and git config user.email.",
            )
        config = load_runtime_config(root)
        adapter_versions: dict[str, str] = {}
        for role, agent in config.agents.items():
            harness = self.harnesses.get(agent.harness)
            if harness is None:
                raise InfrastructureError(
                    f"Selected {role.value} harness '{agent.harness}' is unavailable.",
                    "Install and authenticate the configured harness, or choose an installed harness in stms.yml.",
                )
            caps = {item.name: item for item in harness.capabilities()}
            required = ("sessions", "structured_output", "cwd", "model_effort", "tool_policy")
            absent = [name for name in required if not caps.get(name, Capability(name=name, supported=False)).supported]
            if absent:
                raise InfrastructureError(
                    f"Harness '{agent.harness}' lacks required capabilities: {', '.join(absent)}.",
                    "Use a compatible harness/version; STMS does not silently downgrade capabilities.",
                )
            for capability_name in ("authentication", f"model:{agent.model}", f"effort:{agent.effort}"):
                declared = caps.get(capability_name)
                if declared is not None and not declared.supported:
                    raise InfrastructureError(
                        f"Harness '{agent.harness}' cannot use configured {capability_name}.",
                        "Authenticate the harness or select a compatible model and effort in stms.yml.",
                    )
            probe = getattr(harness, "preflight", None)
            if callable(probe):
                result = probe(model=agent.model, effort=agent.effort)
                if not isinstance(result, dict):
                    raise InfrastructureError(
                        f"Harness '{agent.harness}' returned an invalid preflight probe result.",
                        "Upgrade the harness adapter so it reports authentication, model, and effort availability.",
                    )
                if not result.get("authenticated", False):
                    raise InfrastructureError(f"Harness '{agent.harness}' is not authenticated.", "Authenticate the configured harness before starting STMS.")
                if not result.get("model", False):
                    raise InfrastructureError(f"Harness '{agent.harness}' cannot use model '{agent.model}'.", "Select an available model in stms.yml.")
                if not result.get("effort", False):
                    raise InfrastructureError(f"Harness '{agent.harness}' cannot use effort '{agent.effort}'.", "Select an effort level supported by the configured model.")
            primary = caps.get(agent.harness)
            adapter_versions[agent.harness] = primary.version if primary and primary.version else "unknown"
        sandbox_caps = {item.name: item for item in self.sandbox.capabilities()}
        selected_sandbox = sandbox_caps.get(config.security.sandbox)
        if selected_sandbox is None or not selected_sandbox.supported:
            raise InfrastructureError(
                f"Configured sandbox '{config.security.sandbox}' is unavailable.",
                "Install a compatible sandbox or select an explicitly authorized equivalent fallback.",
            )
        if not sandbox_caps or not all(item.supported for item in sandbox_caps.values()):
            if not config.security.allow_native_fallback:
                raise InfrastructureError(
                    "Configured sandbox is unavailable or incompatible.",
                    "Install the configured sandbox or explicitly enable an equivalent native fallback in stms.yml.",
                )
        for command in config.tests.commands:
            candidate = root / command.cwd / command.argv[0]
            if "/" in command.argv[0]:
                valid = candidate.is_file() and candidate.exists()
            else:
                valid = shutil.which(command.argv[0]) is not None
            if not valid:
                raise ConfigurationError(
                    f"Configured test command is unavailable: {command.argv[0]!r}.",
                    "Install it in the project environment or correct tests.commands in stms.yml.",
                )
        return PreflightResult(root, config, branch_base, commit_base, name, email, adapter_versions)

    def _repository_root(self) -> Path:
        try:
            return Path(self._git("rev-parse", "--show-toplevel")).resolve()
        except InfrastructureError as error:
            raise InfrastructureError("Current directory is not inside a Git repository.", "Run STMS from the root of a Git repository with a valid HEAD.") from error

    def _git(self, *args: str) -> str:
        result = subprocess.run(["git", *args], cwd=self.repository, text=True, capture_output=True)
        if result.returncode:
            message = result.stderr.strip() or result.stdout.strip() or "git command failed"
            raise InfrastructureError(f"Git {' '.join(args)} failed: {message}", "Repair the Git repository state and retry.")
        return result.stdout.strip()
