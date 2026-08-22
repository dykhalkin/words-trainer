"""Checking for LLM-generated context exercises."""

from __future__ import annotations

from .base import CheckResult, check_text


def check(expected: dict, answer: str) -> CheckResult:
    canonical = str(expected["answer"])
    accepted = [str(value) for value in expected.get("accepted") or [canonical]]
    return check_text(answer.strip(), accepted, canonical, exact_quality=5)
