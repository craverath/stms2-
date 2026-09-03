"""Deterministic workflow policy helpers."""
from __future__ import annotations

from collections.abc import Mapping

from .models import Severity


def blocking_findings(
    round_number: int,
    severities: list[Severity],
    policy: Mapping[str, list[Severity]],
) -> list[Severity]:
    key = f"round_{round_number}"
    if key not in policy:
        raise ValueError("review round must be configured between 1 and 4")
    blocking = set(policy[key])
    return [severity for severity in severities if severity in blocking]


def escalates_to_human(
    round_number: int,
    severities: list[Severity],
    policy: Mapping[str, list[Severity]],
) -> bool:
    escalated = set(policy.get(f"round_{round_number}", []))
    return any(severity in escalated for severity in severities)


def retry_exhausted(attempts_already_used: int, allowed_retries: int) -> bool:
    return attempts_already_used >= allowed_retries


def planner_gate_required(turns_without_plan: int) -> bool:
    return turns_without_plan >= 10
