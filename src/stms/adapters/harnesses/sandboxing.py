"""Validate and translate an approved STMS sandbox policy at SDK boundaries."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from stms.domain.errors import InfrastructureError
from stms.domain.models import AgentRole, HarnessRequest


def sdk_sandbox_options(request: HarnessRequest) -> dict[str, Any]:
    """Return only controls that were derived from a managed policy file.

    A provider SDK must receive these controls explicitly; a path in ``tools`` is
    not itself sandbox enforcement.  The SDK calls below reject unsupported
    controls instead of dropping them.
    """
    value = request.tools.get("sandbox_policy")
    if not isinstance(value, str) or not value:
        raise InfrastructureError("Harness has no prepared sandbox policy.", "Prepare a role-specific STMS sandbox policy before starting the agent.")
    path = Path(value)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise InfrastructureError("Harness sandbox policy is unreadable.", "Regenerate the policy with the configured sandbox runtime.") from error
    if data.get("role") != request.role.value or data.get("allow_git_mutation") is not False:
        raise InfrastructureError("Harness sandbox policy does not match the requested role.", "Generate a fresh least-privilege policy; direct Git mutation is never allowed.")
    cwd = str(Path(request.cwd).resolve())
    readable = {str(Path(item).resolve()) for item in data.get("readable_paths", [])}
    writable = {str(Path(item).resolve()) for item in data.get("writable_paths", [])}
    if not any(cwd == item or cwd.startswith(item + "/") for item in readable):
        raise InfrastructureError("Harness cwd is outside its read policy.", "Use the approved repository or worktree as cwd.")
    needs_write = request.role is AgentRole.IMPLEMENTER
    if needs_write != any(cwd == item or cwd.startswith(item + "/") for item in writable):
        raise InfrastructureError("Harness write policy does not match the role.", "Use a writable task worktree only for the implementer.")
    return {
        # These are provider-facing controls, not advisory prompt text.
        "sandbox": "workspace_write" if needs_write else "read_only",
        "network_allowed": bool(data.get("network_allowed", False)),
        "network_domains": list(data.get("network_domains", [])),
        "allow_git_mutation": False,
    }
