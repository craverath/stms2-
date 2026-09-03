"""Deterministic workflow policy helpers."""
from __future__ import annotations

from .models import Severity

_BLOCKING = {1: {Severity.HIGH, Severity.MEDIUM, Severity.LOW}, 2: {Severity.HIGH, Severity.MEDIUM}, 3: {Severity.HIGH}, 4: set()}


def blocking_findings(round_number: int, severities: list[Severity]) -> list[Severity]:
    if round_number not in _BLOCKING: raise ValueError("review round must be between 1 and 4")
    return [severity for severity in severities if severity in _BLOCKING[round_number]]


def escalates_to_human(round_number: int, severities: list[Severity]) -> bool:
    return round_number == 4 and Severity.HIGH in severities


def retry_exhausted(attempts_already_used: int, allowed_retries: int) -> bool:
    return attempts_already_used >= allowed_retries


def planner_gate_required(turns_without_plan: int) -> bool:
    return turns_without_plan >= 10
