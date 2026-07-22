"""Target-language recall flashcards plus the legacy native-answer checker.

Only ``RuDe`` is registered for new tasks: the prompt may contain a native-language
translation, but the learner always types the target-language lemma. ``DeRu`` remains
solely so historical task data can still be interpreted during the migration window.
"""

from __future__ import annotations

import random
import re
from typing import Any

from psycopg import AsyncConnection

from ..languages import ExerciseContext, language_spec
from ..models import Noun, VerbPrep, Word
from .base import CheckResult, GeneratedExercise, check_text


def translation_variants(translation: str) -> list[str]:
    """'думать о чем-то' -> itself; 'двигать, шевелить' -> both parts."""
    parts = re.split(r"[,/]", translation)
    variants = [translation] + [p for p in parts if p.strip()]
    return variants


class DeRu:
    """Legacy, unregistered checker for historical ``flashcard_de_ru`` rows."""

    @staticmethod
    async def generate(
        word: Word,
        conn: AsyncConnection[dict[str, Any]],
        rng: random.Random,
        context: ExerciseContext,
    ):
        payload = {"prompt": f"Переведи на русский: «{word.lemma}»"}
        expected = {"translation": word.translation}
        return payload, expected

    @staticmethod
    def check(expected: dict, answer: str) -> CheckResult:
        translation = expected["translation"]
        return check_text(answer, translation_variants(translation), translation)


class RuDe:
    @staticmethod
    async def generate(
        word: Word,
        conn: AsyncConnection[dict[str, Any]],
        rng: random.Random,
        context: ExerciseContext,
    ):
        hints = {
            "noun": " (с артиклем)",
            "verb_prep": " (с предлогом и падежом)",
        }
        payload = {
            "prompt": (
                f"Скажи {language_spec(context.language).prompt_name_ru}: "
                f"«{word.translation}»{hints.get(word.kind, '')}"
            )
        }
        expected = {
            "lemma": word.lemma,
            "kind": word.kind,
            "headword": word.headword,
        }
        return GeneratedExercise(
            payload, expected, "free_text", context.language, "tutor_on_mismatch"
        )

    @staticmethod
    def check(expected: dict, answer: str) -> CheckResult:
        lemma = expected["lemma"]
        return check_text(answer, [lemma], lemma, exact_quality=5)
