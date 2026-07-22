"""Exercise generators and answer checkers.

Each registered generator exposes:
    await generate(word, conn, rng, context) -> GeneratedExercise | None
    check(expected, answer)   -> CheckResult

The result explicitly declares response mode, answer language, and grading policy in
addition to the visible payload and server-side expected data.
"""

from . import choice, cloze, flashcard, grammar
from .base import CheckResult, GeneratedExercise
from ..languages import ExerciseContext

GENERATORS = {
    "choice": choice,
    "flashcard_ru_de": flashcard.RuDe,
    "cloze": cloze,
    "grammar": grammar,
}

__all__ = ["GENERATORS", "CheckResult", "GeneratedExercise", "ExerciseContext"]
