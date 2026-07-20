"""Exercise generators and answer checkers.

Each module exposes:
    await generate(word, conn, rng, context) -> (payload, expected) | None
    check(expected, answer)   -> CheckResult

payload  — what the user sees (prompt, options, ...), JSON-safe
expected — internal data needed to grade the answer, JSON-safe
"""

from . import choice, cloze, flashcard, grammar
from .base import CheckResult
from ..languages import ExerciseContext

GENERATORS = {
    "choice": choice,
    "flashcard_de_ru": flashcard.DeRu,
    "flashcard_ru_de": flashcard.RuDe,
    "cloze": cloze,
    "grammar": grammar,
}

__all__ = ["GENERATORS", "CheckResult", "ExerciseContext"]
