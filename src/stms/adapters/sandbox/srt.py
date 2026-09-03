"""Anthropic Sandbox Runtime adapter with explicit capability checks."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from stms.domain.errors import InfrastructureError
from stms.domain.models import AgentRole, Capability
from .policy import SandboxPolicy, role_policy


class SrtSandboxRuntime:
    def __init__(self, executable: str = "srt", *, policy_directory: Path | None = None) -> None:
        self.executable = executable; self.policy_directory = policy_directory or Path(tempfile.gettempdir()) / "stms-policies"

    def capabilities(self) -> list[Capability]:
        path = shutil.which(self.executable)
        if not path: return [Capability(name="srt", supported=False)]
        version = subprocess.run([path, "--version"], text=True, capture_output=True)
        if version.returncode: return [Capability(name="srt", supported=False)]
        return [
            Capability(name="srt", version=version.stdout.strip(), supported=True),
            Capability(name="filesystem_policy", supported=True), Capability(name="network_policy", supported=True),
            Capability(name="git_policy", supported=True), Capability(name="command_wrapping", supported=True),
        ]

    def require_available(self) -> None:
        if not all(capability.supported for capability in self.capabilities()):
            raise InfrastructureError("Sandbox Runtime is unavailable or incompatible.", "Install a compatible SRT or explicitly configure a capable native fallback.")

    def prepare(self, role: str, repository: Path, worktree: Path | None = None, *, planner_web: bool = False, test_network: bool = False, install_domains: list[str] | None = None) -> Path:
        self.require_available()
        policy = role_policy(AgentRole(role), repository, worktree, planner_web=planner_web, test_network=test_network, install_domains=install_domains)
        self.policy_directory.mkdir(parents=True, exist_ok=True)
        file = tempfile.NamedTemporaryFile("w", encoding="utf-8", prefix="stms-", suffix=".json", dir=self.policy_directory, delete=False)
        with file: json.dump(policy.model_dump(mode="json"), file); file.flush()
        return Path(file.name)

    def wrap_command(self, policy_path: Path, argv: list[str]) -> list[str]:
        """Return the actual argv executed under SRT, never merely a policy hint.

        The wrapper is intentionally used by both the deterministic test runner
        and subprocess-based harnesses.  A missing/tampered policy is a hard
        failure instead of a permissive direct execution.
        """
        self.require_available()
        if not policy_path.is_file() or policy_path.parent.resolve() != self.policy_directory.resolve():
            raise InfrastructureError("Sandbox policy is missing or outside the managed policy directory.", "Generate a fresh policy through the configured sandbox runtime.")
        if not argv:
            raise InfrastructureError("Cannot sandbox an empty command.", "Provide a non-empty argv command.")
        return [self.executable, "run", "--policy", str(policy_path), "--", *argv]

    @staticmethod
    def remove_policy(path: Path) -> None:
        if path.parent.name == "stms-policies": path.unlink(missing_ok=True)


class FakeSandboxRuntime:
    """Offline backend that records every wrapped command and policy request."""

    def __init__(self, policy_directory: Path) -> None:
        self.policy_directory = policy_directory.resolve()
        self.prepared: list[tuple[str, Path, Path | None]] = []
        self.wrapped: list[tuple[Path, list[str]]] = []

    def capabilities(self) -> list[Capability]:
        return [
            Capability(name="srt", version="fake", supported=True), Capability(name="filesystem_policy", supported=True),
            Capability(name="network_policy", supported=True), Capability(name="git_policy", supported=True),
            Capability(name="command_wrapping", supported=True),
        ]

    def prepare(self, role: str, repository: Path, worktree: Path | None = None, **_options: object) -> Path:
        self.policy_directory.mkdir(parents=True, exist_ok=True)
        policy = role_policy(AgentRole(role), repository, worktree)
        path = self.policy_directory / f"{role}-{len(self.prepared)}.json"
        path.write_text(json.dumps(policy.model_dump(mode="json")), encoding="utf-8")
        self.prepared.append((role, repository.resolve(), worktree.resolve() if worktree else None))
        return path

    def wrap_command(self, policy_path: Path, argv: list[str]) -> list[str]:
        if not policy_path.is_file():
            raise InfrastructureError("Sandbox policy is missing.", "Prepare a sandbox policy before starting a process.")
        self.wrapped.append((policy_path, list(argv)))
        # The fake is deterministic: recording represents enforcement while the
        # unmodified argv permits offline process fixtures to execute.
        return list(argv)
