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


def phrase_gap(lemma: str, example: str) -> tuple[str, str] | None:
    """Blank the complete studied phrase when it occurs in the example."""
    if len(TOKEN_RE.findall(lemma)) < 2:
        return None
    target = re.search(
        rf"(?<!\w){re.escape(lemma)}(?!\w)", example, flags=re.IGNORECASE
    )
    if target is None:
        return None
    answer = target.group(0)
    gap = "_" * max(4, len(answer))
    prompt = example[: target.start()] + gap + example[target.end() :]
    return prompt, answer


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
        gap = phrase_gap(word.lemma, word.example)
        if gap is None:
            return None
        sentence, target = gap
        payload = {
            "prompt": (
                f"Вставь изучаемую фразу: {sentence}\nЗначение: {word.translation}"
            )
        }
        expected = {
            "answer": target,
            "phrase": word.lemma,
            "phrase_type": word.phrase_type,
            "example": word.example,
            "accepted": list(dict.fromkeys([target, word.lemma])),
        }
        return GeneratedExercise(
            payload,
            expected,
            "free_text",
            context.language,
            "tutor_on_mismatch",
            objective="recall",
            visible_constraints={"meaning_hint": word.translation},
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
                f"передающую смысл «{word.translation}». Используй фразу «{word.lemma}»."
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
            payload,
            expected,
            "free_text",
            context.language,
            "tutor_on_mismatch",
            objective="target_usage",
            visible_constraints={"required_target": word.lemma},
        )

    @staticmethod
    def check(expected: dict, answer: str) -> CheckResult:
        return check_text(
            answer,
            list(expected.get("accepted") or [expected["answer"]]),
            expected["answer"],
            exact_quality=5,
        )
