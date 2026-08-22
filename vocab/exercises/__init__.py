"""Exercise generators and answer checkers.

Each registered generator exposes:
    await generate(word, conn, rng, context) -> GeneratedExercise | None
    check(expected, answer)   -> CheckResult

The result explicitly declares response mode, answer language, and grading policy in
addition to the visible payload and server-side expected data.
"""

from . import choice, choice_ru_de, cloze, context, flashcard, grammar, phrase
from .base import CheckResult, GeneratedExercise
from ..languages import ExerciseContext

GENERATORS = {
    "choice": choice,
    "choice_ru_de": choice_ru_de,
    "flashcard_ru_de": flashcard.RuDe,
    "cloze": cloze,
    "context_llm": context,
    "grammar": grammar,
    "phrase_cloze": phrase.Cloze,
    "phrase_context": phrase.Context,
}

__all__ = ["GENERATORS", "CheckResult", "GeneratedExercise", "ExerciseContext"]
