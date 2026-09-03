"""Read-only validation performed before a run or a lock is created."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
from typing import Mapping

from stms.application.configuration import load_runtime_config
from stms.domain.errors import ConfigurationError, InfrastructureError
from stms.domain.models import Capability, RunState, RuntimeConfig
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


@dataclass(frozen=True)
class Diagnostic:
    name: str
    status: str
    detail: str


@dataclass(frozen=True)
class DoctorResult:
    diagnostics: tuple[Diagnostic, ...]

    @property
    def ready(self) -> bool:
        return all(item.status != "ERROR" for item in self.diagnostics)


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

    def diagnose(self) -> DoctorResult:
        """Run prerequisite probes without agents, project tests, runs, locks, or worktrees."""
        checks: list[Diagnostic] = []

        root_result = self._git_result("rev-parse", "--show-toplevel")
        root: Path | None = None
        if root_result.returncode:
            checks.append(Diagnostic("git.root", "ERROR", _command_error(root_result)))
        else:
            root = Path(root_result.stdout.strip()).resolve()
            if root != self.repository:
                checks.append(Diagnostic("git.root", "ERROR", f"run from repository root {root}"))
            else:
                checks.append(Diagnostic("git.root", "OK", str(root)))

        for name, args in (
            ("git.head", ("rev-parse", "--verify", "HEAD")),
            ("git.branch", ("symbolic-ref", "--quiet", "--short", "HEAD")),
        ):
            result = self._git_result(*args)
            checks.append(Diagnostic(name, "ERROR" if result.returncode else "OK", _command_error(result) if result.returncode else result.stdout.strip()))

        status = self._git_result("status", "--porcelain", "--untracked-files=no")
        if status.returncode:
            checks.append(Diagnostic("git.clean", "ERROR", _command_error(status)))
        elif status.stdout.strip():
            checks.append(Diagnostic("git.clean", "ERROR", "repository has tracked changes"))
        else:
            checks.append(Diagnostic("git.clean", "OK", "tracked files are clean"))

        name_result = self._git_result("config", "--get", "user.name")
        email_result = self._git_result("config", "--get", "user.email")
        identity = f"{name_result.stdout.strip()} <{email_result.stdout.strip()}>"
        if name_result.returncode or email_result.returncode or not name_result.stdout.strip() or not email_result.stdout.strip():
            checks.append(Diagnostic("git.identity", "ERROR", "git user.name and user.email are required"))
        else:
            checks.append(Diagnostic("git.identity", "OK", identity))

        config: RuntimeConfig | None = None
        config_root = root if root is not None else self.repository
        try:
            config = load_runtime_config(config_root)
            checks.append(Diagnostic("config", "OK", f"valid; digest {config.digest()}"))
        except ConfigurationError as error:
            checks.append(Diagnostic("config", "ERROR", str(error)))

        if config is not None:
            for role, agent in config.agents.items():
                prefix = f"harness.{role.value}"
                harness = self.harnesses.get(agent.harness)
                if harness is None:
                    checks.append(Diagnostic(prefix, "ERROR", f"{agent.harness!r} is unavailable"))
                    continue
                try:
                    capabilities = {item.name: item for item in harness.capabilities()}
                    required = ("sessions", "structured_output", "cwd", "model_effort", "tool_policy")
                    missing = [item for item in required if not capabilities.get(item, Capability(name=item, supported=False)).supported]
                    if missing:
                        checks.append(Diagnostic(prefix, "ERROR", f"missing capabilities: {', '.join(missing)}"))
                        continue
                    probe = getattr(harness, "preflight", None)
                    result = probe(model=agent.model, effort=agent.effort) if callable(probe) else {}
                    unavailable = [item for item in ("authenticated", "model", "effort") if not isinstance(result, dict) or not result.get(item, False)]
                    if unavailable:
                        checks.append(Diagnostic(prefix, "ERROR", f"{agent.harness} failed: {', '.join(unavailable)}"))
                    else:
                        checks.append(Diagnostic(prefix, "OK", f"{agent.harness}; model={agent.model}; effort={agent.effort}; authenticated"))
                except Exception as error:
                    checks.append(Diagnostic(prefix, "ERROR", f"probe failed: {error}"))

            try:
                sandbox_caps = {item.name: item for item in self.sandbox.capabilities()}
                selected = sandbox_caps.get(config.security.sandbox)
                compatible = selected is not None and selected.supported
                if compatible and (config.security.allow_native_fallback or all(item.supported for item in sandbox_caps.values())):
                    checks.append(Diagnostic("sandbox", "OK", f"{config.security.sandbox} is available"))
                else:
                    checks.append(Diagnostic("sandbox", "ERROR", f"{config.security.sandbox} is unavailable or incompatible"))
            except Exception as error:
                checks.append(Diagnostic("sandbox", "ERROR", f"probe failed: {error}"))

            for index, command in enumerate(config.tests.commands):
                command_root = (config_root / command.cwd).resolve()
                in_repository = command_root == config_root or config_root in command_root.parents
                if not in_repository or not command_root.is_dir():
                    checks.append(Diagnostic(f"test.{index}", "ERROR", f"invalid cwd {command.cwd!r}"))
                    continue
                candidate = command_root / command.argv[0]
                executable = candidate.is_file() if "/" in command.argv[0] else shutil.which(command.argv[0]) is not None
                detail = f"cwd={command.cwd}; executable={command.argv[0]}"
                checks.append(Diagnostic(f"test.{index}", "OK" if executable else "ERROR", detail if executable else f"unavailable {detail}"))
            if not config.tests.commands:
                checks.append(Diagnostic("tests", "OK", "no configured test commands"))

        from stms.application.run_admin import inspect_runs, repository_lock
        try:
            records, issues = inspect_runs(config_root)
            active = [record.run_id for record in records if record.snapshot.state not in {RunState.COMPLETED, RunState.FAILED}]
            if issues:
                checks.append(Diagnostic("runs", "ERROR", "; ".join(f"{item.run_id}: {item.message}" for item in issues)))
            elif active:
                checks.append(Diagnostic("runs", "ERROR", f"active/resumable: {', '.join(active)}"))
            else:
                checks.append(Diagnostic("runs", "OK", f"{len(records)} terminal run(s)"))
        except Exception as error:
            checks.append(Diagnostic("runs", "ERROR", str(error)))
        try:
            lock = repository_lock(config_root)
            if lock is None:
                checks.append(Diagnostic("lock", "OK", "no repository lock"))
            elif lock.alive:
                checks.append(Diagnostic("lock", "ERROR", f"{lock.run_id}; process {lock.pid} is alive"))
            else:
                checks.append(Diagnostic("lock", "OK", f"stale lock for {lock.run_id}; process {lock.pid} is not alive"))
        except Exception as error:
            checks.append(Diagnostic("lock", "ERROR", str(error)))
        return DoctorResult(tuple(checks))

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

    def _git_result(self, *args: str) -> subprocess.CompletedProcess[str]:
        command = ["git", *args]
        try:
            return subprocess.run(command, cwd=self.repository, text=True, capture_output=True)
        except OSError as error:
            return subprocess.CompletedProcess(command, 127, "", str(error))


def _command_error(result: subprocess.CompletedProcess[str]) -> str:
    return result.stderr.strip() or result.stdout.strip() or "command failed"
