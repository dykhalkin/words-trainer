"""Shared answer-checking helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class GeneratedExercise:
    payload: dict[str, Any]
    expected: dict[str, Any]
    response_mode: Literal["choice", "free_text"]
    answer_language: str | None
    grading_policy: Literal["deterministic", "tutor_on_mismatch"]

    def __iter__(self):
        """Keep tuple unpacking compatible for low-level generator callers."""
        yield self.payload
        yield self.expected


@dataclass
class CheckResult:
    correct: bool
    quality: int  # SM-2 quality 1..5
    expected: str  # canonical answer to show the user
    note: str = ""  # e.g. "опечатка", "пропущен артикль"


def normalize(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[.!?,;:]+$", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


def fold_umlauts(text: str) -> str:
    for src, dst in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(src, dst)
    return text


def same(a: str, b: str) -> bool:
    a, b = normalize(a), normalize(b)
    return a == b or fold_umlauts(a) == fold_umlauts(b)


def check_text(
    answer: str, accepted: list[str], canonical: str,
    *, exact_quality: int = 4, allow_typo: bool = False,
) -> CheckResult:
    """Accept only normalized variants; semantic mismatches are graded by the tutor."""
    for variant in accepted:
        if same(answer, variant):
            return CheckResult(True, exact_quality, canonical)
    return CheckResult(False, 1, canonical)
