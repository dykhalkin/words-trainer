"""Language-independent exercises for multiword learning units."""

from __future__ import annotations

import random
import re
from typing import Any

from psycopg import AsyncConnection

from ..languages import ExerciseContext, language_spec
from ..models import Phrase, Word
from .base import CheckResult, GeneratedExercise, check_text

TOKEN_RE = re.compile(r"[^\W\d_]+(?:[-'’][^\W\d_]+)*", flags=re.UNICODE)


def phrase_gap(lemma: str) -> tuple[str, str] | None:
    """Blank the final fixed lexical component of a phrase or frame."""
    matches = list(TOKEN_RE.finditer(lemma))
    if len(matches) < 2:
        return None
    target = matches[-1]
    gap = "_" * max(4, len(target.group(0)))
    prompt = lemma[: target.start()] + gap + lemma[target.end() :]
    return prompt, target.group(0)


class Cloze:
    @staticmethod
    async def generate(
        word: Word,
        conn: AsyncConnection[dict[str, Any]],
        rng: random.Random,
        context: ExerciseContext,
    ):
        if not isinstance(word, Phrase):
            return None
        gap = phrase_gap(word.lemma)
        if gap is None:
            return None
        prompt_phrase, target = gap
        payload = {
            "prompt": (
                f"Вставь пропущенную часть фразы: «{prompt_phrase}»\n"
                f"Значение: {word.translation}"
            )
        }
        expected = {
            "answer": target,
            "phrase": word.lemma,
            "phrase_type": word.phrase_type,
            "example": word.example,
            "accepted": [target, word.lemma],
        }
        return GeneratedExercise(
            payload, expected, "free_text", context.language, "tutor_on_mismatch"
        )

    @staticmethod
    def check(expected: dict, answer: str) -> CheckResult:
        return check_text(
            answer,
            list(expected.get("accepted") or [expected["answer"]]),
            expected["answer"],
            exact_quality=5,
        )


class Context:
    @staticmethod
    async def generate(
        word: Word,
        conn: AsyncConnection[dict[str, Any]],
        rng: random.Random,
        context: ExerciseContext,
    ):
        if not isinstance(word, Phrase):
            return None
        payload = {
            "prompt": (
                f"Составь короткую реплику {language_spec(context.language).prompt_name_ru}, "
                f"передающую смысл «{word.translation}». Используй изучаемую фразу."
            )
        }
        expected = {
            "answer": word.lemma,
            "phrase": word.lemma,
            "phrase_type": word.phrase_type,
            "example": word.example,
            "accepted": [word.lemma, word.example],
        }
        return GeneratedExercise(
            payload, expected, "free_text", context.language, "tutor_on_mismatch"
        )

    @staticmethod
    def check(expected: dict, answer: str) -> CheckResult:
        return check_text(
            answer,
            list(expected.get("accepted") or [expected["answer"]]),
            expected["answer"],
            exact_quality=5,
        )
