"""Test execution whose outcome is solely the deterministic process result."""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from stms.adapters.persistence.artifact_store import LocalArtifactStore
from stms.domain.models import ProcessResult, TestAttempt, TestCommand
from .process_runner import ProcessRunner


class CommandSandbox(Protocol):
    def wrap_command(self, policy_path: Path, argv: list[str]) -> list[str]: ...


class DeterministicTestRunner:
    def __init__(self, process_runner: ProcessRunner | None = None, artifact_store: LocalArtifactStore | None = None, *, sandbox: CommandSandbox | None = None, policy_path: Path | None = None) -> None:
        self.process_runner = process_runner or ProcessRunner(); self.artifact_store = artifact_store
        self.sandbox = sandbox; self.policy_path = policy_path

    def run(self, command: TestCommand, cwd: Path, *, repository: Path | None = None, allow_shell: bool = False) -> ProcessResult:
        root = cwd.resolve(); command_cwd = (root / command.cwd).resolve()
        if root != command_cwd and root not in command_cwd.parents:
            from stms.domain.errors import SecurityError
            raise SecurityError("Test command cwd escapes its worktree.", "Use a relative cwd inside the task or integration worktree.")
        if not command_cwd.is_dir():
            from stms.domain.errors import InfrastructureError
            raise InfrastructureError("Test command cwd does not exist.", "Create the approved working directory or correct TestCommand.cwd.")
        executable = command
        if self.sandbox is not None:
            if self.policy_path is None:
                from stms.domain.errors import InfrastructureError
                raise InfrastructureError("Sandboxed test execution has no policy.", "Prepare a TestRunner sandbox policy before executing tests.")
            executable = command.model_copy(update={"argv": self.sandbox.wrap_command(self.policy_path, command.argv), "cwd": "."})
        return self.process_runner.run(executable, command_cwd, repository=repository or root, allow_shell=allow_shell)

    def run_attempt(self, command: TestCommand, cwd: Path, *, repository: Path | None = None, allow_shell: bool = False) -> TestAttempt:
        result = self.run(command, cwd, repository=repository, allow_shell=allow_shell)
        attempt_id = __import__("uuid").uuid4().hex
        log_path = None
        if self.artifact_store:
            path, artifact_truncated = self.artifact_store.write_test_log(attempt_id, f"$ {' '.join(command.argv)}\n\nSTDOUT\n{result.stdout}\n\nSTDERR\n{result.stderr}")
            result = result.model_copy(update={"truncated": result.truncated or artifact_truncated}); log_path = str(path)
        return TestAttempt(id=attempt_id, command=command, result=result, log_path=log_path)
