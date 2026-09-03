"""Native fallback is only selected explicitly after proving equivalent policy."""
from __future__ import annotations

from stms.domain.errors import InfrastructureError
from .policy import SandboxPolicy, policy_satisfies


class NativeSandboxFallback:
    def __init__(self, offered_policy: SandboxPolicy, *, explicitly_allowed: bool) -> None:
        self.offered_policy = offered_policy; self.explicitly_allowed = explicitly_allowed

    def authorize(self, required: SandboxPolicy) -> SandboxPolicy:
        if not self.explicitly_allowed:
            raise InfrastructureError("Native sandbox fallback is not explicitly allowed.", "Set security.allow_native_fallback only after reviewing native capabilities.")
        if not policy_satisfies(required, self.offered_policy):
            raise InfrastructureError("Native sandbox cannot satisfy the required policy.", "Use SRT or a native sandbox with equivalent filesystem and network restrictions.")
        return self.offered_policy
