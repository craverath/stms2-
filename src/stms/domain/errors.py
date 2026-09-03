"""Actionable errors that deliberately exclude secret values."""

class StmsError(Exception):
    def __init__(self, message: str, corrective_action: str) -> None:
        super().__init__(message)
        self.corrective_action = corrective_action

    def __str__(self) -> str:
        return f"{super().__str__()} Corrective action: {self.corrective_action}"


class ConfigurationError(StmsError): pass
class DomainError(StmsError): pass
class InvalidTransitionError(DomainError): pass
class InfrastructureError(StmsError): pass
class SecurityError(StmsError): pass
class CompatibilityError(StmsError): pass
class LockError(InfrastructureError): pass


class StructuredOutputError(InfrastructureError):
    """A provider exhausted the bounded repair attempts for a typed response."""

    def __init__(self, attempts: int) -> None:
        super().__init__(
            f"Agent output did not match its required schema after {attempts} repair attempts.",
            "Pause the run and ask a human to correct the request or provider configuration.",
        )
        self.attempts = attempts


class SessionLostError(InfrastructureError):
    """The provider no longer has a resumable session."""
