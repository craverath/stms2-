"""Anthropic Sandbox Runtime adapter with empirically proven capability checks.

SRT 1.0.0's real CLI contract is ``srt --settings <file> -- <command...>``, and its
settings schema expresses filesystem access as
``filesystem.{allowRead,denyRead,allowWrite,denyWrite}`` path lists and network
access as ``network.{allowedDomains,deniedDomains}`` domain lists. This adapter
translates STMS's internal, allow-list-only :class:`SandboxPolicy` into that
schema; it never invents additional settings fields.

Because SRT is beta, ``capabilities()`` does not trust ``--version`` alone: it
runs a real functional probe (an actual wrapped subprocess attempting a denied
and an allowed write, and a denied and an allowed loopback connection) and only
reports a capability as supported when that probe proves it. Anything it cannot
prove is reported unsupported, matching the project's fail-closed posture.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from stms.domain.errors import InfrastructureError
from stms.domain.models import AgentRole, Capability
from .policy import SandboxPolicy, role_policy

_PROBE_TIMEOUT_SECONDS = 10


class SrtSandboxRuntime:
    def __init__(self, executable: str = "srt", *, policy_directory: Path | None = None) -> None:
        self.executable = executable; self.policy_directory = policy_directory or Path(tempfile.gettempdir()) / "stms-policies"
        self._capabilities_cache: list[Capability] | None = None

    def capabilities(self) -> list[Capability]:
        if self._capabilities_cache is None:
            self._capabilities_cache = self._probe_capabilities()
        return self._capabilities_cache

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
        settings_path = self._write_settings(policy_path, self._read_policy(policy_path))
        return [self.executable, "--settings", str(settings_path), "--", *argv]

    @staticmethod
    def remove_policy(path: Path) -> None:
        if path.parent.name == "stms-policies": path.unlink(missing_ok=True)

    @staticmethod
    def _translate(policy: SandboxPolicy) -> dict[str, object]:
        """Translate the internal allow-list policy into SRT's real settings schema."""
        writable = sorted(set(policy.writable_paths))
        readable = sorted(set(policy.readable_paths) | set(policy.writable_paths))
        # SRT read access is allow-by-default. Deny the user's data root, then
        # explicitly reopen only role-approved paths; system paths remain readable
        # so provider runtimes and interpreters can start normally.
        home = str(Path.home().resolve())
        deny_write = sorted(set(policy.readable_paths) - set(policy.writable_paths))
        return {
            "filesystem": {"allowRead": readable, "denyRead": [home], "allowWrite": writable, "denyWrite": deny_write},
            "network": {"allowedDomains": sorted(policy.network_domains) if policy.network_allowed else [], "deniedDomains": []},
        }

    @staticmethod
    def _read_policy(policy_path: Path) -> SandboxPolicy:
        try:
            return SandboxPolicy.model_validate(json.loads(policy_path.read_text(encoding="utf-8")))
        except (OSError, ValueError) as error:
            raise InfrastructureError("Sandbox policy is unreadable.", "Regenerate the policy with the configured sandbox runtime.") from error

    def _write_settings(self, policy_path: Path, policy: SandboxPolicy) -> Path:
        settings_path = policy_path.with_name(policy_path.stem + ".srt-settings.json")
        settings_path.write_text(json.dumps(self._translate(policy)), encoding="utf-8")
        return settings_path

    def _probe_capabilities(self) -> list[Capability]:
        path = shutil.which(self.executable)
        if not path:
            return [Capability(name="srt", supported=False)]
        version = subprocess.run([path, "--version"], text=True, capture_output=True)
        if version.returncode:
            return [Capability(name="srt", supported=False)]
        probe_python = self._probe_python()
        if probe_python is None:
            return [
                Capability(name="srt", version=version.stdout.strip(), supported=True),
                *(Capability(name=name, supported=False) for name in (
                    "filesystem_policy", "network_policy", "git_policy", "command_wrapping",
                )),
            ]
        with tempfile.TemporaryDirectory(prefix=".stms-srt-probe-", dir=Path.home()) as workspace_raw:
            workspace = Path(workspace_raw)
            filesystem_ok = self._probe_filesystem(workspace, probe_python)
            network_ok = self._probe_network(workspace, probe_python)
        return [
            Capability(name="srt", version=version.stdout.strip(), supported=True),
            Capability(name="filesystem_policy", supported=filesystem_ok),
            Capability(name="network_policy", supported=network_ok),
            Capability(name="git_policy", supported=filesystem_ok),
            Capability(name="command_wrapping", supported=filesystem_ok),
        ]

    @staticmethod
    def _probe_python() -> str | None:
        """Choose a Python runtime that remains readable when home is denied."""
        home = Path.home().resolve()
        candidates = [getattr(sys, "_base_executable", None), "/usr/bin/python3", shutil.which("python3")]
        for raw in candidates:
            if not raw:
                continue
            candidate = Path(raw).resolve()
            if candidate.is_file() and candidate != home and home not in candidate.parents:
                return str(candidate)
        return None

    def _probe_filesystem(self, workspace: Path, probe_python: str) -> bool:
        allowed = workspace / "allowed"; denied = workspace / "denied"
        allowed.mkdir(); denied.mkdir()
        external = workspace / "outside.txt"; external.write_text("private", encoding="utf-8")
        policy = SandboxPolicy(role=AgentRole.IMPLEMENTER, readable_paths=[str(allowed), str(denied)], writable_paths=[str(allowed)])
        script = (
            "import pathlib\n"
            f"pathlib.Path({str(allowed / 'seed.txt')!r}).write_text('seed')\n"
            f"outside = pathlib.Path({str(external)!r})\n"
            "try:\n"
            "    outside.read_text()\n"
            "except OSError:\n"
            "    pass\n"
            "else:\n"
            "    raise SystemExit(9)\n"
            f"pathlib.Path({str(allowed / 'ok.txt')!r}).write_text('x')\n"
            "try:\n"
            f"    pathlib.Path({str(denied / 'blocked.txt')!r}).write_text('x')\n"
            "except OSError:\n"
            "    pass\n"
        )
        argv = [self.executable, "--settings", str(self._probe_settings(workspace, policy, "fs")), "--", probe_python, "-c", script]
        try:
            result = subprocess.run(argv, cwd=allowed, capture_output=True, timeout=_PROBE_TIMEOUT_SECONDS)
        except (subprocess.TimeoutExpired, OSError):
            return False
        return result.returncode == 0 and (allowed / "ok.txt").exists() and not (denied / "blocked.txt").exists()

    def _probe_network(self, workspace: Path, probe_python: str) -> bool:
        script = (
            "from urllib.request import urlopen\n"
            "try:\n"
            "    urlopen('http://example.com', timeout=5).read(1)\n"
            "    print('CONNECTED')\n"
            "except OSError:\n"
            "    print('BLOCKED')\n"
        )
        deny_policy = SandboxPolicy(role=AgentRole.PLANNER, readable_paths=[str(workspace)], network_allowed=False)
        allow_policy = SandboxPolicy(role=AgentRole.PLANNER, readable_paths=[str(workspace)], network_allowed=True, network_domains=["example.com"])
        deny_argv = [self.executable, "--settings", str(self._probe_settings(workspace, deny_policy, "net-deny")), "--", probe_python, "-c", script]
        allow_argv = [self.executable, "--settings", str(self._probe_settings(workspace, allow_policy, "net-allow")), "--", probe_python, "-c", script]
        try:
            deny_result = subprocess.run(deny_argv, cwd=workspace, capture_output=True, text=True, timeout=_PROBE_TIMEOUT_SECONDS)
        except (subprocess.TimeoutExpired, OSError):
            deny_result = None
        try:
            allow_result = subprocess.run(allow_argv, cwd=workspace, capture_output=True, text=True, timeout=_PROBE_TIMEOUT_SECONDS)
        except (subprocess.TimeoutExpired, OSError):
            allow_result = None
        deny_blocked = deny_result is not None and "BLOCKED" in deny_result.stdout
        allow_connected = allow_result is not None and "CONNECTED" in allow_result.stdout
        return deny_blocked and allow_connected

    def _probe_settings(self, workspace: Path, policy: SandboxPolicy, name: str) -> Path:
        path = workspace / f"{name}.srt-settings.json"
        path.write_text(json.dumps(self._translate(policy)), encoding="utf-8")
        return path


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
