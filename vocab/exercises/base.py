"""Shared answer-checking helpers."""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

TYPO_RATIO = 0.85


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


def is_typo(answer: str, expected: str) -> bool:
    a, e = fold_umlauts(normalize(answer)), fold_umlauts(normalize(expected))
    return difflib.SequenceMatcher(None, a, e).ratio() >= TYPO_RATIO


def check_text(
    answer: str, accepted: list[str], canonical: str,
    *, exact_quality: int = 4, allow_typo: bool = True,
) -> CheckResult:
    """Grade a free-text answer against accepted variants.

    allow_typo=False for grammar drills, where endings are the whole point.
    """
    for variant in accepted:
        if same(answer, variant):
            return CheckResult(True, exact_quality, canonical)
    if allow_typo:
        for variant in accepted:
            if is_typo(answer, variant):
                return CheckResult(True, 3, canonical, note="почти верно (опечатка)")
    return CheckResult(False, 1, canonical)
