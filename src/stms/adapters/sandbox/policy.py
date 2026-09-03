"""Least-privilege role policies, independent of a particular sandbox syntax."""
from __future__ import annotations

from pathlib import Path

from pydantic import Field

from stms.domain.models import AgentRole, StrictModel


class SandboxPolicy(StrictModel):
    role: AgentRole
    readable_paths: list[str] = Field(default_factory=list)
    writable_paths: list[str] = Field(default_factory=list)
    network_allowed: bool = False
    network_domains: list[str] = Field(default_factory=list)
    allow_git_mutation: bool = False


def role_policy(role: AgentRole, repository: Path, worktree: Path | None = None, *, planner_web: bool = False, test_network: bool = False, install_domains: list[str] | None = None) -> SandboxPolicy:
    repo = str(repository.resolve()); target = str((worktree or repository).resolve())
    if role is AgentRole.PLANNER:
        return SandboxPolicy(role=role, readable_paths=[repo], network_allowed=planner_web, network_domains=[])
    if role is AgentRole.IMPLEMENTER:
        return SandboxPolicy(role=role, readable_paths=[target], writable_paths=[target], network_allowed=True, allow_git_mutation=False)
    if role is AgentRole.REVIEWER:
        return SandboxPolicy(role=role, readable_paths=[target], network_allowed=True, allow_git_mutation=False)
    return SandboxPolicy(role=role, readable_paths=[target], writable_paths=[target], network_allowed=test_network or bool(install_domains), network_domains=list(install_domains or []), allow_git_mutation=False)


def policy_satisfies(required: SandboxPolicy, offered: SandboxPolicy) -> bool:
    return (set(required.readable_paths) <= set(offered.readable_paths)
        and set(required.writable_paths) <= set(offered.writable_paths)
        and (not required.network_allowed or offered.network_allowed)
        and set(required.network_domains) <= set(offered.network_domains)
        and (not required.allow_git_mutation or offered.allow_git_mutation))
